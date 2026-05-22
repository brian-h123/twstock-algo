from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import pytest

from twstock.ui.charts import candlestick_volume, compute_bollinger, compute_sma


def _make_ohlcv(n: int = 25) -> pd.DataFrame:
    base = date(2024, 1, 2)
    close = [float(100 + i) for i in range(n)]
    return pd.DataFrame(
        {
            "date": [base + timedelta(days=i) for i in range(n)],
            "open": close,
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "volume": [1000] * n,
        }
    )


class TestComputeSma:
    def test_matches_hand_computed_values(self):
        close = pd.Series(range(1, 21), dtype=float)
        result = compute_sma(close, 5)
        assert result.iloc[:4].isna().all()
        assert result.iloc[4] == pytest.approx(3.0)  # mean(1..5)
        assert result.iloc[5] == pytest.approx(4.0)  # mean(2..6)
        assert result.iloc[19] == pytest.approx(18.0)  # mean(16..20)

    def test_same_length_as_input(self):
        close = pd.Series(range(1, 21), dtype=float)
        assert len(compute_sma(close, 5)) == 20


class TestComputeBollinger:
    def test_middle_band_equals_sma20(self):
        close = pd.Series(range(1, 41), dtype=float)
        lower, middle, upper = compute_bollinger(close, 20, 2.0)
        pd.testing.assert_series_equal(middle, compute_sma(close, 20))

    def test_outer_bands_are_two_sigma(self):
        close = pd.Series(range(1, 41), dtype=float)
        lower, middle, upper = compute_bollinger(close, 20, 2.0)
        rolling_std = close.rolling(20).std()
        pd.testing.assert_series_equal(upper, middle + 2.0 * rolling_std)
        pd.testing.assert_series_equal(lower, middle - 2.0 * rolling_std)

    def test_returns_three_series(self):
        close = pd.Series(range(1, 41), dtype=float)
        result = compute_bollinger(close)
        assert len(result) == 3
        assert all(isinstance(s, pd.Series) for s in result)


class TestCandlestickVolume:
    def test_returns_figure(self):
        assert isinstance(candlestick_volume(_make_ohlcv()), go.Figure)

    def test_trace_types_no_overlays(self):
        fig = candlestick_volume(_make_ohlcv())
        assert len(fig.data) == 2
        assert isinstance(fig.data[0], go.Candlestick)
        assert isinstance(fig.data[1], go.Bar)

    def test_one_overlay_adds_one_trace(self):
        df = _make_ohlcv()
        sma = compute_sma(pd.Series(df["close"].values), 5)
        fig = candlestick_volume(df, overlays={"SMA(5)": sma})
        assert len(fig.data) == 3
        assert isinstance(fig.data[2], go.Scatter)

    def test_two_overlays_adds_two_traces(self):
        df = _make_ohlcv()
        close = pd.Series(df["close"].values)
        fig = candlestick_volume(
            df,
            overlays={
                "SMA(5)": compute_sma(close, 5),
                "SMA(10)": compute_sma(close, 10),
            },
        )
        assert len(fig.data) == 4

    def test_empty_ex_date_markers_no_error(self):
        df = _make_ohlcv()
        empty = pd.DataFrame(columns=["date", "action_type", "cash_dividend", "stock_ratio"])
        fig = candlestick_volume(df, ex_date_markers=empty)
        assert len(fig.data) == 2

    def test_none_ex_date_markers_no_error(self):
        fig = candlestick_volume(_make_ohlcv(), ex_date_markers=None)
        assert isinstance(fig, go.Figure)

    def test_ex_date_marker_adds_scatter_trace(self):
        df = _make_ohlcv()
        markers = pd.DataFrame(
            {
                "date": [df["date"].iloc[5]],
                "action_type": ["cash_dividend"],
                "cash_dividend": [2.5],
                "stock_ratio": [0.0],
            }
        )
        fig = candlestick_volume(df, ex_date_markers=markers)
        assert len(fig.data) == 3  # candlestick + volume + marker scatter

    def test_rangeslider_off(self):
        fig = candlestick_volume(_make_ohlcv())
        assert fig.layout.xaxis.rangeslider.visible is False
