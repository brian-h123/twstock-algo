"""Unit tests for DonchianBreakout — hand-built fixtures, no real market data."""

import pandas as pd
import pytest

from twstock.strategies.donchian_breakout import DonchianBreakout

_WINDOW = 2


def _make_bars(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2020-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [10_000.0] * n,
        },
        index=idx,
    )


# Basic enter/exit sequence (window=2):
#
#   Channels (prev_upper / prev_lower computed after shift(1)):
#   idx:    0    1    2    3    4    5
#   upper: NaN  100  100  105  110  110
#   lower: NaN   90   90   90   88   85
#   p_up:  NaN  NaN  100  100  105  110
#   p_lo:  NaN  NaN   90   90   90   88
#
#   Bar 0,1: NaN — warmup
#   Bar 2:   close=95 < prev_upper=100 → NaN
#   Bar 3:   close=102 > prev_upper=100, flat → enter: signal 1.0
#   Bar 4:   close=90 = prev_lower=90, not < → NaN (boundary: equal is not below)
#   Bar 5:   close=87 < prev_lower=88, long → exit: signal 0.0
_BARS = _make_bars(
    highs=[100.0, 100.0, 100.0, 105.0, 110.0, 108.0],
    lows=[90.0, 90.0, 90.0, 92.0, 88.0, 85.0],
    closes=[95.0, 95.0, 95.0, 102.0, 90.0, 87.0],
)

# Grey-zone fixture: after entry at bar 3, close is clearly above prev_lower
# before eventually breaking below it at bar 5.
#
#   Bar 4: close=93 > prev_lower=90 → NaN (in grey zone, stays long)
#   Bar 5: close=87 < prev_lower=88 → exit: signal 0.0
_BARS_GREY = _make_bars(
    highs=[100.0, 100.0, 100.0, 105.0, 110.0, 108.0],
    lows=[90.0, 90.0, 90.0, 92.0, 88.0, 85.0],
    closes=[95.0, 95.0, 95.0, 102.0, 93.0, 87.0],
)

# No re-entry fixture: a second breakout above prev_upper fires at bar 4
# while already in a long position — guard blocks it.
#
#   Bar 3: close=102 > prev_upper=100 → enter: signal 1.0
#   Bar 4: close=112 > prev_upper=105, in_position → NaN
_BARS_NO_REENTRY = _make_bars(
    highs=[100.0, 100.0, 100.0, 105.0, 115.0],
    lows=[90.0, 90.0, 90.0, 92.0, 95.0],
    closes=[95.0, 95.0, 95.0, 102.0, 112.0],
)

# Two complete cycles: enter→exit→enter→exit.
# Between cycles (bar 5), close=95 stays below prev_upper=105 → NaN.
#
#   Bar 3: enter (1.0)   Bar 4: exit (0.0)
#   Bar 6: re-enter (1.0) Bar 7: exit (0.0)
_BARS_TWO_CYCLES = _make_bars(
    highs=[100.0, 100.0, 100.0, 105.0, 100.0, 100.0, 105.0, 100.0],
    lows=[90.0, 90.0, 90.0, 92.0, 82.0, 90.0, 92.0, 82.0],
    closes=[95.0, 95.0, 95.0, 102.0, 83.0, 95.0, 102.0, 83.0],
)

# Flat market: close never exceeds prev_upper → no signals ever.
_BARS_FLAT = _make_bars(
    highs=[100.0] * 6,
    lows=[90.0] * 6,
    closes=[95.0] * 6,
)


class TestDonchianBreakoutSignals:
    def test_warmup_is_nan(self):
        """First `window` bars are NaN, never 0."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS)
        assert signals.iloc[:_WINDOW].isna().all()

    def test_enters_on_upside_breakout(self):
        """Signal = 1.0 when close exceeds the prior bar's upper channel."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS)
        assert signals.iloc[3] == pytest.approx(1.0)

    def test_no_signal_below_prev_upper(self):
        """NaN on bar 2 — close below prev_upper, no breakout."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS)
        assert pd.isna(signals.iloc[2])

    def test_no_reentry_while_long(self):
        """NaN when close > prev_upper but already in position."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS_NO_REENTRY)
        assert signals.iloc[3] == pytest.approx(1.0)
        assert pd.isna(signals.iloc[4])

    def test_exits_on_downside_breakdown(self):
        """Signal = 0.0 when close drops below the prior bar's lower channel."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS)
        assert signals.iloc[5] == pytest.approx(0.0)

    def test_grey_zone_while_long(self):
        """NaN when in position and close is above prev_lower (grey zone)."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS_GREY)
        assert pd.isna(signals.iloc[4])
        assert signals.iloc[5] == pytest.approx(0.0)

    def test_boundary_at_lower_channel_does_not_exit(self):
        """Close exactly equal to prev_lower does not exit (condition is strict <)."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS)
        # bar 4: close=90.0 == prev_lower=90.0 → NaN, not 0.0
        assert pd.isna(signals.iloc[4])

    def test_two_complete_cycles(self):
        """Strategy correctly handles two consecutive enter-exit cycles."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS_TWO_CYCLES)
        assert signals.iloc[3] == pytest.approx(1.0)
        assert signals.iloc[4] == pytest.approx(0.0)
        assert signals.iloc[6] == pytest.approx(1.0)
        assert signals.iloc[7] == pytest.approx(0.0)

    def test_never_enters_in_flat_market(self):
        """No signals emitted when close never exceeds the prior upper channel."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS_FLAT)
        assert signals.dropna().empty

    def test_signal_values_are_zero_one_or_nan(self):
        """Signal series contains only 0.0, 1.0, and NaN — nothing else."""
        signals = DonchianBreakout(window=_WINDOW).generate_signals(_BARS_TWO_CYCLES)
        assert set(signals.dropna().unique()).issubset({0.0, 1.0})

    def test_generate_signals_is_pure(self):
        """Calling generate_signals twice on the same bars returns identical output."""
        strat = DonchianBreakout(window=_WINDOW)
        s1 = strat.generate_signals(_BARS)
        s2 = strat.generate_signals(_BARS)
        pd.testing.assert_series_equal(s1, s2)


class TestDonchianBreakoutParamValidation:
    def test_raises_when_window_is_zero(self):
        with pytest.raises(ValueError):
            DonchianBreakout(window=0)

    def test_raises_when_window_is_negative(self):
        with pytest.raises(ValueError):
            DonchianBreakout(window=-1)

    def test_canonical_window_stored(self):
        """Default window matches the spec (window=20)."""
        assert DonchianBreakout().window == 20
