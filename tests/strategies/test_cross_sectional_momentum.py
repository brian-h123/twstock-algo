"""Unit tests for CrossSectionalMomentum — hand-built fixtures, no real market data."""

import pandas as pd
import pytest

from twstock.strategies.cross_sectional_momentum import CrossSectionalMomentum


def _make_panel(prices_by_ticker: dict[str, list[float]]) -> dict[str, pd.DataFrame]:
    n = len(next(iter(prices_by_ticker.values())))
    idx = pd.date_range("2020-01-02", periods=n, freq="B")
    return {
        ticker: pd.DataFrame(
            {
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": [10_000.0] * n,
            },
            index=idx,
        )
        for ticker, prices in prices_by_ticker.items()
    }


# Small params keep fixtures short while exercising the same logic as 252/21.
# warmup = lookback + skip = 3 + 2 = 5 bars; a 6-bar panel → only bar 5 is non-NaN.
_LOOKBACK = 3
_SKIP = 2

# score at bar 5 = pct_change(3) at bar 3 = close[3]/close[0] - 1 = 106/100 - 1 = 0.06
_PRICES_LINEAR = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]

# UP rises 5 pts/bar → score at bar 5 = 115/100 - 1 = 0.15
# DOWN falls 5 pts/bar → score at bar 5 = 85/100 - 1 = -0.15
_PRICES_UP = [100.0, 105.0, 110.0, 115.0, 120.0, 125.0]
_PRICES_DOWN = [100.0, 95.0, 90.0, 85.0, 80.0, 75.0]

_N_BARS = len(_PRICES_LINEAR)  # 6


class TestCrossSectionalMomentumScores:
    def test_warmup_bars_are_nan(self):
        """First lookback+skip bars are NaN — ticker is ineligible during warmup."""
        panel = _make_panel({"A": _PRICES_LINEAR})
        scores = CrossSectionalMomentum(lookback=_LOOKBACK, skip=_SKIP).generate_scores(panel)
        warmup = _LOOKBACK + _SKIP  # 5
        assert scores["A"].iloc[:warmup].isna().all()

    def test_first_valid_bar_is_correct(self):
        """Score at bar lookback+skip equals hand-computed pct_change(lookback) at bar lookback."""
        panel = _make_panel({"A": _PRICES_LINEAR})
        scores = CrossSectionalMomentum(lookback=_LOOKBACK, skip=_SKIP).generate_scores(panel)
        # bar 5 = shift(2) of pct_change(3)[3] = 106/100 - 1
        expected = _PRICES_LINEAR[_LOOKBACK] / _PRICES_LINEAR[0] - 1
        assert scores["A"].iloc[_LOOKBACK + _SKIP] == pytest.approx(expected)

    def test_higher_momentum_ticker_scores_higher(self):
        """Ticker with rising prices scores higher than one with falling prices."""
        panel = _make_panel({"UP": _PRICES_UP, "DOWN": _PRICES_DOWN})
        scores = CrossSectionalMomentum(lookback=_LOOKBACK, skip=_SKIP).generate_scores(panel)
        last = scores.iloc[-1]
        assert last["UP"] > last["DOWN"]

    def test_skip_zero_warmup_equals_lookback_only(self):
        """With skip=0, the first non-NaN bar is at index lookback (not lookback+skip)."""
        panel = _make_panel({"A": _PRICES_LINEAR})
        scores = CrossSectionalMomentum(lookback=_LOOKBACK, skip=0).generate_scores(panel)
        assert scores["A"].iloc[:_LOOKBACK].isna().all()
        assert scores["A"].iloc[_LOOKBACK:].notna().all()

    def test_generate_scores_is_stateless(self):
        """Calling generate_scores twice on the same panel returns identical DataFrames."""
        panel = _make_panel({"A": _PRICES_LINEAR, "B": _PRICES_UP})
        strategy = CrossSectionalMomentum(lookback=_LOOKBACK, skip=_SKIP)
        first = strategy.generate_scores(panel)
        second = strategy.generate_scores(panel)
        pd.testing.assert_frame_equal(first, second)


class TestCrossSectionalMomentumPanelShape:
    def test_output_shape_matches_panel(self):
        """scores.shape is (n_dates, n_tickers)."""
        panel = _make_panel({"A": _PRICES_LINEAR, "B": _PRICES_UP})
        scores = CrossSectionalMomentum(lookback=_LOOKBACK, skip=_SKIP).generate_scores(panel)
        assert scores.shape == (_N_BARS, 2)

    def test_columns_match_panel_keys(self):
        """scores.columns equals the panel dict keys in insertion order."""
        panel = _make_panel({"A": _PRICES_LINEAR, "B": _PRICES_UP})
        scores = CrossSectionalMomentum(lookback=_LOOKBACK, skip=_SKIP).generate_scores(panel)
        assert list(scores.columns) == ["A", "B"]

    def test_index_matches_bars_index(self):
        """scores.index equals the DatetimeIndex of the panel DataFrames."""
        panel = _make_panel({"A": _PRICES_LINEAR})
        scores = CrossSectionalMomentum(lookback=_LOOKBACK, skip=_SKIP).generate_scores(panel)
        assert scores.index.equals(panel["A"].index)


class TestCrossSectionalMomentumParamValidation:
    def test_raises_when_lookback_is_zero(self):
        with pytest.raises(ValueError):
            CrossSectionalMomentum(lookback=0)

    def test_raises_when_lookback_is_negative(self):
        with pytest.raises(ValueError):
            CrossSectionalMomentum(lookback=-1)

    def test_raises_when_skip_is_negative(self):
        with pytest.raises(ValueError):
            CrossSectionalMomentum(skip=-1)

    def test_skip_zero_is_valid(self):
        """skip=0 means no reversal skip; should not raise."""
        CrossSectionalMomentum(lookback=10, skip=0)

    def test_canonical_defaults(self):
        """Default params match the spec (lookback=252, skip=21)."""
        s = CrossSectionalMomentum()
        assert s.lookback == 252
        assert s.skip == 21
