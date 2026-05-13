# CLAUDE.md

Instructions for Claude Code when working in this repository. Read this in full at the start of every session, plus the current week's journal entry in `journal/weekly/`.

**Last updated:** 2026-05-13
**Current phase:** Phase 1 — Data Pipeline (full spec: `PHASE_1.md`)

---

## 1. Project Goal

A personal algorithmic trading project for the Taiwan stock market (TWSE), built as a portfolio piece for data science / ML engineering job applications. The project incrementally builds a full pipeline:

1. Data ingestion (FinMind + TWSE)
2. Inspection dashboard (Streamlit)
3. Backtesting engine
4. Technical-indicator strategies
5. Paper-trading simulator + broker integration
6. ML-based strategies
7. Alternative-data strategies (news / NLP)
8. Productionization

Owner is a fresh graduate with <1 year of personal Taiwan stock trading experience. Honesty over impressiveness: strategies that lose to buy-and-hold should be reported as such, not hidden.

## 2. Locked Architectural Decisions

These were decided in the planning workspace and should not be changed without an explicit decision being recorded in `journal/decisions/`.

| Area | Decision |
|---|---|
| Time horizon | Daily only. Intraday is a future Phase 9+ expansion. |
| Universe | TWSE Top 50 (0050 ETF constituents), hard-coded snapshot for now |
| Capital assumption | NT$1,000,000 for backtests; max 10% per position |
| Python | 3.12 |
| Dependency manager | uv |
| Lint/format | ruff |
| Tests | pytest |
| Pre-commit | ruff + pytest smoke test |
| Data in-memory | pandas |
| Data on disk | Parquet files |
| **Storage layout** | **One Parquet file per ticker under `data/by_ticker/{ticker}.parquet`; access only through `storage.py`** |
| Data queries | DuckDB over Parquet |
| Primary data source | FinMind (daily pulls, free tier) |
| Fallback data source | TWSE official (source of truth for corporate actions) |
| **Adjustment policy** | **Store raw OHLCV + separate corporate-actions table; adjust on read via pure function** |
| **Schema validation** | **pandera schemas in `data/schemas.py`; enforced at every source/storage boundary** |
| **Trading calendar** | **`exchange_calendars` library, XTAI calendar** |
| Frontend | Streamlit + Plotly |
| Backtester | vectorbt (final pick deferred to Phase 3 kickoff) |
| Broker (paper) | Self-built simulator (Phase 5) |
| Broker (live) | Fubon Neo API (Phase 8+); Sinopac/Shioaji as backup |
| License | MIT |

Phase-specific decisions (test fixture strategy, backfill script ergonomics, etc.) live in the relevant `PHASE_N.md` and per-ADR files in `journal/decisions/`.

## 3. Code Conventions

- Type hints on all public function signatures
- Google-style docstrings on all public functions and classes
- No business logic in notebooks. `/notebooks` is exploration only; nothing in `src/twstock/` may import from it.
- All market-data file I/O goes through `src/twstock/data/storage.py`. Do not read or write Parquet directly anywhere else.
- All external data fetching goes through a `DataSource` implementation in `src/twstock/data/sources/`. New sources must implement the interface in `base.py`.
- All DataFrames crossing a source or storage boundary must validate against a pandera schema in `src/twstock/data/schemas.py`.
- Configuration constants live in `src/twstock/config.py`. Do not hard-code paths, magic numbers, or universe lists elsewhere.
- Secrets (API tokens, broker credentials, certificates) are read from environment variables. They never enter the repo. `.env.example` is the only env file in version control.
- Prefer pure functions where possible; isolate side effects (network, disk, broker calls) at the edges. Adjustment math, DQ checks, and feature engineering must be pure.
- Use `pathlib.Path`, never raw strings, for filesystem paths.
- Tests that touch the network are marked `@pytest.mark.integration` and excluded from default runs.

## 4. Repo Structure

```
twstock-algo/
├── README.md
├── CLAUDE.md                    ← this file (current operational state)
├── PROJECT_PLAN.md              ← strategic intent (source of truth for "what")
├── PHASE_0.md                   ← Phase 0 spec (done)
├── PHASE_1.md                   ← Phase 1 spec (active)
├── LICENSE                      ← MIT
├── pyproject.toml
├── .python-version
├── .gitignore
├── .env.example
├── .pre-commit-config.yaml
├── src/twstock/
│   ├── __init__.py
│   ├── config.py
│   ├── universe.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── sources/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── finmind.py
│   │   │   └── twse.py
│   │   ├── schemas.py           ← Phase 1
│   │   ├── storage.py
│   │   ├── adjustments.py       ← Phase 1
│   │   ├── quality.py           ← Phase 1
│   │   └── api.py               ← Phase 1 (public entry point)
│   ├── indicators/              ← Phase 2/4
│   ├── backtest/                ← Phase 3
│   ├── strategies/              ← Phase 4
│   ├── broker/                  ← Phase 5
│   └── ui/                      ← Phase 2
├── notebooks/
├── tests/
│   ├── fixtures/                ← hand-written test data (Phase 1+)
│   ├── cassettes/               ← VCR cassettes for source tests (Phase 1+)
│   ├── data/                    ← unit tests for data layer
│   └── integration/             ← @pytest.mark.integration (Phase 1+)
├── data/                        ← gitignored; Parquet store
├── logs/                        ← gitignored; backfill run logs (JSONL)
├── journal/
│   ├── README.md
│   ├── weekly/
│   ├── decisions/
│   └── dependencies.md          ← rationale for each new dep
└── scripts/
    └── backfill.py              ← Phase 1
    └── build_anchors.py         ← Phase 1 (regenerate adjustment cross-check fixture)
```

## 5. Data Source Contract

Every concrete data source must subclass `DataSource` and implement at minimum:

```python
class DataSource(ABC):
    @abstractmethod
    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        """Return daily OHLCV for `ticker` between `start` and `end` inclusive.
        May raise RateLimitError, SourceUnavailableError, or NoDataError."""

    @abstractmethod
    def get_corporate_actions(
        self, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        """Return splits, cash/stock dividends, rights issues."""

    @abstractmethod
    def list_universe(self, snapshot_date: date) -> list[str]:
        """Return tickers in the configured universe as of `snapshot_date`."""
```

Custom exceptions are defined in `sources/base.py`:
- `SourceError` (base class)
- `RateLimitError` — provider throttled us
- `SourceUnavailableError` — provider is down or returning 5xx
- `NoDataError` — valid request, no data for this ticker/range

Returned DataFrames must conform to the schemas in `src/twstock/data/schemas.py`. Schema enforcement uses `pandera`.

## 6. Current State

**Done:** Phase 0 — Foundation (closed 2026-05-11). Repo, tooling, conventions, hard-coded universe, abstract `DataSource`, journal structure all in place.

**Active:** Phase 1 — Data Pipeline. Full spec in `PHASE_1.md`. ~2 weeks. Exit criteria are listed in §9 of that doc.

**Mid-phase amendment (2026-05-13):** Decision D9 was superseded. The
adjustment cross-validation method changed from a Yahoo Finance CSV
fixture (Yahoo paywalled bulk downloads; Stooq does not cover TWSE) to
a hand-curated anchor fixture at `tests/fixtures/anchor_dividends.json`,
built from TSMC SEC 6-K filings (dividend amounts) plus TWSE's official
STOCK_DAY endpoint (raw close prices). The test is now a local-invariant
ratio assertion rather than a bulk-row comparison. Full rationale in
`journal/decisions/2026-05-13-adjustment-cross-check-method.md`; spec
changes in `PHASE_1.md` D9 and §4.6.

**Day 1 task before any production code:** the FinMind corporate-actions spike (`PHASE_1.md` §8). The spike findings are recorded in `journal/decisions/` before any of the modules in `PHASE_1.md` §3 get implemented.

**Build order:** follow `PHASE_1.md` §5 strictly. Schemas first, then storage, then adjustments, then sources, then quality/api, then the backfill script.

## 7. Guardrails — DO NOT

- Do not place orders against a real broker account. All trading code paths through Phase 7 must use the simulator. Live trading is gated on Phase 8 explicit greenlight.
- Do not write strategies before the backtester is operational. Phases run in order: 1 → 2 → 3 → 4 → ...
- Do not bypass the `DataSource` interface to fetch market data ad-hoc.
- Do not read or write Parquet outside of `storage.py`.
- Do not regenerate `tests/fixtures/anchor_dividends.json` from CI, pre-commit, or test setup. The committed JSON is the source of truth at test time. Regenerate only by running `scripts/build_anchors.py` deliberately, and only when the dividend reference table inside that script has been edited (e.g. adding a new ticker or correcting a source citation). Any regeneration must be reviewed in a commit diff alongside the dividend-table edit that justified it.
- Do not auto-re-record VCR cassettes in CI or pre-commit. Re-record manually and review the diff.
- Do not commit `.env`, anything in `data/`, anything in `logs/`, broker certificates, or anything in `secrets/`.
- Do not delete or rewrite past journal entries; append corrections in a new entry.
- Do not add new top-level Python modules to `src/twstock/` without updating section 4 of this file.
- Do not introduce a new dependency without recording the rationale in `journal/dependencies.md`.

## 8. Working Rhythm

- **Sunday:** weekly sprint plan in `journal/weekly/<YYYY-WNN>.md`
- **Each working day:** append a dated section (`## <YYYY-MM-DD>`) to that week's file — three sub-sections: *Did*, *Learned*, *Next*
- **Saturday:** append a retrospective section to that week's file
- **Architectural decisions:** captured in `journal/decisions/<NNNN>-<slug>.md` (lightweight ADR format)

When starting a session, read the current week's file to pick up context. When ending a session, append tomorrow's dated stub with the *Next* section filled in.

## 9. Useful Commands

```bash
# Setup
uv sync                          # install all deps
uv run pre-commit install        # set up git hooks

# Daily dev
uv run pytest                    # unit + cassette tests (default; integration excluded)
uv run pytest -m integration     # integration only (hits real APIs; needs FINMIND_TOKEN)
uv run pytest tests/path/to/test_x.py::test_y   # run one
uv run ruff check src tests
uv run ruff format src tests

# Adding deps
uv add <package>                 # runtime dep (record in journal/dependencies.md)
uv add --dev <package>           # dev-only dep

# Phase 1 — data backfill
uv run python scripts/backfill.py --tickers 2330       # one ticker, default range
uv run python scripts/backfill.py                      # all Top 50, default range
uv run python scripts/backfill.py --start 2020-01-01   # custom range
uv run python scripts/backfill.py --tickers 2330 --force   # re-fetch existing

# Phase 1 — regenerate adjustment cross-check anchor fixture
# (run only after editing the dividend reference table inside the script)
uv run python scripts/build_anchors.py

# Streamlit (Phase 2+)
uv run streamlit run src/twstock/ui/app.py
```
