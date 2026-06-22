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
