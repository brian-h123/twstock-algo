import pandas as pd
import pytest

from twstock.data.adjustments import adjust

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ohlcv(dates: list[str], closes: list[float], ticker: str = "2330") -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ticker": [ticker] * n,
            "open": [c - 1.0 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        }
    )


def _cash_div(ex_date: str, cash_amount: float, ticker: str = "2330") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime([ex_date]),
            "ticker": [ticker],
            "action_type": ["cash_dividend"],
            "cash_amount": [float(cash_amount)],
            "ratio": [float("nan")],
        }
    )


def _split(ex_date: str, ratio: float, ticker: str = "2330") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime([ex_date]),
            "ticker": [ticker],
            "action_type": ["split"],
            "cash_amount": [float("nan")],
            "ratio": [float(ratio)],
        }
    )


def _stock_div(ex_date: str, ratio: float, ticker: str = "2330") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime([ex_date]),
            "ticker": [ticker],
            "action_type": ["stock_dividend"],
            "cash_amount": [float("nan")],
            "ratio": [float(ratio)],
        }
    )


def _empty_actions() -> pd.DataFrame:
    return pd.DataFrame(columns=["ex_date", "ticker", "action_type", "cash_amount", "ratio"])


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


def test_no_actions_returns_same_values():
    raw = _ohlcv(["2023-01-03", "2023-01-04", "2023-01-05"], [100.0, 200.0, 300.0])
    result = adjust(raw, _empty_actions())
    pd.testing.assert_frame_equal(result, raw)


def test_action_before_data_range_is_noop():
    # ex_date predates all OHLCV rows — no rows to adjust
    raw = _ohlcv(["2023-01-03", "2023-01-04", "2023-01-05"], [100.0, 200.0, 300.0])
    actions = _cash_div("2022-12-01", cash_amount=5.0)
    result = adjust(raw, actions)
    pd.testing.assert_frame_equal(result, raw)


# ---------------------------------------------------------------------------
# Cash dividend
# ---------------------------------------------------------------------------


def test_cash_dividend_scales_prior_ohlc():
    # Jan 3 close=100, Jan 4 close=102 (last before ex_date), Jan 5 close=97
    # factor = (102 - 5) / 102 = 97/102
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-05"],
        [100.0, 102.0, 97.0],
    )
    actions = _cash_div("2023-01-05", cash_amount=5.0)
    result = adjust(raw, actions)

    factor = 97.0 / 102.0
    assert result.loc[0, "close"] == pytest.approx(100.0 * factor)
    assert result.loc[0, "open"] == pytest.approx(99.0 * factor)
    assert result.loc[0, "high"] == pytest.approx(101.0 * factor)
    assert result.loc[0, "low"] == pytest.approx(98.0 * factor)
    # Jan 4's close lands exactly on the reference price
    assert result.loc[1, "close"] == pytest.approx(97.0)


def test_cash_dividend_leaves_ex_date_bar_unchanged():
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-05"],
        [100.0, 102.0, 97.0],
    )
    actions = _cash_div("2023-01-05", cash_amount=5.0)
    result = adjust(raw, actions)

    assert result.loc[2, "close"] == pytest.approx(97.0)
    assert result.loc[2, "open"] == pytest.approx(96.0)


def test_cash_dividend_leaves_volume_unchanged():
    raw = _ohlcv(["2023-01-03", "2023-01-04", "2023-01-05"], [100.0, 102.0, 97.0])
    actions = _cash_div("2023-01-05", cash_amount=5.0)
    result = adjust(raw, actions)

    pd.testing.assert_series_equal(result["volume"], raw["volume"])


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


def test_split_halves_prior_prices():
    # 2:1 split on Jan 5 — prior closes halved; Jan 5 bar untouched
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-05"],
        [200.0, 200.0, 100.0],
    )
    actions = _split("2023-01-05", ratio=2.0)
    result = adjust(raw, actions)

    assert result.loc[0, "close"] == pytest.approx(100.0)
    assert result.loc[1, "close"] == pytest.approx(100.0)
    assert result.loc[2, "close"] == pytest.approx(100.0)  # unchanged


# ---------------------------------------------------------------------------
# Stock dividend
# ---------------------------------------------------------------------------


def test_stock_dividend_scales_prior_prices():
    # 10% stock dividend on Jan 5 — prior prices scaled by 1/1.1
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-05"],
        [110.0, 110.0, 100.0],
    )
    actions = _stock_div("2023-01-05", ratio=0.1)
    result = adjust(raw, actions)

    assert result.loc[0, "close"] == pytest.approx(100.0)
    assert result.loc[1, "close"] == pytest.approx(100.0)
    assert result.loc[2, "close"] == pytest.approx(100.0)  # unchanged


# ---------------------------------------------------------------------------
# Multiple actions — compounding
# ---------------------------------------------------------------------------


def test_multiple_actions_compound_correctly():
    # Jan 3 close=100, Jan 4 close=200, Jan 6 close=400
    # Action A (cash_div): ex=Jan 4, amount=10
    #   last_close before Jan 4 = 100 → factor_A = (100-10)/100 = 0.9
    #   Jan 3 scaled by 0.9; Jan 4 and Jan 6 untouched by A
    # Action B (split):    ex=Jan 6, ratio=2
    #   last_close before Jan 6 = 200 (Jan 4, unchanged by A) → factor_B = 0.5
    #   Jan 3 scaled again by 0.5; Jan 4 scaled by 0.5; Jan 6 untouched
    # Final: Jan 3 = 100 * 0.9 * 0.5 = 45.0
    #        Jan 4 = 200 * 0.5        = 100.0
    #        Jan 6 = 400              (unchanged)
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-06"],
        [100.0, 200.0, 400.0],
    )
    actions = pd.concat(
        [
            _cash_div("2023-01-04", cash_amount=10.0),
            _split("2023-01-06", ratio=2.0),
        ],
        ignore_index=True,
    )
    result = adjust(raw, actions)

    assert result.loc[0, "close"] == pytest.approx(45.0)
    assert result.loc[1, "close"] == pytest.approx(100.0)
    assert result.loc[2, "close"] == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# Input immutability
# ---------------------------------------------------------------------------


def test_adjust_does_not_mutate_raw_ohlcv():
    raw = _ohlcv(["2023-01-03", "2023-01-04"], [100.0, 200.0])
    original_closes = raw["close"].tolist()
    adjust(raw, _cash_div("2023-01-04", cash_amount=5.0))
    assert raw["close"].tolist() == original_closes


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_bad_cash_amount_raises_value_error():
    # cash_amount=110 >= last_close=100 — factor would be negative
    raw = _ohlcv(["2023-01-03", "2023-01-04"], [100.0, 90.0])
    actions = _cash_div("2023-01-04", cash_amount=110.0)
    with pytest.raises(ValueError, match="cash_amount"):
        adjust(raw, actions)
