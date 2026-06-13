from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    """Immutable artifact produced by one Engine.run() call.

    All downstream reporting — tear sheets, equity figures, analyzers —
    is a pure function over this artifact (D10). Never coupled to Engine
    internals; re-running produces a new instance.

    Column contracts (breaking changes treated like method-signature changes):
      trades:          timestamp, ticker, side, shares, fill_price,
                       commission, tax, notional, total_cost
      positions_daily: MultiIndex (date, ticker); columns shares, avg_cost,
                       market_value

    Note: frozen=True enforces attribute reassignment safety. pd.Series and
    pd.DataFrame fields remain internally mutable — a known limitation
    documented for Phase 6 review.
    """

    strategy_name: str
    tickers: tuple[str, ...]  # tuple, not list, for consistent immutability
    start: date
    end: date
    initial_capital: float
    final_equity: float
    equity_curve: pd.Series  # DatetimeIndex, float64; one entry per trading day
    trades: pd.DataFrame  # one row per trade; see column contract above
    positions_daily: pd.DataFrame  # MultiIndex (date, ticker); see above
    total_commission: float
    total_tax: float
    total_slippage_cost: float  # estimated: (fill - mid) × shares summed across all trades
