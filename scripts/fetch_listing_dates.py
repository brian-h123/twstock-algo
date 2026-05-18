"""Fetch TWSE listing dates for the Top-50 universe from the TWSE Open API.

Prints a dict literal ready to paste into get_listing_dates() in
src/twstock/universe.py. Re-run whenever the universe expands.

Usage:
    uv run python scripts/fetch_listing_dates.py
"""

import json
import sys
import urllib.request
from datetime import date

from twstock.universe import get_top50_universe

TWSE_COMPANY_LIST_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"


def fetch_twse_listing_dates() -> dict[str, date]:
    """Fetch all TWSE-listed companies and return {ticker: listing_date}.

    Returns:
        Dict mapping ticker string to its TWSE listing date.

    Raises:
        RuntimeError: If the API call fails or response is not valid JSON.
    """
    req = urllib.request.Request(
        TWSE_COMPANY_LIST_URL,
        headers={"User-Agent": "twstock-algo/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch TWSE company list: {exc}") from exc

    result: dict[str, date] = {}
    for row in data:
        ticker = row.get("公司代號", "").strip()
        raw_date = row.get("上市日期", "").strip()
        if not ticker or not raw_date:
            continue
        try:
            # TWSE format: YYYYMMDD
            listing_date = date(
                int(raw_date[:4]),
                int(raw_date[4:6]),
                int(raw_date[6:8]),
            )
        except (ValueError, IndexError):
            continue
        result[ticker] = listing_date

    return result


def main() -> None:
    universe = get_top50_universe()

    print("Fetching TWSE company list...", file=sys.stderr)
    all_dates = fetch_twse_listing_dates()

    missing = [t for t in universe if t not in all_dates]
    if missing:
        print(f"WARNING: no listing date found for: {missing}", file=sys.stderr)

    print("\n# Paste this into get_listing_dates() in src/twstock/universe.py")
    print("# Source: TWSE Open API t187ap03_L, fetched", date.today().isoformat())
    print("{")
    for ticker in universe:
        listing_date = all_dates.get(ticker)
        if listing_date:
            y, m, d = listing_date.year, listing_date.month, listing_date.day
            print(f'    "{ticker}": date({y}, {m}, {d}),  # {ticker}')
        else:
            print(f'    # "{ticker}": ???  # NOT FOUND in TWSE API')
    print("}")

    print("\n# Summary (tickers listed after 2016-01-01):", file=sys.stderr)
    cutoff = date(2016, 1, 1)
    at_risk = [(t, all_dates[t]) for t in universe if t in all_dates and all_dates[t] > cutoff]
    if at_risk:
        for ticker, d in sorted(at_risk, key=lambda x: x[1]):
            print(f"  {ticker}  listed {d.isoformat()}", file=sys.stderr)
    else:
        print("  (none — all tickers listed before 2016)", file=sys.stderr)


if __name__ == "__main__":
    main()
