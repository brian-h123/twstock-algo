"""Unit tests for Macd — hand-built fixtures, no real market data."""

import pandas as pd
import pytest

from twstock.strategies.macd import Macd


def _make_bars(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-02", periods=len(prices), freq="B")
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [10_000.0] * len(prices),
        },
        index=idx,
    )


_FAST = 2
_SLOW = 3
_SIGNAL = 2

# Warmup: first (slow + signal - 2) bars are NaN.
# With fast=2, slow=3, signal=2: warmup = 3 bars (indices 0-2), first valid at index 3.
#
# Prices [100, 100, 100, 200, 50]:
#   Indices 0-2: NaN — MACD indicator warmup
#   Index 3 (200): macd_line > signal_line → 1.0 (spike lifts fast EMA above slow)
#   Index 4 (50):  macd_line < signal_line → 0.0 (drop pushes fast EMA below slow)
_WARMUP = _SLOW + _SIGNAL - 2  # = 3
_BARS = _make_bars([100.0] * _SLOW + [200.0] + [50.0])


class TestMacdSignals:
    def test_warmup_is_nan(self):
        """First (slow + signal - 2) bars are NaN, never 0."""
        signals = Macd(fast=_FAST, slow=_SLOW, signal=_SIGNAL).generate_signals(_BARS)
        assert signals.iloc[:_WARMUP].isna().all()

    def test_no_nan_after_warmup(self):
        """Every bar from the first valid index onwards is 0.0 or 1.0, never NaN."""
        signals = Macd(fast=_FAST, slow=_SLOW, signal=_SIGNAL).generate_signals(_BARS)
        post = signals.iloc[_WARMUP:]
        assert post.notna().all()
        assert set(post.unique()).issubset({0.0, 1.0})

    def test_signal_values_are_zero_one_or_nan(self):
        """Signal series contains only 0.0, 1.0, and NaN — nothing else."""
        signals = Macd(fast=_FAST, slow=_SLOW, signal=_SIGNAL).generate_signals(_BARS)
        assert set(signals.dropna().unique()).issubset({0.0, 1.0})

    def test_long_when_macd_above_signal(self):
        """Signal = 1.0 on the spike bar where macd_line crosses above signal_line."""
        signals = Macd(fast=_FAST, slow=_SLOW, signal=_SIGNAL).generate_signals(_BARS)
        assert signals.iloc[_WARMUP] == pytest.approx(1.0)

    def test_flat_when_macd_below_signal(self):
        """Signal = 0.0 on the drop bar where macd_line crosses below signal_line."""
        signals = Macd(fast=_FAST, slow=_SLOW, signal=_SIGNAL).generate_signals(_BARS)
        assert signals.iloc[_WARMUP + 1] == pytest.approx(0.0)

    def test_equal_prices_give_zero_not_long(self):
        """Flat prices → macd_line == signal_line == 0 → signal is 0, not 1 (strict >)."""
        bars = _make_bars([100.0] * (_WARMUP + 1))
        signals = Macd(fast=_FAST, slow=_SLOW, signal=_SIGNAL).generate_signals(bars)
        assert signals.iloc[_WARMUP] == pytest.approx(0.0)

    def test_generate_signals_is_pure(self):
        """Calling generate_signals twice on the same bars returns identical output."""
        strat = Macd(fast=_FAST, slow=_SLOW, signal=_SIGNAL)
        s1 = strat.generate_signals(_BARS)
        s2 = strat.generate_signals(_BARS)
        pd.testing.assert_series_equal(s1, s2)


class TestMacdParamValidation:
    def test_raises_when_fast_equals_slow(self):
        with pytest.raises(ValueError):
            Macd(fast=5, slow=5, signal=9)

    def test_raises_when_fast_greater_than_slow(self):
        with pytest.raises(ValueError):
            Macd(fast=10, slow=5, signal=9)

    def test_raises_when_fast_is_zero(self):
        with pytest.raises(ValueError):
            Macd(fast=0, slow=5, signal=9)

    def test_raises_when_signal_is_zero(self):
        with pytest.raises(ValueError):
            Macd(fast=2, slow=3, signal=0)

    def test_canonical_params_stored(self):
        """Default params match the spec (fast=12, slow=26, signal=9)."""
        s = Macd()
        assert s.fast == 12
        assert s.slow == 26
        assert s.signal == 9
