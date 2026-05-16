# Phase 1 — Known Gaps and Follow-ups

Items discovered during Phase 1 implementation that are accepted as known gaps
for now but should be resolved before Phase 2 begins (or explicitly deferred with
a written rationale). Append new items as they are found; do not delete resolved
ones — mark them **[RESOLVED]** with the date and closing note.

---

## FU-01 — `rights_issue` has no FinMind equivalent dataset

**Discovered:** 2026-05-14 (Step 6)
**Affects:** `FinMindSource.get_corporate_actions`, `CORPORATE_ACTIONS_SCHEMA`

### Gap

`CORPORATE_ACTIONS_SCHEMA` lists `rights_issue` as a valid `action_type`, but
`FinMindSource.get_corporate_actions` never produces a row with that type. No
FinMind dataset was identified during the Phase 1 spike that maps cleanly to a
rights issue (現金增資配股) event.

The type is therefore dead code in the schema for all data produced by `FinMindSource`.
If any caller filters or branches on `action_type == "rights_issue"`, it will never
fire on FinMind-sourced data.

### Investigation needed before closing

1. Check whether `TaiwanStockDividend` (the raw declaration table, as opposed to
   `TaiwanStockDividendResult`) contains a rights-issue flag or separate columns
   for 現金增資. The spike focused on `TaiwanStockDividendResult`; the raw table
   was not fully explored.
2. Check whether `TaiwanStockCapitalReductionReferencePrice` or any other FinMind
   dataset covers 現金增資配股 or 私募增資.
3. If no FinMind dataset covers it, decide: (a) remove `rights_issue` from
   `CORPORATE_ACTIONS_SCHEMA` until a source is found, or (b) keep it as a
   forward-compatible placeholder and document the gap in `sources/finmind.py`.

### Resolution options

- **Add support:** find the dataset, implement, add cassette + integration tests.
- **Remove the type:** if no dataset exists, drop `rights_issue` from the schema's
  `isin` check and add a comment noting it is out of scope for the FinMind source.
  Requires an ADR if the schema change lands post-Phase-1.
- **Keep as placeholder:** leave the type in the schema but add a docstring note in
  `FinMindSource.get_corporate_actions` stating it is never produced.

---

## FU-02 — Anchor fixture covers cash dividends only; no stock-dividend or split events

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

### Resolution

Add at least one stock-dividend anchor and one split or capital-reduction anchor
from a different Top 50 ticker before Phase 1 exit.

Candidate tickers with known stock-dividend history:
- **2317 Hon Hai** — has issued stock dividends in recent years
- **1101 Taiwan Cement** — long history of mixed cash + stock dividends
- **1216 Uni-President** — similar profile

Steps:
1. Pick a ticker and identify a confirmed stock-dividend event from TWSE records.
2. Add the event to the dividend reference table inside `scripts/build_anchors.py`.
3. Run `uv run python scripts/build_anchors.py` to regenerate
   `tests/fixtures/anchor_dividends.json`.
4. Review the diff before committing (per CLAUDE.md guardrail).

**Phase 1 exit criterion satisfied** by having this gap listed here (PHASE_1.md §9
requires at least one non-cash-dividend gap documented, not resolved). Resolution
itself is deferred to post-backfill within Phase 1, before Phase 2 begins.

---

## FU-03 — `get_corporate_actions` `end` parameter is unused in `FinMindSource`

**Discovered:** 2026-05-13 (Step 5)
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

---

## FU-04 — Backfill resume logic never skips tickers when run on consecutive days

**Discovered:** 2026-05-16 (Step 12)
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

## FU-05 — Pre-TWSE listing prices stored for tickers that transferred from TPEx

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
