# Phase 1 — Known Gaps and Follow-ups

Items discovered during Phase 1 implementation that are accepted as known gaps
for now but should be resolved before Phase 2 begins (or explicitly deferred with
a written rationale). Append new items as they are found; do not delete resolved
ones — mark them **[RESOLVED]** with the date and closing note.

---

## FU-01 — `rights_issue` has no FinMind equivalent dataset **[RESOLVED 2026-05-18]**

**Discovered:** 2026-05-14 (Step 6)
**Affects:** `FinMindSource.get_corporate_actions`, `CORPORATE_ACTIONS_SCHEMA`

### Gap

`CORPORATE_ACTIONS_SCHEMA` listed `rights_issue` as a valid `action_type`, but
`FinMindSource.get_corporate_actions` never produced a row with that type. No
FinMind dataset was identified during the Phase 1 spike that maps cleanly to a
rights issue (現金增資配股) event.

### Closing note (2026-05-18)

`TaiwanStockDividend` (the raw declaration table) does contain rights-issue fields:
`TotalNumberOfCashCapitalIncrease`, `ParticipateDistributionOfTotalShares`, and
`CashIncreaseSubscriptionpRrice`. As part of the fu-02 corporate-actions overhaul,
`_build_dividends` now emits `rights_issue` rows when these fields are non-zero, with
dilution ratio derived from share counts (not `CashIncreaseSubscriptionRate`, which
reflects post-carve-out entitlement rather than total dilution). The correct TWSE
back-adjustment formula `(Y + ratio×sub_price) / (Y×(1+ratio))` is implemented in
`adjustments._compute_factor`. Schema, unit tests, and integration cross-validator all
cover the type. Resolved in branch `phase-1-fu-02`.

---

## FU-02 — Anchor fixture covers cash dividends only; no stock-dividend or split events **[RESOLVED 2026-05-19]**

**Discovered:** 2026-05-13 (Step 4) — recorded in
`journal/decisions/2026-05-13-adjustment-cross-check-method.md`
**Planned resolution:** After Step 12 (backfill run). Adding anchors before the
backfill is pointless — the anchor tests skip until 2330 is stored, so new events
can't be verified until real data is present. Sequence: complete backfill → identify
event → run `build_anchors.py` → confirm new parametrized cases pass → commit.
**Affects:** `tests/fixtures/anchor_dividends.json`, `tests/data/test_api.py`

### Gap

The six TSMC anchor events in `tests/fixtures/anchor_dividends.json` are all
cash-dividend events (2018–2024). TSMC has not issued a stock dividend or undergone
a split since 2008, so these code paths in `adjustments.py` are exercised by no
anchor at all. A bug in the stock-dividend or split adjustment math would not be
caught by `test_adjustment_anchors`.

### Partial resolution note (2026-05-18)

Two 1101 (Taiwan Cement) `cash_and_stock_dividend` anchors added (2020-08-12 and
2022-07-21). These exercise the combined adjustment formula — both the cash leg
`Y/(Y+D)` and the stock leg `1/(1+R)` in a single compounded factor — and validate
the combined formula end-to-end against real stored prices. 1101 is backfilled as
test-only data (not in the trading universe).

Still open:
- No pure `stock_dividend` anchor (stock leg only, no cash).
- No `capital_reduction` anchor (none of the Top 50 tickers that are backfilled have
  a capital reduction in the stored window). These paths are covered by unit tests in
  `test_adjustments.py` but not by end-to-end anchor tests against real stored data.

### Revised resolution (2026-05-18)

**`stock_dividend` anchor — accepted as unit-test-only coverage; anchor requirement
dropped.** The ADR (2026-05-18) confirms that pure stock-dividend events (stock leg
only, no cash on the same ex-date) are essentially nonexistent for TWSE Top 50
post-2015 — verified across 2317 (Hon Hai), 1101 (Taiwan Cement), 1216
(Uni-President), 2892 (First Financial). 2317 was the originally proposed candidate
and is ruled out by this finding. The `1/(1+R)` stock leg is already exercised
end-to-end through the 1101 `cash_and_stock_dividend` anchors (both legs present).
Requiring a standalone anchor for an event type that does not occur in the universe
is a dead requirement; unit-test coverage in `test_adjustments.py` is accepted as
sufficient for this path.

**`capital_reduction` anchor — one remaining blocker before Phase 2 exit.** Capital
reductions do occur on TWSE, just not in the stored Top 50 window. Resolution:
find a non-Top-50 ticker with a capital reduction event in the backfill window,
backfill as test-only data (same pattern as 1101), and add one anchor event via
`scripts/build_anchors.py`. Query `TaiwanStockCapitalReductionReferencePrice` on
FinMind to identify a candidate.

### Final resolution note (2026-05-19)

2327 (Yageo) identified as the `capital_reduction` anchor candidate. Yageo has three
capital-reduction events in the backfill window (2016-08-15, 2017-08-18, 2022-10-31).
2327 backfilled as test-only data (same pattern as 1101). The 2022-10-31 event anchored
via `build_anchors.py`.

Two blockers resolved in sequence before the anchor passed:

1. **FU-06 duplicate rows** (see FU-06 below): FinMind emitted two rows for the
   2022-10-31 event (2022-10-21 suspension-start + 2022-10-31 ex-date). Fixed by
   full-row dedup in `_build_capital_reductions`. The stale 2022-10-21 row had already
   been written to `data/corporate_actions/2327.parquet` by a prior backfill run; because
   `write_corporate_actions` merges by `(ticker, ex_date, action_type)`, the old row
   survived a fresh fetch. The file was deleted and re-backfilled to purge it.

2. **`build_anchors.py` formula mismatch**: `_compute_expected_ratio` used
   `X / (R×Y + C)`, which relies on the wealth-continuity invariant `X ≈ R×Y + C`
   — valid only when the market closes exactly at the TWSE reference price. For the Yageo
   2022 event the deviation was ~0.066%, causing the anchor to fail at the 0.01%
   tolerance. Corrected to `(X - C) / (R × Y)`, matching what `adjust()` computes via
   the stored price-jump factor
   `PostReductionReferencePrice / ClosingPriceonTheLastTradingDay`. Fixture regenerated.

All 9 anchor tests pass. 129 passed, 6 skipped. FU-02 fully resolved in branch
`phase-1-fu-02`.

---

## FU-03 — `get_corporate_actions` `end` parameter is unused in `FinMindSource`

**Discovered:** 2026-05-13 (Step 5)
**Still open as of 2026-05-18** — confirmed unresolved during fu-02 overhaul. The
switch from `TaiwanStockDividendResult` to `TaiwanStockDividend` did not change the
`end` handling; neither dataset accepts an `end_date` filter.
**Affects:** `FinMindSource.get_corporate_actions`, `DataSource` ABC

### Gap

`DataSource.get_corporate_actions(ticker, start, end)` accepts an `end` parameter
per the ABC contract. `FinMindSource` does not pass `end` to the API (FinMind's
`TaiwanStockDividendResult` and `TaiwanStockCapitalReductionReferencePrice` endpoints
have no `end_date` filter) and does not post-filter results by `ex_date <= end`.
Callers who pass a bounded `end` date receive more rows than requested.

### Resolution options

1. **Post-fetch filter (minimal change):** add
   `df = df[df["ex_date"] <= pd.Timestamp(end)]` before `validate()` in
   `get_corporate_actions`. No interface change needed; corrects the behaviour.
2. **Document as advisory:** add a docstring note that `end` is advisory for
   `FinMindSource` and that results may extend beyond it. Honest, but leaves
   a silent API contract violation.
3. **Change the ABC:** make `end` optional (`end: date | None = None`) to reflect
   that not all sources can filter by end date. Requires an ADR since it modifies
   a locked interface decision.

Option 1 is the lowest-risk resolution. Options 2 and 3 need deliberate discussion.

### Deferral note (2026-05-19)

Deferred to Phase 2 with the following rationale:

- No caller in the current codebase passes a meaningful bounded `end` to
  `get_corporate_actions`. `api.py` passes only `start`; `backfill.py` does not call
  `get_corporate_actions` with an `end` constraint. The contract violation has no current
  consumer, so there is no observable correctness impact today.
- The fix (Option 1) is a one-liner. It will be straightforward to apply whenever a
  caller actually needs bounded results (e.g. Phase 2 dashboard filtering by date range
  or Phase 3 backtest walk-forward splits).
- Resolving it now would change untested code paths with no regression signal — an
  adverse risk/reward for a phase-exit commit.

Phase 2 should apply Option 1 the first time a caller passes a real `end` date.

---

## FU-04 — Backfill resume logic never skips tickers when run on consecutive days

**Discovered:** 2026-05-16 (Step 12)
**Still open as of 2026-05-18** — no changes to `resolve_fetch_window` in fu-02.
**Affects:** `scripts/backfill.py`

### Gap

The backfill script's skip condition requires `stored_end >= requested_end`. Because
`--end` defaults to today, stored data (which ends at yesterday's close) always falls
one day short of the requested window. Every ticker is therefore re-fetched in full
on every run — relying on `write_ohlcv`'s dedup merge for idempotency rather than
truly skipping. This is correct but wasteful: a full 10-year fetch per ticker, every day.

### Resolution

Replace the simple range comparison with a delta fetch: if `stored_end` is within N
trading days of `requested_end` (e.g. N=3), fetch only `[stored_end, requested_end]`
rather than the full window. The existing merge semantics in `write_ohlcv` handle the
overlap correctly regardless.

This is a performance improvement, not a correctness bug — defer until after Phase 1
exit criteria are met.

---

## FU-05 — Pre-TWSE listing prices stored for tickers that transferred from TPEx **[RESOLVED 2026-05-17]**

**Discovered:** 2026-05-16 (Step 12)
**Priority:** Resolve before Phase 2 begins.
**Affects:** `FinMindSource.get_daily_ohlcv`, `scripts/backfill.py`, stored Parquet for
tickers confirmed affected: **6669, 6919, 7769** (and any other Top-50 ticker that
transferred from TPEx to TWSE).

### Gap

FinMind returns price history starting from a ticker's first day on any market, not
from its TWSE listing date (掛牌日期). For tickers that previously traded on TPEx or
the emerging stock board (興櫃), the pre-TWSE rows have incorrect `open` semantics:
FinMind encodes the **previous day's average price** as `open`, since TPEx has no
opening call auction. This causes `open > high` on gap-down days, breaking the
`high >= open` schema invariant.

**Current workaround (FU-05 temporary):** `get_daily_ohlcv` clamps `open` to
`[low, high]` after fetch. This makes the schema pass but leaves the pre-TWSE rows
in the store with a mangled `open`. For backtesting open-price entries (Phase 3+),
these rows will produce silently wrong results.

### Why resolve before Phase 2

Phase 2 builds the inspection dashboard on top of stored OHLCV. Pre-TWSE rows with
wrong prices will appear as valid data in charts and statistics. Phase 3 backtesting
will simulate trades using these prices. The longer the bad data stays, the more
downstream work inherits it.

### Resolution

1. Find the TWSE listing date for each Top-50 ticker. Options:
   - TWSE's market info endpoint (`exchangeReport/STOCK_DAY_AVG` or the company basic
     info page) — check if a listing-date field is exposed.
   - Build a static lookup table for the small number of affected tickers (~3–5).
2. In `backfill.py`, pass `start=max(requested_start, twse_listing_date)` when
   calling `source.get_daily_ohlcv`.
3. Re-backfill the affected tickers with `--force` to purge the pre-TWSE rows from
   the Parquet store.
4. Remove the clamping workaround from `get_daily_ohlcv` once the store is clean.

### Closing note (2026-05-17)

TWSE Open API (`t187ap03_L`) used as the authoritative source — covers all 50 tickers
in one call. 4 affected tickers identified (3711, 6669, 6919, 7769 — 3711 was a new
discovery not in the original gap report). `get_listing_dates()` added to
`src/twstock/universe.py` as a static dict; `scripts/fetch_listing_dates.py` added for
future universe expansions. `backfill.py` updated to apply
`effective_start = max(requested_start, listing_dates[ticker])` per ticker. Clamping
workaround removed from `FinMindSource.get_daily_ohlcv`. Full Parquet store wiped and
re-backfilled; `check_all()` reports 50/50 ok with correct start dates per ticker.

---

## FU-06 — `TaiwanStockCapitalReductionReferencePrice` emits two rows per event **[RESOLVED 2026-05-19]**

**Discovered:** 2026-05-19 (during FU-02 capital-reduction anchor work)
**Affects:** `FinMindSource._build_capital_reductions`

### Gap

`TaiwanStockCapitalReductionReferencePrice` returns two rows per capital-reduction
event for a given ticker: one dated at the **trading-suspension start** (e.g.
2022-10-21 for Yageo 2327) and one dated at the **first post-reduction trading day**
(e.g. 2022-10-31). Both rows carry identical price fields
(`ClosingPriceonTheLastTradingDay`, `PostReductionReferencePrice`, and all derived
reference prices). Without deduplication, `_build_capital_reductions` passed both rows
through, producing two `capital_reduction` entries with different `ex_date` values but
the same ratio. `adjust()` then applied the factor twice to all prices before the
suspension date, causing a compounded error (observed ratio ≈ 1.135 instead of
expected 0.911 for the Yageo 2327 2022-10-31 anchor).

### Resolution (2026-05-19)

Deduplication added in `_build_capital_reductions` before subsetting columns:

```python
df_raw = df_raw.sort_values("date").drop_duplicates(
    subset=[c for c in df_raw.columns if c != "date"], keep="last"
)
```

Deduplicating on the **full non-date row** (not just the three price fields used
downstream) is intentional: if FinMind ever emits two rows that share price fields
but differ on any other column (`LimitUp`, `ReasonforCapitalReduction`, etc.), both
rows are preserved rather than silently collapsed — the anomaly surfaces downstream
rather than disappearing at the parse step.

Two unit tests added to `tests/data/sources/test_finmind.py`:
- `test_build_capital_reductions_drops_suspension_start_duplicate` — two rows identical
  except date → one row survives (the later ex-date).
- `test_build_capital_reductions_preserves_distinct_events` — two rows sharing price
  fields but differing on `ReasonforCapitalReduction` → both rows survive.

**Downstream discovery (2026-05-19):** Once the double-application was removed, the
2327 anchor still failed (ratio 0.91049 ≠ expected 0.91111, 0.066% gap). Root cause:
`build_anchors.py`'s `_compute_expected_ratio` used `X/(R×Y+C)`, relying on the
wealth-continuity invariant (`X ≈ R×Y+C`) rather than the TWSE theoretical formula.
Fixed in the same branch — see FU-02 final resolution note for full details.
