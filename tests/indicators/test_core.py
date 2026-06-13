import math

import pandas as pd
import pytest

from twstock.indicators.core import bollinger_bands, ema, rsi, sma


def make_series(values, start="2020-01-01"):
    """Build a float64 Series with a DatetimeIndex."""
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype="float64")


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------


class TestSma:
    def test_values(self):
        # window=3 on [1,2,3,4,5]: mean slides over each triple
        s = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(s, window=3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(3.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_warmup_nan_on_30_rows(self):
        s = make_series([float(i) for i in range(1, 31)])
        result = sma(s, window=5)
        assert result.iloc[:4].isna().all()
        assert result.iloc[4:].notna().all()

    def test_index_preserved(self):
        s = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
        assert sma(s, window=3).index.equals(s.index)

    def test_type_error_not_series(self):
        with pytest.raises(TypeError):
            sma([1.0, 2.0, 3.0], window=3)

    def test_type_error_no_datetime_index(self):
        s = pd.Series([1.0, 2.0, 3.0], dtype="float64")
        with pytest.raises(TypeError):
            sma(s, window=2)

    def test_type_error_wrong_dtype(self):
        idx = pd.date_range("2020-01-01", periods=3, freq="B")
        s = pd.Series([1, 2, 3], index=idx, dtype="int64")
        with pytest.raises(TypeError):
            sma(s, window=2)


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


class TestEma:
    def test_values(self):
        # span=3 → α=0.5; seeds: EMA[0]=1, EMA[1]=1.5, EMA[2]=2.25 (first non-NaN)
        s = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ema(s, window=3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.25)
        assert result.iloc[3] == pytest.approx(3.125)
        assert result.iloc[4] == pytest.approx(4.0625)

    def test_warmup_nan_on_30_rows(self):
        s = make_series([float(i) for i in range(1, 31)])
        result = ema(s, window=5)
        assert result.iloc[:4].isna().all()
        assert result.iloc[4:].notna().all()

    def test_reacts_faster_than_sma(self):
        # At the first bar of a step-up, EMA has already moved higher than SMA
        # because EMA weights recent prices more; SMA is still averaging old values
        prices = [10.0] * 15 + [20.0] * 15
        s = make_series(prices)
        assert ema(s, window=5).iloc[15] > sma(s, window=5).iloc[15]

    def test_index_preserved(self):
        s = make_series([float(i) for i in range(1, 31)])
        assert ema(s, window=5).index.equals(s.index)

    def test_type_error_not_series(self):
        with pytest.raises(TypeError):
            ema([1.0, 2.0, 3.0], window=3)

    def test_type_error_no_datetime_index(self):
        s = pd.Series([1.0, 2.0, 3.0], dtype="float64")
        with pytest.raises(TypeError):
            ema(s, window=2)

    def test_type_error_wrong_dtype(self):
        idx = pd.date_range("2020-01-01", periods=3, freq="B")
        s = pd.Series([1, 2, 3], index=idx, dtype="int64")
        with pytest.raises(TypeError):
            ema(s, window=2)


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------


class TestBollingerBands:
    def test_middle_equals_sma(self):
        s = make_series([float(i) for i in range(1, 31)])
        _, middle, _ = bollinger_bands(s, window=5)
        pd.testing.assert_series_equal(middle, sma(s, window=5))

    def test_bands_symmetric_around_middle(self):
        s = make_series([float(i) for i in range(1, 31)])
        lower, middle, upper = bollinger_bands(s, window=5)
        pd.testing.assert_series_equal(upper - middle, middle - lower)

    def test_upper_above_lower(self):
        # Series with variance so bands are non-degenerate
        s = make_series([float(i) + (i % 3) * 0.5 for i in range(1, 31)])
        lower, _, upper = bollinger_bands(s, window=5)
        assert (upper.dropna() > lower.dropna()).all()

    def test_values(self):
        # window=3, k=1: std([1,2,3], ddof=0) = sqrt(2/3)
        s = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
        lower, middle, upper = bollinger_bands(s, window=3, k=1.0)
        expected_std = math.sqrt(2 / 3)
        assert middle.iloc[2] == pytest.approx(2.0)
        assert upper.iloc[2] == pytest.approx(2.0 + expected_std)
        assert lower.iloc[2] == pytest.approx(2.0 - expected_std)

    def test_warmup_nan_on_30_rows(self):
        s = make_series([float(i) for i in range(1, 31)])
        lower, middle, upper = bollinger_bands(s, window=5)
        for band in (lower, middle, upper):
            assert band.iloc[:4].isna().all()
            assert band.iloc[4:].notna().all()

    def test_type_error_not_series(self):
        with pytest.raises(TypeError):
            bollinger_bands([1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


class TestRsi:
    def test_warmup_nan_on_30_rows(self):
        s = make_series([float(i) for i in range(1, 31)])
        result = rsi(s, window=14)
        assert result.iloc[:14].isna().all()
        assert result.iloc[14:].notna().all()

    def test_bounded_between_0_and_100(self):
        s = make_series([float(i) for i in range(1, 31)])
        result = rsi(s, window=5)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_all_gains_gives_100(self):
        # Strictly increasing prices → avg_loss=0 → RSI=100
        s = make_series([float(i) for i in range(1, 31)])
        result = rsi(s, window=5)
        assert (result.dropna() == 100.0).all()

    def test_all_losses_gives_0(self):
        # Strictly decreasing prices → avg_gain=0 → RS=0 → RSI=0
        s = make_series([float(30 - i) for i in range(30)])
        result = rsi(s, window=5)
        assert (result.dropna() == 0.0).all()

    def test_index_preserved(self):
        s = make_series([float(i) for i in range(1, 31)])
        assert rsi(s, window=5).index.equals(s.index)

    def test_type_error_not_series(self):
        with pytest.raises(TypeError):
            rsi([1.0, 2.0, 3.0])

    def test_type_error_no_datetime_index(self):
        s = pd.Series([float(i) for i in range(30)], dtype="float64")
        with pytest.raises(TypeError):
            rsi(s, window=14)

    def test_type_error_wrong_dtype(self):
        idx = pd.date_range("2020-01-01", periods=30, freq="B")
        s = pd.Series(list(range(30)), index=idx, dtype="int64")
        with pytest.raises(TypeError):
            rsi(s, window=14)
