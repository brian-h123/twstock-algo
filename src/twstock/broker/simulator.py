import warnings
from datetime import date
from decimal import Decimal

import exchange_calendars as xcals
import pandas as pd

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.trades import LotLedger
from twstock.broker.account import Account, fold
from twstock.broker.base import BrokerBase
from twstock.broker.corporate_actions import CashMutation, DatedMutation, ShareMutation, decompose
from twstock.broker.events import BarBatch, BrokerEvent, EventKind
from twstock.broker.fill import FillModel
from twstock.broker.order import Order, OrderId, OrderState, Side
from twstock.broker.rejection import (
    DerivedPriceLimits,
    PriceLimitProvider,
    RejectionReason,
    SuspensionCalendar,
    classify_missing_bar,
)
from twstock.broker.settlement import SettlementModel

_XTAI = xcals.get_calendar("XTAI")


def _max_affordable_shares(
    buying_power: Decimal,
    ticker: str,
    reference_price: Decimal,
    cost_model: TaiwanCostModel,
    ceiling: int,
) -> int:
    lo, hi = 0, ceiling
    while lo < hi:
        mid = (lo + hi + 1) // 2
        fill_price, commission = cost_model.buy_costs(ticker, mid, float(reference_price))
        cost = Decimal(str(fill_price)) * mid + Decimal(str(commission))
        hi, lo = (hi, mid) if cost <= buying_power else (mid - 1, lo)
    return lo


def _mutation_to_event(ticker: str, mutation: DatedMutation) -> BrokerEvent:
    if isinstance(mutation, ShareMutation):
        return BrokerEvent(
            ts=mutation.effective_date,
            kind=EventKind.SPLIT,
            order_id=None,
            payload={"ticker": ticker, "ratio": str(mutation.ratio)},
        )
    elif isinstance(mutation, CashMutation):
        return BrokerEvent(
            ts=mutation.declared_date,
            kind=EventKind.DIVIDEND,
            order_id=None,
            payload={"amount": str(mutation.amount), "pay_date": mutation.settle_date.isoformat()},
        )
    else:
        raise ValueError(f"unhandled mutation type: {type(mutation)}")


class BrokerSimulator(BrokerBase):
    def __init__(
        self,
        *,
        capital: Decimal,
        cost_model: TaiwanCostModel,
        fill_model: FillModel,
        settlement: SettlementModel,
        suspension_calendar: SuspensionCalendar | None = None,
        price_limit_provider: PriceLimitProvider | None = None,
    ) -> None:
        self._account: Account = Account(initial_capital=capital, settlement=settlement)
        self._event_buffer: list[BrokerEvent] = []
        self._working_orders: dict[OrderId, Order] = {}
        self._prior_volume: dict[str, int] = {}
        self.cost_model = cost_model
        self.fill_model = fill_model
        self.suspension_calendar = (
            suspension_calendar if suspension_calendar is not None else SuspensionCalendar()
        )
        self.price_limit_provider = (
            price_limit_provider if price_limit_provider is not None else DerivedPriceLimits()
        )

    def _emit(self, event: BrokerEvent) -> None:
        fold([event], into=self._account)
        self._event_buffer.append(event)

    def submit(self, order: Order) -> OrderId:
        if order.order_id in self._account._orders:
            raise ValueError(f"order_id {order.order_id!r} already submitted")
        self._working_orders[order.order_id] = order
        self._emit(
            BrokerEvent(
                ts=order.decided_at,
                kind=EventKind.SUBMITTED,
                order_id=order.order_id,
                payload={"quantity": str(order.quantity)},
            )
        )
        return order.order_id

    def cancel(self, order_id: OrderId) -> None:
        if order_id not in self._working_orders:
            if order_id in self._account._orders:
                status = self._account._orders[order_id].status
                raise ValueError(f"cannot cancel order {order_id!r}: already {status.value}")
            raise ValueError(f"cannot cancel order {order_id!r}: no such order")

        order = self._working_orders.pop(order_id)
        self._emit(
            BrokerEvent(ts=order.decided_at, kind=EventKind.CANCEL, order_id=order_id, payload={})
        )

    def query(self, order_id: OrderId) -> OrderState:
        if order_id not in self._account._orders:
            raise ValueError(f"no order found for order id: {order_id!r}")
        return self._account._orders[order_id]

    def drain_events(self) -> list[BrokerEvent]:
        events = self._event_buffer
        self._event_buffer = []
        return events

    def _reject(self, order: Order, ts: date, reason: RejectionReason) -> None:
        self._emit(
            BrokerEvent(
                ts, kind=EventKind.REJECT, order_id=order.order_id, payload={"reason": reason.value}
            )
        )
        del self._working_orders[order.order_id]

    def _fill(
        self, order: Order, ts: date, reference_price: Decimal, executed: int, remaining: int
    ) -> None:
        if order.side == Side.BUY:
            fill_price, commission = self.cost_model.buy_costs(
                order.ticker, executed, float(reference_price)
            )
            total_cost = Decimal(str(commission))
        else:
            fill_price, commission, tax = self.cost_model.sell_costs(
                order.ticker, executed, float(reference_price)
            )
            total_cost = Decimal(str(commission)) + Decimal(str(tax))

        kind = EventKind.FILL if executed == remaining else EventKind.PARTIAL_FILL
        self._emit(
            BrokerEvent(
                ts,
                kind,
                order_id=order.order_id,
                payload={
                    "ticker": order.ticker,
                    "side": order.side.value,
                    "shares": str(executed),
                    "fill_price": str(Decimal(str(fill_price))),
                    "total_cost": str(total_cost),
                },
            )
        )
        if kind == EventKind.PARTIAL_FILL:
            self._emit(BrokerEvent(ts, kind=EventKind.EXPIRE, order_id=order.order_id, payload={}))

        del self._working_orders[order.order_id]

    def _resolve_order(self, order: Order, bar_batch: BarBatch) -> None:
        remaining = self._account._orders[order.order_id].remaining_quantity
        ticker = order.ticker
        bar = bar_batch.bars.get(ticker)

        if bar is None:
            reason = classify_missing_bar(
                ticker, bar_batch.date, has_bar=False, suspension_calendar=self.suspension_calendar
            )
            assert reason is not None, f"process_bar called with non-session date {bar_batch.date}"
            self._reject(order, bar_batch.date, reason)
            return

        band = self.price_limit_provider.band(ticker, bar.date)
        locked = band is not None and (
            (order.side == Side.BUY and bar.open >= band[1])
            or (order.side == Side.SELL and bar.open <= band[0])
        )
        if locked:
            self._reject(order, bar_batch.date, RejectionReason.PRICE_LIMIT_LOCK)
            return

        if ticker in self._prior_volume:
            volume_ceiling = self.fill_model.fillable(remaining, self._prior_volume[ticker])
        else:
            volume_ceiling = remaining

        if order.side == Side.BUY:
            cash_ceiling = _max_affordable_shares(
                buying_power=self._account.buying_power(bar_batch.date),
                ticker=ticker,
                reference_price=bar.open,
                cost_model=self.cost_model,
                ceiling=volume_ceiling,
            )
            executed = min(volume_ceiling, cash_ceiling)
        else:
            executed = volume_ceiling

        if executed == 0:
            reason = RejectionReason.INSUFFICIENT_BUYING_POWER if order.side == Side.BUY else None
            assert reason is not None, f"unreachable, zero-fill sell for {ticker}"
            self._reject(order, bar_batch.date, reason)
            return

        self._fill(order, bar_batch.date, bar.open, executed, remaining)

    def process_bar(self, bar_batch: BarBatch) -> None:
        if not _XTAI.is_session(pd.Timestamp(bar_batch.date)):
            warnings.warn(
                f"process_bar called with non-session date {bar_batch.date}; skipping bar"
            )
            return

        self._emit(BrokerEvent(ts=bar_batch.date, kind=EventKind.SETTLE, order_id=None, payload={}))

        for ticker, bar in bar_batch.bars.items():
            self.price_limit_provider.observe(bar)

            position = self._account.positions.get(ticker, LotLedger())
            for action in bar.corporate_actions:
                for mutation in decompose(action, position, bar.date):
                    self._emit(_mutation_to_event(ticker, mutation))

        sells = [o for o in self._working_orders.values() if o.side == Side.SELL]
        buys = [o for o in self._working_orders.values() if o.side == Side.BUY]

        for order in sells + buys:
            self._resolve_order(order, bar_batch)

        for ticker, bar in bar_batch.bars.items():
            self._prior_volume[ticker] = bar.volume
