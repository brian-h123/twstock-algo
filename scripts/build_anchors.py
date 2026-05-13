"""Build adjustment cross-check anchor fixture from TWSE official + primary dividend sources.

This script fetches raw close prices directly from TWSE's STOCK_DAY endpoint
(independent of FinMind) and combines them with dividend amounts sourced from
TSMC SEC 6-K filings and TSMC IR. The output is a JSON fixture used by the
adjustment cross-validation test in Phase 1.

The test design is a *local invariant* test. For each anchor event with
ex-date T, dividend D, raw_close[T-1]=X, raw_close[T]=Y, the back-adjustment
factor any correct adjustment function must apply at that event is exactly
Y / (Y + D). The corresponding ratio of adjusted closes across the event is

    adj_close[T-1] / adj_close[T]  =  X / (Y + D)

This ratio is the value stored in `expected_adj_close_ratio` and is exactly
what the cross-validation test asserts the user's adjusted-close series
exhibits across each anchor event, within tolerance.

Run from project root:
    uv run python scripts/build_anchors.py

Output: tests/fixtures/anchor_dividends.json
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import requests

TWSE_STOCK_DAY = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
OUTPUT_PATH = Path("tests/fixtures/anchor_dividends.json")
THROTTLE_SECONDS = 3.0  # TWSE is sensitive to rapid requests; be polite.


# ----- Dividend reference data --------------------------------------------------
#
# Each entry is a verified TSMC dividend event. The `dividend_amount` value is
# the NT$ per common share figure from the primary source listed in `source`.
# These are NOT sourced from FinMind — that is intentional. The cross-check
# loses meaning if both sides come from the same upstream pipeline.
#
# Sources used:
#   - SEC EDGAR Form 6-K filings (TSMC files quarterly with the US SEC because
#     of its NYSE ADR listing; these contain the exact NT$ per-share figures)
#   - TSMC investor relations (pr.tsmc.com and investor.tsmc.com)
#   - stockanalysis.com TPE:2330 dividend history (which is itself sourced
#     from TWSE/TSMC; used here as a cross-check of the SEC filings)
#
# Coverage: post-2018 only. Pre-2018 events can be added later; SEC EDGAR
# has filings going back to ~2010 in the same format.

ANCHOR_EVENTS: list[dict] = [
    {
        "ticker": "2330",
        "ex_date": "2018-06-25",
        "action_type": "cash_dividend",
        "dividend_amount": 8.0,
        "source": ("TSMC press release 2018-06-05 + SEC 6-K. Annual dividend for FY2017 profits."),
    },
    {
        "ticker": "2330",
        "ex_date": "2019-06-24",
        "action_type": "cash_dividend",
        "dividend_amount": 8.0,
        "source": (
            "TSMC press release 2019-06-05 + SEC 6-K. "
            "Final annual-style dividend (FY2018 profits) before "
            "transition to quarterly cadence."
        ),
    },
    {
        "ticker": "2330",
        "ex_date": "2020-06-18",
        "action_type": "cash_dividend",
        "dividend_amount": 2.5,
        "source": ("TSMC SEC 6-K filed 2020-02-11. Q4 2019 quarterly dividend."),
    },
    {
        "ticker": "2330",
        "ex_date": "2022-06-16",
        "action_type": "cash_dividend",
        "dividend_amount": 2.75,
        "source": (
            "stockanalysis.com TPE:2330 dividend history (corroborated via "
            "TSMC IR). Q4 2021 quarterly dividend."
        ),
    },
    {
        "ticker": "2330",
        "ex_date": "2023-12-14",
        "action_type": "cash_dividend",
        "dividend_amount": 3.0,
        "source": ("stockanalysis.com TPE:2330 dividend history. Q2 2023 quarterly dividend."),
    },
    {
        "ticker": "2330",
        "ex_date": "2024-06-13",
        "action_type": "cash_dividend",
        "dividend_amount": 3.49979,
        "source": (
            "stockanalysis.com TPE:2330 dividend history (full precision "
            "from TWSE filing; small per-share adjustment relative to the "
            "headline NT$3.50 reflects employee stock option dilution). "
            "Q4 2023 quarterly dividend."
        ),
    },
]


# ----- Fetch logic --------------------------------------------------------------


@dataclass
class Anchor:
    """Final output schema for one anchor event.

    `expected_adj_close_ratio` is the precomputed value the cross-validation
    test asserts the user's adjusted-close series matches across this event:

        adj_close[T-1] / adj_close[T]  ≈  raw_close_t_minus_1 / (raw_close_t + dividend_amount)

    For cash dividends this equals X / (Y + D). For a perfectly "clean"
    dividend drop (raw_close_t_minus_1 == raw_close_t + dividend_amount) the
    ratio equals 1.0; intraday market moves on the ex-date push it off 1.0 by
    the daily-return magnitude. The textbook back-adjustment *factor*
    Y / (Y + D) is a different number; it is intentionally NOT what this
    field stores. The test uses the ratio directly.
    """

    ticker: str
    ex_date: str  # YYYY-MM-DD
    action_type: str
    dividend_amount: float
    raw_close_t_minus_1: float
    raw_close_t: float
    expected_adj_close_ratio: float
    source_dividend: str
    source_prices: str = "TWSE STOCK_DAY official endpoint"
    notes: str = ""


def _roc_to_gregorian(roc_str: str) -> date:
    """Convert TWSE date format '113/06/13' (ROC year) to Python date."""
    y, m, d = roc_str.split("/")
    return date(int(y) + 1911, int(m), int(d))


def fetch_month(ticker: str, year: int, month: int) -> list[dict]:
    """Fetch one month of OHLCV for a TWSE ticker. Returns list of daily records."""
    params = {
        "response": "json",
        "date": f"{year}{month:02d}01",
        "stockNo": ticker,
    }
    headers = {"User-Agent": "twstock-algo/0.1 (anchor fixture builder)"}
    r = requests.get(TWSE_STOCK_DAY, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if payload.get("stat") != "OK":
        raise RuntimeError(
            f"TWSE returned non-OK status for {ticker} {year}-{month:02d}: {payload.get('stat')}"
        )
    # Columns: 日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數, 註記
    rows = []
    for raw in payload["data"]:
        rows.append(
            {
                "date": _roc_to_gregorian(raw[0]),
                "close": float(raw[6].replace(",", "")),
            }
        )
    return rows


def find_anchor_closes(rows: list[dict], ex_date: date) -> tuple[float, float]:
    """Return (raw_close_t_minus_1, raw_close_t) for the ex-date.

    raw_close_t is the close on ex_date itself.
    raw_close_t_minus_1 is the close on the most recent trading day strictly
    before ex_date that appears in the same month's data.

    If ex_date is the first trading day of the month, the previous trading day
    falls into the previous month — caller is responsible for handling this
    edge case (none of the curated anchors trigger it).
    """
    rows_sorted = sorted(rows, key=lambda r: r["date"])
    t_row = next((r for r in rows_sorted if r["date"] == ex_date), None)
    if t_row is None:
        raise RuntimeError(
            f"ex_date {ex_date} not found in fetched data; check if it was "
            f"actually a trading day (typhoon closures etc.)"
        )
    prior_rows = [r for r in rows_sorted if r["date"] < ex_date]
    if not prior_rows:
        raise RuntimeError(
            f"No trading day strictly before {ex_date} in this month's data. "
            f"Need to fetch the prior month as well."
        )
    t_minus_1_row = prior_rows[-1]
    return t_minus_1_row["close"], t_row["close"]


def build_anchor(event: dict) -> Anchor:
    """Convert a dividend-event spec into a fully-populated Anchor."""
    ex_date_obj = datetime.strptime(event["ex_date"], "%Y-%m-%d").date()
    rows = fetch_month(event["ticker"], ex_date_obj.year, ex_date_obj.month)
    t_minus_1_close, t_close = find_anchor_closes(rows, ex_date_obj)
    div = event["dividend_amount"]
    # X / (Y + D) — directly the value the test asserts (not the back-adjustment
    # factor Y / (Y + D), which is a different number; see Anchor docstring).
    ratio = t_minus_1_close / (t_close + div)
    return Anchor(
        ticker=event["ticker"],
        ex_date=event["ex_date"],
        action_type=event["action_type"],
        dividend_amount=div,
        raw_close_t_minus_1=t_minus_1_close,
        raw_close_t=t_close,
        expected_adj_close_ratio=ratio,
        source_dividend=event["source"],
    )


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    anchors: list[Anchor] = []
    for i, event in enumerate(ANCHOR_EVENTS):
        print(f"[{i + 1}/{len(ANCHOR_EVENTS)}] {event['ticker']} {event['ex_date']}")
        anchor = build_anchor(event)
        anchors.append(anchor)
        if i < len(ANCHOR_EVENTS) - 1:
            time.sleep(THROTTLE_SECONDS)
    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "description": (
            "Hand-curated cross-check anchors for the adjustment function. "
            "Each event's raw close prices are fetched from TWSE's official "
            "STOCK_DAY endpoint; dividend amounts are from TSMC SEC 6-K "
            "filings and TSMC IR. Sources deliberately independent of FinMind. "
            "The `expected_adj_close_ratio` field is the value the test "
            "asserts directly (X / (Y + D)), not the textbook back-adjustment "
            "factor (Y / (Y + D)). See "
            "journal/decisions/2026-05-13-adjustment-cross-check-method.md."
        ),
        "anchors": [asdict(a) for a in anchors],
    }
    OUTPUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(anchors)} anchors → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
