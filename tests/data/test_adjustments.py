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
            "subscription_price": [float("nan")],
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
            "subscription_price": [float("nan")],
        }
    )


def _cash_and_stock_div(
    ex_date: str, cash_amount: float, ratio: float, ticker: str = "2330"
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime([ex_date]),
            "ticker": [ticker],
            "action_type": ["cash_and_stock_dividend"],
            "cash_amount": [float(cash_amount)],
            "ratio": [float(ratio)],
            "subscription_price": [float("nan")],
        }
    )


def _rights_issue(
    ex_date: str, ratio: float, subscription_price: float, ticker: str = "2330"
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime([ex_date]),
            "ticker": [ticker],
            "action_type": ["rights_issue"],
            "cash_amount": [float("nan")],
            "ratio": [float(ratio)],
            "subscription_price": [float(subscription_price)],
        }
    )


def _capital_reduction(ex_date: str, ratio: float, ticker: str = "2330") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime([ex_date]),
            "ticker": [ticker],
            "action_type": ["capital_reduction"],
            "cash_amount": [float("nan")],
            "ratio": [float(ratio)],
            "subscription_price": [float("nan")],
        }
    )


def _empty_actions() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["ex_date", "ticker", "action_type", "cash_amount", "ratio", "subscription_price"]
    )


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
    # factor = T_close / (T_close + D) = 97 / (97 + 5) = 97/102
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
# Cash-and-stock dividend (combined)
# ---------------------------------------------------------------------------


def test_cash_and_stock_dividend_applies_combined_factor():
    # Jan 3, Jan 4 (pre-ex), Jan 5 (ex-date, close=97)
    # cash_amount=2, ratio=0.05 (5% stock div)
    # combined factor = (97/(97+2)) * (1/(1+0.05)) = (97/99) * (1/1.05)
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-05"],
        [100.0, 102.0, 97.0],
    )
    actions = _cash_and_stock_div("2023-01-05", cash_amount=2.0, ratio=0.05)
    result = adjust(raw, actions)

    factor = (97.0 / (97.0 + 2.0)) * (1.0 / (1.0 + 0.05))
    assert result.loc[0, "close"] == pytest.approx(100.0 * factor)
    assert result.loc[1, "close"] == pytest.approx(102.0 * factor)
    assert result.loc[2, "close"] == pytest.approx(97.0)  # ex-date bar unchanged


def test_cash_and_stock_dividend_leaves_ex_date_bar_unchanged():
    raw = _ohlcv(["2023-01-03", "2023-01-04", "2023-01-05"], [100.0, 102.0, 97.0])
    actions = _cash_and_stock_div("2023-01-05", cash_amount=2.0, ratio=0.05)
    result = adjust(raw, actions)

    assert result.loc[2, "close"] == pytest.approx(97.0)
    assert result.loc[2, "open"] == pytest.approx(96.0)


# ---------------------------------------------------------------------------
# Rights issue
# ---------------------------------------------------------------------------


def test_rights_issue_scales_prior_prices():
    # Jan 3, Jan 4 (pre-ex), Jan 5 (ex-date, close=100)
    # ratio=0.2, sub_price=50
    # factor = (100 + 0.2*50) / (100 * (1+0.2)) = 110 / 120 = 11/12
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-05"],
        [120.0, 120.0, 100.0],
    )
    actions = _rights_issue("2023-01-05", ratio=0.2, subscription_price=50.0)
    result = adjust(raw, actions)

    factor = (100.0 + 0.2 * 50.0) / (100.0 * (1.0 + 0.2))  # 110/120
    assert result.loc[0, "close"] == pytest.approx(120.0 * factor)
    assert result.loc[1, "close"] == pytest.approx(120.0 * factor)
    assert result.loc[2, "close"] == pytest.approx(100.0)  # ex-date bar unchanged


# ---------------------------------------------------------------------------
# Capital reduction
# ---------------------------------------------------------------------------


def test_capital_reduction_scales_prior_prices():
    # ratio = PostReductionReferencePrice / ClosingPriceonTheLastTradingDay
    # For a 20% cash capital reduction at face value NT$10 on a stock at 100:
    # post-ref ≈ 122.5, ratio = 1.225. Pre-event prices scale UP by 1.225.
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-05"],
        [100.0, 100.0, 122.5],
    )
    actions = _capital_reduction("2023-01-05", ratio=1.225)
    result = adjust(raw, actions)

    assert result.loc[0, "close"] == pytest.approx(100.0 * 1.225)
    assert result.loc[1, "close"] == pytest.approx(100.0 * 1.225)
    assert result.loc[2, "close"] == pytest.approx(122.5)  # ex-date bar unchanged


# ---------------------------------------------------------------------------
# Multiple actions — compounding
# ---------------------------------------------------------------------------


def test_multiple_actions_compound_correctly():
    # Jan 3 close=100, Jan 4 close=200, Jan 6 close=400
    # Action A (cash_div): ex=Jan 4, amount=10
    #   T close (Jan 4) = 200 → factor_A = 200/(200+10) = 20/21 ≈ 0.9524
    #   Jan 3 scaled by 20/21; Jan 4 and Jan 6 untouched by A
    # Action B (capital_reduction): ex=Jan 6, ratio=0.5
    #   factor_B = 0.5
    #   Jan 3 scaled again by 0.5; Jan 4 scaled by 0.5; Jan 6 untouched
    # Final: Jan 3 = 100 * (20/21) * 0.5 = 1000/21 ≈ 47.619
    #        Jan 4 = 200 * 0.5            = 100.0
    #        Jan 6 = 400                  (unchanged)
    raw = _ohlcv(
        ["2023-01-03", "2023-01-04", "2023-01-06"],
        [100.0, 200.0, 400.0],
    )
    actions = pd.concat(
        [
            _cash_div("2023-01-04", cash_amount=10.0),
            _capital_reduction("2023-01-06", ratio=0.5),
        ],
        ignore_index=True,
    )
    result = adjust(raw, actions)

    assert result.loc[0, "close"] == pytest.approx(1000.0 / 21.0)
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
    # cash_amount=110 >= T close=90 — factor would be > 1 (bad data)
    raw = _ohlcv(["2023-01-03", "2023-01-04"], [100.0, 90.0])
    actions = _cash_div("2023-01-04", cash_amount=110.0)
    with pytest.raises(ValueError, match="cash_amount"):
        adjust(raw, actions)


def test_cash_and_stock_dividend_bad_cash_amount_raises():
    # cash_amount=110 >= T close=90 for combined event
    raw = _ohlcv(["2023-01-03", "2023-01-04"], [100.0, 90.0])
    actions = _cash_and_stock_div("2023-01-04", cash_amount=110.0, ratio=0.05)
    with pytest.raises(ValueError, match="cash_amount"):
        adjust(raw, actions)
