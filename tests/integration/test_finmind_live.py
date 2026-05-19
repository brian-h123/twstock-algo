from datetime import date, timedelta

import pytest

from twstock.data.sources.base import SourceError
from twstock.data.sources.finmind import FinMindSource

pytestmark = pytest.mark.integration


@pytest.fixture
def source():
    try:
        return FinMindSource()
    except SourceError:
        pytest.skip("FINMIND_TOKEN not set")


def test_can_fetch_2330_last_30_days(source):
    end = date.today()
    start = end - timedelta(days=30)
    df = source.get_daily_ohlcv("2330", start, end)
    assert len(df) > 0
    assert (df["ticker"] == "2330").all()
    assert df["close"].gt(0).all()


def test_full_backfill_one_ticker(source):
    df_ohlcv = source.get_daily_ohlcv("2330", date(2020, 1, 1), date(2020, 12, 31))
    df_actions = source.get_corporate_actions("2330", date(2020, 1, 1), date(2020, 12, 31))

    assert len(df_ohlcv) > 0
    assert {"date", "ticker", "open", "high", "low", "close", "volume"}.issubset(df_ohlcv.columns)

    assert len(df_actions) > 0
    assert {
        "ex_date",
        "ticker",
        "action_type",
        "cash_amount",
        "ratio",
        "subscription_price",
    }.issubset(df_actions.columns)
    assert (
        df_actions["action_type"]
        .isin(
            [
                "cash_dividend",
                "stock_dividend",
                "cash_and_stock_dividend",
                "rights_issue",
                "capital_reduction",
            ]
        )
        .all()
    )
