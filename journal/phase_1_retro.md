# Phase 1 Retrospective — Data Pipeline

**Written:** 2026-05-17
**Phase duration:** 2026-05-11 → 2026-05-17 (7 calendar days; ~1 week of actual work)
**Estimated duration:** ~2 weeks
**Exit criteria check:** all criteria met as of this writing

---

## 1. What was built

All modules from `PHASE_1.md` §3 are implemented and tested:

| Module | Status | Tests |
|---|---|---|
| `data/schemas.py` | Done | `test_schemas.py` |
| `data/storage.py` | Done | `test_storage.py` (22 tests) |
| `data/adjustments.py` | Done | `test_adjustments.py` |
| `data/quality.py` | Done | `test_quality.py` (15 tests) |
| `data/api.py` | Done | `test_api.py` (6 unit + 12 anchor) |
| `data/sources/base.py` | Done | — |
| `data/sources/finmind.py` | Done | `test_finmind.py` (5 cassette tests) |
| `data/sources/twse.py` | Done (minimal) | `test_twse.py` (7 cassette tests) |
| `scripts/backfill.py` | Done | `test_backfill.py` (19 unit tests) |

Suite total: **111 tests passing, 3 skipped (integration), 3.5 s**. Pre-commit clean.

Backfill: 50/50 tickers stored, 50/50 DQ `ok=True`. Date range 2016-05-17 to 2026-05-15.

---

## 2. Exit criteria — final status

- [x] `get_adjusted_history("2330", "2016-01-01", "2026-05-10")` returns in < 1 s (warm)
- [x] `test_adjustment_anchors` — all 6 anchors pass at 0.01% tolerance
- [x] `test_raw_close_fidelity` — all 6 anchors pass at 0.1% tolerance
- [x] Followups file lists stock-dividend / split coverage gap (FU-02)
- [x] Backfill script ran end-to-end on all 50 tickers, exit code 0
- [x] `quality.check_all()` → 50/50 `ok=True`
- [x] Unit suite < 5 s and passes
- [x] Cassette tests run offline and pass
- [x] Integration tests pass when run manually with token
- [x] Pre-commit clean
- [x] Decisions D1–D9 recorded in `journal/decisions/`
- [x] `journal/phase_1_retro.md` — this document
- [ ] `PROJECT_PLAN.md` progress tracker updated — next step after this file

---

## 3. What went well

**Build order held.** The §5 sequence (schemas → storage → adjustments → sources → quality → api → backfill → run) was the right order. Each step had working tests before the next step started. No backtracking.

**FinMind spike saved time.** The Day 1 spike (ADR 0002) correctly identified `TaiwanStockDividendResult` as the right corporate-actions dataset (uses ex-date directly; `TaiwanStockDividend` uses the record date — wrong anchor for price adjustment). If this had been discovered mid-implementation it would have forced a rewrite of the adapter.

**Anchor cross-validation is better than the original plan.** The Yahoo Finance plan was invalidated by a paywall before it could be executed. The replacement — six hand-curated anchor events traced to SEC 6-K filings and TWSE STOCK_DAY — is more transparent, independently verifiable in ~5 minutes per anchor, and more diagnostic when it fails. A bulk-CSV comparison would have reported "0.X% drift somewhere"; the anchor test reports exactly which event type is wrong.

**Structured JSONL run logs paid off immediately.** The backfill first run failed for 21 of 50 tickers. The JSONL log made the pattern obvious within minutes: specific exception types per ticker, grouped by root cause. Without the logs, debugging 21 failures would have required re-running individual tickers with verbose mode.

**Pandera at boundaries caught real data issues.** Several FinMind data quality problems (zero open/close rows, `open > high` in TPEx-period rows, all-object-dtype empty DataFrames) were caught by schema validation at the source boundary — exactly where they should be caught. None propagated silently into storage.

**VCR cassettes make the test suite truly offline.** The default `record_mode="none"` means anyone can clone the repo and run `uv run pytest` without a FinMind token or network. This is the right default for a portfolio repo.

---

## 4. What went wrong / what was harder than expected

### 4.1 Back-adjustment formula was wrong from the start

**The bug:** `_compute_factor` for `cash_dividend` used `(X - D) / X` where X is the T-1 close. The correct TWSE 還原權息 factor is `Y / (Y + D)` where Y is the T close (close on the ex-date itself). These are equal only when `X - D = Y` — i.e., the stock dropped by *exactly* the dividend amount — which never holds in practice.

**Why it survived until step 13:** Five of six TSMC anchor events have a T-1→T price drop that closely approximates the dividend amount (TSMC is a large, liquid stock that tends to gap down predictably on ex-dates). In those five cases the error was well within the 0.01% tolerance. The 2018-06-25 event — where TSMC dropped 9.5 NT$ on an 8.0 NT$ dividend — pushed the error to ~0.024%, exposing the bug.

**The discovery path:** the anchor test for 2018-06-25 started failing after the test fixture's start-date bug was fixed (see 4.2 below). The `test_raw_close_fidelity` sub-assertion passed (raw data was correct); only `test_adjustment_anchors` failed. This split-assertion design did exactly what it was designed to do: isolated "adjustment math wrong" from "raw data wrong."

**The fix:** in `adjust()`, look up the T close from `df` (which is unmodified at that loop iteration since only bars *before* ex_date have been scaled) and pass it as `ref_close`. Formula: `ref_close / (ref_close + cash_amount)`.

**Lesson:** the two-formula approach — `(X-D)/X` vs `Y/(Y+D)` — is a well-known source of confusion in retail backtesting. The ADR (2026-05-13) correctly specified `Y/(Y+D)` in the math; the implementation deviated from it. More careful cross-referencing between spec and code at the time of writing `adjustments.py` would have caught this in step 4 rather than step 13.

### 4.2 Anchor test fixture had a start-date bug

**The bug:** the `adj_2330` and `raw_2330` pytest fixtures set `start = earliest_anchor_ex_date` (i.e., 2018-06-25). The test then filters `adj_2330[adj_2330.index < ex_date]` — which is empty when ex_date is the start of the data. The 2018-06-25 anchor therefore raised `AssertionError: No adjusted data before ex_date` rather than testing the math.

**Why it wasn't caught earlier:** steps 9–11 ran with 2330 not yet backfilled, so all 12 anchor tests were skipping via the module-scoped guard. The bug was only reachable after step 12 stored real data.

**The fix:** `start="2016-01-01"` — well before the earliest anchor, well within the backfill range.

### 4.3 Three FinMind data quality bugs found during the backfill run

None of these were foreseeable without real data. All three required examining JSONL logs + raw API responses + external market references to diagnose:

1. **Zero open/close rows** — FinMind encodes full-day trading halts and anomalous restructuring events as rows with `open=0, close=0`. These fail the `> 0` schema check. Fix: drop rows where `open == 0 or close == 0` in `get_daily_ohlcv`.

2. **`權息` unmapped** — `TaiwanStockDividendResult.stock_or_cache_dividend` has three values, not two: `息` (cash), `權` (stock), `權息` (combined). The initial TYPE_MAP only handled two. Fix: map `權息` → `cash_dividend` because `stock_and_cash_dividend` in that dataset equals the full price deduction regardless of cash/stock mix.

3. **Pre-TWSE `open > high` for TPEx-transfer tickers (6669, 7769, 6919)** — FinMind returns price history from a ticker's first day on *any* market, not just TWSE. For tickers that previously traded on TPEx, the pre-TWSE `open` field encodes the previous day's average price (TPEx has no opening call auction). This causes `open > high` on gap-down days. Temporary workaround: clamp `open` to `[low, high]`. Proper fix deferred as FU-05.

### 4.4 VCR `record_mode` gotcha

Setting `record_mode` inside a `vcr_config` pytest fixture silently overrides the `--record-mode` CLI flag, making it impossible to re-record cassettes without editing the fixture. The correct pattern: set `addopts = "--record-mode=none"` in `pyproject.toml` and leave the fixture alone. The fixture should never hardcode a record mode.

### 4.5 DuckDB requires `union_by_name=True` for optional columns

The optional `value` and `fetched_at` columns are not present in all parquet files (older writes predating those columns). DuckDB errors on schema mismatch unless `union_by_name=True` is set in `read_parquet`. This is easy to miss and only surfaces when the store is populated with files from different schema versions.

---

## 5. Decisions made during the phase

| Decision | Where recorded |
|---|---|
| D9 (superseded): anchor-event cross-validation replaces Yahoo Finance CSV plan | `journal/decisions/2026-05-13-adjustment-cross-check-method.md` |
| `TaiwanStockDividendResult` as primary corporate-actions source (not `TaiwanStockDividend`) | `journal/decisions/0002-finmind-corp-actions-spike.md` |
| Adjustment factor = `Y/(Y+D)` (T close, not T-1 close) — corrected in step 13 | `journal/decisions/0002-finmind-corp-actions-spike.md` — Amendment 2026-05-17 |
| `権息` maps to `cash_dividend` (not a new action type) | Weekly journal 2026-05-16, Learned section |
| Pre-TWSE prices clamped rather than dropped (temporary FU-05 workaround) | `journal/phase_1_followups.md` FU-05 |

---

## 6. Open follow-ups entering Phase 2

These are tracked in `journal/phase_1_followups.md`. Summary:

| ID | Description | Priority before Phase 2 |
|---|---|---|
| FU-01 | `rights_issue` has no FinMind dataset; schema type is dead code | Low — document in `finmind.py` |
| FU-02 | Anchor fixture covers cash dividends only; no stock-dividend or split anchor | Medium — add one before Phase 2 |
| FU-03 | `get_corporate_actions` `end` param unused in `FinMindSource` | Low — post-fetch filter is a one-liner |
| FU-04 | Backfill resume never skips on consecutive runs (always full re-fetch) | Low — performance only, not correctness |
| FU-05 | Pre-TWSE prices stored for 6669, 7769, 6919 with mangled `open` | **High — must fix before Phase 2** |

FU-05 is the blocking item. Phase 2 builds the inspection dashboard on top of stored OHLCV; pre-TWSE rows with clamped `open` will appear as valid data in candlestick charts and will corrupt Phase 3 backtesting open-price entries.

---

## 7. Honest assessment

**On pace:** the phase completed in 7 calendar days against a 2-week estimate. This is partly because the build order was well-designed and the spike answered the right questions early, and partly because some follow-up items (FU-02 to FU-05) were explicitly deferred rather than resolved.

**On correctness:** the adjustment formula bug (4.1) is the most significant quality miss. The anchor test caught it — which is the test's entire purpose — but the bug existed in committed code from step 4 through to step 13. A careful proof-read of the formula against the ADR at commit time would have caught it. Going forward: any formula that appears in both a spec/ADR and in code should be verified symbolically at the time of writing.

**On the test design:** the split between `test_adjustment_anchors` and `test_raw_close_fidelity` is the right call. When `test_adjustment_anchors` failed for 2018-06-25, `test_raw_close_fidelity` passing immediately ruled out data corruption and pointed to math. That two-test structure saved diagnosis time.

**On survivorship bias:** the universe is a fixed snapshot of today's TWSE Top 50. Tickers that were delisted or dropped from the index between 2016 and 2026 are not represented. Any Phase 3–6 backtest results are survivorship-biased by construction. This is accepted for now (Phase 9 expands universe handling) but must be stated clearly in every tear sheet.

**On FinMind data quality:** the source has real data issues beyond the documented TPEx-period problem (zero rows, `open > high`). For a retail-grade backtest, this is acceptable; for a production risk system, it would not be. The schema validation at boundaries means bad data is caught and logged rather than silently corrupting downstream results.

**On commit message quality:** all Phase 1 commits use a flat `feat: Phase 1 Day N — <summary>` format. Two problems. First, the type is always `feat` regardless of what was actually committed — the Day 12 commit was majority bug fixes but is indistinguishable from a feature commit in `git log`. Second, the body format was inconsistent across commits: some used bullet points, some used prose paragraphs, with no clear rule for when to use which. A reviewer scanning the log cannot reliably tell what changed or why. Phase 2 should adopt a consistent commit title convention and a fixed body format (to be decided at Phase 2 kickoff).

**Hiring signal assessment:** the data engineering is solid — atomic writes, merge semantics, schema validation at boundaries, VCR cassettes for offline tests, structured run logs. The adjustment-formula bug and the FU-05 data contamination are the honest weak points to acknowledge if this comes up in a portfolio review.

---

## 8. Phase 2 kickoff notes

Before starting Phase 2:

1. **Resolve FU-05** — build a TWSE listing-date lookup (static table for the ~3–5 affected tickers is sufficient), restrict backfill start to TWSE listing date, re-backfill 6669/7769/6919 with `--force`, remove the clamping workaround from `get_daily_ohlcv`.

2. **Add a stock-dividend anchor (FU-02)** — pick 2317 Hon Hai or 1101 Taiwan Cement, identify a confirmed event, add to `build_anchors.py`, regenerate fixture, confirm the parametrized test passes.

3. **Update `PROJECT_PLAN.md`** — mark Phase 1 done, record end date, add Phase 2 start.

4. **Read `PHASE_1.md` §11 (out of scope)** — confirm nothing in Phase 2 scope was accidentally built. Dashboard is the next deliverable; no indicators, no strategies, no backtester logic.
