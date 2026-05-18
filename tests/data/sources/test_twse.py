import pathlib
from datetime import date

import pytest

from twstock.data.sources.base import NoDataError, SourceUnavailableError
from twstock.data.sources.twse import TWSESource


@pytest.fixture
def source():
    return TWSESource()


@pytest.fixture(scope="module")
def vcr_cassette_dir():
    return str(pathlib.Path(__file__).parent.parent.parent / "cassettes" / "twse")


@pytest.mark.vcr
def test_get_daily_ohlcv_returns_valid_schema(source):
    df = source.get_daily_ohlcv("2330", date(2024, 1, 2), date(2024, 1, 31))
    assert len(df) > 0
    assert {"date", "ticker", "open", "high", "low", "close", "volume"}.issubset(df.columns)
    assert (df["ticker"] == "2330").all()
    assert df["close"].gt(0).all()


@pytest.mark.vcr
def test_get_daily_ohlcv_multi_month_stitches(source):
    # spans Jan and Feb — verifies loop, concat, and date trim
    # cassette: Jan has rows for Jan 2 (trimmed), Jan 15, Jan 31
    #           Feb has rows for Feb 1, Feb 15, Feb 29 (trimmed)
    # after trim [Jan 15, Feb 15]: 4 rows
    df = source.get_daily_ohlcv("2330", date(2024, 1, 15), date(2024, 2, 15))
    assert len(df) == 4
    assert df["date"].min().date() == date(2024, 1, 15)
    assert df["date"].max().date() == date(2024, 2, 15)


@pytest.mark.vcr
def test_get_daily_ohlcv_suspended_day_dropped(source):
    # cassette has 3 rows: Jan 2 (normal), Jan 8 ("--"), Jan 15 (normal)
    df = source.get_daily_ohlcv("2330", date(2024, 1, 2), date(2024, 1, 31))
    assert len(df) == 2


@pytest.mark.vcr
def test_get_daily_ohlcv_no_data_raises(source):
    with pytest.raises(NoDataError):
        source.get_daily_ohlcv("9999", date(2024, 1, 1), date(2024, 1, 31))


@pytest.mark.vcr
def test_source_unavailable_raises(source):
    with pytest.raises(SourceUnavailableError):
        source.get_daily_ohlcv("2330", date(2024, 1, 1), date(2024, 1, 31))


def test_get_corporate_actions_raises_not_implemented(source):
    with pytest.raises(NotImplementedError):
        source.get_corporate_actions("2330", date(2024, 1, 1), date(2024, 1, 31))


def test_list_universe_returns_top50(source):
    universe = source.list_universe()
    assert len(universe) == 50
    assert all(len(t) == 4 and t.isdigit() for t in universe)
