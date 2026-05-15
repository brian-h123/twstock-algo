import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from tqdm import tqdm

from twstock.data.api import get_universe
from twstock.data.sources.base import (
    DataSource,
    NoDataError,
    RateLimitError,
    SourceError,
    SourceUnavailableError,
)
from twstock.data.sources.finmind import FinMindSource
from twstock.data.sources.twse import TWSESource
from twstock.data.storage import get_ticker_date_range, write_corporate_actions, write_ohlcv

BACKOFF_SCHEDULE = [5, 30, 300]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backfill", description="Backfill OHLCV + corporate actions for the TWSE Top 50."
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated list, e.g. 2330,0050. Default: all Top 50.",
    )
    parser.add_argument(
        "--start",
        default=None,
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
    )
    parser.add_argument(
        "--end",
        default=None,
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
    )
    parser.add_argument("--throttle-ms", type=int, default=200)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--source", choices=["finmind", "twse"], default="finmind")
    parser.add_argument("--skip-actions", action="store_true")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def resolve_fetch_window(
    ticker: str, requested_start: date, requested_end: date, force: bool
) -> tuple[date, date] | None:
    existing_range = get_ticker_date_range(ticker)

    if existing_range is None:
        return (requested_start, requested_end)

    stored_start, stored_end = existing_range

    if not force and stored_start <= requested_start and stored_end >= requested_end:
        return None

    fetch_start = min(requested_start, stored_start)
    fetch_end = max(requested_end, stored_end)
    return (fetch_start, fetch_end)


def fetch_with_backoff(fn: Callable, *args: Any, **kwargs: Any):
    for wait in BACKOFF_SCHEDULE:
        try:
            return fn(*args, **kwargs)
        except (RateLimitError, SourceUnavailableError):
            time.sleep(wait)
    return fn(*args, **kwargs)


def fetch_ticker(
    source: DataSource,
    ticker: str,
    fetch_start: date,
    fetch_end: date,
    throttle_s: float,
    skip_actions: bool,
) -> dict:
    t0 = time.time()
    rows_written = 0
    actions_written = 0

    # --- OHLCV ---
    try:
        ohlcv_df = fetch_with_backoff(source.get_daily_ohlcv, ticker, fetch_start, fetch_end)
        write_ohlcv(ticker, ohlcv_df)
        rows_written = len(ohlcv_df)
    except NoDataError:
        rows_written = 0
    time.sleep(throttle_s)

    # --- Corporate actions ---
    if not skip_actions:
        try:
            ca_df = fetch_with_backoff(source.get_corporate_actions, ticker, fetch_start, fetch_end)
            write_corporate_actions(ticker, ca_df)
            actions_written = len(ca_df)
        except NoDataError:
            actions_written = 0
        time.sleep(throttle_s)

    return {
        "ts": datetime.now(UTC).isoformat(),
        "ticker": ticker,
        "status": "ok",
        "rows_written": rows_written,
        "actions_written": actions_written,
        "fetch_window": [fetch_start.isoformat(), fetch_end.isoformat()],
        "duration_ms": int((time.time() - t0) * 1000),
    }


def write_log(log_path: Path, record: dict) -> None:
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def main(args: argparse.Namespace) -> None:
    tickers = args.tickers.split(",") if args.tickers else get_universe()
    start = args.start or date.today() - timedelta(days=365 * 10)
    end = args.end or date.today()
    throttle_s = args.throttle_ms / 1000

    try:
        source = FinMindSource() if args.source == "finmind" else TWSESource()
    except SourceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / f"backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    records = []

    for ticker in tqdm(tickers):
        try:
            fetch_range = resolve_fetch_window(ticker, start, end, args.force)
            if fetch_range is None:
                record = {"ticker": ticker, "status": "skipped_current"}
            else:
                record = fetch_ticker(source, ticker, *fetch_range, throttle_s, args.skip_actions)
        except SourceUnavailableError as exc:
            record = {"ticker": ticker, "status": "failed", "error": f"source unavailable: {exc}"}
        except RateLimitError as exc:
            record = {"ticker": ticker, "status": "rate_limited", "error": str(exc)}
        except Exception as exc:
            record = {"ticker": ticker, "status": "failed", "error": str(exc)}

        records.append(record)
        write_log(log_path, record)

    ok_count = sum(1 for r in records if r["status"] == "ok")
    skipped_count = sum(1 for r in records if r["status"] == "skipped_current")
    failed_count = sum(1 for r in records if r["status"] == "failed")
    rl_count = sum(1 for r in records if r["status"] == "rate_limited")

    summary = {
        "ts": datetime.now(UTC).isoformat(),
        "summary": True,
        "ok": ok_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "rate_limited": rl_count,
    }
    write_log(log_path, summary)
    print(
        f"\nDone. ok={ok_count} skipped={skipped_count} "
        f"failed={failed_count} rate_limited={rl_count}"
    )

    sys.exit(1 if (failed_count or rl_count) else 0)


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    main(args)
