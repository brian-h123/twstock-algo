"""Build adjustment cross-check anchor fixture from TWSE official + primary dividend sources.

This script fetches raw close prices directly from TWSE's STOCK_DAY endpoint
(independent of FinMind) and combines them with dividend amounts and capital-
reduction parameters sourced from SEC 6-K filings (TSMC), AGM resolutions /
TWSE MOPS announcements (Taiwan Cement, Yageo), and corroborating news reports.
The output is a JSON fixture used by the adjustment cross-validation test in
Phase 1.

Four event types are supported, each with its own local-invariant formula
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

  capital_reduction (ratio R = new_shares/old_shares with R <= 1, plus cash
  refund C per OLD share — C = 0 for pure 彌補虧損減資 loss-coverage events,
  C > 0 for 現金減資 cash-refund events). TWSE sets the ex-reduction
  reference price PostRef = (X - C) / R. The back-adjustment factor scales
  pre-event prices so T-1 continuity-adjusts to PostRef:
      back-adj factor = (X - C) / (R * X) = PostRef / X
      adj_close[T-1] / adj_close[T] = (X - C) / (R * Y)
  Note: the wealth-continuity identity X ≈ R*Y + C (equivalently, the
  alternative formula X / (R*Y + C)) holds only when Y = PostRef exactly,
  which is rarely the case in practice — use (X - C) / (R * Y) instead.
  At C=0: degenerates to X / (R*Y) = (1/R) * (X/Y), the pure share-count
  adjustment.

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


# ----- Corporate-action reference data ------------------------------------------
#
# Each entry is a verified corporate-action event. Dividend amounts, stock-
# dividend ratios, and capital-reduction parameters come from primary sources
# (SEC 6-K, company IR / AGM resolutions, TWSE MOPS announcements). These are
# NOT sourced from FinMind — that is intentional. The cross-check loses meaning
# if both sides come from the same upstream pipeline.
#
# Coverage:
#   - TSMC (2330) cash dividends, post-2018
#   - Taiwan Cement (1101) combined cash + stock dividends, 2020 and 2022
#   - Yageo (2327) cash capital reduction, 2022
#
# Sources used:
#   - SEC EDGAR Form 6-K filings (TSMC files quarterly with the US SEC because
#     of its NYSE ADR listing; these contain the exact NT$ per-share figures)
#   - TSMC investor relations (pr.tsmc.com and investor.tsmc.com)
#   - stockanalysis.com TPE:2330 dividend history (sourced from TWSE/TSMC;
#     used as a cross-check of the SEC filings)
#   - Taiwan Cement AGM resolutions + TWSE MOPS ex-rights/ex-dividend
#     announcements for 1101 (2020-08-12 and 2022-07-21)
#   - Yageo TWSE-filed corporate-action plan (via MOPS), Liberty Times and UDN
#     news reports quoting that filing, and TWSE's published ex-reduction
#     reference price for 2327 (ex-date 2022-10-31)
#
# Pure stock-dividend events (no cash component on the same ex-date) are
# essentially absent from TWSE Top 50 post-2015 — Taiwan companies almost
# universally combine cash and stock distributions onto a single ex-date
# (除權息合一). The `stock_dividend` action_type remains supported in the
# build_anchor logic for future use (e.g. small-cap pure-stock events,
# splits handled as ratio-only adjustments) but no real anchor of that type
# is currently included.
#
# Capital reduction events on Taiwan equities most often take the cash-refund
# form (現金減資). The Yageo 2022 event below is a clean cash-refund reduction
# with no concurrent dividend or stock split — ideal for exercising both the
# R (share-count) and C (cash-per-share) branches of the capital_reduction
# formula in a single anchor.

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
    {
        # Yageo Corp 2022 cash capital reduction (現金減資).
        #
        # Field semantics for capital_reduction:
        #   ratio            = new_shares / old_shares (R, <= 1)
        #   dividend_amount  = cash refund per OLD share (C, NT$)
        #
        # Exact figures derived from Yageo's TWSE-filed corporate-action plan:
        #   shares eliminated         = 108,011,200
        #   pre-event outstanding     = 529,272,005  (= 544,060,005 issued
        #                                              less 14,788,000 treasury
        #                                              shares awaiting cancellation)
        #   elimination ratio         = 108,011,200 / 529,272,005
        #                             = 0.20407502943  (exact)
        #   R = 1 - elimination ratio = 0.79592497057
        #   C = NT$10 par × elim_ratio = NT$2.0407502943 per old share
        #
        # Headline news rounds these to "20% reduction / NT$2 cash refund" but
        # the full-precision numbers are required at the anchor test's
        # rel=1e-4 tolerance — the rounded figures would fail.
        "ticker": "2327",
        "ex_date": "2022-10-31",
        "action_type": "capital_reduction",
        "dividend_amount": 2.0407502943,
        "ratio": 0.79592497057,
        "source": (
            "Yageo Corp (2327) cash capital reduction (現金減資). Board "
            "resolution 2022-03-17, AGM-approved June 2022. Per Yageo's "
            "TWSE-filed corporate-action plan: 108,011,200 shares eliminated "
            "from a pre-event outstanding base of 529,272,005 shares "
            "(= 544,060,005 issued less 14,788,000 treasury shares awaiting "
            "cancellation), giving exact elimination ratio "
            "108,011,200 / 529,272,005 = 0.20407502943, R = 0.79592497057 "
            "(i.e. 795.92497057 new shares per 1,000 old shares), and cash "
            "refund NT$2.0407502943 per OLD share (= NT$10 par × elimination "
            "ratio). Trading suspended 2022-10-20 to 2022-10-28; reduction "
            "record date 2022-10-28; new shares first traded 2022-10-31 at "
            "TWSE-set ex-reduction reference price NT$334.15. "
            "Cross-validated against THREE documents that do not share the "
            "FinMind pipeline: (1) Yageo's MOPS-filed cash-reduction operational plan "
            "(主旨: 公告本公司董事會訂定換股作業計劃及減資換發股票基準日等相關事宜), "
            "announcement 序號 1 dated 民國111年09月13日 17:06:49 at "
            "https://mops.twse.com.tw/mops/web/t05st09_2 — directly states "
            "529,272,005 outstanding shares, 108,011,200 shares eliminated, "
            "NT$2.0407502943 cash refund per old share, basis date "
            "2022-10-28, new shares first traded 2022-10-31. Confirmed by "
            "Yageo's subsequent post-completion filing (主旨: 公告本公司辦理"
            "現金減資完成實收資本額變更登記) dated 2022-09-16 reporting "
            "final outstanding count 436,048,805 (= 421,260,805 + 14,788,000 "
            "treasury yet to be cancelled). Cross-validated against (2) "
            "Liberty Times Net news report at "
            "https://ec.ltn.com.tw/article/breakingnews/4056976 (2022-09-13) "
            "and (3) UDN report on TWSE's published ex-reduction reference "
            "price NT$334.15 at "
            "https://money.udn.com/money/story/5607/6727376 (2022-10-31). "
            "All sources agree precisely on share counts, refund per share, "
            "and TWSE-set reference price. "
            "Picked because: (a) clean single-action ex-date — no concurrent "
            "dividend or stock split, (b) modest representative ratio "
            "(~20% reduction) — avoiding extreme cases, (c) cash-refund "
            "event rather than pure loss-coverage, so the anchor exercises "
            "BOTH the R (share-count) and C (cash-per-share) branches of "
            "the capital_reduction formula in a single event."
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
                                  (ratio = stock-dividend fraction, e.g. 0.10
                                   means 100 new shares per 1,000 existing)
        capital_reduction        → both dividend_amount and ratio set
                                  (ratio = new_shares / old_shares, R <= 1;
                                   dividend_amount = cash refund per OLD
                                   share, 0.0 for pure loss-coverage events)

    `expected_adj_close_ratio` is the precomputed value the cross-validation
    test asserts the user's adjusted-close series matches across this event.
    For each type, given raw_close[T-1] = X and raw_close[T] = Y:

        cash_dividend            → X / (Y + dividend_amount)
        stock_dividend           → X / (Y * (1 + ratio))
        cash_and_stock_dividend  → X / ((Y + dividend_amount) * (1 + ratio))
        capital_reduction        → (X - dividend_amount) / (ratio * Y)

    These ratios all sit near 1.0 for a "clean" market reaction to the event
    (i.e. when raw_close[T] equals the theoretical ex-rights/ex-div / ex-
    reduction reference price); intraday moves on the ex-date push the ratio
    off 1.0 by the daily-return magnitude. The textbook back-adjustment
    *factors* (e.g. Y/(Y+D) for cash, 1/(1+R) for stock, Y/(R*Y+C) for
    capital reduction) are different numbers than what is stored here; the
    test uses these ratios directly.
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

    For capital_reduction events with a multi-day trading suspension preceding
    the ex-date (e.g. Yageo 2022: suspended 10-20 through 10-28, ex-date
    10-31), the most recent trading day for that ticker before ex_date is the
    last day of normal trading before suspension (here, 10-19). Because the
    TWSE STOCK_DAY endpoint only returns rows on trading days for the queried
    ticker, suspension days simply do not appear in `rows` and the "most
    recent prior trading day" logic remains correct without special handling.
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
    if action_type == "capital_reduction":
        if ratio is None:
            raise ValueError("capital_reduction event missing ratio (new_shares / old_shares)")
        if dividend_amount is None:
            raise ValueError(
                "capital_reduction event missing dividend_amount "
                "(cash refund per OLD share; use 0.0 for pure loss-coverage "
                "reductions with no cash component)"
            )
        return (t_minus_1_close - dividend_amount) / (ratio * t_close)
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
            "Covers cash dividends, stock dividends, combined cash+stock "
            "dividends (the Taiwan large-cap convention, 除權息合一), and "
            "cash capital reductions (現金減資). Each event's raw close "
            "prices are fetched from TWSE's official STOCK_DAY endpoint; "
            "dividend amounts, stock-dividend ratios, and capital-reduction "
            "parameters are from primary sources (SEC 6-K for TSMC, AGM "
            "resolutions and TWSE MOPS announcements for Taiwan Cement, "
            "TWSE-filed corporate-action plan and corroborating news for "
            "Yageo). Sources deliberately independent of FinMind. The "
            "`expected_adj_close_ratio` field is the value the test asserts "
            "directly: X / (Y + D) for cash, X / (Y * (1 + R)) for stock, "
            "X / ((Y + D) * (1 + R)) for combined, (X - C) / (R * Y) for "
            "capital reduction. These are NOT the textbook back-adjustment "
            "factors (which are Y/(Y+D), 1/(1+R), their product, and "
            "(X-C)/(R*X) respectively). "
            "See journal/decisions/"
            "2026-05-18-corporate-actions-ingestion-overhaul.md."
        ),
        "anchors": [asdict(a) for a in anchors],
    }
    OUTPUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(anchors)} anchors → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
