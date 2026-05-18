from dataclasses import dataclass
from datetime import date

import exchange_calendars as ecals

from twstock.data.schemas import OHLCV_SCHEMA
from twstock.data.storage import list_stored_tickers, read_ohlcv


@dataclass
class DQReport:
    """Data quality report for one ticker's stored OHLCV data."""

    ticker: str
    n_rows: int
    date_range: tuple[date, date]
    missing_trading_days: list[date]
    duplicate_keys: int
    zero_volume_days: int
    schema_violations: list[str]
    ok: bool


def check_ticker(ticker: str) -> DQReport:
    """Run data quality checks on a single ticker's stored OHLCV data.

    Args:
        ticker: Four-digit TWSE ticker string.

    Returns:
        DQReport with all quality metrics populated. ok is False if any
        critical issue is found (schema violation, duplicate key, or more
        than 5% of expected trading days missing).
    """
    df = read_ohlcv(ticker)

    if df.empty:
        return DQReport(
            ticker=ticker,
            n_rows=0,
            date_range=(date.min, date.min),
            missing_trading_days=[],
            duplicate_keys=0,
            zero_volume_days=0,
            schema_violations=["no data stored for this ticker"],
            ok=False,
        )

    n_rows = len(df)
    date_range = (df["date"].min().date(), df["date"].max().date())
    duplicate_keys = int(df.duplicated(["ticker", "date"]).sum())
    zero_volume_days = int((df["volume"] == 0).sum())

    try:
        OHLCV_SCHEMA.validate(df, lazy=True)
        schema_violations = []
    except Exception as e:
        schema_violations = [str(e)]

    cal = ecals.get_calendar("XTAI")
    sessions = cal.sessions_in_range(date_range[0], date_range[1])
    expected = set(s.date() for s in sessions)
    actual = set(df["date"].dt.date)
    missing_trading_days = sorted(expected - actual)

    pct_missing = len(missing_trading_days) / len(expected) if expected else 0
    ok = len(schema_violations) == 0 and duplicate_keys == 0 and pct_missing <= 0.05

    return DQReport(
        ticker=ticker,
        n_rows=n_rows,
        date_range=date_range,
        missing_trading_days=missing_trading_days,
        duplicate_keys=duplicate_keys,
        zero_volume_days=zero_volume_days,
        schema_violations=schema_violations,
        ok=ok,
    )


def check_all() -> list[DQReport]:
    """Run data quality checks on all stored tickers.

    Returns:
        List of DQReport, one per ticker found in storage.
    """
    return [check_ticker(t) for t in list_stored_tickers()]
