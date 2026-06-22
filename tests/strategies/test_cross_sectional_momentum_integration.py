"""Integration test: CrossSectionalMomentum on real universe data (2020–2025).

Requires local Parquet store. Run with:
    uv run pytest -m integration tests/strategies/test_cross_sectional_momentum_integration.py
"""

from datetime import date

import numpy as np
import pytest

from twstock.backtest import BuyAndHold, Engine, TaiwanCostModel
from twstock.backtest.allocator import EqualWeightTopN
from twstock.backtest.audit import assert_no_lookahead_panel
from twstock.backtest.rebalance import Monthly
from twstock.backtest.tearsheet import tear_sheet
from twstock.backtest.tiebreak import LiquidityTieBreak
from twstock.data.api import get_adjusted_history
from twstock.strategies.cross_sectional_momentum import CrossSectionalMomentum
from twstock.universe import get_top50_universe

pytestmark = pytest.mark.integration

_START = date(2020, 1, 1)
_END = date(2025, 12, 31)

# Three-ticker subset for the panel audit — enough to exercise the panel path
# without loading all 50 tickers a second time.
_AUDIT_TICKERS = ["2330", "2317", "2454"]
_AUDIT_START = date(2023, 1, 1)  # ~500 bars: plenty for lookback=252, skip=21

_REQUIRED_NONNULL_METRICS = [
    "total_return",
    "cagr",
    "sharpe",
    "sortino",
    "max_drawdown",
    "turnover",
    "total_fees",
    "n_trades",
]


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine(
        capital=1_000_000,
        cost_model=TaiwanCostModel(),
        max_position_pct=0.10,
    )


@pytest.fixture(scope="module")
def result(engine):
    return engine.run(
        strategy=CrossSectionalMomentum(lookback=252, skip=21),
        tickers=get_top50_universe(),
        allocator=EqualWeightTopN(n=10, tiebreak=LiquidityTieBreak()),
        schedule=Monthly(),
        start=_START,
        end=_END,
    )


@pytest.fixture(scope="module")
def benchmark(engine):
    return engine.run(
        strategy=BuyAndHold(),
        tickers=["0050"],
        start=_START,
        end=_END,
    )


@pytest.fixture(scope="module")
def sheet(result):
    return tear_sheet(result)


def test_metrics_nonnull(sheet):
    """(a) All required metrics are present and non-NaN."""
    for key in _REQUIRED_NONNULL_METRICS:
        assert key in sheet, f"missing metric: {key}"
        val = sheet[key]
        assert not (isinstance(val, float) and np.isnan(val)), f"{key} is NaN"


def test_costs_applied(result):
    """(b) Total fees are positive — cost model was engaged."""
    total_fees = result.total_commission + result.total_tax + result.total_slippage_cost
    assert total_fees > 0, "total fees are zero — cost model was not applied"


def test_positions_nonempty(result):
    """(c) Strategy took at least one position during the backtest."""
    assert not result.positions_daily.empty, "positions_daily is empty — strategy never traded"


def test_trades_nonempty(result):
    """(d) Trade ledger is non-empty and has expected columns."""
    assert not result.trades.empty, "no trades recorded"
    required_cols = {"timestamp", "ticker", "side", "shares", "fill_price"}
    assert required_cols.issubset(result.trades.columns)


def test_panel_no_lookahead():
    """(e) CrossSectionalMomentum passes the panel lookahead audit."""
    panel = {
        ticker: get_adjusted_history(ticker, start=_AUDIT_START, end=_END)
        for ticker in _AUDIT_TICKERS
    }
    assert_no_lookahead_panel(CrossSectionalMomentum(lookback=252, skip=21), panel)


def test_benchmark_comparison_available(result, benchmark):
    """(f) Both strategy and benchmark results are present for honest comparison.

    Does not assert the strategy beats 0050 — that is an empirical finding.
    Losers reported, not dropped (D11).
    """
    strategy_sheet = tear_sheet(result)
    benchmark_sheet = tear_sheet(benchmark)
    assert "total_return" in strategy_sheet
    assert "total_return" in benchmark_sheet
    assert "sharpe" in strategy_sheet
    assert "sharpe" in benchmark_sheet
