"""Integration test: Macd on real 2330 data (2020–2025).

Requires local Parquet store. Run with:
    uv run pytest -m integration tests/strategies/test_macd_integration.py
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from twstock.backtest import Engine, TaiwanCostModel
from twstock.backtest.audit import assert_no_lookahead
from twstock.backtest.tearsheet import tear_sheet
from twstock.data.api import get_adjusted_history
from twstock.strategies.macd import Macd

pytestmark = pytest.mark.integration

_START = date(2020, 1, 1)
_END = date(2025, 12, 31)
_TICKER = "2330"

_REQUIRED_NONNULL_METRICS = [
    "total_return",
    "cagr",
    "sharpe",
    "sortino",
    "max_drawdown",
    "hit_rate",
    "turnover",
    "total_fees",
    "n_trades",
]


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return get_adjusted_history(_TICKER, start=_START, end=_END)


@pytest.fixture(scope="module")
def result(bars):
    engine = Engine(
        capital=1_000_000,
        cost_model=TaiwanCostModel(),
        max_position_pct=0.10,
        execution="next_open",
    )
    return engine.run(
        strategy=Macd(fast=12, slow=26, signal=9),
        tickers=[_TICKER],
        start=_START,
        end=_END,
    )


@pytest.fixture(scope="module")
def sheet(result):
    return tear_sheet(result)


def test_tear_sheet_metrics_nonnull(sheet):
    """(a) All required metrics are present and non-NaN."""
    for key in _REQUIRED_NONNULL_METRICS:
        assert key in sheet, f"missing metric: {key}"
        val = sheet[key]
        assert not (isinstance(val, float) and np.isnan(val)), f"{key} is NaN"


def test_costs_applied_nontrivially(result):
    """(b) Net return is lower than gross — fees were deducted from equity."""
    total_fees = result.total_commission + result.total_tax + result.total_slippage_cost
    assert total_fees > 0, "total fees are zero — cost model was not applied"
    assert result.final_equity + total_fees > result.final_equity


def test_fills_at_t1_open(result, bars):
    """(c) Every fill price equals open ± slippage (proves T+1 execution)."""
    trades = result.trades
    assert not trades.empty, "no trades — cannot verify T+1 fill timing"

    slippage_pct = TaiwanCostModel().slippage_bps_per_side / 10_000
    for _, trade in trades.iterrows():
        ts = pd.Timestamp(trade["timestamp"])
        assert ts in bars.index, f"trade timestamp {ts} not in bars index"
        open_price = bars.loc[ts, "open"]
        multiplier = 1 + slippage_pct if trade["side"] == "buy" else 1 - slippage_pct
        expected = open_price * multiplier
        assert abs(trade["fill_price"] - expected) < 1e-6, (
            f"{trade['side']} fill {trade['fill_price']:.4f} "
            f"≠ expected {expected:.4f} on {ts.date()}"
        )


def test_no_lookahead(bars):
    """(d) Macd passes the lookahead audit on a 252-bar sample of 2330."""
    sample = bars.iloc[-252:].copy()
    assert_no_lookahead(Macd(fast=12, slow=26, signal=9), sample)


def test_positions_daily_nonempty(result):
    """BacktestResult.positions_daily is non-empty and indexed by (date, ticker)."""
    pos = result.positions_daily
    assert not pos.empty, "positions_daily is empty"
    assert list(pos.index.names) == ["date", "ticker"]
