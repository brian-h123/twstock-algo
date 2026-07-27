"""Refresh the local suspension-halt store from TWSE's TWTAWU endpoint.

Fetches all halts in [START, END], filters to the Top-50 universe, and
writes them via storage.write_suspensions (idempotent — safe to re-run).
Mirrors backfill.py's role for OHLCV, scaled to this table's size.

Usage:
    uv run python scripts/fetch_suspensions.py
"""

from datetime import date

from twstock.data.sources.twse import fetch_suspension_halts
from twstock.data.storage import write_suspensions
from twstock.universe import get_top50_universe

# Covers the full history backfilled since Phase 1 through today.
START = date(2020, 1, 1)
END = date.today()


def main() -> None:
    universe = get_top50_universe()
    halts = fetch_suspension_halts(START, END)
    halts = halts[halts["ticker"].isin(universe)]
    for ticker, group in halts.groupby("ticker"):
        write_suspensions(str(ticker), group)
    print(f"wrote {len(halts)} halt(s) across {halts['ticker'].nunique()} ticker(s)")


if __name__ == "__main__":
    main()
