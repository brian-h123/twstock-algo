"""Engine integration tests for the cross-sectional dispatch path.

Patches get_adjusted_history so tests run without network or disk I/O while
exercising the full Engine → CrossSectionalStrategy → allocator → reconciler
→ pending-order execution pipeline.

Coverage:
  - Engine.run raises ValueError when allocator or schedule is omitted for a
    CrossSectionalStrategy
  - CrossSectionalAdapter(SmaCrossover()) end-to-end smoke test
  - Rotation regression: fully-invested portfolio rotates from [A1, A2] to B
    without InsufficientCashError — proves sells execute before buys
  - BacktestResult shape contracts from the cross-sectional path
"""

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from tests.backtest.conftest import make_synthetic_panel
from twstock.backtest.allocator import EqualWeightTopN
from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.cross_sectional import CrossSectionalAdapter, CrossSectionalStrategy
from twstock.backtest.engine import Engine
from twstock.backtest.rebalance import Daily, Monthly
from twstock.strategies.sma_crossover import SmaCrossover

_PATCH = "twstock.backtest.engine.get_adjusted_history"


# ─── helpers ──────────────────────────────────────────────────────────────────


class _SwitchStrategy(CrossSectionalStrategy):
    """Deterministic cross-sectional strategy for rotation tests.

    ``tickers_a`` score 1.0 for the first ``switch_bar`` bars; ``tickers_b``
    score 1.0 from ``switch_bar`` onward. All other scores are NaN. This
    produces a clean, predictable rotation with no warmup ambiguity.
    """

    def __init__(
        self,
        tickers_a: list[str],
        tickers_b: list[str],
        switch_bar: int,
    ) -> None:
        self._a = set(tickers_a)
        self._b = set(tickers_b)
        self._switch_bar = switch_bar

    def generate_scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        all_dates = sorted({ts for bars in panel.values() for ts in bars.index})
        idx = pd.DatetimeIndex(all_dates)
        data: dict[str, pd.Series] = {}
        for ticker in panel:
            series = pd.Series(float("nan"), index=idx)
            for i in range(len(idx)):
                if ticker in self._a and i < self._switch_bar:
                    series.iloc[i] = 1.0
                elif ticker in self._b and i >= self._switch_bar:
                    series.iloc[i] = 1.0
            data[ticker] = series
        return pd.DataFrame(data)


@pytest.fixture
def cs_engine() -> Engine:
    return Engine(
        capital=1_000_000,
        cost_model=TaiwanCostModel(),
        max_position_pct=0.10,
    )


# ─── dispatcher validation ────────────────────────────────────────────────────


def test_cross_sectional_requires_allocator(cs_engine: Engine) -> None:
    """Engine.run must raise ValueError when allocator is None for a CrossSectionalStrategy."""
    from tests.backtest.conftest import make_synthetic_bars

    bars = make_synthetic_bars(30)
    strategy = CrossSectionalAdapter(SmaCrossover())

    with patch(_PATCH, return_value=bars):
        with pytest.raises(ValueError, match="allocator"):
            cs_engine.run(
                strategy=strategy,
                tickers=["A"],
                start=date(2020, 1, 1),
                end=date(2020, 12, 31),
                allocator=None,
                schedule=Monthly(),
            )


def test_cross_sectional_requires_schedule(cs_engine: Engine) -> None:
    """Engine.run must raise ValueError when schedule is None for a CrossSectionalStrategy."""
    from tests.backtest.conftest import make_synthetic_bars

    bars = make_synthetic_bars(30)
    strategy = CrossSectionalAdapter(SmaCrossover())

    with patch(_PATCH, return_value=bars):
        with pytest.raises(ValueError, match="schedule"):
            cs_engine.run(
                strategy=strategy,
                tickers=["A"],
                start=date(2020, 1, 1),
                end=date(2020, 12, 31),
                allocator=EqualWeightTopN(n=10),
                schedule=None,
            )


# ─── adapter smoke test ───────────────────────────────────────────────────────


def test_adapter_sma_crossover_returns_valid_result(cs_engine: Engine) -> None:
    """CrossSectionalAdapter(SmaCrossover()) end-to-end smoke test.

    Verifies the full dispatch → generate_scores → allocate → reconcile path
    completes without error and returns a well-formed BacktestResult.
    EqualWeightTopN(n=10) with max_position_pct=0.10 passes the n × pct ≥ 1
    validation; SmaCrossover's 60-bar warmup means the first ~60 bars produce
    no signals (valid, not an error).
    """
    tickers = ["A", "B", "C"]
    panel = make_synthetic_panel(tickers, n_days=150, trend=0.10, vol=0.20)

    def _mock(ticker: str, start, end) -> pd.DataFrame:
        return panel[ticker]

    with patch(_PATCH, side_effect=_mock):
        result = cs_engine.run(
            strategy=CrossSectionalAdapter(SmaCrossover(fast=20, slow=60)),
            tickers=tickers,
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
            allocator=EqualWeightTopN(n=10),
            schedule=Monthly(),
        )

    assert len(result.equity_curve) > 0
    assert result.initial_capital == 1_000_000
    assert result.final_equity > 0
    assert result.tickers == tuple(tickers)

    if not result.trades.empty:
        required_cols = {"timestamp", "ticker", "side", "shares", "fill_price"}
        assert required_cols.issubset(result.trades.columns)

    assert not result.positions_daily.empty or result.trades.empty


# ─── rotation regression ─────────────────────────────────────────────────────


def test_rotation_sells_before_buys_no_cash_error() -> None:
    """A heavily-invested portfolio rotating from [A1, A2] to B must not raise.

    Setup: EqualWeightTopN(n=3, max_position_pct=0.40). With only A1 and A2
    eligible in phase A, each gets weight = min(1/2, 0.40) = 0.40, leaving the
    portfolio ~80% invested and ~20% in cash (3 × 0.40 = 1.2 ≥ 1 passes
    allocator validation). On the switch bar the target flips to B only;
    the reconciler emits [sell A1, sell A2, buy B].

    The buy of B costs ~40% of equity. Available cash is only ~20%, so if the
    engine processed the buy before the sells it would raise InsufficientCashError.
    Correct ordering (sells first) frees ~80% cash before the buy executes,
    leaving plenty of headroom (ADR 0005 regression guard).
    """
    engine = Engine(
        capital=1_000_000,
        cost_model=TaiwanCostModel(),
        max_position_pct=0.40,
    )
    tickers = ["A1", "A2", "B"]
    panel = make_synthetic_panel(tickers, n_days=60)

    strategy = _SwitchStrategy(tickers_a=["A1", "A2"], tickers_b=["B"], switch_bar=20)

    def _mock(ticker: str, start, end) -> pd.DataFrame:
        return panel[ticker]

    # Daily schedule ensures the rotation fires exactly at switch_bar=20.
    # Any InsufficientCashError or other exception fails the test.
    with patch(_PATCH, side_effect=_mock):
        result = engine.run(
            strategy=strategy,
            tickers=tickers,
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
            allocator=EqualWeightTopN(n=3),
            schedule=Daily(),
        )

    assert result.final_equity > 0

    # After the rotation, B must appear in the trade ledger as a buy
    assert not result.trades.empty
    traded = set(result.trades["ticker"])
    assert "B" in traded, "B must have been traded after the rotation"

    buy_b = result.trades[(result.trades["ticker"] == "B") & (result.trades["side"] == "buy")]
    assert len(buy_b) >= 1, "expected at least one buy of B after switch_bar"
