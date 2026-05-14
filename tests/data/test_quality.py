from datetime import date

import exchange_calendars as ecals
import pandas as pd
import pytest

from twstock.data.quality import check_all, check_ticker
from twstock.data.storage import write_ohlcv

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    d = tmp_path / "by_ticker"
    d.mkdir()
    monkeypatch.setattr("twstock.data.storage.DATA_DIR", tmp_path)
    return tmp_path


def _make_ohlcv(ticker: str = "2330", dates: list[str] | None = None) -> pd.DataFrame:
    if dates is None:
        dates = ["2023-07-03"]
    n = len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ticker": [ticker] * n,
            "open": [500.0] * n,
            "high": [510.0] * n,
            "low": [495.0] * n,
            "close": [505.0] * n,
            "volume": [10_000_000] * n,
        }
    )


def _trading_dates(start: str, end: str) -> list[str]:
    cal = ecals.get_calendar("XTAI")
    sessions = cal.sessions_in_range(start, end)
    return [s.strftime("%Y-%m-%d") for s in sessions]


# ---------------------------------------------------------------------------
# check_ticker — no stored data
# ---------------------------------------------------------------------------


def test_check_ticker_no_data_returns_not_ok(storage_dir):
    report = check_ticker("2330")
    assert report.ticker == "2330"
    assert report.n_rows == 0
    assert report.ok is False
    assert len(report.schema_violations) > 0


# ---------------------------------------------------------------------------
# check_ticker — clean data
# ---------------------------------------------------------------------------


def test_check_ticker_clean_data_ok(storage_dir):
    dates = _trading_dates("2023-07-03", "2023-07-07")  # one full week
    write_ohlcv("2330", _make_ohlcv(dates=dates))

    report = check_ticker("2330")

    assert report.ok is True
    assert report.n_rows == len(dates)
    assert report.duplicate_keys == 0
    assert report.zero_volume_days == 0
    assert report.schema_violations == []
    assert report.missing_trading_days == []
    assert report.date_range == (date(2023, 7, 3), date(2023, 7, 7))


# ---------------------------------------------------------------------------
# check_ticker — duplicate keys (critical)
# ---------------------------------------------------------------------------


def test_check_ticker_duplicate_keys_not_ok(storage_dir):
    # write parquet directly to bypass storage deduplication
    df = _make_ohlcv(dates=["2023-07-03", "2023-07-03"])
    path = storage_dir / "by_ticker" / "2330.parquet"
    df.to_parquet(path, index=False)

    report = check_ticker("2330")

    assert report.duplicate_keys == 1
    assert report.ok is False


# ---------------------------------------------------------------------------
# check_ticker — schema violations (critical)
# ---------------------------------------------------------------------------


def test_check_ticker_schema_violation_not_ok(storage_dir):
    # write parquet directly to bypass schema validation in write_ohlcv
    df = _make_ohlcv(dates=["2023-07-03"])
    df["close"] = -1.0  # violates close > 0
    path = storage_dir / "by_ticker" / "2330.parquet"
    df.to_parquet(path, index=False)

    report = check_ticker("2330")

    assert len(report.schema_violations) > 0
    assert report.ok is False


# ---------------------------------------------------------------------------
# check_ticker — missing trading days
# ---------------------------------------------------------------------------


def test_check_ticker_missing_days_under_threshold_ok(storage_dir):
    # 20 trading days, skip 1 in the middle → 1/20 = 5% ≤ threshold → ok
    dates = _trading_dates("2023-07-03", "2023-07-28")
    assert len(dates) >= 20

    dates_without_one = dates[:9] + dates[10:]  # skip index 9, keep endpoints
    write_ohlcv("2330", _make_ohlcv(dates=dates_without_one))

    report = check_ticker("2330")

    assert len(report.missing_trading_days) == 1
    assert report.ok is True


def test_check_ticker_missing_days_over_threshold_not_ok(storage_dir):
    # 20 trading days, skip enough to exceed 5% → ok=False
    dates = _trading_dates("2023-07-03", "2023-07-28")
    n = len(dates)
    n_skip = int(n * 0.05) + 1  # guaranteed > 5%

    dates_sparse = dates[:5] + dates[5 + n_skip :]  # skip n_skip days, keep endpoints
    write_ohlcv("2330", _make_ohlcv(dates=dates_sparse))

    report = check_ticker("2330")

    assert len(report.missing_trading_days) >= n_skip
    assert report.ok is False


# ---------------------------------------------------------------------------
# check_ticker — zero volume (reported but not critical)
# ---------------------------------------------------------------------------


def test_check_ticker_zero_volume_not_critical(storage_dir):
    dates = _trading_dates("2023-07-03", "2023-07-07")
    df = _make_ohlcv(dates=dates)
    df.loc[0, "volume"] = 0  # one suspended/zero-volume day
    write_ohlcv("2330", df)

    report = check_ticker("2330")

    assert report.zero_volume_days == 1
    assert report.ok is True  # zero volume alone does not flip ok


# ---------------------------------------------------------------------------
# check_all
# ---------------------------------------------------------------------------


def test_check_all_empty_store_returns_empty_list(storage_dir):
    assert check_all() == []


def test_check_all_returns_one_report_per_stored_ticker(storage_dir):
    write_ohlcv("2330", _make_ohlcv(ticker="2330"))
    write_ohlcv("2317", _make_ohlcv(ticker="2317"))

    reports = check_all()
    tickers = [r.ticker for r in reports]

    assert len(reports) == 2
    assert "2330" in tickers
    assert "2317" in tickers
