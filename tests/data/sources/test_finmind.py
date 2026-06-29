import os
from datetime import date

import pandas as pd
import pytest
from dotenv import load_dotenv

from twstock.data.sources.base import RateLimitError
from twstock.data.sources.finmind import FinMindSource


@pytest.fixture
def source(monkeypatch):
    load_dotenv()
    if not os.getenv("FINMIND_TOKEN"):
        monkeypatch.setenv("FINMIND_TOKEN", "test-token")
    return FinMindSource()


@pytest.mark.vcr
def test_get_daily_ohlcv_returns_valid_schema(source):
    df = source.get_daily_ohlcv("2330", date(2024, 1, 2), date(2024, 2, 1))
    assert len(df) > 0
    assert {"date", "ticker", "open", "high", "low", "close", "volume"}.issubset(df.columns)
    assert (df["ticker"] == "2330").all()
    assert df["close"].gt(0).all()


@pytest.mark.vcr
def test_get_daily_ohlcv_spanning_dividend(source):
    # date range chosen to include TSMC ex-dividend 2023-09-21
    df = source.get_daily_ohlcv("2330", date(2023, 9, 1), date(2023, 10, 31))
    assert len(df) > 0
    assert (df["ticker"] == "2330").all()


@pytest.mark.vcr
def test_get_daily_ohlcv_recent_listing(source):
    # 2454 (MediaTek) with a short window — verifies adapter handles fewer rows cleanly
    df = source.get_daily_ohlcv("2454", date(2023, 1, 1), date(2023, 3, 31))
    assert len(df) > 0
    assert (df["ticker"] == "2454").all()


@pytest.mark.vcr
def test_rate_limit_raises(source):
    with pytest.raises(RateLimitError):
        source.get_daily_ohlcv("2330", date(2024, 1, 2), date(2024, 2, 1))


@pytest.mark.vcr
def test_get_corporate_actions_returns_valid_schema(source):
    df = source.get_corporate_actions("2330", date(2019, 1, 1), date(2024, 12, 31))
    assert len(df) > 0
    assert {
        "ex_date",
        "ticker",
        "action_type",
        "cash_amount",
        "pay_date",
        "ratio",
        "subscription_price",
    }.issubset(df.columns)
    assert (df["ticker"] == "2330").all()
    assert (
        df["action_type"]
        .isin(
            [
                "cash_dividend",
                "stock_dividend",
                "cash_and_stock_dividend",
                "rights_issue",
                "capital_reduction",
                "stock_split",
            ]
        )
        .all()
    )
    cash_rows = df[df["action_type"].isin(["cash_dividend", "cash_and_stock_dividend"])]
    assert cash_rows["pay_date"].notna().all()


# ---------------------------------------------------------------------------
# _build_capital_reductions — deduplication unit tests
# ---------------------------------------------------------------------------

_CR_ROW_BASE = {
    "stock_id": "2327",
    "ClosingPriceonTheLastTradingDay": 400.0,
    "PostReductionReferencePrice": 498.81,
    "LimitUp": 548.0,
    "LimitDown": 448.0,
    "OpeningReferencePrice": 498.81,
    "ExrightReferencePrice": 0.0,
    "ReasonforCapitalReduction": "現金減資",
}


def test_build_capital_reductions_drops_suspension_start_duplicate(source):
    # FinMind emits two rows per event: suspension-start date and ex-date.
    # All non-date fields are identical. Only the ex-date row (later) should survive.
    rows = [
        {"date": "2022-10-21", **_CR_ROW_BASE},
        {"date": "2022-10-31", **_CR_ROW_BASE},
    ]
    df = source._build_capital_reductions(rows)
    assert len(df) == 1
    assert df.iloc[0]["ex_date"] == pd.Timestamp("2022-10-31")


def test_build_capital_reductions_preserves_distinct_events(source):
    # Two rows sharing price fields but differing on a non-date field
    # (different ReasonforCapitalReduction) — not genuine duplicates, both kept.
    rows = [
        {"date": "2021-10-31", **_CR_ROW_BASE},
        {"date": "2022-10-31", **{**_CR_ROW_BASE, "ReasonforCapitalReduction": "彌補虧損"}},
    ]
    df = source._build_capital_reductions(rows)
    assert len(df) == 2


# ---------------------------------------------------------------------------
# _build_dividends — pay_date unit tests
# ---------------------------------------------------------------------------

_DIV_ROW_BASE = {
    "stock_id": "2330",
    "year": "112年第2季",
    "StockEarningsDistribution": 0.0,
    "StockStatutorySurplus": 0.0,
    "StockExDividendTradingDate": "",
    "CashEarningsDistribution": 0.0,
    "CashStatutorySurplus": 0.0,
    "CashExDividendTradingDate": "",
    "CashDividendPaymentDate": "",
    "TotalNumberOfCashCapitalIncrease": 0.0,
    "ParticipateDistributionOfTotalShares": 25930000000.0,
    "CashIncreaseSubscriptionpRrice": 0.0,
}


def test_build_dividends_cash_dividend_pay_date_is_populated(source):
    raw = {
        **_DIV_ROW_BASE,
        "CashEarningsDistribution": 3.0,
        "CashExDividendTradingDate": "2023-09-14",
        "CashDividendPaymentDate": "2023-10-05",
    }
    df = source._build_dividends([raw])
    assert len(df) == 1
    assert df.iloc[0]["action_type"] == "cash_dividend"
    assert df.iloc[0]["pay_date"] == pd.Timestamp("2023-10-05")


def test_build_dividends_stock_dividend_pay_date_is_null(source):
    raw = {
        **_DIV_ROW_BASE,
        "StockEarningsDistribution": 0.5,
        "StockExDividendTradingDate": "2021-07-20",
    }
    df = source._build_dividends([raw])
    assert len(df) == 1
    assert df.iloc[0]["action_type"] == "stock_dividend"
    assert pd.isna(df.iloc[0]["pay_date"])


def test_build_dividends_cash_and_stock_dividend_pay_date_is_populated(source):
    raw = {
        **_DIV_ROW_BASE,
        "stock_id": "1101",
        "CashEarningsDistribution": 2.0,
        "StockEarningsDistribution": 0.3,
        "CashExDividendTradingDate": "2022-07-21",
        "StockExDividendTradingDate": "2022-07-21",
        "CashDividendPaymentDate": "2022-08-15",
    }
    df = source._build_dividends([raw])
    assert len(df) == 1
    assert df.iloc[0]["action_type"] == "cash_and_stock_dividend"
    assert df.iloc[0]["pay_date"] == pd.Timestamp("2022-08-15")


def test_build_dividends_rights_only_pay_date_is_null(source):
    # has_rights=True, has_cash=False, has_stock=False → rights_issue row, pay_date NaT
    raw = {
        **_DIV_ROW_BASE,
        "stock_id": "3312",
        "TotalNumberOfCashCapitalIncrease": 5000000.0,
        "StockExDividendTradingDate": "2021-06-15",
        "CashIncreaseSubscriptionpRrice": 17.0,
    }
    df = source._build_dividends([raw])
    assert len(df) == 1
    assert df.iloc[0]["action_type"] == "rights_issue"
    assert pd.isna(df.iloc[0]["pay_date"])


def test_build_dividends_cash_plus_rights_sub_row_pay_dates(source):
    # has_rights=True, has_cash=True, has_stock=False → two rows:
    # cash_dividend gets real pay_date; rights_issue gets NaT.
    raw = {
        **_DIV_ROW_BASE,
        "stock_id": "2912",
        "CashEarningsDistribution": 1.5,
        "CashExDividendTradingDate": "2021-07-15",
        "StockExDividendTradingDate": "2021-07-15",
        "CashDividendPaymentDate": "2021-08-10",
        "TotalNumberOfCashCapitalIncrease": 5000000.0,
        "CashIncreaseSubscriptionpRrice": 15.0,
    }
    df = source._build_dividends([raw])
    assert len(df) == 2
    cash_row = df[df["action_type"] == "cash_dividend"].iloc[0]
    rights_row = df[df["action_type"] == "rights_issue"].iloc[0]
    assert cash_row["pay_date"] == pd.Timestamp("2021-08-10")
    assert pd.isna(rights_row["pay_date"])
