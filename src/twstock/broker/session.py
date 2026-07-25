import warnings
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import ClassVar, cast

import pandas as pd

from twstock.backtest.allocator import Allocator
from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.cross_sectional import CrossSectionalStrategy
from twstock.backtest.portfolio import Portfolio
from twstock.backtest.rebalance import RebalanceContext, RebalanceSchedule
from twstock.backtest.reconciler import budget_weights, reconcile
from twstock.backtest.strategy import Strategy
from twstock.broker.account import Account, fold
from twstock.broker.events import BarBatch, BrokerEvent, EventKind, EventLog
from twstock.broker.feed import BarFeed
from twstock.broker.fill import FillModel
from twstock.broker.order import Order, OrderId, Side, TimeInForce
from twstock.broker.rejection import PriceLimitProvider, SuspensionCalendar
from twstock.broker.result import SimResult
from twstock.broker.settlement import SettlementModel
from twstock.broker.simulator import BrokerSimulator
from twstock.config import BROKER_SESSION_LOG_DIR
from twstock.data.api import get_adjusted_history


def _current_weights(account: Account, last_raw_close: dict[str, Decimal]) -> pd.Series:
    equity = account.total_value(prices=last_raw_close)
    if equity <= 0:
        return pd.Series(dtype=float)
    return pd.Series(
        {
            t: float(ledger.open_shares * last_raw_close.get(t, Decimal(0)) / equity)
            for t, ledger in account.positions.items()
            if ledger.open_shares
        },
        dtype=float,
    )


def _portfolio_shim(account: Account, equity: float) -> SimpleNamespace:
    return SimpleNamespace(
        equity=equity,
        positions={
            t: SimpleNamespace(shares=ledger.open_shares)
            for t, ledger in account.positions.items()
            if ledger.open_shares
        },
    )


def _trade_row(event: BrokerEvent, bar_batch: BarBatch, cost_model: TaiwanCostModel) -> dict:
    ticker = event.payload["ticker"]
    side = event.payload["side"]
    shares = int(event.payload["shares"])
    fill_price = Decimal(event.payload["fill_price"])
    reference_price = float(bar_batch.bars[ticker].open)

    if side == "buy":
        _, commission = cost_model.buy_costs(ticker, shares, reference_price)
        tax = 0.0
    else:
        _, commission, tax = cost_model.sell_costs(ticker, shares, reference_price)

    notional = fill_price * shares
    return {
        "timestamp": pd.Timestamp(bar_batch.date),
        "ticker": ticker,
        "side": side,
        "shares": shares,
        "fill_price": float(fill_price),
        "commission": float(commission),
        "tax": float(tax),
        "notional": float(notional),
        "total_cost": float(commission) + float(tax),
    }


class PaperSession:
    _next_session_seq: ClassVar[int] = 0

    def __init__(
        self,
        *,
        strategy: Strategy | CrossSectionalStrategy,
        tickers: list[str],
        feed: BarFeed,
        capital: Decimal,
        cost_model: TaiwanCostModel,
        fill_model: FillModel,
        settlement: SettlementModel,
        allocator: Allocator | None = None,
        schedule: RebalanceSchedule | None = None,
        max_position_pct: float = 0.10,
        suspension_calendar: SuspensionCalendar | None = None,
        price_limit_provider: PriceLimitProvider | None = None,
        session_id: str | None = None,
        liquidate_at_end: bool = False,
    ) -> None:
        if isinstance(strategy, CrossSectionalStrategy):
            if allocator is None or schedule is None:
                raise ValueError("CrossSectionalStrategy requires both allocator and schedule")
        elif allocator is not None or schedule is not None:
            warnings.warn("allocator and schedule are ignored for single-asset strategy")

        self.strategy = strategy
        self.tickers = tickers
        self.feed = feed
        self.capital = capital
        self.cost_model = cost_model
        self.fill_model = fill_model
        self.settlement = settlement
        self.allocator = allocator
        self.schedule = schedule
        self.max_position_pct = max_position_pct
        self.suspension_calendar = suspension_calendar
        self.price_limit_provider = price_limit_provider
        self.liquidate_at_end = liquidate_at_end
        if session_id is None:
            PaperSession._next_session_seq += 1
            session_id = (
                f"{datetime.now():%Y%m%d_%H%M%S_%f}"
                f"_{PaperSession._next_session_seq:04d}_{strategy.name}"
            )
        self.session_id = session_id

        self.broker = BrokerSimulator(
            capital=capital,
            cost_model=cost_model,
            fill_model=fill_model,
            settlement=settlement,
            suspension_calendar=suspension_calendar,
            price_limit_provider=price_limit_provider,
        )
        self.account = Account(
            initial_capital=self.broker.capital, settlement=self.broker.settlement
        )
        self.event_log = EventLog(path=BROKER_SESSION_LOG_DIR / f"{self.session_id}.jsonl")
        self._order_seq = 0
        self._has_run = False

    def with_settlement(self, settlement: SettlementModel) -> "PaperSession":
        """New sibling PaperSession, identical config, different settlement."""
        return PaperSession(
            strategy=self.strategy,
            tickers=self.tickers,
            feed=self.feed,
            capital=self.capital,
            cost_model=self.cost_model,
            fill_model=self.fill_model,
            settlement=settlement,
            allocator=self.allocator,
            schedule=self.schedule,
            max_position_pct=self.max_position_pct,
            suspension_calendar=self.suspension_calendar,
            price_limit_provider=self.price_limit_provider,
            liquidate_at_end=self.liquidate_at_end,
        )

    def _next_id(self) -> OrderId:
        self._order_seq += 1
        return OrderId(f"O{self._order_seq:06d}")

    def _make_cross_sectional_decide(
        self,
        strategy: CrossSectionalStrategy,
        tickers: list[str],
        start: date,
        end: date,
        allocator: Allocator,
        schedule: RebalanceSchedule,
    ) -> Callable[[BarBatch, dict[str, Decimal]], None]:
        panel = {t: get_adjusted_history(ticker=t, start=start, end=end) for t in tickers}
        scores = strategy.generate_scores(panel=panel)

        def decide(T: BarBatch, last_raw_close: dict[str, Decimal]) -> None:
            ts = pd.Timestamp(T.date)
            if ts not in scores.index:
                return
            raw_close: dict[str, Decimal] = {t: bar.close for t, bar in T.bars.items()}
            target_weights = allocator.allocate(
                scores_at_T=cast(pd.Series, scores.loc[ts]), max_position_pct=self.max_position_pct
            )
            current_weights = _current_weights(self.account, last_raw_close)

            ctx = RebalanceContext(
                date=ts,
                current_weights=current_weights,
                target_weights=target_weights,
                signal_changed=False,
            )
            if not schedule.should_rebalance(ctx):
                return

            equity = float(self.account.total_value(prices=raw_close))
            gross_bp = float(self.account.buying_power(as_of=T.date))
            prices: dict[str, float] = {t: float(c) for t, c in raw_close.items()}
            adjusted_weights, shortfalls = budget_weights(
                target_weights, current_weights, equity, gross_bp, prices, self.broker.cost_model
            )
            for s in shortfalls:
                event = BrokerEvent(
                    ts=T.date,
                    kind=EventKind.CASH_SHORTFALL,
                    order_id=None,
                    payload={
                        "ticker": s.ticker,
                        "target_notional": str(s.target_notional),
                        "allocated_notional": str(s.allocated_notional),
                    },
                )
                self.event_log.append(event)
                fold([event], into=self.account)

            shim = cast(Portfolio, _portfolio_shim(self.account, equity))
            for order in reconcile(target_weights=adjusted_weights, portfolio=shim, prices=prices):
                self.broker.submit(
                    Order(
                        order_id=self._next_id(),
                        ticker=order.ticker,
                        decided_at=T.date,
                        side=Side.BUY if order.side == "buy" else Side.SELL,
                        quantity=order.shares,
                        tif=TimeInForce.IOC,
                    )
                )

        return decide

    def _make_single_asset_decide(
        self, strategy: Strategy, tickers: list[str], start: date, end: date
    ) -> Callable[[BarBatch, dict[str, Decimal]], None]:
        signals = {
            t: strategy.generate_signals(bars=get_adjusted_history(ticker=t, start=start, end=end))
            for t in tickers
        }
        last_signal: dict[str, float] = {}

        def decide(T: BarBatch, last_raw_close: dict[str, Decimal]) -> None:
            ts = pd.Timestamp(T.date)
            for ticker, sig in signals.items():
                if ts not in sig.index or ticker not in T.bars:
                    continue
                current = sig.loc[ts]
                if pd.isna(current) or current == last_signal.get(ticker):
                    continue

                flat = (
                    ticker not in self.account.positions
                    or self.account.positions[ticker].open_shares == 0
                )

                if current == 1 and flat:
                    equity = self.account.total_value(prices=last_raw_close)
                    spendable = min(
                        equity * Decimal(str(self.max_position_pct)),
                        self.account.buying_power(T.date),
                    )
                    shares = int(spendable // T.bars[ticker].close)
                    if shares > 0:
                        self.broker.submit(
                            Order(
                                order_id=self._next_id(),
                                ticker=ticker,
                                decided_at=T.date,
                                side=Side.BUY,
                                quantity=shares,
                                tif=TimeInForce.IOC,
                            )
                        )
                elif current == 0 and not flat:
                    self.broker.submit(
                        Order(
                            order_id=self._next_id(),
                            ticker=ticker,
                            decided_at=T.date,
                            side=Side.SELL,
                            quantity=self.account.positions[ticker].open_shares,
                            tif=TimeInForce.IOC,
                        )
                    )
                last_signal[ticker] = current

        return decide

    def _force_liquidate(self, as_of: date) -> None:
        for ticker, ledger in self.account.positions.items():
            if ledger.open_shares > 0:
                self.broker.submit(
                    Order(
                        order_id=self._next_id(),
                        ticker=ticker,
                        decided_at=as_of,
                        side=Side.SELL,
                        quantity=ledger.open_shares,
                        tif=TimeInForce.IOC,
                    )
                )

    def _execute(
        self,
        batches: list[BarBatch],
        decide: Callable[[BarBatch, dict[str, Decimal]], None],
    ) -> SimResult:
        last_raw_close: dict[str, Decimal] = {}
        equity_curve: dict[date, Decimal] = {}
        settled_cash_curve: dict[date, Decimal] = {}
        unsettled_cash_curve: dict[date, Decimal] = {}
        positions_rows: list[dict] = []
        trades_rows: list[dict] = []

        def _advance_and_mark(bar_batch: BarBatch, *, is_last: bool = False) -> None:
            self.broker.process_bar(bar_batch)
            events = self.broker.drain_events()
            for e in events:
                self.event_log.append(e)
                if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL):
                    trades_rows.append(_trade_row(e, bar_batch, self.cost_model))
            fold(events, into=self.account)
            if is_last:
                # No later bar's SETTLE will ever flush this bar's own net
                # (on_bar_start only fires for bars still to come) — close()
                # covers exactly that gap before the final equity is read.
                self.account.settlement.close(self.account._ledger)
            last_raw_close.update({t: bar.close for t, bar in bar_batch.bars.items()})
            equity_curve[bar_batch.date] = self.account.total_value(prices=last_raw_close)
            dated_ledger = self.account._ledger
            settled_cash_curve[bar_batch.date] = dated_ledger.settled_cash
            pending_sum = sum((cf.amount for cf in dated_ledger.pending), Decimal(0))
            unsettled_cash_curve[bar_batch.date] = pending_sum + dated_ledger.bar_net
            for ticker, ledger in self.account.positions.items():
                if ledger.open_shares:
                    positions_rows.append(
                        {
                            "date": bar_batch.date,
                            "ticker": ticker,
                            "shares": ledger.open_shares,
                            "avg_cost": ledger.avg_cost,
                            "market_value": ledger.open_shares * last_raw_close[ticker],
                        }
                    )

        _advance_and_mark(batches[0], is_last=(len(batches) == 1))

        for i in range(len(batches) - 1):
            T, T_next = batches[i], batches[i + 1]
            is_last = i == len(batches) - 2
            if is_last and self.liquidate_at_end:
                self._force_liquidate(T.date)
            else:
                decide(T, last_raw_close)
            _advance_and_mark(T_next, is_last=is_last)

        if positions_rows:
            positions_daily = pd.DataFrame(positions_rows).set_index(["date", "ticker"])
        else:
            positions_daily = pd.DataFrame(
                columns=["shares", "avg_cost", "market_value"],
                index=pd.MultiIndex.from_tuples([], names=["date", "ticker"]),
            )

        trades_df = (
            pd.DataFrame(trades_rows)
            if trades_rows
            else pd.DataFrame(
                columns=[
                    "timestamp",
                    "ticker",
                    "side",
                    "shares",
                    "fill_price",
                    "commission",
                    "tax",
                    "notional",
                    "total_cost",
                ]
            )
        )
        slippage_pct = self.cost_model.slippage_bps_per_side / 10_000

        return SimResult(
            strategy_name=self.strategy.name,
            tickers=tuple(self.tickers),
            start=batches[0].date,
            end=batches[-1].date,
            initial_capital=self.broker.capital,
            final_equity=equity_curve[batches[-1].date],
            equity_curve=pd.Series(equity_curve),
            trades=trades_df,
            positions_daily=positions_daily,
            total_commission=Decimal(str(trades_df["commission"].sum()))
            if trades_rows
            else Decimal(0),
            total_tax=Decimal(str(trades_df["tax"].sum())) if trades_rows else Decimal(0),
            total_slippage_cost=Decimal(str((trades_df["notional"] * slippage_pct).sum()))
            if trades_rows
            else Decimal(0),
            settled_cash_curve=pd.Series(settled_cash_curve),
            unsettled_cash_curve=pd.Series(unsettled_cash_curve),
            event_log_path=self.event_log.path,
        )

    def run(self) -> SimResult:
        if self._has_run:
            raise RuntimeError("PaperSession.run() can only be called once per session")
        self._has_run = True

        batches = list(self.feed)
        start, end = batches[0].date, batches[-1].date

        if isinstance(self.strategy, CrossSectionalStrategy):
            assert self.allocator is not None and self.schedule is not None
            decide = self._make_cross_sectional_decide(
                self.strategy, self.tickers, start, end, self.allocator, self.schedule
            )
        else:
            decide = self._make_single_asset_decide(self.strategy, self.tickers, start, end)

        return self._execute(batches, decide)
