"""Build adjustment cross-check anchor fixture from TWSE official + primary dividend sources.

This script fetches raw close prices directly from TWSE's STOCK_DAY endpoint
(independent of FinMind) and combines them with dividend amounts sourced from
TSMC SEC 6-K filings, TSMC IR, and Taiwan Cement (TSC) AGM resolutions / TWSE
MOPS announcements. The output is a JSON fixture used by the adjustment
cross-validation test in Phase 1.

Three event types are supported, each with its own local-invariant formula
applied at ex-date T (raw_close[T-1] = X, raw_close[T] = Y):

  cash_dividend (amount D):
      back-adj factor = Y / (Y + D)
      adj_close[T-1] / adj_close[T] = X / (Y + D)

  stock_dividend (ratio R, decimal per-share fraction):
      back-adj factor = 1 / (1 + R)
      adj_close[T-1] / adj_close[T] = X / (Y * (1 + R))

  cash_and_stock_dividend (D and R both present, same ex-date — the Taiwan
  convention for large-cap distributions, 除權息合一):
      back-adj factor = Y / (Y + D) * 1 / (1 + R)
      adj_close[T-1] / adj_close[T] = X / ((Y + D) * (1 + R))

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
# Each entry is a verified corporate-action event. Dividend amounts and stock-
# dividend ratios come from primary sources (SEC 6-K, company IR / AGM
# resolutions, TWSE MOPS announcements). These are NOT sourced from FinMind —
# that is intentional. The cross-check loses meaning if both sides come from
# the same upstream pipeline.
#
# Coverage:
#   - TSMC (2330) cash dividends, post-2018
#   - Taiwan Cement (1101) combined cash + stock dividends, 2020 and 2022
#
# Sources used:
#   - SEC EDGAR Form 6-K filings (TSMC files quarterly with the US SEC because
#     of its NYSE ADR listing; these contain the exact NT$ per-share figures)
#   - TSMC investor relations (pr.tsmc.com and investor.tsmc.com)
#   - stockanalysis.com TPE:2330 dividend history (sourced from TWSE/TSMC;
#     used as a cross-check of the SEC filings)
#   - Taiwan Cement AGM resolutions + TWSE MOPS ex-rights/ex-dividend
#     announcements for 1101 (2020-08-12 and 2022-07-21)
#
# Pure stock-dividend events (no cash component on the same ex-date) are
# essentially absent from TWSE Top 50 post-2015 — Taiwan companies almost
# universally combine cash and stock distributions onto a single ex-date
# (除權息合一). The `stock_dividend` action_type remains supported in the
# build_anchor logic for future use (e.g. small-cap pure-stock events,
# splits handled as ratio-only adjustments) but no real anchor of that type
# is currently included.

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
    {
        "ticker": "1101",
        "ex_date": "2020-08-12",
        "action_type": "cash_and_stock_dividend",
        "dividend_amount": 2.5,
        "ratio": 0.05,
        "source": (
            "Taiwan Cement Corp. (TSC) 2020 AGM resolution distributing FY2019 "
            "earnings: cash dividend NT$2.50 per share + stock dividend NT$0.50 "
            "per share at NT$10 face value (i.e. 50 new shares per 1,000 "
            "existing shares, ratio = 0.05). Cross-validated against the TWSE "
            "MOPS ex-rights/ex-dividend announcement at "
            "https://mops.twse.com.tw/mops/web/t05st09_2 (1101, ex-date "
            "2020/08/12) and TSC corporate IR."
        ),
    },
    {
        "ticker": "1101",
        "ex_date": "2022-07-21",
        "action_type": "cash_and_stock_dividend",
        "dividend_amount": 1.0,
        "ratio": 0.1,
        "source": (
            "Taiwan Cement Corp. (TSC) 2022 AGM resolution distributing FY2021 "
            "earnings: cash dividend NT$1.00 per share + stock dividend NT$1.00 "
            "per share at NT$10 face value (i.e. 100 new shares per 1,000 "
            "existing shares, ratio = 0.10). Cross-validated against the TWSE "
            "MOPS ex-rights/ex-dividend announcement at "
            "https://mops.twse.com.tw/mops/web/t05st09_2 (1101, ex-date "
            "2022/07/21) and TSC corporate IR. Picked as the second 1101 "
            "anchor for ratio diversity (0.05 vs 0.10) and to give the "
            "cross-check two independent combined-event data points."
        ),
    },
]


# ----- Fetch logic --------------------------------------------------------------


@dataclass
class Anchor:
    """Final output schema for one anchor event.

    Field semantics by `action_type`:
        cash_dividend            → dividend_amount set, ratio = None
        stock_dividend           → dividend_amount = None, ratio set
        cash_and_stock_dividend  → both dividend_amount and ratio set

    `expected_adj_close_ratio` is the precomputed value the cross-validation
    test asserts the user's adjusted-close series matches across this event.
    For each type, given raw_close[T-1] = X and raw_close[T] = Y:

        cash_dividend            → X / (Y + dividend_amount)
        stock_dividend           → X / (Y * (1 + ratio))
        cash_and_stock_dividend  → X / ((Y + dividend_amount) * (1 + ratio))

    These ratios all sit near 1.0 for a "clean" market reaction to the event
    (i.e. when raw_close[T] equals the theoretical ex-rights/ex-div reference
    price); intraday moves on the ex-date push the ratio off 1.0 by the
    daily-return magnitude. The textbook back-adjustment *factors* (e.g.
    Y/(Y+D) for cash, 1/(1+R) for stock) are different numbers than what is
    stored here; the test uses these ratios directly.
    """

    ticker: str
    ex_date: str  # YYYY-MM-DD
    action_type: str
    raw_close_t_minus_1: float
    raw_close_t: float
    expected_adj_close_ratio: float
    source_dividend: str
    dividend_amount: float | None = None
    ratio: float | None = None
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


def _compute_expected_ratio(
    action_type: str,
    t_minus_1_close: float,
    t_close: float,
    dividend_amount: float | None,
    ratio: float | None,
) -> float:
    """Compute expected adj-close ratio per the event type's local invariant.

    See `Anchor` docstring for the per-type formulas.
    """
    if action_type == "cash_dividend":
        if dividend_amount is None:
            raise ValueError("cash_dividend event missing dividend_amount")
        return t_minus_1_close / (t_close + dividend_amount)
    if action_type == "stock_dividend":
        if ratio is None:
            raise ValueError("stock_dividend event missing ratio")
        return t_minus_1_close / (t_close * (1 + ratio))
    if action_type == "cash_and_stock_dividend":
        if dividend_amount is None or ratio is None:
            raise ValueError(
                "cash_and_stock_dividend event requires both dividend_amount and ratio"
            )
        return t_minus_1_close / ((t_close + dividend_amount) * (1 + ratio))
    raise ValueError(f"Unknown action_type: {action_type!r}")


def build_anchor(event: dict) -> Anchor:
    """Convert a corporate-action event spec into a fully-populated Anchor."""
    ex_date_obj = datetime.strptime(event["ex_date"], "%Y-%m-%d").date()
    rows = fetch_month(event["ticker"], ex_date_obj.year, ex_date_obj.month)
    t_minus_1_close, t_close = find_anchor_closes(rows, ex_date_obj)
    dividend_amount = event.get("dividend_amount")
    ratio = event.get("ratio")
    expected = _compute_expected_ratio(
        event["action_type"],
        t_minus_1_close,
        t_close,
        dividend_amount,
        ratio,
    )
    return Anchor(
        ticker=event["ticker"],
        ex_date=event["ex_date"],
        action_type=event["action_type"],
        dividend_amount=dividend_amount,
        ratio=ratio,
        raw_close_t_minus_1=t_minus_1_close,
        raw_close_t=t_close,
        expected_adj_close_ratio=expected,
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
            "Covers cash dividends, stock dividends, and combined cash+stock "
            "dividends (the Taiwan large-cap convention, 除權息合一). Each "
            "event's raw close prices are fetched from TWSE's official "
            "STOCK_DAY endpoint; dividend amounts and stock-dividend ratios "
            "are from primary sources (SEC 6-K for TSMC, AGM resolutions and "
            "TWSE MOPS announcements for Taiwan Cement). Sources deliberately "
            "independent of FinMind. The `expected_adj_close_ratio` field is "
            "the value the test asserts directly: X / (Y + D) for cash, "
            "X / (Y * (1 + R)) for stock, X / ((Y + D) * (1 + R)) for "
            "combined. These are NOT the textbook back-adjustment factors "
            "(which are Y/(Y+D), 1/(1+R), and their product respectively). "
            "See journal/decisions/"
            "2026-05-18-corporate-actions-ingestion-overhaul.md."
        ),
        "anchors": [asdict(a) for a in anchors],
    }
    OUTPUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(anchors)} anchors → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
