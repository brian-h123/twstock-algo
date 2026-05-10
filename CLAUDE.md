# CLAUDE.md

Instructions for Claude Code when working in this repository. Read this in full at the start of every session, plus the most recent daily journal entry in `journal/daily/`.

**Last updated:** 2026-05-10
**Current phase:** Phase 0 — Foundation setup

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
| Time horizon | Daily only. Intraday is a future Phase 7+ expansion. |
| Universe | TWSE Top 50 (0050 ETF constituents), hard-coded snapshot for now |
| Capital assumption | NT$1,000,000 for backtests; max 10% per position |
| Python | 3.12 |
| Dependency manager | uv |
| Lint/format | ruff |
| Tests | pytest |
| Pre-commit | ruff + pytest smoke test |
| Data in-memory | pandas |
| Data on disk | Parquet files |
| Data queries | DuckDB over Parquet |
| Primary data source | FinMind (daily pulls, free tier) |
| Fallback data source | TWSE official (source of truth for corporate actions) |
| Frontend | Streamlit + Plotly |
| Backtester | vectorbt (final pick deferred to Phase 3 kickoff) |
| Broker (paper) | Self-built simulator (Phase 5) |
| Broker (live) | Fubon Neo API (Phase 8+); Sinopac/Shioaji as backup |
| License | MIT |

## 3. Code Conventions

- Type hints on all public function signatures
- Google-style docstrings on all public functions and classes
- No business logic in notebooks. `/notebooks` is exploration only; nothing in `src/twstock/` may import from it.
- All market-data file I/O goes through `src/twstock/data/storage.py`. Do not read or write Parquet directly anywhere else.
- All external data fetching goes through a `DataSource` implementation in `src/twstock/data/sources/`. New sources must implement the interface in `base.py`.
- Configuration constants live in `src/twstock/config.py`. Do not hard-code paths, magic numbers, or universe lists elsewhere.
- Secrets (API tokens, broker credentials, certificates) are read from environment variables. They never enter the repo. `.env.example` is the only env file in version control.
- Prefer pure functions where possible; isolate side effects (network, disk, broker calls) at the edges.
- Use `pathlib.Path`, never raw strings, for filesystem paths.

## 4. Repo Structure

```
twstock-algo/
├── README.md
├── CLAUDE.md                    ← this file
├── PHASE_0.md                   ← current phase spec
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
│   │   ├── schemas.py
│   │   └── storage.py
│   ├── indicators/              # Phase 2/4
│   ├── backtest/                # Phase 3
│   ├── strategies/              # Phase 4
│   ├── broker/                  # Phase 5
│   └── ui/                      # Phase 2
├── notebooks/
├── tests/
├── data/                        # gitignored
├── journal/
│   ├── README.md
│   ├── daily/
│   ├── weekly/
│   └── decisions/
└── scripts/
```

## 5. Data Source Contract

Every concrete data source must subclass `DataSource` and implement at minimum:

```python
class DataSource(ABC):
    @abstractmethod
    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        """Return daily OHLCV for `ticker` between `start` and `end` inclusive."""

    @abstractmethod
    def get_corporate_actions(
        self, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        """Return splits, dividends, and ticker changes."""

    @abstractmethod
    def list_universe(self, snapshot_date: date) -> list[str]:
        """Return tickers in the configured universe as of `snapshot_date`."""
```

Returned DataFrames must conform to the schemas in `src/twstock/data/schemas.py` (created in Phase 1). Schema enforcement uses `pandera` or equivalent.

## 6. Current State

**Done:** Phase 0 Days 1–2 complete

**Active:** Phase 0 — see `PHASE_0.md` for exit criteria and daily breakdown.

**Next phase:** Phase 1 — data ingestion. Spec to be written when Phase 0 closes.

## 7. Guardrails — DO NOT

- Do not place orders against a real broker account. All trading code paths through Phase 7 must use the simulator. Live trading is gated on Phase 8 explicit greenlight.
- Do not write strategies before the backtester is operational. Phases run in order: 1 → 2 → 3 → 4 → ...
- Do not bypass the `DataSource` interface to fetch market data ad-hoc.
- Do not commit `.env`, anything in `data/`, broker certificates, or anything in `secrets/`.
- Do not delete or rewrite past journal entries; append corrections in a new entry.
- Do not add new top-level Python modules to `src/twstock/` without updating section 4 of this file.
- Do not introduce a new dependency without recording the rationale in the relevant journal entry.

## 8. Working Rhythm

- **Sunday:** weekly sprint plan in `journal/weekly/<YYYY-WNN>.md`
- **Each working day:** entry in `journal/daily/<YYYY-MM-DD>.md` — three sections: *Did*, *Learned*, *Next*
- **Saturday:** retrospective appended to that week's weekly file
- **Architectural decisions:** captured in `journal/decisions/<NNNN>-<slug>.md` (lightweight ADR format)

When starting a session, read the most recent daily entry to pick up context. When ending a session, write tomorrow's daily entry stub with the *Next* section filled in.

## 9. Useful Commands

```bash
# Setup
uv sync                          # install all deps
uv run pre-commit install        # set up git hooks

# Daily dev
uv run pytest                    # run all tests
uv run pytest tests/path/to/test_x.py::test_y   # run one
uv run ruff check src tests
uv run ruff format src tests

# Adding deps
uv add <package>                 # runtime dep
uv add --dev <package>           # dev-only dep

# Streamlit (Phase 2+)
uv run streamlit run src/twstock/ui/app.py
```
