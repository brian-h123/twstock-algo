# Phase 2 — Inspection Dashboard

> **Purpose.** A Streamlit app that lets a human visually sanity-check the Phase 1 data layer. The dashboard exists so that we can trust the data — not so it can be shown to anyone. Build the minimum needed to verify adjustments, ex-dates, and basic overlays; resist every temptation to add a fourth feature.

**Status:** Ready to start (spec finalized)
**Last updated:** 2026-05-20
**Estimated duration:** 1–2 weeks (hard cap; pull stretch items to Phase 8 tickets)
**Predecessor:** Phase 1 (complete)

---

## 1. Goal

After Phase 2, the following command launches an interactive dashboard where any Top 50 ticker can be inspected end-to-end in under two seconds:

```bash
uv run streamlit run src/twstock/ui/app.py
```

Specifically, the dashboard must let a human (a) pull up any Top 50 ticker's adjusted candlestick chart with SMA/Bollinger overlays, (b) toggle to raw prices and visually confirm that the adjustment math closed the dividend gap, and (c) see ex-dates as vertical markers with hover detail. Nothing more.

---

## 2. Locked Decisions

These are settled. Do not relitigate without an ADR.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Indicators computed on-the-fly**, wrapped in `@st.cache_data`. No pre-computed Parquet indicator layer. | SMA on 2.5k rows is <10 ms; Phase 3 will build a proper indicator module from scratch. Pre-computing now creates a layer Phase 3 has to delete. |
| D2 | **Single-ticker view only.** No multi-ticker comparison in this phase. | "Low hiring signal" rule from PROJECT_PLAN.md §3 Phase 2. Pulled to a Phase 8 ticket. |
| D3 | **Adjusted/raw price toggle.** Default to adjusted. | Directly visually validates the headline Phase 1 deliverable. Two-line cost in the loader. |
| D4 | **Corporate-action markers on chart**, default on. Hover shows action type and amount. | The single most useful sanity-check feature in the phase. `read_corporate_actions` is already available. |
| D5 | **Ticker name mapping via hard-coded `dict[str, str]`** in `src/twstock/ui/names.py`. | 50 entries, sourced once from TWSE, then frozen. No network dependency, no init cost, robust forever. |
| D6 | **Conventional Commits** with mandatory type and recommended scope. Body format documented in `CLAUDE.md`. | Phase 1 retro: all commits defaulted to `feat:`, body format drifted between paragraph and bullet style. See `CLAUDE.md §{commit-convention}` for the full spec. |
| D7 | **`ui/` import boundary.** `src/twstock/ui/*` may import only from `twstock.data.api`, `twstock.data.storage`, `twstock.universe`, and standard third-party libraries (streamlit, plotly, pandas, etc.). It must not reach into `data.sources`, `data.adjustments`, `data.quality`, or any future strategy/backtest module. | Keeps the dashboard a thin presentation layer. Prevents the UI from becoming a god-module in later phases. Enforced by code review during Phase 2; an `import-linter` config is a Phase 8 ticket. |

---

## 3. Deliverables (modules)

```
src/twstock/ui/
├── __init__.py
├── app.py              # Streamlit entry point: page layout, widgets, orchestration (NEW)
├── data_loader.py      # @st.cache_data wrappers around twstock.data.api/storage (NEW)
├── charts.py           # Pure Plotly figure builders + small overlay helpers (NEW)
└── names.py            # TICKER_NAMES: dict[str, str] for the Top 50 (NEW)

tests/ui/
├── __init__.py
├── test_names.py       # Coverage of all 50 tickers
├── test_data_loader.py # Cached loaders with underlying api mocked
├── test_charts.py      # Pure functions: SMA / Bollinger / figure-trace counts
└── test_smoke.py       # `import twstock.ui.app` succeeds with no side effects
```

**Total: 4 source files + 4 test files.** Nothing else in `src/twstock/` is modified.

`CLAUDE.md` is updated once during Phase 2 (commit convention spec, `ui/` import rule, and the new module list). `README.md` gets a short "Run the dashboard" section at the end of the phase.

---

## 4. Module specifications

### 4.1 `ui/names.py`

Pure data. No logic beyond the lookup helper.

```python
TICKER_NAMES: dict[str, str] = {
    "2330": "台積電",
    "2317": "鴻海",
    # ... all 50 entries
}

def display_label(ticker: str) -> str:
    """Return 'ticker — name' for dropdown display.
    Falls back to ticker alone if not in TICKER_NAMES (defensive)."""
```

**Source for the names:** TWSE listed-company page, copied once into the dict. Traditional Chinese, short form (台積電, not 台灣積體電路製造股份有限公司). If a ticker rotates out of the universe in a later phase, leave the stale entry in place rather than thrashing the dict.

### 4.2 `ui/data_loader.py`

Caching layer between Streamlit and the Phase 1 API. Every function `@st.cache_data` decorated. No business logic — these are one-line wrappers whose only purpose is to attach Streamlit's session-level cache to the existing API.

```python
@st.cache_data(show_spinner=False)
def load_adjusted(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Wraps twstock.data.api.get_adjusted_history."""

@st.cache_data(show_spinner=False)
def load_raw(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Wraps twstock.data.storage.read_ohlcv."""

@st.cache_data(show_spinner=False)
def load_corporate_actions(ticker: str) -> pd.DataFrame:
    """Wraps twstock.data.storage.read_corporate_actions."""

@st.cache_data(show_spinner=False)
def get_available_range(ticker: str) -> tuple[date, date]:
    """Wraps twstock.data.storage.get_ticker_date_range. Used for
    date-picker clamping on short-history tickers (3711, 6669, 6919, 7769)."""
```

Implementation notes:
- `start` and `end` are `date`, not `datetime`. The caller (app.py) normalizes before invoking. Mixing the two causes cache-key drift across reruns.
- Never mutate a cached return value. `@st.cache_data` reuses the returned object across reruns; mutation corrupts the cache.
- `ttl=None` (session lifetime). Local Parquet only changes when the user manually re-runs the backfill, which already requires restarting Streamlit.

### 4.3 `ui/charts.py`

Pure functions taking DataFrames in, returning Plotly `Figure` objects or `Series`. **No streamlit import in this module.** This is the only Phase 2 module with non-trivial logic, and therefore the only one with substantive unit tests.

```python
def compute_sma(close: pd.Series, window: int) -> pd.Series: ...

def compute_bollinger(
    close: pd.Series, window: int = 20, k: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (lower_band, middle_band, upper_band)."""

def candlestick_volume(
    df: pd.DataFrame,
    overlays: dict[str, pd.Series] | None = None,
    ex_date_markers: pd.DataFrame | None = None,
    title: str = "",
) -> go.Figure:
    """Single Plotly figure with 2 stacked subplots (price 70%, volume 30%),
    shared x-axis. Candlestick uses Taiwan-convention colors (red up,
    green down). Overlays are line traces on the price subplot. Ex-date
    markers are vertical lines with hover annotations."""
```

Implementation notes:
- **Taiwan candlestick convention:** red for up days, green for down days (opposite of US convention). Set `increasing_line_color="red"` and `decreasing_line_color="green"`.
- **Range slider off:** `fig.update_xaxes(rangeslider_visible=False)`. Plotly's default rangeslider triples render time on 10y of bars.
- **Ex-date marker hover format:** `"{ex_date} · {action_type} · cash={cash_amount}, stock_ratio={stock_ratio}, ..."` — show whichever fields are populated for that action_type.
- `compute_sma` and `compute_bollinger` are **intentionally throwaway**: they live in `ui/charts.py` and Phase 3 will build a proper indicator module from scratch. Do not move them to `src/twstock/indicators.py` or anywhere else. See ADR (§9).

### 4.4 `ui/app.py`

The Streamlit page. Sidebar for controls, main area for the info panel and chart. Top-to-bottom rerun on every widget change — **no callbacks**, no `st.fragment`, no `st.experimental_*`. Target: readable end-to-end in under 100 lines.

**Sidebar layout, top to bottom:**

1. **Ticker selectbox.** Options = `[display_label(t) for t in get_top50_universe()]`. Default index: position of `"2330"`.
2. **Date range pickers.** Two `st.date_input` widgets keyed `"start_date"` and `"end_date"`. On every rerun, clamp the persisted values to the current ticker's `get_available_range`. Defaults: max(available_start, today − 2 years) to today.
3. **Price view radio.** `["Adjusted", "Raw"]`, default `"Adjusted"`. Switches the loader call between `load_adjusted` and `load_raw`.
4. **Overlay checkboxes.** SMA(20) ✓, SMA(60) ✓, Bollinger Bands ✗, Ex-date markers ✓.

**Main area, top to bottom:**

1. **Info panel.** `st.columns(4)` with `st.metric` for: name (from `TICKER_NAMES`), last close, daily change (close vs previous close as both NTD and %), 52-week range (min/max close over trailing 252 trading days or available history). NaN-safe: render `"—"` when computation isn't possible.
2. **Candlestick + volume chart.** Output of `candlestick_volume(...)`, rendered with `st.plotly_chart(fig, use_container_width=True)`.

**The orchestration logic** of app.py is small enough to enumerate:

1. Read all widget state.
2. Normalize `start`/`end` to `date` (not `datetime`).
3. Call `load_adjusted` or `load_raw` based on radio.
4. Compute requested overlays via `charts.compute_sma` / `charts.compute_bollinger`.
5. If ex-date marker checkbox is on, call `load_corporate_actions` and filter to the visible date range.
6. Build the figure via `charts.candlestick_volume`.
7. Render the info panel and the chart.

---

## 5. Build sequence

Build in this order. Each step is a single commit following the convention in `CLAUDE.md`.

1. **`chore: scaffold ui module`** — `uv add streamlit plotly`. Create empty `src/twstock/ui/{__init__,app}.py` (app.py prints a placeholder), `tests/ui/{__init__,test_smoke}.py`. Verify `uv run streamlit run src/twstock/ui/app.py` launches and `pytest tests/ui` passes.
2. **`feat(ui): add ticker name dictionary`** — `names.py` with all 50 entries + `display_label`. `test_names.py` asserts coverage of `get_top50_universe()` and non-empty values.
3. **`feat(ui): add cached data loader`** — `data_loader.py` with the four wrappers. `test_data_loader.py` mocks the underlying `twstock.data.api` / `twstock.data.storage` calls; no Parquet reads in unit tests.
4. **`feat(ui): add chart builders`** — `charts.py` with `compute_sma`, `compute_bollinger`, and `candlestick_volume` (no overlays, no markers yet). `test_charts.py` covers the two compute functions against hand-computed values and asserts trace counts on the figure.
5. **`feat(ui): wire ticker and date range controls`** — sidebar with ticker selectbox and date range pickers (with clamping). No chart yet — verify state flows correctly and short-history tickers (6919, 7769) clamp without error.
6. **`feat(ui): render candlestick chart`** — connect chart to sidebar state, adjusted prices only, no overlays. Main-area output replaces the placeholder.
7. **`feat(ui): add raw/adjusted price toggle`** — radio control wired to loader selection. Manual verification: a known ex-dividend date (e.g. 2330 2024-07-18) shows a visible gap in raw, no gap in adjusted.
8. **`feat(ui): add SMA and Bollinger overlays`** — overlay checkboxes plumbed through to chart traces.
9. **`feat(ui): add info panel`** — `st.metric` strip above the chart.
10. **`feat(ui): add corporate action markers`** — vertical lines + hover annotations at ex-dates, gated by the checkbox.
11. **`perf(ui): verify <2s warm render`** *(only if perf adjustments are needed)* — spot-check 5 tickers; if any exceed 2s warm, document the bottleneck and either fix or relax the criterion in this commit's message.
12. **`docs: phase 2 retrospective and CLAUDE.md update`** — write `journal/phase_2_retro.md`, update `CLAUDE.md` §4 with the new module list, update `PROJECT_PLAN.md` progress tracker.

**Slip-handling rule.** If step 8 isn't complete by the end of week 1, ship without SMA(60) and Bollinger. The hard exit criterion is SMA(20); the rest are nice-to-have. Phase 2 ends; the missing overlays become Phase 8 tickets.

---

## 6. Test strategy

Phase 1 set the bar at 111 tests in 3.5s. Phase 2 should add ~12–18 unit tests and stay under 5 seconds total. **No Streamlit rendering tests, no `streamlit testing` framework, no Selenium, no Playwright.**

### 6.1 What gets tested

| File | What it covers |
|---|---|
| `test_names.py` | (1) every ticker in `get_top50_universe()` has a `TICKER_NAMES` entry; (2) all values are non-empty strings; (3) `display_label("9999")` returns `"9999"` without raising. |
| `test_data_loader.py` | Each loader returns a DataFrame with the expected columns. Underlying `twstock.data.api` / `twstock.data.storage` calls are mocked — no Parquet I/O. Verify repeated calls with identical args go through the cache (or at least don't re-call the mock unexpectedly). |
| `test_charts.py` | (1) `compute_sma(s, 5)` matches a hand-computed reference Series on a 20-row input; (2) `compute_bollinger(s, 20, 2.0)` returns three Series with the middle-band exactly equal to SMA(20) and the outer bands ±2σ; (3) `candlestick_volume` returns a `go.Figure` with the expected number of traces and subplots given various overlay combinations; (4) figure construction handles empty `ex_date_markers` without error. |
| `test_smoke.py` | `import twstock.ui.app` succeeds. No top-level side effects: no Streamlit calls fire at import time. (Use `importlib` plus a check that `st.set_page_config` etc. is inside `if __name__ == "__main__":` or guarded otherwise.) |

### 6.2 What is explicitly NOT tested

- Streamlit widget interaction or page rendering.
- Plotly visual output (pixel-level or otherwise).
- Performance — the <2s exit criterion is verified manually, not in CI.
- The corporate-action marker hover text content — verified by eye.

### 6.3 Fixture strategy

Hand-write small DataFrames inline in each test (10–50 rows). No new files in `tests/fixtures/`. Phase 1's anchor fixtures are for the data layer; Phase 2 tests are about presentation logic and should not depend on the data layer's fixtures.

### 6.4 Pre-commit and lint

Existing `ruff` + `pytest` pre-commit hooks apply unchanged. `tests/ui/` is picked up automatically by the existing pytest config. No new pytest markers needed.

---

## 7. Streamlit caching & state (DETAILED)

The two non-obvious parts of this phase. Both are about Streamlit's rerun-everything-from-the-top model.

### 7.1 Caching with `@st.cache_data`

- All four `data_loader.py` functions are decorated. Cache key is the tuple of positional and keyword arguments. **Therefore arguments must be hashable and stable across reruns** — `date` (not `datetime.now()`), `str` (not coerced ints), `int` (not numpy scalars).
- `show_spinner=False` on every loader. The spinners overlap badly when multiple loaders run on the same rerun.
- `ttl=None` and `max_entries=None`. Cache is session-bound and rebuilt on every fresh `streamlit run`. Phase 1's `lru_cache` in `data.api.get_adjusted_history` provides a second tier inside the same process.
- **Never mutate a cached return value.** `df = load_adjusted(...).copy()` if you intend to modify. Direct mutation corrupts the cache for subsequent reruns within the session.

### 7.2 Session state and date-range clamping

Streamlit's `st.date_input` persists its value across reruns by default, keyed on its widget key. When the user switches from 2330 (10 years of history) to 6919 (listed 2024-10), the persisted start date is still 2016, which is invalid for 6919 and produces an empty DataFrame.

Pattern:

```python
ticker = st.sidebar.selectbox("Ticker", ..., key="ticker")
avail_start, avail_end = get_available_range(ticker)

# Clamp any persisted state to the new ticker's available range.
# Done BEFORE rendering the date inputs.
if "start_date" in st.session_state:
    st.session_state.start_date = min(
        max(st.session_state.start_date, avail_start), avail_end,
    )
if "end_date" in st.session_state:
    st.session_state.end_date = min(
        max(st.session_state.end_date, avail_start), avail_end,
    )

start = st.sidebar.date_input(
    "Start", value=max(avail_start, avail_end - timedelta(days=730)),
    min_value=avail_start, max_value=avail_end, key="start_date",
)
end = st.sidebar.date_input(
    "End", value=avail_end,
    min_value=avail_start, max_value=avail_end, key="end_date",
)
```

The `min_value` / `max_value` arguments give the user visual feedback (grayed-out unavailable dates in the calendar), while the explicit session-state clamping handles the cross-ticker switch.

This is the only place `st.session_state` is used. Everything else is ephemeral — the rerun reads widget return values directly.

### 7.3 No callbacks, no fragments

`st.fragment`, `on_change` callbacks, and `st.rerun()` are all out of scope. The page is small enough that a full rerun on every change is imperceptible after the first warm load.

### 7.4 Performance budget

| Stage | Budget |
|---|---|
| Cold first render (fresh `streamlit run`) | ≤5 s |
| Warm rerun (cached data, widget change only) | ≤2 s |
| Ticker switch (cache miss on new ticker) | ≤3 s |

Verified manually by spot-checking 5 tickers across the universe. If any tier blows budget, the first thing to inspect is Plotly rangeslider (off, per §4.3) and the date range (don't default to 10y for first render).

---

## 8. Exit criteria

Phase 2 is done when **all** of the following hold:

- [ ] `uv run streamlit run src/twstock/ui/app.py` launches the dashboard with no errors
- [ ] Any Top 50 ticker renders a candlestick + volume chart with SMA(20) overlay and ex-date markers in **<2 seconds** on a warm rerun
- [ ] Toggling adjusted → raw produces a visible price gap at a known ex-dividend date (e.g. 2330 2024-07-18); toggling back closes the gap
- [ ] Ex-date markers appear at the dates returned by `read_corporate_actions` for at least 3 spot-checked tickers; hover shows action type and amount
- [ ] Info panel shows reasonable values (sanity-checked by eye against the chart) for at least 3 tickers
- [ ] Short-history tickers (6919 listed 2024-10, 7769 listed 2025-11) load without error and the date picker clamps to their available range
- [ ] `uv run pytest tests/ui/` passes; total project test suite still runs in <5s (excluding integration tests)
- [ ] Pre-commit clean on a fresh clone
- [ ] All commits in the Phase 2 range follow the convention in `CLAUDE.md` (`feat:`/`fix:`/`refactor:`/`test:`/`docs:`/`chore:` discrimination, scope where applicable, bullet body when multi-part)
- [ ] One ADR written: `journal/decisions/2026-XX-XX-ui-indicators-are-throwaway.md` records decision D1 explicitly so a future reader doesn't promote `compute_sma` to a shared module
- [ ] `CLAUDE.md` updated with: (a) the commit convention, (b) the `ui/` import boundary (D7), (c) the new module list
- [ ] `README.md` has a "Run the dashboard" section
- [ ] `journal/phase_2_retro.md` written: wins, slips, surprises, decisions to revisit, prep notes for Phase 3
- [ ] `PROJECT_PLAN.md` progress tracker updated (Phase 2 row → ended date)

---

## 9. Pitfalls and known gaps

**Tickers are strings.** `"2330"` not `2330`. The dropdown's display label is `"2330 — 台積電"`; the underlying value passed to loaders must be the 4-digit string. If you build the label/value mapping wrong, debugging is slow because the page just shows empty data.

**`date` vs `datetime` in cache keys.** `@st.cache_data` keys on argument identity. If `start` is `datetime.now()` instead of `date.today()` you'll cache-miss on every rerun and the dashboard will feel slow. Normalize to `date` in `app.py` before calling any loader.

**Short-history tickers will break naive defaults.** 6919 (listed 2024-10-02) and 7769 (listed 2025-11-27) have <1 year of data. A default 10-year date range produces empty DataFrames and a blank chart. The clamping pattern in §7.2 is non-negotiable.

**NaN in the `value` column.** Some rows are missing turnover. The info panel must not propagate NaN into `st.metric` (it'll render as "nan"). Render `"—"` instead. (The info panel doesn't currently display turnover, but defensive handling matters anyway because volume can have zero-fill rows from suspensions.)

**Phase 3 boundary on indicators.** It will be tempting to extract `compute_sma` / `compute_bollinger` into `src/twstock/indicators.py` "since Phase 3 will need them anyway." Don't. Phase 3 will build the indicator module with lookahead-safety helpers, vectorbt integration, and different signatures. Premature extraction creates a worse version of the Phase 3 module that has to be replaced. The ADR in §8 exit criteria locks this in writing.

**Cached mutation.** A subtle Streamlit bug: `df = load_adjusted(...); df["sma_20"] = ...` mutates the cached object. On the next rerun, the loader "returns" a DataFrame that already has `sma_20`. Always `.copy()` if mutating, or use chained ops that return new objects.

**Plotly rangeslider.** Default-on, triples render time on 10y of bars. Explicitly off in `candlestick_volume`.

**Streamlit dev loop.** `streamlit run` doesn't always pick up new module imports cleanly during the build sequence. If something seems missing after a code change, restart the server.

**Cassettes and integration tests are out of scope.** Phase 2 mocks the data layer in unit tests; it does not need new VCR cassettes. The integration tests inherited from Phase 1 continue to run on their existing marker.

---

## 10. Out of scope (explicit)

To prevent scope creep, the following are **not** Phase 2:

- Multi-ticker comparison view
- Normalized return view (rebase to 100)
- Additional overlays beyond SMA(20), SMA(60), Bollinger Bands
- Custom date-range preset buttons (1M / 3M / 1Y / 5Y / Max)
- Export chart as PNG
- URL query params for shareable state
- Dark mode polish
- Mobile-responsive layout
- Real-time / intraday tick updates (Phase 5)
- Multi-page Streamlit app structure
- Indicators or strategies as a reusable module (Phase 3/4)
- Any new module under `src/twstock/` outside `ui/`
- An `import-linter` config enforcing D7 (a Phase 8 ticket; reviewed by hand for now)

If you find yourself building any of these mid-phase, stop, write a one-line ticket in `journal/phase_8_backlog.md`, and move on.

---

## 11. New dependencies

| Package | Why |
|---|---|
| `streamlit` | Frontend framework (decision in PROJECT_PLAN.md §2) |
| `plotly` | Candlestick rendering, interactive charts |

Add each to `pyproject.toml` with `uv add` and record a one-line justification in `journal/dependencies.md`. No transitive change to existing dependencies expected; if any version pins shift, note it in the commit body for step 1 of §5.

---

## 12. Kickoff checklist

Before pasting this into Claude Code, confirm:

- [ ] Phase 1 is fully complete (`PROJECT_PLAN.md §6` shows Phase 1 ended)
- [ ] All 50 tickers' Parquet files are present in `data/by_ticker/` and `data/corporate_actions/`
- [ ] `quality.check_all()` reports `ok=True` for all 50 tickers (re-run if in doubt)
- [ ] `CLAUDE.md` has been updated with: the commit convention spec, the `ui/` import boundary (D7), and a placeholder note that the module list will be refreshed at the end of Phase 2
- [ ] TWSE listed-company names for all 50 universe tickers have been collected (a one-off browser session against the TWSE listing page) — the `names.py` content is ready to paste
- [ ] You have ~1–2 uninterrupted weeks of bandwidth (or willingness to ship without SMA(60) / Bollinger per the slip rule)
- [ ] You have read this spec end-to-end and have no open questions
