# Phase 1 — Data Pipeline

> **Purpose.** Build a clean, queryable historical OHLCV store for the TWSE Top 50, with corporate actions handled correctly. This is the foundation every later phase rests on; over-invest in correctness here, under-invest in features.

**Status:** Ready to start (spec finalized)
**Last updated:** 2026-05-13
**Estimated duration:** ~2 weeks
**Predecessor:** Phase 0 (complete)

---

## 1. Goal

After Phase 1, the following call returns a clean, gap-handled, adjusted DataFrame in under 1 second:

```python
from twstock.data.api import get_adjusted_history
df = get_adjusted_history("2330", start="2016-01-01", end="2026-05-10")
```

And the entire Top 50 universe × ~10 years of daily data has been backfilled to local Parquet, schema-validated, and DQ-checked.

---

## 2. Locked Decisions

These are settled. Do not relitigate without an ADR.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Storage layout:** one Parquet file per ticker at `data/by_ticker/{ticker}.parquet` | Matches dominant access pattern (per-ticker history). 50 files is fine. |
| D2 | **Storage abstraction:** all callers go through `storage.py`'s public API; parquet paths and pyarrow objects never leak out | Makes layout change a one-evening migration if needed. |
| D3 | **Backfill window:** 10 years back from today, per-ticker truncated to listing date if shorter | Covers multiple regimes; sufficient for Phase 6 ML. |
| D4 | **Adjustment policy:** store raw OHLCV + separate corporate-actions table; adjust on read via pure function | Reproducibility, bug recovery, auditability against TWSE published close. |
| D5 | **Schema validation:** pandera schemas in `data/schemas.py`; every DataFrame entering storage or leaving a `DataSource` is validated | Catches bad data at the boundary, not 5 layers in. |
| D6 | **Test fixture strategy:** hybrid — hand-written fixtures for pure logic, VCR cassettes for source-layer realism, integration tests marked separately | See §6 for full detail. |
| D7 | **Backfill script:** idempotent, resumable, partial-run capable, throttled, per-ticker error isolation, structured JSONL log | See §7 for full detail. |
| D8 | **Trading calendar:** use `exchange_calendars` library with XTAI calendar for "missing day" DQ checks | Avoids hand-rolled holiday lists; well-maintained. |
| D9 | **Adjustment cross-validation:** hand-curated anchor events in `tests/fixtures/anchor_dividends.json`. Each event records the dividend amount (from TSMC SEC 6-K filings) and the T-1 / T raw closes (from TWSE STOCK_DAY official endpoint). Test asserts the user's adjusted-close ratio across each event matches the expected back-adjustment factor within 0.01%. | Yahoo paywalled CSV downloads; Stooq does not cover TWSE. Anchors derive from primary documents on both sides (SEC + TWSE), making the cross-check fully independent of FinMind and ToS-clean for a public repo. See `journal/decisions/2026-05-13-adjustment-cross-check-method.md`. |

---

## 3. Deliverables (modules)

```
src/twstock/data/
├── __init__.py
├── schemas.py              # pandera schemas (NEW)
├── storage.py              # parquet read/write + duckdb query (FLESH OUT)
├── adjustments.py          # pure adjustment function (NEW)
├── quality.py              # DQ checks (NEW)
├── api.py                  # public entry point: get_adjusted_history etc. (NEW)
└── sources/
    ├── base.py             # DataSource abstract class (REFINE)
    ├── finmind.py          # FinMindSource (IMPLEMENT)
    └── twse.py             # TwseSource fallback (MINIMAL IMPL)

scripts/
└── backfill.py             # CLI tool (NEW)

tests/
├── fixtures/               # hand-written CSV/JSON for unit tests (NEW DIR)
├── cassettes/              # VCR cassettes, gitignored except canonical ones (NEW DIR)
├── data/
│   ├── test_schemas.py
│   ├── test_storage.py
│   ├── test_adjustments.py
│   ├── test_quality.py
│   ├── test_api.py
│   └── sources/
│       ├── test_finmind.py
│       └── test_twse.py
└── integration/            # @pytest.mark.integration, hits real API
    └── test_finmind_live.py
```

---

## 4. Module specifications

### 4.1 `data/schemas.py`

Two pandera DataFrameSchemas, exported as constants.

**`OHLCV_SCHEMA`** — raw daily bars, one row per (ticker, date):

| Column | Dtype | Required | Checks |
|---|---|---|---|
| `date` | `datetime64[ns]` | yes | not null |
| `ticker` | `string` | yes | regex `^\d{4}$` |
| `open` | `float64` | yes | > 0 |
| `high` | `float64` | yes | > 0, ≥ `open`, ≥ `low`, ≥ `close` |
| `low` | `float64` | yes | > 0, ≤ `open`, ≤ `high`, ≤ `close` |
| `close` | `float64` | yes | > 0 |
| `volume` | `int64` | yes | ≥ 0 |
| `value` | `float64` | optional | ≥ 0, NTD turnover |
| `fetched_at` | `datetime64[ns]` | optional | audit timestamp; set on write |

Index: none; rely on `(ticker, date)` unique check at the storage layer.

**`CORPORATE_ACTIONS_SCHEMA`** — sparse event table:

| Column | Dtype | Required | Checks |
|---|---|---|---|
| `ex_date` | `datetime64[ns]` | yes | not null |
| `ticker` | `string` | yes | regex `^\d{4}$` |
| `action_type` | `string` | yes | isin `{cash_dividend, stock_dividend, split, rights_issue}` |
| `cash_amount` | `float64` | conditional | required if `action_type == cash_dividend`; ≥ 0 |
| `ratio` | `float64` | conditional | required for `split` and `stock_dividend`; > 0 |

Both schemas exported as `pandera.DataFrameSchema` objects. Provide a small helper `validate(df, schema)` that wraps `.validate()` with a useful error message.

### 4.2 `data/storage.py`

Public API only (everything else is private):

```python
def write_ohlcv(ticker: str, df: pd.DataFrame) -> None:
    """Validate against OHLCV_SCHEMA, merge with existing data by (ticker, date)
    keeping latest, write atomically."""

def read_ohlcv(
    ticker: str,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Returns raw OHLCV for ticker over [start, end]. Empty DataFrame if none."""

def write_corporate_actions(ticker: str, df: pd.DataFrame) -> None: ...
def read_corporate_actions(ticker: str) -> pd.DataFrame: ...

def list_stored_tickers() -> list[str]: ...
def get_ticker_date_range(ticker: str) -> tuple[date, date] | None: ...

def query(sql: str) -> pd.DataFrame:
    """Run DuckDB SQL against the parquet store. Views available:
    - `ohlcv` (all tickers, all dates)
    - `corporate_actions`
    """
```

Implementation notes:
- Atomic writes: write to `{ticker}.parquet.tmp`, then rename
- Merge semantics on write: read existing → concat → `drop_duplicates(['ticker','date'], keep='last')` → sort → write. Never blindly append.
- `query()` opens a DuckDB connection and registers parquet glob `data/by_ticker/*.parquet` as view `ohlcv`. Connection can be cached per-process.
- Paths: build from `config.DATA_DIR`, always `pathlib.Path`.

### 4.3 `data/sources/base.py`

Refine the existing abstract class:

```python
class DataSource(ABC):
    @abstractmethod
    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        """Return raw OHLCV conforming to OHLCV_SCHEMA. May raise
        RateLimitError, SourceUnavailableError, or NoDataError."""

    @abstractmethod
    def get_corporate_actions(
        self, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        """Return corporate actions conforming to CORPORATE_ACTIONS_SCHEMA."""

    @abstractmethod
    def list_universe(self) -> list[str]:
        """Return source's view of the trading universe (sanity check vs
        hard-coded Top 50)."""
```

Define source-specific exceptions in the same file: `SourceError`, `RateLimitError`, `SourceUnavailableError`, `NoDataError`.

### 4.4 `data/sources/finmind.py`

Implement `FinMindSource(DataSource)`:
- Token from `os.environ["FINMIND_TOKEN"]`, loaded via `python-dotenv` at module level
- Use `requests` (no SDK dependency) — keeps it inspectable for VCR
- Translate FinMind's column names to our schema in this layer; downstream sees only our schema
- Handle FinMind error responses: 402 → `RateLimitError`, 5xx → `SourceUnavailableError` with retry-after info, empty result → `NoDataError`
- Built-in throttle: minimum interval between calls (configurable, default 200ms), enforced via simple `time.sleep`

### 4.5 `data/sources/twse.py`

Minimal `TwseSource(DataSource)`:
- Implement `get_daily_ohlcv` only (fallback for FinMind gaps)
- `get_corporate_actions` may raise `NotImplementedError` for now — flag in §10 as known gap
- `list_universe` returns the hard-coded Top 50 from `universe.py` (TWSE doesn't have a clean "Top 50" endpoint)

### 4.6 `tests/fixtures/anchor_dividends.json`

Hand-curated cross-check anchors. Each anchor exercises the adjustment
function's behaviour at one known corporate-action event. Schema per
anchor entry:

| Field | Type | Source |
|---|---|---|
| `ticker` | string (4-digit) | hard-coded |
| `ex_date` | YYYY-MM-DD | primary announcement |
| `action_type` | `cash_dividend` / `stock_dividend` / `split` / `rights_issue` | classification |
| `dividend_amount` | float, NT$ per share | TSMC SEC 6-K + TSMC IR (cash) |
| `raw_close_t_minus_1` | float | TWSE `exchangeReport/STOCK_DAY` |
| `raw_close_t` | float | TWSE `exchangeReport/STOCK_DAY` |
| `expected_adj_close_ratio` | float | precomputed: `raw_close_t_minus_1 / (raw_close_t + dividend_amount)` for cash; this is exactly the ratio the test asserts the user's adjusted-close series exhibits across the event (not the textbook back-adjustment factor `Y / (Y + D)`, which is a different number). Analogous formula per event type. |
| `source_dividend` | string | citation for the dividend figure |
| `source_prices` | string | always `"TWSE STOCK_DAY official endpoint"` for fixture v1 |
| `notes` | string | optional |

The fixture is built by `scripts/build_anchors.py` from a hard-coded
table of verified dividend events plus live TWSE queries. Re-run the
script only when the dividend reference table is edited; the JSON is
committed and is the source of truth at test time.

**Test contract** (`tests/data/test_api.py::test_adjustment_anchors`):
for each anchor, the user's adjusted-close series from
`get_adjusted_history(ticker, ..., end=today)` must satisfy:

```python
adj_close[ex_date - 1 trading day] / adj_close[ex_date]  \
    ≈  raw_close_t_minus_1 / (raw_close_t + dividend_amount)
```

with relative tolerance 0.01%. The data-fidelity sub-assertion
(user's raw_close from FinMind matches TWSE's raw_close within 0.1%)
runs as a separate test so failures can be distinguished.

Initial coverage: 6 TSMC cash-dividend events spanning 2018–2024.
Stock-dividend / split / rights-issue coverage is a Phase 1 followup
(see `journal/phase_1_followups.md`).

### 4.7 `data/adjustments.py`

One pure function:

```python
def adjust(
    raw_ohlcv: pd.DataFrame,
    corporate_actions: pd.DataFrame,
) -> pd.DataFrame:
    """Apply back-adjustment for cash dividends, stock dividends, and splits.
    Returns a DataFrame with the same shape as raw_ohlcv, with open/high/low/close
    adjusted and volume left unchanged (volume adjustment is a Phase 9+ concern).

    Pure: same inputs → same outputs. No I/O."""
```

Algorithm: standard back-adjustment using cumulative adjustment factor. For each action in chronological order, compute factor and multiply all *prior* bars' prices by it. Reference: standard 還原權息 (back-adjustment) convention used by TWSE brokers.

**Cross-validation requirement:** `test_adjustment_anchors` in `tests/data/test_api.py` asserts the adjusted-close ratio across each event in `tests/fixtures/anchor_dividends.json` matches the expected back-adjustment factor within 0.01%. See §4.6 for the fixture schema and `journal/decisions/2026-05-13-adjustment-cross-check-method.md` for rationale. The fixture is regenerated only by running `scripts/build_anchors.py` deliberately.

### 4.8 `data/quality.py`

Pure functions taking DataFrames in, returning a report:

```python
@dataclass
class DQReport:
    ticker: str
    n_rows: int
    date_range: tuple[date, date]
    missing_trading_days: list[date]   # vs exchange_calendars XTAI
    duplicate_keys: int
    zero_volume_days: int              # potential suspensions
    schema_violations: list[str]       # human-readable
    ok: bool                            # True iff no critical issues

def check_ticker(ticker: str) -> DQReport: ...
def check_all() -> list[DQReport]: ...
```

"Critical issues" that flip `ok` to False:
- Any schema violation
- Any duplicate `(ticker, date)`
- More than 5% of expected trading days missing (after accounting for listing date)

Pure missing-day count or zero-volume day count alone is **not** critical — these are reported but don't fail the check.

### 4.9 `data/api.py`

The public entry point. This is what every other phase imports from.

```python
def get_adjusted_history(
    ticker: str,
    start: date | str,
    end: date | str | None = None,
) -> pd.DataFrame:
    """Returns adjusted OHLCV for ticker over [start, end] (end defaults to today).
    Combines raw OHLCV + corporate actions, applies adjustment, returns clean
    DataFrame with date index. Cached in-process."""

def get_universe() -> list[str]:
    """Returns the active universe (Top 50). Pass-through to universe.py for now."""
```

In-process caching: simple `functools.lru_cache` keyed on `(ticker, start, end)`. Bust manually if needed (Phase 8 concern).

### 4.10 `scripts/backfill.py`

See §7 for the full spec — too important to bury inline here.

---

## 5. Build sequence

Build in this order. Each step has its own commit and tests.

1. **Day 1 — FinMind spike (no code committed yet).** See §8.
2. `data/schemas.py` + `tests/data/test_schemas.py`. Pure pandera, fastest feedback loop.
3. `data/storage.py` (write/read OHLCV only, no DuckDB yet) + tests with hand-made DataFrames. No source yet.
4. `data/adjustments.py` + tests against hand-made action scenarios + anchor cross-check fixture (§4.6).
5. `data/sources/base.py` refinement (exceptions, signatures locked).
6. `data/sources/finmind.py` + cassette tests + 1 integration test (marked).
7. `data/sources/twse.py` (minimal) + tests.
8. Storage: add corporate_actions read/write + DuckDB `query()`.
9. `data/quality.py` + tests.
10. `data/api.py` + tests.
11. `scripts/backfill.py` (see §7) + tests.
12. Run full backfill. Run `check_all()`. Iterate until clean.
13. Write `journal/phase_1_retro.md`. Update PROJECT_PLAN.md progress tracker.

---

## 6. Test & fixture strategy (DETAILED)

Three categories, each with its own discipline.

### 6.1 Pure logic unit tests

**What:** anything that takes DataFrame(s) in and produces DataFrame(s) or values out, with no I/O.

**Covers:** schema validation, storage merge semantics (mocking the disk), adjustment math, DQ checks, API-layer logic.

**Fixture style:** constructed inline in the test, or loaded from small hand-written CSV files in `tests/fixtures/`.

**Examples of fixtures to author:**
- `tests/fixtures/2330_2023_clean.csv` — 20 rows of clean 2330 data
- `tests/fixtures/2330_2023_with_split.csv` — same window with an injected 2:1 split
- `tests/fixtures/2330_actions_2020_2024.csv` — known dividends and splits
- `tests/fixtures/anchor_dividends.json` — hand-curated anchor events for adjustment cross-validation. **Source:** TSMC SEC 6-K filings (dividend amounts) + TWSE STOCK_DAY endpoint (raw closes). Regenerated only via `scripts/build_anchors.py`; see §4.6.
- `tests/fixtures/malformed_ohlcv.csv` — schema violations, for negative tests

**Rule:** fixtures stay small (< 100 rows). If you need bigger, you're testing the wrong thing.

**Speed target:** the entire unit suite runs in < 5 seconds.

### 6.2 Source-layer cassette tests

**What:** verify that our source adapters correctly parse what the real API actually returns.

**Tool:** `pytest-recording` (the maintained vcrpy wrapper). One cassette per representative scenario.

**Cassettes to record (canonical, checked into git):**
- `tests/cassettes/finmind_2330_normal.yaml` — 30 days of 2330, no special events
- `tests/cassettes/finmind_2330_with_dividend.yaml` — date range spanning a 2330 ex-dividend day
- `tests/cassettes/finmind_recent_listing.yaml` — a ticker with short history (pick one identified during the §8 spike)
- `tests/cassettes/finmind_rate_limit.yaml` — a recorded 402 response (may need to record while actually rate-limited)
- `tests/cassettes/finmind_corporate_actions_2330.yaml` — corporate actions endpoint for 2330 over 5 years

**Filtering required:** add a `conftest.py` rule that strips `FINMIND_TOKEN` from request headers and any URL params before saving. Verify by opening cassette files and grepping for the token.

**Recording mode:** `record_mode="none"` in CI / pre-commit (never re-record automatically). Re-record manually with `--record-mode=rewrite` and review the diff before committing.

**Cassettes are gitignored by default**, *except* the canonical list above which is force-added. This prevents accidental cassette sprawl from local experiments.

### 6.3 Integration tests

**What:** end-to-end flows that hit the real FinMind API.

**Marker:** `@pytest.mark.integration`. Excluded from default pytest run. Run via `uv run pytest -m integration`.

**Not in pre-commit.** Not in any quick feedback loop.

**Examples:**
- `test_finmind_live.py::test_can_fetch_2330_last_30_days` — sanity check that FinMind is reachable and token works
- `test_finmind_live.py::test_full_backfill_one_ticker` — backfill 2330 end-to-end

**Skip behavior:** if `FINMIND_TOKEN` not set, skip with a clear message rather than fail.

### 6.4 Conftest plumbing

Single `tests/conftest.py` with:
- Path to `tests/fixtures/` exposed as a fixture
- VCR config (token filtering, cassette path resolution)
- Marker registration: `integration` (so pytest doesn't warn about unknown markers)
- Default `record_mode="none"` for VCR
- `--run-integration` CLI flag that, when absent, deselects integration tests (belt and suspenders alongside the marker)

---

## 7. Backfill script (DETAILED)

### 7.1 CLI

```
uv run python scripts/backfill.py [OPTIONS]

Options:
  --tickers TEXT       Comma-separated ticker list. Default: all Top 50.
  --start DATE         Start date (YYYY-MM-DD). Default: today - 10 years.
  --end DATE           End date (YYYY-MM-DD). Default: today.
  --throttle-ms INT    Min interval between API calls in ms. Default: 200.
  --force              Re-fetch even if data already present and current.
  --source TEXT        'finmind' (default) or 'twse'.
  --skip-actions       Skip corporate actions fetch (debug only).
  --log-dir PATH       Where to write JSONL run log. Default: ./logs/
  -v, --verbose        Verbose stdout.
```

### 7.2 Semantics

**Idempotent.** Re-running with the same args is safe. Existing data is merged, not duplicated. The merge guarantee lives in `storage.write_ohlcv`, so the script doesn't need to think about it.

**Resumable.** Before fetching each ticker, check `storage.get_ticker_date_range(ticker)`. If existing range already covers requested range, skip (log as `skipped_current`) unless `--force`. If existing range partially covers, fetch only the missing window.

**Per-ticker error isolation.** Each ticker's fetch is wrapped in try/except. Failures are logged with full exception info but do not abort the run. Final summary reports failures.

**Throttling.** Sleep `throttle_ms` between API calls (both OHLCV and corporate actions count). On `RateLimitError`, exponential backoff: 5s, 30s, 5min, then give up on this ticker.

**Progress.** `tqdm` bar over tickers. Per-ticker: print one summary line at completion (or use logging at INFO).

### 7.3 Run log format

One JSONL file per run at `logs/backfill_{YYYYMMDD_HHMMSS}.jsonl`. One line per ticker-attempt:

```json
{"ts": "2026-05-11T14:23:01Z", "ticker": "2330", "status": "ok",
 "rows_written": 2487, "actions_written": 23,
 "fetch_window": ["2016-05-11", "2026-05-10"], "duration_ms": 1820}
```

Statuses: `ok`, `skipped_current`, `partial_ok` (some range, not all), `failed`, `rate_limited`.

After the loop, a final summary line:
```json
{"ts": "...", "summary": true, "ok": 47, "skipped": 2, "partial": 0,
 "failed": 1, "rate_limited": 0, "total_duration_s": 142}
```

And the same summary printed to stdout in human form.

### 7.4 Exit code

`0` if no `failed` or `rate_limited` tickers. `1` otherwise. Useful for CI / cron in Phase 8.

### 7.5 Testing

- Unit tests with the source mocked: verify resume logic (existing data → skip), force flag, partial-window detection, error isolation.
- One integration test (marked) that runs full backfill for a 2-ticker subset over 1 year of data.

---

## 8. Day 1 spike: validate FinMind corporate actions

**Before writing any production code,** run a one-day investigation:

1. Register for FinMind, obtain token, add to `.env`.
2. Hit FinMind's corporate-actions endpoints for 2330 over 2016–2026. Manually inspect: what fields are returned, are cash dividends and stock dividends both covered, are splits represented (Taiwan splits are rare — most adjustments are dividends), is there backfill coverage that deep?
3. Cross-check against TWSE's published dividend history for 2330.
4. Repeat for 1 small-cap Top 50 ticker (e.g. one with thinner coverage).
5. Write findings to `journal/decisions/2026-05-XX-finmind-corp-actions.md`.

**Decision gates from the spike:**
- If FinMind corporate actions coverage is solid → proceed as planned.
- If it's spotty for older data → expand `TwseSource.get_corporate_actions` scope, allocate extra week.
- If it's broken entirely → escalate before continuing; this changes the phase scope materially.

This spike is in scope for Phase 1 itself — not Phase 0 cleanup.

---

## 9. Exit criteria

Phase 1 is done when **all** of the following hold:

- [ ] `get_adjusted_history("2330", "2016-01-01", "2026-05-10")` returns a clean adjusted DataFrame in < 1 second (warm cache; cold can be slower)
- [ ] Adjustment cross-check: `test_adjustment_anchors` passes for all anchor events in `tests/fixtures/anchor_dividends.json` (tolerance 0.01% on the back-adjustment ratio; data-fidelity sub-assertion 0.1% on raw close match against TWSE)
- [ ] Phase 1 followups file lists at least one stock-dividend / split coverage gap to address before Phase 2
- [ ] Backfill script runs end-to-end on all 50 tickers without manual intervention; final exit code is 0
- [ ] `quality.check_all()` reports `ok=True` for at least 48 of 50 tickers; any exceptions are documented in the journal with explanation
- [ ] Unit test suite runs in < 5 seconds and passes
- [ ] Cassette tests run offline and pass
- [ ] Integration tests pass when run manually with token
- [ ] Pre-commit clean on a fresh clone
- [ ] All decisions D1–D8 plus any new ones recorded in `journal/decisions/`
- [ ] `journal/phase_1_retro.md` written with honest assessment
- [ ] PROJECT_PLAN.md progress tracker updated

---

## 10. Pitfalls and known gaps

**Tickers are strings.** `"0050"` not `50`. Everywhere. Schema enforces, but be vigilant in argparse/CSV ingestion.

**Listing-date handling.** Some Top 50 tickers may not have 10 years of history (e.g. 3711 ASE Tech listed in 2018). DQ checks must compute "missing trading days" relative to `min(date)` of the ticker, not the universe-wide start.

**Adjustment math is the #1 retail backtest bug.** The anchor cross-validation test is non-negotiable. The 0.01% tolerance is for float-precision drift only; the math itself must be exact. The data-fidelity sub-assertion's looser 0.1% tolerance accounts for the rare case where TWSE's published close and FinMind's stored close differ at the cent level due to ingest-time rounding. Treat any anchor failure as a release-blocker bug.

**FinMind free tier rate limits.** Documented in spike. Throttle aggressively; the backfill is a one-time cost.

**Survivorship bias.** Universe is fixed snapshot. Acknowledged. Re-state in `journal/phase_1_retro.md`.

**TwseSource corporate actions stub.** Will raise `NotImplementedError`. If the spike reveals FinMind gaps, this becomes Phase 1 scope. Otherwise deferred to Phase 9.

**Volume adjustment.** Not done. Split-adjusting volume is a Phase 9+ concern; for now, treat volume as raw. Adjustment function docstring states this.

**Trading-calendar edge cases.** Taiwan has typhoon closures and ad-hoc holidays. `exchange_calendars` covers the published calendar; ad-hoc closures may show as "missing trading days" in DQ. Acceptable; flag in retro.

---

## 11. Out of scope (explicit)

To prevent scope creep, the following are **not** Phase 1:

- Intraday data
- Tickers outside the hard-coded Top 50
- Real-time data (Phase 5)
- Fundamentals (revenue, EPS, etc.)
- Alternative data (Phase 7)
- Any indicators or strategies (Phase 2/4)
- Dashboards (Phase 2)
- Forward-adjusted prices (only back-adjustment is in scope)
- Volume adjustment

---

## 12. New dependencies (rationale required in journal)

| Package | Why |
|---|---|
| `pandera` | Schema validation at boundaries (decision D5) |
| `pyarrow` | Parquet I/O |
| `duckdb` | SQL queries across parquet store |
| `requests` | FinMind HTTP calls (no SDK; keeps it VCR-friendly) |
| `python-dotenv` | Load `FINMIND_TOKEN` from `.env` |
| `exchange_calendars` | Trading-day calendar (decision D8) |
| `pytest-recording` | VCR cassette tests (decision D6) |
| `tqdm` | Backfill progress bar |

Add each to `pyproject.toml` with `uv add` and record a one-line justification in `journal/dependencies.md`.

---

## 13. Kickoff checklist

Before pasting this into Claude Code, confirm:

- [ ] FinMind account registered, token in local `.env`, token NOT committed
- [ ] Spike (§8) completed, findings recorded
- [ ] `scripts/build_anchors.py` runs cleanly and produces `tests/fixtures/anchor_dividends.json` with 6 populated TSMC anchors (per §4.6)
- [ ] You have ~2 uninterrupted weeks of bandwidth (or willingness to slip)
- [ ] Phase 0 conventions are clear in `CLAUDE.md` and will be respected
