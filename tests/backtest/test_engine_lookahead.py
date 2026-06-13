"""Lookahead-bias tests for twstock.backtest.audit.assert_no_lookahead.

Coverage:
  - assert_no_lookahead raises LookaheadError on a strategy that uses
    bars["close"].shift(-1) (classic lookahead via future close)
  - assert_no_lookahead passes silently for a fully causal strategy
  - LookaheadError message identifies the number of differing bars
    and the perturbation point
"""

import numpy as np
import pandas as pd
import pytest

from twstock.backtest.audit import LookaheadError, assert_no_lookahead
from twstock.backtest.strategy import Strategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bars(n: int = 100, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-02", periods=n, freq="B")
    close = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.01, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    noise = rng.uniform(0.001, 0.005, n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * (1 + noise),
            "low": np.minimum(open_, close) * (1 - noise),
            "close": close,
            "volume": np.full(n, 10_000),
        },
        index=idx,
    )


class _LookaheadStrategy(Strategy):
    """shift(-1) is textbook lookahead: signal[T] uses close[T+1]."""

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        return (bars["close"].shift(-1) > bars["close"]).astype(float)


class _CausalStrategy(Strategy):
    """shift(+1) is the previous close — fully causal."""

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        return (bars["close"] > bars["close"].shift(1)).astype(float)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bars_100() -> pd.DataFrame:
    return _make_bars(n=100)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_catches_shift_minus_one_lookahead(bars_100: pd.DataFrame) -> None:
    """shift(-1) on close touches the perturbed future window and must be caught."""
    with pytest.raises(LookaheadError, match=r"\d+ bar\(s\) before"):
        assert_no_lookahead(_LookaheadStrategy(), bars_100)


def test_accepts_causal_strategy(bars_100: pd.DataFrame) -> None:
    """A strategy that only looks backwards must pass the audit without raising."""
    assert_no_lookahead(_CausalStrategy(), bars_100)  # must not raise


def test_lookahead_error_names_perturbation_index(bars_100: pd.DataFrame) -> None:
    """LookaheadError message must include the perturbation index (window boundary)."""
    # Default perturbation_start=-50 on 100 rows -> window=50
    with pytest.raises(LookaheadError, match="50"):
        assert_no_lookahead(_LookaheadStrategy(), bars_100)


def test_custom_perturbation_start_does_not_flag_causal(bars_100: pd.DataFrame) -> None:
    """Custom perturbation_start must not falsely flag a causal strategy."""
    for start in (-20, -80):
        assert_no_lookahead(_CausalStrategy(), bars_100, perturbation_start=start)


def test_causal_strategy_various_window_sizes() -> None:
    """Causal strategy passes for both small and large safe windows."""
    for n in (60, 150, 252):
        assert_no_lookahead(_CausalStrategy(), _make_bars(n=n))
