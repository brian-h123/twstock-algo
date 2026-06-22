"""Unit tests for RsiReversion — hand-built fixtures, no real market data."""

import pandas as pd
import pytest

from twstock.strategies.rsi_reversion import RsiReversion


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


_PERIOD = 3

# Warmup → oversold → overbought.
# RSI uses Wilder's EWM (alpha = 1/period). With all-down bars after warmup,
# avg_gain → 0 so RSI → 0. A big up bar then pushes RSI >> 50.
#
#   Bar 0–2 (100): NaN — first `period` bars are always NaN
#   Bar 3   (95):  RSI = 0 < 30, flat  → enter: signal 1.0
#   Bar 4   (90):  RSI = 0, long       → NaN (no change; enter guard is `not in_position`)
#   Bar 5   (85):  RSI = 0, long       → NaN
#   Bar 6   (120): RSI ≈ 83 > 50, long → exit: signal 0.0
#   Bar 7   (130): RSI ≈ 88, flat      → NaN (exit guard is `in_position`)
_BARS = _make_bars([100.0, 100.0, 100.0, 95.0, 90.0, 85.0, 120.0, 130.0])

# Grey-zone fixture: after oversold entry, RSI bounces into the 30–50 band
# before eventually crossing 50 and triggering the exit.
#
#   Bar 6 (90):  RSI ≈ 41.5 — in the 30–50 band while long → NaN (stay long)
#   Bar 7 (95):  RSI ≈ 64 > 50, long → exit: signal 0.0
_BARS_GREY = _make_bars([100.0, 100.0, 100.0, 95.0, 90.0, 85.0, 90.0, 95.0])

# Two full cycles: oversold → overbought → oversold → overbought.
# The alternating 95/200 blocks drive RSI to ≈ 0 then ≈ 99 in each leg,
# so the second entry and exit are unambiguous.
#
#   Bar  3: enter (1.0)    Bar  6: exit (0.0)
#   Bar  9: re-enter (1.0) Bar 12: exit (0.0)
_BARS_TWO = _make_bars([100.0] * 3 + [95.0] * 3 + [200.0] * 3 + [95.0] * 3 + [200.0] * 3)


class TestRsiReversionSignals:
    def test_warmup_is_nan(self):
        """First `period` bars are NaN, never 0."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS)
        assert signals.iloc[:_PERIOD].isna().all()

    def test_enter_on_oversold(self):
        """Signal = 1.0 on the first bar where RSI < 30 while flat."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS)
        assert signals.iloc[_PERIOD] == pytest.approx(1.0)  # bar 3

    def test_no_signal_while_long_and_rsi_below_exit(self):
        """NaN on bars 4-5 — RSI still < 30 but already in position."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS)
        assert pd.isna(signals.iloc[_PERIOD + 1])  # bar 4
        assert pd.isna(signals.iloc[_PERIOD + 2])  # bar 5

    def test_exit_on_overbought(self):
        """Signal = 0.0 on the bar where RSI > 50 while long."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS)
        assert signals.iloc[_PERIOD + 3] == pytest.approx(0.0)  # bar 6

    def test_no_signal_after_exit_when_still_overbought(self):
        """NaN after exit — RSI > 50 but already flat, so exit guard blocks."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS)
        assert pd.isna(signals.iloc[_PERIOD + 4])  # bar 7

    def test_grey_zone_does_not_exit_long(self):
        """RSI in the 30–50 band while long → NaN (neither enter nor exit fires)."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS_GREY)
        # bar 6: RSI ≈ 41.5 — above 30 (no entry) and below 50 (no exit)
        assert pd.isna(signals.iloc[6])
        # bar 7: RSI ≈ 64 — exits
        assert signals.iloc[7] == pytest.approx(0.0)

    def test_two_complete_cycles(self):
        """Strategy correctly handles two consecutive enter-exit cycles."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS_TWO)
        assert signals.iloc[3] == pytest.approx(1.0)  # first entry
        assert signals.iloc[6] == pytest.approx(0.0)  # first exit
        assert signals.iloc[9] == pytest.approx(1.0)  # re-entry
        assert signals.iloc[12] == pytest.approx(0.0)  # second exit

    def test_never_enters_in_rising_market(self):
        """All-up prices keep RSI >> 50; strategy stays flat with no signals."""
        bars = _make_bars([100.0, 105.0, 110.0, 115.0, 120.0, 125.0])
        signals = RsiReversion(period=_PERIOD).generate_signals(bars)
        assert signals.dropna().empty

    def test_signal_values_are_zero_one_or_nan(self):
        """Signal series contains only 0.0, 1.0, and NaN — nothing else."""
        signals = RsiReversion(period=_PERIOD).generate_signals(_BARS_TWO)
        assert set(signals.dropna().unique()).issubset({0.0, 1.0})

    def test_generate_signals_is_pure(self):
        """Calling generate_signals twice on the same bars returns identical output."""
        strat = RsiReversion(period=_PERIOD)
        s1 = strat.generate_signals(_BARS)
        s2 = strat.generate_signals(_BARS)
        pd.testing.assert_series_equal(s1, s2)


class TestRsiReversionParamValidation:
    def test_raises_when_period_is_one(self):
        with pytest.raises(ValueError):
            RsiReversion(period=1)

    def test_raises_when_period_is_zero(self):
        with pytest.raises(ValueError):
            RsiReversion(period=0)

    def test_canonical_period_stored(self):
        """Default period matches the spec (period=14)."""
        assert RsiReversion().period == 14
