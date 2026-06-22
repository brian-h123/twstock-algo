"""Unit tests for BollingerBreakout — hand-built fixtures, no real market data."""

import pandas as pd
import pytest

from twstock.strategies.bollinger_breakout import BollingerBreakout


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


_WINDOW = 3
_K = 1.0

# Fixture note: canonical params are window=20, k=2.0, but for a small hand-built
# fixture we use window=3, k=1.0. The reason is mathematical: the maximum z-score
# of any single bar within its own rolling window (ddof=0) is sqrt(n-1). With k=2.0
# and window=3, sqrt(2) ≈ 1.41 < 2.0, so a breakout is geometrically impossible in
# a 3-bar window. k=1.0 is achievable (sqrt(2) > 1.0), and exercises identical logic.
#
# Prices [100, 100, 200, 90, 100]:
#   Bar 0-1 (100): NaN  — bollinger warmup (need window=3 bars; first non-NaN at index 2)
#   Bar 2   (200): window=[100,100,200] → mean≈133, std≈47, upper≈180 < 200 → entry 1.0
#   Bar 3   (90):  window=[100,200,90]  → mean=130, middle=130 > 90          → exit  0.0
#   Bar 4   (100): window=[200,90,100]  → mean=130, upper≈180 > 100, flat    → NaN
_BARS = _make_bars([100.0, 100.0, 200.0, 90.0, 100.0])

# Grey-zone fixture: after entry the close stays between middle and upper for one
# bar before eventually dropping below the middle and triggering exit.
#
# Prices [100, 100, 200, 155, 90]:
#   Bar 2 (200): entry 1.0
#   Bar 3 (155): window=[100,200,155] → middle≈152, upper≈193; 152 < 155 < 193 → NaN (grey zone)
#   Bar 4 (90):  window=[200,155,90]  → middle≈148 > 90                        → exit 0.0
_BARS_GREY = _make_bars([100.0, 100.0, 200.0, 155.0, 90.0])

# Two full cycles: breakout → fall-below-middle → breakout → fall-below-middle.
#
# Prices [100, 100, 200, 90, 100, 200, 90]:
#   Bar 2: entry 1.0   Bar 3: exit 0.0
#   Bar 4: NaN         (flat; close=100 not > upper≈180)
#   Bar 5: entry 1.0   Bar 6: exit 0.0
_BARS_TWO = _make_bars([100.0, 100.0, 200.0, 90.0, 100.0, 200.0, 90.0])


class TestBollingerBreakoutSignals:
    def test_warmup_is_nan(self):
        """First window-1 bars are NaN, never 0."""
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(_BARS)
        assert signals.iloc[: _WINDOW - 1].isna().all()

    def test_enter_on_upper_breakout(self):
        """Signal = 1.0 on the first bar where close breaks above the upper band."""
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(_BARS)
        assert signals.iloc[_WINDOW - 1] == pytest.approx(1.0)  # bar 2

    def test_no_signal_while_long_in_grey_zone(self):
        """NaN while long and close is between the middle and upper band."""
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(_BARS_GREY)
        assert pd.isna(signals.iloc[3])  # bar 3: close above middle, below upper

    def test_exit_below_middle(self):
        """Signal = 0.0 on the first bar where close drops below the middle band while long."""
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(_BARS)
        assert signals.iloc[_WINDOW] == pytest.approx(0.0)  # bar 3

    def test_no_signal_when_flat_and_below_middle(self):
        """NaN when already flat and close is below middle — exit condition doesn't re-fire."""
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(_BARS)
        assert pd.isna(signals.iloc[_WINDOW + 1])  # bar 4: flat, no breakout

    def test_two_complete_cycles(self):
        """Strategy correctly handles two consecutive enter-exit cycles."""
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(_BARS_TWO)
        assert signals.iloc[2] == pytest.approx(1.0)  # first entry
        assert signals.iloc[3] == pytest.approx(0.0)  # first exit
        assert pd.isna(signals.iloc[4])  # flat, no breakout
        assert signals.iloc[5] == pytest.approx(1.0)  # re-entry
        assert signals.iloc[6] == pytest.approx(0.0)  # re-exit

    def test_never_enters_in_stable_market(self):
        """Flat prices → std=0 → close == upper → strictly-greater guard prevents entry."""
        bars = _make_bars([100.0] * 10)
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(bars)
        assert signals.dropna().empty

    def test_signal_values_are_zero_one_or_nan(self):
        """Signal series contains only 0.0, 1.0, and NaN — nothing else."""
        signals = BollingerBreakout(window=_WINDOW, k=_K).generate_signals(_BARS_TWO)
        assert set(signals.dropna().unique()).issubset({0.0, 1.0})

    def test_generate_signals_is_pure(self):
        """Calling generate_signals twice on the same bars returns identical output."""
        strat = BollingerBreakout(window=_WINDOW, k=_K)
        s1 = strat.generate_signals(_BARS)
        s2 = strat.generate_signals(_BARS)
        pd.testing.assert_series_equal(s1, s2)


class TestBollingerBreakoutParamValidation:
    def test_raises_when_window_is_one(self):
        with pytest.raises(ValueError):
            BollingerBreakout(window=1)

    def test_raises_when_window_is_zero(self):
        with pytest.raises(ValueError):
            BollingerBreakout(window=0)

    def test_raises_when_k_is_zero(self):
        with pytest.raises(ValueError):
            BollingerBreakout(k=0.0)

    def test_raises_when_k_is_negative(self):
        with pytest.raises(ValueError):
            BollingerBreakout(k=-1.0)

    def test_canonical_params_stored(self):
        """Default params match the spec (window=20, k=2.0)."""
        s = BollingerBreakout()
        assert s.window == 20
        assert s.k == pytest.approx(2.0)
