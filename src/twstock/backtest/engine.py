import math
import warnings
from datetime import date
from typing import Literal

import pandas as pd

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.portfolio import Portfolio
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

    def run(self, strategy: Strategy, tickers: list[str], start: date, end: date) -> BacktestResult:
        """Run `strategy` on `tickers` from `start` to `end`.

        Algorithm:
        1. For each ticker, load adjusted bars via get_adjusted_history.
        2. For each ticker, call strategy.generate_signals(bars) ONCE
           upfront. The strategy receives the full bars frame; lookahead
           safety is the strategy author's responsibility (audited by
           backtest.audit).
        3. Walk the union of trading days chronologically. At each
           day T:
           a. Diff signal[T] vs current position to derive desired orders.
           b. Schedule those orders for execution at T+1's open.
           c. Execute orders scheduled at T (from T-1's signals) using
              T's open as the reference price.
           d. Mark-to-market at T's close. Append to equity curve.
        4. On the final day, force-close all positions at the last
           available open (or close if no next bar exists; document choice).
        5. Build BacktestResult and return.
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

        result = BacktestResult(
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

        return result
