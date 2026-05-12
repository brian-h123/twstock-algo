from datetime import date
from pathlib import Path

import pandas as pd

from twstock.config import DATA_DIR
from twstock.data.schemas import OHLCV_SCHEMA, validate


def _ticker_path(ticker: str) -> Path:
    return DATA_DIR / "by_ticker" / f"{ticker}.parquet"


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
