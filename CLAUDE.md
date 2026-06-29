# CLAUDE.md

Instructions for Claude Code when working in this repository. Read this in full at the start of every session, plus the current week's journal entry in `journal/weekly/`.

**Last updated:** 2026-06-22
**Current phase:** Phase 5 — Paper-Trading Simulator (planning complete: `PHASE_5.md` + ADRs 0008/0009/0010 finalized; implementation pending. Phase 4 complete; see `journal/phase_4_retro.md`)

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

| Area                   | Decision                                                                                                                                                                                                                                                                                                         |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Time horizon           | Daily only. Intraday is a future Phase 9 expansion.                                                                                                                                                                                                                                                             |
| Universe               | TWSE Top 50 (0050 ETF constituents), hard-coded snapshot for now                                                                                                                                                                                                                                                 |
| Capital assumption     | NT$1,000,000 for backtests; max 10% per position                                                                                                                                                                                                                                                                 |
| Python                 | 3.12                                                                                                                                                                                                                                                                                                             |
| Dependency manager     | uv                                                                                                                                                                                                                                                                                                               |
| Lint/format            | ruff                                                                                                                                                                                                                                                                                                             |
| Tests                  | pytest                                                                                                                                                                                                                                                                                                           |
| Pre-commit             | ruff + pytest smoke test                                                                                                                                                                                                                                                                                         |
| Data in-memory         | pandas                                                                                                                                                                                                                                                                                                           |
| Data on disk           | Parquet files                                                                                                                                                                                                                                                                                                    |
| **Storage layout**     | **One Parquet file per ticker under `data/by_ticker/{ticker}.parquet`; access only through `storage.py`**                                                                                                                                                                                                        |
| Data queries           | DuckDB over Parquet                                                                                                                                                                                                                                                                                              |
| Primary data source    | FinMind (daily pulls, free tier)                                                                                                                                                                                                                                                                                 |
| Fallback data source   | TWSE official (source of truth for corporate actions)                                                                                                                                                                                                                                                            |
| **Adjustment policy**  | **Store raw OHLCV + separate corporate-actions table; adjust on read via pure function**                                                                                                                                                                                                                         |
| **Schema validation**  | **pandera schemas in `data/schemas.py`; enforced at every source/storage boundary**                                                                                                                                                                                                                              |
| **Trading calendar**   | **`exchange_calendars` library, XTAI calendar**                                                                                                                                                                                                                                                                  |
| Frontend               | Streamlit + Plotly                                                                                                                                                                                                                                                                                               |
| **Backtester**         | **Custom thin engine; vectorbt and backtrader studied as architectural references only. See `journal/decisions/0004-custom-backtester-rejecting-vectorbt.md`.**                                                                                                                                                  |
| **Execution model**    | **Signal at T close, fill at T+1 open. No same-bar execution.**                                                                                                                                                                                                                                                  |
| **Cost model**         | **Taiwan-specific: 0.3% sell-side tax (0.1% for ETFs), 0.0855% per-side commission default (0.1425% statutory cap × 6折), 5 bps per-side slippage. Always on.**                                                                                                                                                  |
| **Strategy interface** | **Single-asset `Strategy` (locked, ADR 0004) + sibling `CrossSectionalStrategy` (panel → scores) for portfolio strategies + `CrossSectionalAdapter` for binary lifts. Sibling, not a unified method, to preserve the stateless lookahead audit. See `journal/decisions/0006-portfolio-runner-architecture.md`.** |
| **Portfolio runner**   | **Cross-sectional scores → holdings-agnostic allocator (`EqualWeightTopN`, validates `n × max_position_pct ≥ 1`) → monthly XTAI rebalance → reconciler. Allocation and cadence are run config, not strategy state. See ADR 0006.**                                                                               |
| **Cash settlement**    | **Instant-settling shared cash pool; all sells execute before all buys on any order-executing bar. TWSE T+2 net settlement is the justification, not modeled. See `journal/decisions/0005-cash-settlement-model-and-sell-before-buy-ordering.md`.**                                                              |
| **Trade accounting**   | **Lot-consuming FIFO ledger for realized round-trips (canonical FlatToFlat grouping); `Position.avg_cost` retained for the open-position basis. See ADR 0006.**                                                                                                                                                  |
| Broker (paper)         | Self-built simulator (Phase 5)                                                                                                                                                                                                                                                                                   |
| Broker (live)          | Fubon Neo API (Phase 8+); Sinopac/Shioaji as backup                                                                                                                                                                                                                                                              |
| License                | MIT                                                                                                                                                                                                                                                                                                              |

Phase-specific decisions (test fixture strategy, backfill script ergonomics, dashboard layout, backtester module decomposition, etc.) live in the relevant `PHASE_N.md` and per-ADR files in `journal/decisions/`.

## 3. Code Conventions

- Type hints on all public function signatures
- Google-style docstrings on all public functions and classes
- No business logic in notebooks. `/notebooks` is exploration only; nothing in `src/twstock/` may import from it.
- All market-data file I/O goes through `src/twstock/data/storage.py`. Do not read or write Parquet directly anywhere else.
- All external data fetching goes through a `DataSource` implementation in `src/twstock/data/sources/`. New sources must implement the interface in `base.py`.
- All DataFrames crossing a source or storage boundary must validate against a pandera schema in `src/twstock/data/schemas.py`.
- **`src/twstock/ui/` is a presentation-only module.** It may import from `twstock.data.api`, `twstock.data.storage`, `twstock.universe`, and third-party libraries (streamlit, plotly, pandas). It must not import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or any strategy/backtest module. The boundary keeps the dashboard a thin layer over the data API. Enforced by code review; an `import-linter` config is a Phase 8 ticket.
- **`src/twstock/backtest/` is the backtesting layer.** It may import from `twstock.data.api`, `twstock.data.storage`, `twstock.universe`, `twstock.indicators`, and `twstock.config`. It must not import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or `twstock.ui`. Mirrors the `ui/` boundary rule; enforced by code review.
- **`src/twstock/strategies/` is the strategy layer.** It may import from `twstock.backtest`, `twstock.indicators`, `twstock.data.api`, `twstock.universe`, and `twstock.config`. It must not import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or `twstock.ui`. Mirrors the `backtest/` and `ui/` boundary rules; enforced by code review.
- **Lookahead safety is structural.** No indicator, strategy, or feature uses `pd.Series.shift(-k)` for positive `k`. The engine executes orders at T+1's open only; same-bar execution is disallowed in the constructor's `Literal`.
- Configuration constants live in `src/twstock/config.py`. Do not hard-code paths, magic numbers, or universe lists elsewhere.
- Secrets (API tokens, broker credentials, certificates) are read from environment variables. They never enter the repo. `.env.example` is the only env file in version control.
- Prefer pure functions where possible; isolate side effects (network, disk, broker calls) at the edges. Adjustment math, DQ checks, feature engineering, indicator computation, and tear-sheet metrics must be pure.
- Use `pathlib.Path`, never raw strings, for filesystem paths.
- Tests that touch the network are marked `@pytest.mark.integration` and excluded from default runs.

## 4. Repo Structure

```
twstock-algo/
├── README.md
├── CLAUDE.md                    ← this file (current operational state)
├── PROJECT_PLAN.md              ← strategic intent (source of truth for "what")
├── PHASE_0.md                   ← Phase 0 spec (done)
├── PHASE_1.md                   ← Phase 1 spec (done)
├── PHASE_2.md                   ← Phase 2 spec (done)
├── PHASE_3.md                   ← Phase 3 spec (done)
├── PHASE_4.md                   ← Phase 4 spec (done)
├── PHASE_5.md                   ← Phase 5 spec (active)
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
│   │   ├── storage.py
│   │   ├── adjustments.py
│   │   ├── quality.py
│   │   └── api.py               ← public entry point for all downstream phases
│   ├── ui/                      ← Phase 2 — Streamlit dashboard
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── data_loader.py
│   │   ├── charts.py
│   │   └── names.py
│   ├── indicators/              ← Phase 3 — pure pandas indicators
│   │   ├── __init__.py
│   │   └── core.py              ← sma, ema, bollinger_bands, rsi, macd, donchian_channels
│   ├── backtest/                ← Phase 3 engine; Phase 4 portfolio runner
│   │   ├── __init__.py
│   │   ├── strategy.py          ← Strategy ABC + BuyAndHold (Phase 3)
│   │   ├── cross_sectional.py   ← CrossSectionalStrategy ABC + CrossSectionalAdapter (Phase 4)
│   │   ├── cost_model.py        ← TaiwanCostModel
│   │   ├── portfolio.py         ← Portfolio: positions, cash, equity
│   │   ├── allocator.py         ← Allocator ABC + EqualWeightTopN (Phase 4)
│   │   ├── tiebreak.py          ← TieBreakPolicy + LiquidityTieBreak + HoldAll (Phase 4)
│   │   ├── rebalance.py         ← RebalanceSchedule + Monthly/Weekly/Daily/Quarterly (Phase 4)
│   │   ├── reconciler.py        ← target weights → orders, sells before buys (Phase 4)
│   │   ├── engine.py            ← Engine: dispatch single-asset / cross-sectional
│   │   ├── result.py            ← BacktestResult dataclass
│   │   ├── trades.py            ← lot-based round-trip ledger (Phase 4)
│   │   ├── tearsheet.py         ← metrics + Plotly equity curve
│   │   ├── walkforward.py       ← WalkForwardSplit iterator
│   │   └── audit.py             ← lookahead audit (+ panel variant, Phase 4)
│   ├── strategies/              ← Phase 4 — technical strategies
│   │   ├── __init__.py
│   │   ├── sma_crossover.py     ← reference SMA(20)/SMA(60) crossover (Phase 3)
│   │   ├── ema_crossover.py     ← single-asset (Phase 4)
│   │   ├── rsi_reversion.py     ← single-asset (Phase 4)
│   │   ├── bollinger_breakout.py ← single-asset (Phase 4)
│   │   ├── macd.py              ← single-asset (Phase 4)
│   │   ├── donchian_breakout.py ← single-asset (Phase 4)
│   │   └── cross_sectional_momentum.py  ← native CrossSectionalStrategy (Phase 4)
│   └── broker/                  ← Phase 5
├── notebooks/
├── tests/
│   ├── fixtures/                ← hand-written test data (Phase 1+)
│   ├── cassettes/               ← VCR cassettes for source tests (Phase 1+)
│   ├── data/                    ← unit tests for data layer
│   ├── ui/                      ← Phase 2 unit tests
│   ├── indicators/              ← Phase 3 unit tests
│   ├── backtest/                ← Phase 3 + Phase 4 unit tests
│   ├── strategies/              ← Phase 4 per-strategy unit + integration tests
│   └── integration/             ← @pytest.mark.integration (Phase 1+)
├── data/                        ← gitignored; Parquet store (includes 0050 benchmark)
├── logs/                        ← gitignored; backfill run logs (JSONL)
├── journal/
│   ├── README.md
│   ├── weekly/
│   ├── decisions/
│   ├── phase_3_references.md    ← vectorbt + backtrader study notes (Phase 3)
│   ├── phase_5_references.md    ← broker/order/fill Step-0 source reading (Phase 5)
│   ├── strategies/              ← Phase 4 per-strategy writeups + comparison.md
│   └── dependencies.md          ← rationale for each new dep
└── scripts/
    ├── backfill.py              ← Phase 1 (Phase 3 backfills 0050 as benchmark)
    ├── build_anchors.py         ← Phase 1 (regenerate adjustment cross-check fixture)
    ├── fetch_listing_dates.py   ← Phase 1 (one-off TWSE listing-date refresh)
    ├── phase_3_stability_report.py   ← Phase 3 walk-forward SMA report
    └── phase_4_evaluate.py      ← Phase 4 single-asset distribution across 50 tickers
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

**Done:**

- Phase 0 — Foundation (closed 2026-05-11)
- Phase 1 — Data Pipeline (closed 2026-05-19). All 50 tickers backfilled to `data/by_ticker/`; corporate actions in `data/corporate_actions/`; `quality.check_all()` ok for 50/50; adjustment anchors test green at 0.01% tolerance.
- Phase 2 — Inspection Dashboard (closed 2026-05-21). Streamlit dashboard at `src/twstock/ui/`; candlestick + volume chart, SMA/Bollinger overlays, adjusted/raw toggle, ex-date markers, info panel; 160 tests passing. Retro: `journal/phase_2_retro.md`.

- Phase 3 — Backtesting Engine (closed 2026-06-13). Custom thin engine; T+1-open execution; Taiwan cost model always on; `Strategy` ABC; `WalkForwardSplit`; SMA crossover reference strategy; tear sheet with full metrics. ~230 tests passing. Retro: `journal/phase_3_retro.md`.

- Phase 4 — Technical Strategies (closed 2026-06-22). Six single-asset strategies + `CrossSectionalMomentum`; portfolio runner with `CrossSectionalStrategy` ABC, `EqualWeightTopN` allocator, `Monthly` rebalance, `reconcile()`, lot-based FIFO ledger. ~310 tests passing. Retro: `journal/phase_4_retro.md`. Pre-Phase-5 prerequisite: ADR 0007 stock split fix.

**Active:** Phase 5 — Paper-Trading Simulator. Planning complete; implementation pending. Spec: `PHASE_5.md`. Governing ADRs: 0008 (shared execution core), 0009 (settlement modes + cash allocation), 0010 (order lifecycle, broker interface, fill model). Step-0 source reading: `journal/phase_5_references.md`.

**Public API surface for downstream phases** (frozen at end of Phase 1; Phase 2 and Phase 3 import from here only):

```python
from twstock.data.api import get_adjusted_history, get_universe
from twstock.data.storage import (
    read_ohlcv, read_corporate_actions, query,
    list_stored_tickers, get_ticker_date_range,
)
from twstock.data.quality import check_ticker, check_all
from twstock.universe import get_top50_universe, get_listing_dates
```

**Phase 3 exposes these public symbols** for Phase 4+:

```python
from twstock.indicators.core import sma, ema, bollinger_bands, rsi
from twstock.backtest import (
    Engine, TaiwanCostModel, Portfolio,
    Strategy, BuyAndHold, BacktestResult,
    WalkForwardSplit, assert_no_lookahead,
)
from twstock.backtest.tearsheet import tear_sheet, equity_curve_figure
from twstock.strategies.sma_crossover import SmaCrossover
```

**Phase 4 exposes these public symbols** for Phase 5+:

```python
from twstock.backtest.cross_sectional import CrossSectionalStrategy, CrossSectionalAdapter
from twstock.backtest.allocator import EqualWeightTopN
from twstock.backtest.tiebreak import LiquidityTieBreak, HoldAll
from twstock.backtest.rebalance import Monthly, Weekly, Daily, Quarterly
from twstock.backtest.trades import build_round_trips
from twstock.strategies.ema_crossover import EmaCrossover
from twstock.strategies.rsi_reversion import RsiReversion
from twstock.strategies.bollinger_breakout import BollingerBreakout
from twstock.strategies.macd import Macd
from twstock.strategies.donchian_breakout import DonchianBreakout
from twstock.strategies.cross_sectional_momentum import CrossSectionalMomentum
```

**Build order:** follow `PHASE_4.md §5`.

## 7. Guardrails — DO NOT

- Do not place orders against a real broker account. All trading code paths through Phase 7 must use the simulator. Live trading is gated on Phase 8 explicit greenlight.
- Phases run in order: 1 → 2 → 3 → 4 → 5 → ... Do not start Phase 6 work (ML strategies) until Phase 5 exits cleanly.
- Do not bypass the `DataSource` interface to fetch market data ad-hoc.
- Do not read or write Parquet outside of `storage.py`.
- **Do not import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or any strategy/backtest module into `src/twstock/ui/`.** The dashboard is a thin layer over `twstock.data.api`, `twstock.data.storage`, and `twstock.universe`. Mechanical enforcement (`import-linter`) is a Phase 8 ticket; reviewed by hand until then.
- **Do not import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or `twstock.ui` into `src/twstock/backtest/`.** The backtester depends only on the public data API, the indicator module, the universe, and config. Same Phase 8 import-linter ticket covers both boundaries.
- Do not extract `compute_sma` / `compute_bollinger` from `src/twstock/ui/charts.py` into a shared module. They are intentionally throwaway; Phase 3 builds `src/twstock/indicators/core.py` from scratch. Decision recorded in the Phase 2 ADR.
- **Do not execute trades at the same bar as the signal.** Engine execution is `next_open` only — signal at T's close, fill at T+1's open. The `execution` parameter is a `Literal["next_open"]` for v1; adding any other mode requires an ADR.
- **Do not bypass the cost model.** There is no zero-cost backtest path. `TaiwanCostModel.stock_sell_tax = 0.003` and `etf_sell_tax = 0.001` are not configurable; commission and slippage are.
- **Do not use `pd.Series.shift(-k)` for positive `k`** in any indicator, strategy, or feature. This is lookahead bias. Code review check: search for `shift(-` before merging any Phase 3+ commit.
- **Do not give `Strategy` subclasses cross-bar state.** `generate_signals` must be a pure function of `bars` — no instance attributes mutated across bars, no memory carried between calls. `assert_no_lookahead` perturbs future bars and checks earlier signals are unchanged; that audit is only meaningful if the strategy is pure, so a stateful strategy silently voids it. The same purity rule applies to `CrossSectionalStrategy.generate_scores`; the panel audit relies on it. Decisions: `journal/decisions/0004-custom-backtester-rejecting-vectorbt.md` (decision 3) and `journal/decisions/0006-portfolio-runner-architecture.md` (§3).
- **Do not mutate the `bars` DataFrame inside `Strategy.generate_signals`.** It is the engine's reference copy. Call `.copy()` first if mutation is needed for indicator scratchpads.
- **Do not encode return-predictive signal in a `TieBreakPolicy`, `Allocator`, or `RebalanceSchedule`.** Alpha belongs in `generate_scores`, where the lookahead audit covers it; a tiebreak/allocator/cadence rule that ranks on expected return is unaudited alpha. Decision: ADR 0006 (§6). Code-review check.
- **Do not buy before selling on a rebalance bar.** The reconciler emits all sells before all buys (ADR 0005); buying first silently under-invests when a rotation funds a buy from a same-bar sell.
- **Do not optimize strategy parameters for selection.** Canonical parameters are fixed by convention; `WalkForwardSplit` is a diagnostic only (`PHASE_4.md` D2). Reporting an in-sample-optimized number as the result is disqualifying.
- **Do not add short positions, partial fills, or limit orders in Phase 4.** All-or-nothing, long-only, market orders only. Of these, only **partial fills** arrive in Phase 5 (ADR 0010); short positions and limit orders are deferred to later phases (Phase 5 is long-only, market-orders-only).
- **Do not import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or `twstock.ui` into `src/twstock/strategies/`.** Strategies depend only on `twstock.backtest`, the indicators, the public data API, the universe, and config. Same Phase 8 import-linter ticket covers this boundary.
- Do not regenerate `tests/fixtures/anchor_dividends.json` from CI, pre-commit, or test setup. The committed JSON is the source of truth at test time. Regenerate only by running `scripts/build_anchors.py` deliberately, and only when the dividend reference table inside that script has been edited. Any regeneration must be reviewed in a commit diff alongside the dividend-table edit that justified it.
- Do not auto-re-record VCR cassettes in CI or pre-commit. Re-record manually and review the diff.
- Do not commit `.env`, anything in `data/`, anything in `logs/`, broker certificates, or anything in `secrets/`.
- Do not delete or rewrite past journal entries; append corrections in a new entry.
- Do not add new top-level Python modules to `src/twstock/` without updating section 4 of this file.
- Do not introduce a new dependency without recording the rationale in `journal/dependencies.md`.

## 8. Working Rhythm

- **Sunday:** weekly sprint plan in `journal/weekly/<YYYY-WNN>.md`
- **Each working day:** append a dated section (`## <YYYY-MM-DD>`) to that week's file — three sub-sections: _Did_, _Learned_, _Next_. Update toward the end of the day's session, not mid-session. Claude Code should prompt for this at natural session end-points.
- **Saturday:** append a retrospective section to that week's file
- **Architectural decisions:** captured in `journal/decisions/<NNNN>-<slug>.md` (lightweight ADR format)

When starting a session, read the current week's file to pick up context. When ending a session, append tomorrow's dated stub with the _Next_ section filled in.

**Weekly journal commit timing:** `journal/weekly/<YYYY-WNN>.md` is committed once per week — Sunday night or Monday, after the retrospective is written. Do not commit mid-week journal updates as standalone commits; let the week accumulate. Other journal files (decisions, ADRs, retros) follow the normal commit trigger — commit immediately when appended.

**Division of labor:** The user leads implementation. Claude Code's role in `src/` is to provide hints, check mental models, and spot issues when asked — not to write implementation code unprompted. Claude Code's primary code contribution is writing pytest tests. Claude Code must prompt the user before making edits to any file in the codebase and wait for explicit approval.

## 9. Commit Convention

This repository will be public. Commits should be readable and quickly understandable by a stranger six months from now. All commits made during a Claude Code session follow this convention. **Claude Code drafts every commit message; the human reviews and approves before the commit lands.**

### 9.1 Format

```
<type>(<scope>): <subject>

<body — optional, bullets when multi-part>

<footer — Co-Authored-By trailer>
```

**Header** — required, ≤72 characters.

- **`type`** — one of:
  - `feat` — new user-facing capability or module
  - `fix` — bug fix (incorrect behavior, not just bad code)
  - `refactor` — behavior-preserving reorganization
  - `perf` — performance improvement, no functional change
  - `test` — tests only
  - `docs` — README, CLAUDE.md, journal, ADRs, comments, docstrings
  - `chore` — tooling, dependencies, build config, gitignore; nothing in `src/` runtime
- **`scope`** — module or area touched, lowercase, matches the directory name when possible: `data`, `ui`, `storage`, `finmind`, `twse`, `backfill`, `quality`, `adjustments`, `api`, `universe`, `charts`, `names`, `indicators`, `backtest`, `engine`, `portfolio`, `tearsheet`, `walkforward`, `strategies`. Omit only for genuinely cross-cutting commits (`chore: bump pyarrow`).
- **`subject`** — imperative mood ("add", not "adds" or "added"), lowercase first word, no trailing period. Describes the _outcome_, not the activity.

**Body** — required when the change has multiple distinct parts or when the _why_ is not obvious from the header.

- Blank line after header.
- Bullet form using `-` markers. Never paragraph form for multi-point commits. Single-point commits may use a short paragraph instead of bullets.
- Wrap at 72 characters.
- Imperative mood, same as subject.
- Each bullet 1–3 lines. Explain _what_ and _why_. The diff already shows _how_.
- Refer to code by symbol name (`_build_dividends`, `OHLCV_SCHEMA`, `candlestick_volume`, `TaiwanCostModel`, `assert_no_lookahead`), not by file path or line number.
- **No phase or day references in commit messages.** Public-repo readers don't know what "Phase 1 Day 6" means. Use the scope and a descriptive subject instead.
- Breaking changes: prefix the relevant bullet with `BREAKING CHANGE:`.

**Footer** — blank line before.

- `Co-Authored-By: Claude <noreply@anthropic.com>` on every Claude-originated commit, regardless of size or type. No model version, no other variant.
- Optional: `Closes #N` once issue tracking is in use.

### 9.2 Examples

Single-purpose, no body needed:

```
fix(quality): clamp missing-day check to ticker listing date
chore: bump pandera to 0.20.4
docs: add commit convention to CLAUDE.md
test(adjustments): add anchor for Yageo 2022 capital reduction
feat(indicators): add sma, ema, bollinger, rsi
```

Multi-part, bulleted body:

```
feat(finmind): switch to TaiwanStockDividend with multi-row emission

- Replace TaiwanStockDividendResult with TaiwanStockDividend as the
  primary dividend dataset; exposes four-component breakdown and
  per-leg ex-dates
- Rewrite _build_dividends to iterate raw rows and emit 1–3 output
  rows per source row depending on has_cash/has_stock/has_rights
- Derive rights dilution from share counts rather than
  CashIncreaseSubscriptionRate (post-carve-out rate, not total)
- Rename _build_reductions -> _build_capital_reductions
- Re-record VCR cassette against the live endpoint

Co-Authored-By: Claude <noreply@anthropic.com>
```

### 9.3 Anti-patterns (treat as automatic rejects during review)

- Defaulting everything to `feat:` regardless of the actual change type.
- Phase or day references in the subject (`"Phase 1 Day 6 — ..."`).
- Mixing paragraph and bullet body styles within the same repo.
- Past tense (`"added"`, `"fixed"`).
- Vague subjects: `"update code"`, `"fix stuff"`, `"WIP"`, `"Final fix"`.
- Naming a model version in the trailer (`Claude Sonnet 4.6`, `Claude Opus 4.7`). The canonical form is `Claude <noreply@anthropic.com>`. The model identity is not part of the project history.
- Two unrelated changes in one commit. Split them.

### 9.4 Commit checkpoints — when to prompt

Claude Code proactively offers to commit at these moments. Each is a hard trigger; do not wait to be asked.

| Trigger                                                                              | Why                                                                    |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| A step in `PHASE_N.md §5 Build sequence` is complete and tests pass                  | One commit per step is the unit of progress for the phase              |
| A `journal/decisions/`, ADR, or retro file is appended or updated | Non-weekly journal files are part of the record; commit immediately |
| The week ends (Sunday night / Monday) and `journal/weekly/<YYYY-WNN>.md` has the retro | Weekly journals accumulate all week; one commit per completed week |
| `CLAUDE.md`, `PROJECT_PLAN.md`, or any `PHASE_N.md` is edited                        | Spec changes belong in their own commit, separate from code            |
| A test suite turns from red to green                                                 | The fix is a discrete unit; commit before moving on                    |
| A `chore:`-class change is complete (dep added, config edited)                       | Don't let tooling churn pile up with code changes                      |
| Before ending a session if `git status` shows uncommitted changes                    | Don't leave the workspace dirty across sessions                        |

Claude Code does **not** prompt to commit during these:

- Mid-step work, exploration, or partial edits.
- A test is failing — fix or revert first, never commit a known-broken state.
- The change is incomplete and would orphan a half-written function or module.

### 9.5 Commit protocol

When a checkpoint fires, Claude Code:

1. Runs `git status` and reports the modified file list.
2. Drafts a commit message following §9.1, including the Co-Authored-By trailer.
3. Shows the message and asks for approval. Acceptable forms: `y` to commit as drafted, `n` to skip, or a revised message.
4. On approval, runs the pre-commit hooks. If they fail, surfaces the failure and does not commit until the human says how to proceed.
5. On hook success, runs `git add` for the relevant files and `git commit`. Does not use `git add -A` reflexively — only stages files the human has acknowledged.
6. Confirms the commit hash and short subject after execution.

If `git status` shows changes that span multiple logical units (e.g., a code change and an unrelated journal entry and a CLAUDE.md edit), Claude Code proposes splitting into multiple commits and drafts each one in turn.

Claude Code **never** commits without explicit human approval, and never silently amends or force-pushes.

## 10. Useful Commands

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
uv run python scripts/backfill.py --tickers 0050       # benchmark instrument (Phase 3)

# Phase 1 — regenerate adjustment cross-check anchor fixture
# (run only after editing the dividend reference table inside the script)
uv run python scripts/build_anchors.py

# Phase 2 — inspection dashboard
uv run streamlit run src/twstock/ui/app.py

# Phase 3 — backtests
uv run pytest tests/indicators tests/backtest           # phase 3 unit tests
uv run pytest -m integration tests/backtest             # phase 3 end-to-end on real data
uv run python scripts/phase_3_stability_report.py       # SMA walk-forward report
# Programmatic API: see PHASE_3.md §1 for the canonical Engine.run example

# Phase 4 — strategies
uv run pytest tests/strategies tests/backtest         # phase 4 unit tests
uv run pytest -m integration tests/strategies         # per-strategy end-to-end on real data
uv run python scripts/phase_4_evaluate.py --strategy rsi_reversion   # single-asset distribution across 50
# Programmatic API: see PHASE_4.md §1 for single-asset and cross-sectional run examples
```
