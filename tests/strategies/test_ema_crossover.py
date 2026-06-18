"""Unit tests for EmaCrossover — hand-built fixtures, no real market data."""

import pandas as pd
import pytest

from twstock.strategies.ema_crossover import EmaCrossover


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


# Small params keep the fixture short while exercising the same logic as 20/60.
_FAST = 3
_SLOW = 5

# Warm up both EMAs at 100, then spike up so fast EMA leaps above slow EMA,
# then spike down so fast EMA falls back below slow EMA.
#
#   Bar 0-3 (prices=100): NaN — slow EMA needs slow bars to warm up
#   Bar 4   (price=100):  both EMAs = 100  → signal 0 (not strictly greater)
#   Bar 5   (price=200):  fast EMA 150 > slow EMA 133 → signal 1
#   Bar 6   (price=50):   fast EMA 100 < slow EMA 106 → signal 0
_BARS = _make_bars([100.0] * _SLOW + [200.0] + [50.0])


class TestEmaCrossoverSignals:
    def test_warmup_is_nan(self):
        """First slow-1 bars are NaN, never 0."""
        signals = EmaCrossover(fast=_FAST, slow=_SLOW).generate_signals(_BARS)
        assert signals.iloc[: _SLOW - 1].isna().all()

    def test_no_nan_after_warmup(self):
        """Every bar from index slow-1 onwards is 0.0 or 1.0, never NaN."""
        signals = EmaCrossover(fast=_FAST, slow=_SLOW).generate_signals(_BARS)
        post = signals.iloc[_SLOW - 1 :]
        assert post.notna().all()
        assert set(post.unique()).issubset({0.0, 1.0})

    def test_long_when_fast_above_slow(self):
        """Signal = 1 on the spike-up bar where fast EMA crosses above slow EMA."""
        signals = EmaCrossover(fast=_FAST, slow=_SLOW).generate_signals(_BARS)
        # bar index _SLOW is the 200-price spike
        assert signals.iloc[_SLOW] == pytest.approx(1.0)

    def test_flat_when_fast_below_slow(self):
        """Signal = 0 on the bar where fast EMA drops back below slow EMA."""
        signals = EmaCrossover(fast=_FAST, slow=_SLOW).generate_signals(_BARS)
        # bar index _SLOW + 1 is the 50-price drop
        assert signals.iloc[_SLOW + 1] == pytest.approx(0.0)

    def test_equal_prices_give_zero_not_long(self):
        """When fast EMA == slow EMA (all-flat prices), signal is 0, not 1."""
        bars = _make_bars([100.0] * _SLOW)
        signals = EmaCrossover(fast=_FAST, slow=_SLOW).generate_signals(bars)
        # After warmup (bar _SLOW-1) prices are flat → both EMAs = 100 → not long
        assert signals.iloc[_SLOW - 1 :].eq(0.0).all()


class TestEmaCrossoverParamValidation:
    def test_raises_when_fast_equals_slow(self):
        with pytest.raises(ValueError):
            EmaCrossover(fast=5, slow=5)

    def test_raises_when_fast_greater_than_slow(self):
        with pytest.raises(ValueError):
            EmaCrossover(fast=10, slow=5)

    def test_canonical_params_stored(self):
        """Default params match the spec (fast=20, slow=60)."""
        s = EmaCrossover()
        assert s.fast == 20
        assert s.slow == 60
