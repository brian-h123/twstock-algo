import math
import warnings
from datetime import date
from typing import Literal

import pandas as pd

from twstock.backtest.allocator import Allocator
from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.cross_sectional import CrossSectionalStrategy
from twstock.backtest.portfolio import Portfolio
from twstock.backtest.rebalance import RebalanceContext, RebalanceSchedule
from twstock.backtest.reconciler import reconcile
from twstock.backtest.result import BacktestResult
from twstock.backtest.strategy import Strategy
from twstock.data.api import get_adjusted_history


class Engine:
    def __init__(
        self,
        capital: float,
        cost_model: TaiwanCostModel,
        max_position_pct: float = 0.10,
        allow_short: bool = False,
        execution: Literal["next_open"] = "next_open",
    ) -> None:
        self._capital = capital
        self._cost_model = cost_model
        self._max_position_pct = max_position_pct
        self._allow_short = allow_short
        self._execution = execution

    def _run_cross_sectional(
        self,
        strategy: CrossSectionalStrategy,
        tickers: list[str],
        start: date,
        end: date,
        allocator: Allocator,
        schedule: RebalanceSchedule,
    ) -> BacktestResult:
        portfolio = Portfolio(self._capital, self._cost_model)
        panel = {}
        for ticker in tickers:
            bars = get_adjusted_history(ticker=ticker, start=start, end=end)
            if len(bars) < 2:
                warnings.warn(f"requires more than 2 rows, got {len(bars)} rows")
                continue
            panel[ticker] = bars

        scores = strategy.generate_scores(panel=panel)
        all_dates = pd.DatetimeIndex(sorted({ts for bars in panel.values() for ts in bars.index}))

        pending_orders = list()
        last_close = dict()
        equity_curve_data = dict()
        positions_daily_rows: list[dict] = []

        for T in all_dates:
            is_last = T == all_dates[-1]

            open_prices = {
                ticker: bars.loc[T, "open"] for ticker, bars in panel.items() if T in bars.index
            }
            close_prices = {
                ticker: bars.loc[T, "close"] for ticker, bars in panel.items() if T in bars.index
            }

            if is_last:
                pending_orders.clear()
                for ticker, position in portfolio.positions.items():
                    if ticker in open_prices:
                        portfolio.sell(
                            ticker=ticker,
                            shares=position.shares,
                            reference_price=open_prices[ticker],
                            timestamp=T,
                        )
            else:
                for order in pending_orders:
                    if order.ticker not in open_prices:
                        continue  # ticker not trading today — skip
                    if order.side == "buy":
                        ref = open_prices[order.ticker]
                        fill_px = self._cost_model.apply_slippage(ref, "buy")
                        # Two conservative bounds — proportional commission and NT$20 minimum —
                        # take the smaller so residual-cash tiny orders don't overshoot.
                        affordable_prop = math.floor(
                            portfolio.cash / (fill_px * (1 + self._cost_model.commission_pct))
                        )
                        cash_net = portfolio.cash - self._cost_model.commission_min_twd
                        affordable_min = math.floor(cash_net / fill_px) if cash_net > 0 else 0
                        affordable = min(affordable_prop, affordable_min)
                        shares = min(order.shares, max(0, affordable))
                        if shares > 0:
                            portfolio.buy(
                                ticker=order.ticker,
                                shares=shares,
                                reference_price=ref,
                                timestamp=T,
                            )
                    elif order.side == "sell":
                        portfolio.sell(
                            ticker=order.ticker,
                            shares=order.shares,
                            reference_price=open_prices[order.ticker],
                            timestamp=T,
                        )
                pending_orders.clear()

                if T in scores.index:
                    target_weights = allocator.allocate(
                        scores_at_T=scores.loc[T], max_position_pct=self._max_position_pct
                    )

                    equity = portfolio.equity
                    current_weights = (
                        pd.Series(
                            {
                                t: p.shares * last_close.get(t, 0.0) / equity
                                for t, p in portfolio.positions.items()
                            },
                            dtype=float,
                        )
                        if equity > 0
                        else pd.Series(dtype=float)
                    )

                    rebalance = schedule.should_rebalance(
                        ctx=RebalanceContext(
                            date=T,
                            current_weights=current_weights,
                            target_weights=target_weights,
                            signal_changed=False,
                        )
                    )
                    if rebalance:
                        pending_orders = reconcile(
                            target_weights=target_weights, portfolio=portfolio, prices=close_prices
                        )

            mtm_prices = {**last_close, **close_prices}
            portfolio.mark_to_market(mtm_prices)
            equity_curve_data[T] = portfolio.equity

            for ticker, pos in portfolio.positions.items():
                positions_daily_rows.append(
                    {
                        "date": T,
                        "ticker": ticker,
                        "shares": pos.shares,
                        "avg_cost": pos.avg_cost,
                        "market_value": pos.shares
                        * mtm_prices[ticker],  # mtm_prices already covers all held tickers
                    }
                )

            last_close.update(close_prices)

        if positions_daily_rows:
            positions_daily = pd.DataFrame(positions_daily_rows).set_index(["date", "ticker"])
        else:
            positions_daily = pd.DataFrame(
                columns=["shares", "avg_cost", "market_value"],
                index=pd.MultiIndex.from_tuples([], names=["date", "ticker"]),
            )

        trades_list = portfolio.trades
        trades_df = (
            pd.DataFrame([vars(t) for t in trades_list])
            if trades_list
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
        total_commission = sum(t.commission for t in trades_list)
        total_tax = sum(t.tax for t in trades_list)
        slippage_pct = self._cost_model.slippage_bps_per_side / 10_000
        total_slippage_cost = sum(t.notional * slippage_pct for t in trades_list)

        return BacktestResult(
            strategy_name=strategy.name,
            tickers=tuple(panel.keys()),
            start=start,
            end=end,
            initial_capital=self._capital,
            final_equity=portfolio.equity,
            equity_curve=pd.Series(equity_curve_data),
            trades=trades_df,
            positions_daily=positions_daily,
            total_commission=total_commission,
            total_tax=total_tax,
            total_slippage_cost=total_slippage_cost,
        )

    def _run_single_asset(
        self, strategy: Strategy, tickers: list[str], start: date, end: date
    ) -> BacktestResult:
        """Run a single-asset strategy across ``tickers``, sharing a cash pool.

        Each ticker's signals are computed independently — the strategy has no
        cross-ticker awareness. ``max_position_pct`` is an entry-sizing cap
        (``min(max_position_pct × equity, available_cash)`` at T+1 open); it
        is NOT a continuous rebalancing target. Positions are held without drift
        correction until the strategy emits an exit signal.

        Passing multiple tickers is allowed. Each ticker's position is sized
        independently at entry and held without portfolio-level weight management
        thereafter. For weight-managed multi-ticker portfolios — where target
        weights are maintained and drift is corrected on a schedule — use the
        cross-sectional path via ``CrossSectionalStrategy`` and
        ``CrossSectionalAdapter``.

        Phase 4 evaluation (``phase_4_evaluate.py``) always calls this method
        with a single-element ``tickers`` list to isolate per-ticker signal
        quality from portfolio-level allocation effects.
        """
        portfolio = Portfolio(self._capital, self._cost_model)
        bars_by_ticker = dict()
        signals_by_ticker = dict()

        for ticker in tickers:
            bars = get_adjusted_history(ticker=ticker, start=start, end=end)
            if len(bars) < 2:
                warnings.warn(f"requires more than 2 rows, got {len(bars)} rows")
                continue

            bars_by_ticker[ticker] = bars
            signals_by_ticker[ticker] = strategy.generate_signals(bars=bars)

        all_dates = pd.DatetimeIndex(
            sorted({ts for bars in bars_by_ticker.values() for ts in bars.index})
        )

        pending_orders = dict()
        last_signal = dict()
        last_close = dict()
        equity_curve_data = dict()
        positions_daily_rows: list[dict] = []

        for T in all_dates:
            is_last = T == all_dates[-1]

            open_prices = {
                ticker: bars.loc[T, "open"]
                for ticker, bars in bars_by_ticker.items()
                if T in bars.index
            }
            close_prices = {
                ticker: bars.loc[T, "close"]
                for ticker, bars in bars_by_ticker.items()
                if T in bars.index
            }

            if is_last:
                pending_orders.clear()
                for ticker, position in portfolio.positions.items():
                    if ticker in open_prices:
                        portfolio.sell(
                            ticker=ticker,
                            shares=position.shares,
                            reference_price=open_prices[ticker],
                            timestamp=T,
                        )
            else:
                # execute pending orders from T-1's signal
                for ticker, side in list(pending_orders.items()):
                    if ticker not in open_prices:
                        pending_orders.pop(
                            ticker
                        )  # ticker not trading today — drop the stale order
                        continue

                    if side == "buy":
                        if ticker not in portfolio.positions:  # only buy if not already long
                            target_amount = portfolio.equity * self._max_position_pct
                            spendable = min(target_amount, portfolio.cash)
                            shares = math.floor(spendable / open_prices[ticker])
                            if shares > 0:
                                portfolio.buy(
                                    ticker=ticker,
                                    shares=shares,
                                    reference_price=open_prices[ticker],
                                    timestamp=T,
                                )
                        pending_orders.pop(ticker)  # always pop, regardless
                    elif side == "sell":
                        if ticker in portfolio.positions:
                            shares = portfolio.positions[ticker].shares
                            portfolio.sell(
                                ticker=ticker,
                                shares=shares,
                                reference_price=open_prices[ticker],
                                timestamp=T,
                            )
                        pending_orders.pop(ticker)

                # read signal[T], schedule orders for T+1
                for ticker, signals in signals_by_ticker.items():
                    if T not in signals.index:
                        continue
                    current = signals.loc[T]
                    if pd.isna(current):
                        continue  # no opinion — preserve state, don't update last_signal

                    prev = last_signal.get(ticker)  # None if never seen

                    if current == prev:
                        continue

                    if current == 1:
                        pending_orders[ticker] = "buy"
                    elif current == 0 and prev is not None:
                        pending_orders[ticker] = (
                            "sell"  # prev is None → never long → nothing to close
                        )

                    last_signal[ticker] = current

            # mark-to-market
            mtm_prices = {**last_close, **close_prices}
            portfolio.mark_to_market(mtm_prices)
            equity_curve_data[T] = portfolio.equity

            for ticker, pos in portfolio.positions.items():
                positions_daily_rows.append(
                    {
                        "date": T,
                        "ticker": ticker,
                        "shares": pos.shares,
                        "avg_cost": pos.avg_cost,
                        "market_value": pos.shares
                        * mtm_prices[ticker],  # mtm_prices already covers all held tickers
                    }
                )

            last_close.update(close_prices)

        if positions_daily_rows:
            positions_daily = pd.DataFrame(positions_daily_rows).set_index(["date", "ticker"])
        else:
            positions_daily = pd.DataFrame(
                columns=["shares", "avg_cost", "market_value"],
                index=pd.MultiIndex.from_tuples([], names=["date", "ticker"]),
            )

        trades_list = portfolio.trades
        trades_df = (
            pd.DataFrame([vars(t) for t in trades_list])
            if trades_list
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
        total_commission = sum(t.commission for t in trades_list)
        total_tax = sum(t.tax for t in trades_list)
        slippage_pct = self._cost_model.slippage_bps_per_side / 10_000
        total_slippage_cost = sum(t.notional * slippage_pct for t in trades_list)

        return BacktestResult(
            strategy_name=strategy.name,
            tickers=tuple(bars_by_ticker.keys()),
            start=start,
            end=end,
            initial_capital=self._capital,
            final_equity=portfolio.equity,
            equity_curve=pd.Series(equity_curve_data),
            trades=trades_df,
            positions_daily=positions_daily,
            total_commission=total_commission,
            total_tax=total_tax,
            total_slippage_cost=total_slippage_cost,
        )

    def run(
        self,
        strategy: Strategy | CrossSectionalStrategy,
        tickers: list[str],
        start: date,
        end: date,
        allocator: Allocator | None = None,
        schedule: RebalanceSchedule | None = None,
    ) -> BacktestResult:
        """Run ``strategy`` on ``tickers`` from ``start`` to ``end``.

        Dispatches based on strategy type:

        - ``Strategy`` (single-asset): each ticker's signals are computed
          independently with no cross-ticker awareness. Positions are sized
          at entry up to ``max_position_pct`` and held without drift
          correction. ``allocator`` and ``schedule`` are ignored (a warning
          is emitted if either is provided).

        - ``CrossSectionalStrategy``: the full panel is scored together,
          an allocator converts scores to target weights, and a rebalance
          schedule controls when the portfolio is rebalanced. Drift is
          corrected on each rebalance bar. Both ``allocator`` and
          ``schedule`` are required.

        The two paths answer different questions: single-asset measures raw
        signal quality per ticker; cross-sectional measures portfolio-level
        allocation performance. Use ``CrossSectionalAdapter`` to run a
        ``Strategy`` through the cross-sectional path when you want the
        same signal evaluated with portfolio mechanics.
        """
        if isinstance(strategy, CrossSectionalStrategy):
            if allocator is None or schedule is None:
                raise ValueError("CrossSectionalStrategy requires both allocator and schedule")
            return self._run_cross_sectional(strategy, tickers, start, end, allocator, schedule)
        else:
            if allocator is not None or schedule is not None:
                warnings.warn("allocator and schedule are ignored for single-asset Strategy")
            return self._run_single_asset(strategy, tickers, start, end)
