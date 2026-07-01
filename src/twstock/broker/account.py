from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal

from twstock.backtest.trades import LotLedger, Side
from twstock.broker.events import BrokerEvent, EventKind
from twstock.broker.order import OrderId, OrderState, OrderStatus, assert_legal_transition
from twstock.broker.settlement import DatedCashFlow, DatedLedger, Fill, SettlementModel


class Account:
    """Account state, rebuilt by folding an event stream via fold().

    Holds a DatedLedger for cash, a per-ticker LotLedger for open
    positions, and an OrderState dict for pending and terminal orders.
    State is never mutated directly — use fold() to apply BrokerEvents.

    Args:
        initial_capital: Starting settled cash in NT$.
        settlement: The cash-settlement policy for this account.
    """

    def __init__(self, initial_capital: Decimal, settlement: SettlementModel) -> None:
        self._ledger = DatedLedger()
        self._ledger.settled_cash = initial_capital
        self.settlement = settlement
        self.positions: dict[str, LotLedger] = {}
        self._orders: dict[OrderId, OrderState] = {}

    def buying_power(self, as_of: date) -> Decimal:
        """Return spendable cash as of as_of under the account's settlement model.

        Args:
            as_of: The date for which buying power is computed.

        Returns:
            Maximum cash available for new buys.
        """
        return self.settlement.buying_power(ledger=self._ledger, t=as_of)

    def mark_to_market(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Return the market value of all open positions at the given prices.

        Positions with zero open shares are skipped. Tickers absent from
        prices are also skipped (not valued at zero).

        Args:
            prices: Map of ticker → current mark price in NT$.

        Returns:
            Total market value of all open lots.
        """
        result = Decimal(0)
        for ticker, lot_ledger in self.positions.items():
            if lot_ledger.open_shares == 0:
                continue
            if ticker in prices:
                result += lot_ledger.open_shares * prices[ticker]

        return result

    def total_value(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Return total account equity: settled cash + pending cash + position value.

        Args:
            prices: Map of ticker → current mark price, forwarded to
                mark_to_market.

        Returns:
            Net asset value of the account in NT$.
        """
        pending_sum = sum((cf.amount for cf in self._ledger.pending), Decimal(0))
        return self._ledger.settled_cash + pending_sum + self.mark_to_market(prices=prices)


def fold(events: Iterable[BrokerEvent], into: Account) -> Account:
    """Fold an iterable of BrokerEvents into account and order state.

    Pure over the event stream: replaying the same events on a fresh
    Account always produces identical final state (O10). The same fold
    rehydrates live state for the broker adapter.

    Args:
        events: Ordered stream of BrokerEvents to apply.
        into: Account to mutate; typically a fresh Account for full
            replay, or a partially-hydrated account for incremental
            updates.

    Returns:
        The same Account instance after applying all events.

    Raises:
        ValueError: If an unrecognized EventKind is encountered.
    """
    for event in events:
        if event.kind == EventKind.SUBMITTED:
            assert event.order_id is not None
            into._orders[event.order_id] = OrderState(
                order_id=event.order_id,
                status=OrderStatus.PENDING,
                filled_quantity=0,
                remaining_quantity=int(event.payload["quantity"]),
                avg_fill_price=None,
            )
        elif event.kind in (EventKind.PARTIAL_FILL, EventKind.FILL):
            assert event.order_id is not None
            current_state = into._orders[event.order_id]
            new_status = (
                OrderStatus.PARTIALLY_FILLED
                if event.kind == EventKind.PARTIAL_FILL
                else OrderStatus.FILLED
            )
            assert_legal_transition(current_state.status, new_status)

            ticker = event.payload["ticker"]
            side = Side(event.payload["side"])
            shares = int(event.payload["shares"])
            fill_price = Decimal(event.payload["fill_price"])
            total_cost = Decimal(event.payload["total_cost"])

            if ticker not in into.positions:
                into.positions[ticker] = LotLedger()
            into.positions[ticker].on_fill(side, shares, float(fill_price), float(total_cost))

            fill = Fill(side, shares, fill_price, total_cost)
            into.settlement.on_trade(into._ledger, fill, event.ts)

            new_filled = current_state.filled_quantity + shares
            new_remaining = (
                current_state.remaining_quantity - shares
                if event.kind == EventKind.PARTIAL_FILL
                else 0
            )

            if current_state.avg_fill_price is None:
                new_avg = float(fill_price)
            else:
                prev = current_state.avg_fill_price * current_state.filled_quantity
                new_avg = (prev + float(fill_price) * shares) / new_filled

            into._orders[event.order_id] = OrderState(
                order_id=event.order_id,
                status=new_status,
                filled_quantity=new_filled,
                remaining_quantity=new_remaining,
                avg_fill_price=new_avg,
            )
        elif event.kind in (EventKind.REJECT, EventKind.EXPIRE, EventKind.CANCEL):
            assert event.order_id is not None
            terminal = {
                EventKind.REJECT: OrderStatus.REJECTED,
                EventKind.EXPIRE: OrderStatus.EXPIRED,
                EventKind.CANCEL: OrderStatus.CANCELLED,
            }[event.kind]
            current_state = into._orders[event.order_id]
            assert_legal_transition(current_state.status, terminal)
            into._orders[event.order_id] = OrderState(
                order_id=event.order_id,
                status=terminal,
                filled_quantity=current_state.filled_quantity,
                remaining_quantity=current_state.remaining_quantity,
                avg_fill_price=current_state.avg_fill_price,
            )
        elif event.kind == EventKind.DIVIDEND:
            into._ledger.pending.append(
                DatedCashFlow(
                    amount=Decimal(event.payload["amount"]),
                    settle_date=date.fromisoformat(event.payload["pay_date"]),
                )
            )
        elif event.kind == EventKind.SPLIT:
            ticker = event.payload["ticker"]
            ratio = float(event.payload["ratio"])
            if ticker in into.positions:
                into.positions[ticker].on_split(ratio)
        elif event.kind == EventKind.SETTLE:
            into._ledger.mature(event.ts)
        else:
            raise ValueError(f"unhandled event kind: {event.kind}")

    return into
