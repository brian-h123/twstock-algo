from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from twstock.config import DATA_DIR
from twstock.data.schemas import (
    CORPORATE_ACTIONS_SCHEMA,
    OHLCV_SCHEMA,
    SUSPENSIONS_SCHEMA,
    validate,
)


def _ticker_path(ticker: str) -> Path:
    return DATA_DIR / "by_ticker" / f"{ticker}.parquet"


def _ca_path(ticker: str) -> Path:
    return DATA_DIR / "corporate_actions" / f"{ticker}.parquet"


def _suspensions_path(ticker: str) -> Path:
    return DATA_DIR / "suspensions" / f"{ticker}.parquet"


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------


def write_ohlcv(ticker: str, df: pd.DataFrame) -> None:
    df = validate(df, OHLCV_SCHEMA)

    path = _ticker_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        merged = pd.concat([existing, df], ignore_index=True)
    else:
        merged = df.copy()

    merged = merged.drop_duplicates(["ticker", "date"], keep="last")
    merged = merged.sort_values(["ticker", "date"]).reset_index(drop=True)

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    tmp.replace(path)


def read_ohlcv(ticker: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    path = _ticker_path(ticker)

    if not path.exists():
        return pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "value",
                "fetched_at",
            ]
        )

    df = pd.read_parquet(path)

    if start is not None:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["date"] <= pd.Timestamp(end)]

    return df.reset_index(drop=True)


def list_stored_tickers() -> list[str]:
    by_ticker_dir = DATA_DIR / "by_ticker"
    if not by_ticker_dir.exists():
        return []
    return sorted(p.stem for p in by_ticker_dir.glob("*.parquet"))


def get_ticker_date_range(ticker: str) -> tuple[date, date] | None:
    path = _ticker_path(ticker)

    if not path.exists():
        return None
    df = pd.read_parquet(path)
    return df["date"].min().date(), df["date"].max().date()


# ---------------------------------------------------------------------------
# Corporate Actions
# ---------------------------------------------------------------------------


def write_corporate_actions(ticker: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    df = validate(df, CORPORATE_ACTIONS_SCHEMA)

    path = _ca_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        merged = pd.concat([existing, df], ignore_index=True)
    else:
        merged = df.copy()

    merged = merged.drop_duplicates(["ticker", "ex_date", "action_type"], keep="last")
    merged = merged.sort_values(["ticker", "ex_date"]).reset_index(drop=True)

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    tmp.replace(path)


def read_corporate_actions(
    ticker: str, start: date | None = None, end: date | None = None
) -> pd.DataFrame:
    path = _ca_path(ticker)

    if not path.exists():
        return pd.DataFrame(columns=["ex_date", "ticker", "action_type", "cash_amount", "ratio"])

    df = pd.read_parquet(path)

    if start is not None:
        df = df[df["ex_date"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["ex_date"] <= pd.Timestamp(end)]

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Suspensions
# ---------------------------------------------------------------------------


def write_suspensions(ticker: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    df = validate(df, SUSPENSIONS_SCHEMA)

    path = _suspensions_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        merged = pd.concat([existing, df], ignore_index=True)
    else:
        merged = df.copy()

    merged = merged.drop_duplicates(["ticker", "halt_date"], keep="last")
    merged = merged.sort_values(["ticker", "halt_date"]).reset_index(drop=True)

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    tmp.replace(path)


def read_suspensions(ticker: str) -> pd.DataFrame:
    path = _suspensions_path(ticker)

    if not path.exists():
        return pd.DataFrame(columns=["ticker", "halt_date", "resume_date", "source"])

    df = pd.read_parquet(path)
    return df


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def query(sql: str) -> pd.DataFrame:
    ohlcv_dir = DATA_DIR / "by_ticker"
    ca_dir = DATA_DIR / "corporate_actions"
    suspensions_dir = DATA_DIR / "suspensions"

    conn = duckdb.connect()
    if ohlcv_dir.exists() and any(ohlcv_dir.glob("*.parquet")):
        conn.execute(
            f"CREATE VIEW ohlcv AS SELECT * FROM read_parquet("
            f"'{ohlcv_dir.as_posix()}/*.parquet', union_by_name=true)"
        )
    if ca_dir.exists() and any(ca_dir.glob("*.parquet")):
        conn.execute(
            f"CREATE VIEW corporate_actions AS SELECT * FROM read_parquet("
            f"'{ca_dir.as_posix()}/*.parquet', union_by_name=true)"
        )
    if suspensions_dir.exists() and any(suspensions_dir.glob("*.parquet")):
        conn.execute(
            f"CREATE VIEW suspensions AS SELECT * FROM read_parquet("
            f"'{suspensions_dir.as_posix()}/*.parquet', union_by_name=true)"
        )
    return conn.execute(sql).df()
