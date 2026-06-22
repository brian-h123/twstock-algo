import numpy as np
import pandas as pd
import pytest

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.engine import Engine


def make_synthetic_bars(
    n_days: int,
    trend: float = 0.0,
    vol: float = 0.01,
    seed: int = 42,
) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame for engine tests.

    Args:
        n_days: Number of trading bars.
        trend: Annualised drift (fraction, e.g. 0.10 for +10 % per year).
        vol: Annualised volatility (fraction, e.g. 0.20 for 20 %).
        seed: NumPy RNG seed for full reproducibility.

    Returns:
        DataFrame with columns (open, high, low, close, volume) and a
        business-day DatetimeIndex starting 2020-01-02.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-02", periods=n_days, freq="B")

    daily_returns = rng.normal(trend / 252, vol / np.sqrt(252), size=n_days)
    close = 100.0 * np.cumprod(1 + daily_returns)

    # Open = previous close (zero overnight gap); bar 0 opens at 100.
    open_ = np.empty(n_days)
    open_[0] = 100.0
    open_[1:] = close[:-1]

    noise = rng.uniform(0.001, 0.005, size=n_days)
    high = np.maximum(open_, close) * (1 + noise)
    low = np.minimum(open_, close) * (1 - noise)

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 10_000,
        },
        index=idx,
    )


def make_synthetic_panel(
    tickers: list[str],
    n_days: int,
    trend: float = 0.0,
    vol: float = 0.01,
) -> dict[str, pd.DataFrame]:
    """Build a deterministic panel of OHLCV DataFrames for cross-sectional tests.

    Each ticker gets a different seed so price paths differ, but all share
    the same date index (same n_days, same start date).

    Args:
        tickers: Ticker symbols; also determines the number of assets.
        n_days: Number of trading bars per ticker.
        trend: Annualised drift passed to make_synthetic_bars.
        vol: Annualised volatility passed to make_synthetic_bars.

    Returns:
        Dict mapping ticker → OHLCV DataFrame.
    """
    return {
        ticker: make_synthetic_bars(n_days, trend=trend, vol=vol, seed=i)
        for i, ticker in enumerate(tickers)
    }


@pytest.fixture
def panel_3t_100d() -> dict[str, pd.DataFrame]:
    return make_synthetic_panel(["A", "B", "C"], n_days=100)


@pytest.fixture
def cost_model() -> TaiwanCostModel:
    return TaiwanCostModel()


@pytest.fixture
def engine(cost_model: TaiwanCostModel) -> Engine:
    return Engine(
        capital=1_000_000,
        cost_model=cost_model,
        max_position_pct=0.10,
    )
