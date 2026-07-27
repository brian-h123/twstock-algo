import pathlib
from datetime import date

import pandas as pd
import pytest

from twstock.data.sources.base import NoDataError, SourceUnavailableError
from twstock.data.sources.twse import TWSESource, fetch_suspension_halts


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


# ---------------------------------------------------------------------------
# fetch_suspension_halts (TWTAWU) — not part of the DataSource interface
# ---------------------------------------------------------------------------


@pytest.mark.vcr
def test_fetch_suspension_halts_returns_valid_columns():
    # cassette: 2026-05-10..05-16, TWTAWU bulk response, 23 raw rows
    df = fetch_suspension_halts(date(2026, 5, 10), date(2026, 5, 16))
    assert {"ticker", "halt_date", "resume_date", "source"}.issubset(df.columns)
    assert (df["source"] == "TWTAWU").all()
    assert (df["resume_date"] > df["halt_date"]).all()


@pytest.mark.vcr
def test_fetch_suspension_halts_converts_roc_dates():
    # cassette row: ticker 2392, ROC 115/05/13 -> 8:00 -> ROC 115/05/14
    df = fetch_suspension_halts(date(2026, 5, 10), date(2026, 5, 16))
    row = df[df["ticker"] == "2392"].iloc[0]
    assert row["halt_date"] == pd.Timestamp("2026-05-13")
    assert row["resume_date"] == pd.Timestamp("2026-05-14")


@pytest.mark.vcr
def test_fetch_suspension_halts_drops_still_halted_rows(capsys):
    # cassette has 23 raw rows; ticker 040421 has an empty resume_date
    # (halted, never resumed -- an expired warrant) and must be dropped.
    df = fetch_suspension_halts(date(2026, 5, 10), date(2026, 5, 16))
    assert "040421" not in set(df["ticker"])
    assert len(df) == 22
    assert "dropped 1" in capsys.readouterr().err


@pytest.mark.vcr
def test_fetch_suspension_halts_source_unavailable_raises():
    with pytest.raises(SourceUnavailableError):
        fetch_suspension_halts(date(2026, 1, 1), date(2026, 1, 2))
