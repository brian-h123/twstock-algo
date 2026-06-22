# Taiwan Stock Algo Trading — Project Plan

> **Purpose of this document.** A single reference covering project goals, locked decisions, and the full phase plan. Designed to be pasted into new Claude chats so any conversation can pick up project context without re-explaining everything. Updated at the end of each phase.

**Last updated:** 2026-06-22
**Current phase:** Phase 5 — Paper-Trading Simulator (Phase 4 complete; see `journal/phase_4_retro.md`)
**Repo:** `twstock-algo` (private GitHub)

---

## 1. Project Overview

Personal algorithmic trading project targeting the Taiwan stock market, built as a portfolio piece for data science / ML engineering job applications. Owner is a fresh graduate with <1 year of personal Taiwan stock trading experience.

**Core thesis:** the project's value to a hiring manager comes from rigorous engineering and honest evaluation, not from claiming alpha. Strategies that fail to beat buy-and-hold are reported as such, with analysis of why.

**Project guardrails:**
- Daily horizon, TWSE Top 50 universe, NT$1M assumed capital
- Paper-only through Phase 7; live trading gated on Phase 8 explicit greenlight
- Phases run in order — no Phase N+1 work until Phase N exits cleanly
- Every architectural decision recorded in `journal/decisions/`

---

## 2. Locked Architectural Decisions

Do not relitigate without explicit reason. If a future phase requires changing one, record an ADR in `journal/decisions/` and update this table.

| Area | Decision |
|---|---|
| Time horizon | Daily only (intraday is a Phase 9+ expansion) |
| Universe | TWSE Top 50 (0050 ETF constituents), hard-coded snapshot |
| Capital | NT$1,000,000, max 10% per position |
| Language | Python 3.12 |
| Dep manager | uv |
| Lint/format | ruff |
| Tests | pytest |
| Data in-memory | pandas |
| Data on disk | Parquet |
| Storage layout | One Parquet file per ticker under `data/by_ticker/` (resolved Phase 1 kickoff) |
| Adjustment policy | Store raw OHLCV + corporate actions; adjust on read (resolved Phase 1 kickoff) |
| Schema validation | pandera at every source/storage boundary (resolved Phase 1 kickoff) |
| Trading calendar | `exchange_calendars` XTAI (resolved Phase 1 kickoff) |
| Data queries | DuckDB |
| Data: primary | FinMind (free tier) |
| Data: fallback | TWSE official |
| Frontend | Streamlit + Plotly |
| Backtester | vectorbt (provisional, confirm at Phase 3 kickoff) |
| Broker (paper) | Self-built simulator |
| Broker (live) | Fubon Neo API (Sinopac/Shioaji as backup) |
| License | MIT |

---

## 3. Phase Structure

Estimates assume 4–5 hours/day, 5–6 days/week. Padding included; expect slip.

### Phase 0 — Foundation
**Goal:** repo, tooling, and conventions in place.
**Duration:** 1–3 days.
**Builds:** GitHub repo, uv project, folder skeleton, `CLAUDE.md`, `PHASE_0.md`, `README.md`, pre-commit hooks, `universe.py` with hard-coded Top 50, abstract `DataSource` class, journal structure.
**Exit when:** `uv run pytest` and `pre-commit` pass on a fresh clone; first real commit pushed; FinMind token obtained.
**Key decisions to make at kickoff:** none (all locked).
**Pitfalls:** over-engineering. No business logic in this phase except `universe.py`.
**Hiring signal:** moderate — shows project hygiene.

---

### Phase 1 — Data Pipeline
**Goal:** clean, queryable historical OHLCV for the Top 50, with corporate actions handled.
**Duration:** ~2 weeks.

**Kickoff decisions resolved 2026-05-11 — see `PHASE_1.md` for full spec.** Summary: one parquet file per ticker; 10-year backfill window; raw storage + adjust on read; pandera schemas for OHLCV and corporate actions; hybrid test fixtures (hand-written + VCR cassettes + integration tests); idempotent + resumable backfill script with structured JSONL logs.

**Builds:**
- `FinMindSource` and `TwseSource` implementing the `DataSource` interface
- Schema definitions in `data/schemas.py` (pandera)
- Parquet storage layer in `data/storage.py` with partition-by-ticker layout
- DuckDB query helpers
- A `backfill` script that pulls 10 years of daily history for the Top 50
- Adjusted-price logic that correctly handles splits and cash dividends
- Data quality checks: missing days, duplicate rows, suspended trading detection
- Tests with recorded fixtures (no live API calls in default test runs)

**Exit when:** `get_adjusted_history("2330", start, end)` returns a clean, gap-handled DataFrame in <1 second; data quality checks pass for all (or at least 48 of 50) tickers across 10 years.

**Pitfalls:**
- Taiwan tickers are 4-digit strings, not integers ("0050" not 50). Type carefully.
- FinMind free tier has rate limits; throttle backfills.
- Corporate actions are the #1 source of bugs in retail backtesting. Test the adjustment math against a known-good reference (Yahoo Finance adjusted close, manual fixture).
- Survivorship bias: today's Top 50 isn't yesterday's Top 50. For now we accept this since universe is a fixed snapshot, but document it in the journal.

**Hiring signal:** high — clean data engineering is rare in retail-trader portfolios.

---

### Phase 2 — Inspection Dashboard
**Goal:** a Streamlit app that lets you visually sanity-check the data and overlay simple indicators.
**Duration:** 1–2 weeks.
**Builds:**
- Streamlit app at `src/twstock/ui/app.py`
- Candlestick + volume chart via Plotly
- Ticker selector (Top 50 dropdown)
- Date range picker
- 2–3 simple overlays: SMA(20), SMA(60), Bollinger Bands
- Basic info panel: name, last price, daily change, 52-week range

**Exit when:** running `uv run streamlit run …` shows any Top 50 ticker's clean candlestick chart with overlays in <2 seconds.

**Key decisions at kickoff:**
- Are indicators computed on the fly or pre-computed and cached?
- Multi-ticker comparison view in this phase or deferred?

**Pitfalls:**
- Easy to over-invest here. This is a sanity-check tool, not a TradingView clone. 1–2 weeks max. If you're tempted to add a 4th feature, write it as a Phase 8 ticket instead.
- Performance: cache the data load, or recharting on every dropdown change is painful.

**Hiring signal:** low — UI work is not what DS/ML hiring managers care about. Keep it minimal.

---

### Phase 3 — Backtesting Engine
**Goal:** a working backtester with realistic Taiwan costs that can produce a tear sheet for any rule-based strategy.
**Duration:** ~2 weeks.
**Builds:**
- vectorbt (or alternative) wired up to the Phase 1 data
- **Taiwan cost model:** 0.3% sell-side securities transaction tax + broker commission (default 0.1425% × discount; configurable). Slippage assumption (e.g. 0.05% per side).
- Position sizing logic respecting max 10% per position
- Buy-and-hold benchmark on 0050
- One reference strategy: SMA(20)/SMA(60) crossover
- Tear sheet output: total return, CAGR, Sharpe, Sortino, max drawdown, hit rate, average win/loss, turnover, total fees paid
- Walk-forward cross-validation scaffolding (used heavily in Phase 6)

**Exit when:** `backtest(strategy, start, end, capital=1_000_000)` returns a tear sheet for both the SMA crossover and buy-and-hold; SMA crossover's net return after costs is correctly lower than gross.

**Key decisions at kickoff:**
- vectorbt vs backtrader vs custom? Confirm vectorbt now or pivot.
- Slippage model: fixed bps, or volume-based?
- Commission: fixed default, or expose as a config?
- How is "the close" treated for next-day execution? Bar-on-close or bar-on-open?

**Pitfalls:**
- **Lookahead bias.** Easiest mistake to make: using future data to make today's decision. Test for it explicitly.
- **Cost model too generous.** A common failure: omit taxes, get a beautiful Sharpe, then watch it die in live. Be deliberately pessimistic.
- **Survivorship bias** (already noted in Phase 1).

**Hiring signal:** very high — a properly constructed backtester is the centerpiece of any quant portfolio.

---

### Phase 4 — Technical Strategies
**Goal:** implement and honestly evaluate 5–10 classical strategies, document which (if any) beat buy-and-hold after costs.
**Duration:** 2–3 weeks.
**Builds:** strategies — at minimum: SMA crossover, EMA crossover, RSI mean-reversion, Bollinger Band breakout, momentum (top-N), pairs trading on 2 correlated names, MACD, donchian breakout. Each with a tear sheet and a 1-page writeup.
**Exit when:** at least 6 strategies fully evaluated with tear sheets and writeups in `journal/strategies/`. Consolidated comparison table built.

**Key decisions at kickoff:**
- Single-asset (one ticker at a time) vs portfolio strategies (pick top-N from universe)?
- Parameter search policy: grid search with walk-forward validation, or fixed canonical parameters? (Latter is more honest.)

**Pitfalls:**
- **Overfitting via parameter search.** If you grid-search and pick the best, your in-sample Sharpe is meaningless. Use walk-forward validation; report out-of-sample results only.
- **Cherry-picking.** Don't quietly drop strategies that lost. Showing failures is the signal.
- **Multiple-comparisons problem.** Test 100 things, 5 will look great by chance. Account for this.

**Hiring signal:** high — but only if the writeups demonstrate statistical honesty. A "10 strategies all beat the market" report is a red flag to any quant.

---

### Phase 5 — Paper-Trading Simulator
**Goal:** a paper-trading layer that takes signals from a Phase 4 strategy and simulates execution with realistic Taiwan-market fills, runnable in real-time alongside live market data.
**Duration:** 1–2 weeks.
**Builds:**
- `BrokerSimulator` class implementing a clean broker interface (place/cancel/query orders, account state)
- Pessimistic fill model: assume worse side of bid-ask, volume-aware slippage, occasional rejections
- Order book simulation: open/cancel/fill state machine
- Real-time loop: pull live market data (from Fubon market data API or FinMind real-time tier), evaluate strategy, send orders to simulator, log everything
- Run one Phase 4 strategy live-paper for 2+ weeks; produce daily P&L tracking
- Optional: parallel run on Sinopac paper account if available, for cross-validation

**Exit when:** simulator runs continuously for 2 weeks without crashing; daily P&L is logged; a reconciliation report compares simulator results vs Phase 3 backtest output for the same period (they should be close but not identical).

**Key decisions at kickoff:**
- Real-time data source: Fubon Neo market API (no orders sent), FinMind, or polling TWSE?
- Logging strategy: SQLite, JSONL, what?
- How to model partial fills?

**Pitfalls:**
- **Self-deception.** A simulator can be made to confirm any backtest. Make it pessimistic on purpose.
- **Stateful bugs.** Real broker code has lots of state (order pending, partially filled, rejected, etc.). Test state transitions exhaustively.

**Hiring signal:** very high — "I built a broker simulator" is a strong portfolio story, especially with the honest pessimism note.

---

### Phase 6 — ML Strategies
**Goal:** apply ML to predict next-day (or next-week) direction or magnitude, evaluated honestly through the Phase 3 backtester.
**Duration:** 3–4 weeks.
**Builds:**
- Feature engineering pipeline: price-based features (returns, vol, momentum), technical indicators, calendar features, cross-sectional features (rank within universe)
- Train/validate/test split using **walk-forward** cross-validation (no random k-fold — that leaks across time)
- Baseline models in order: logistic regression → random forest → gradient boosting (XGBoost/LightGBM)
- Optional stretch: a simple LSTM or transformer if time permits
- Each model evaluated as a strategy in the backtester (predicted-direction → trade signal → tear sheet)
- Comparison report vs Phase 4 best technical strategy
- Feature-importance / SHAP analysis on the best model

**Exit when:** at least one ML strategy evaluated end-to-end with full tear sheet; walk-forward methodology documented; feature importances reported.

**Key decisions at kickoff:**
- Prediction target: next-day return sign (classification), next-day return magnitude (regression), or rank within universe (cross-sectional)? Cross-sectional rank is often the most successful and most realistic.
- Feature scope: just price/volume, or include macro / fundamentals?
- Universe-level model (one model for all 50 tickers) or per-ticker models? Universe-level is almost always better for small datasets.

**Pitfalls:**
- **Lookahead leakage in features.** Using a moving average that includes today's close to predict today's close. Audit every feature.
- **Walk-forward done wrong.** Refit at appropriate intervals; never use future data to fit past models.
- **Cross-validation done wrong.** Standard k-fold on time series is a beginner trap.
- **Beating the benchmark in-sample is not signal.** Only out-of-sample matters.

**Hiring signal:** very high — this is the phase the resume bullet points come from. "Built walk-forward-validated ML pipeline for Taiwan equities; gradient-boosted ensemble achieved [honest number] Sharpe out-of-sample after costs."

---

### Phase 7 — Alternative Data
**Goal:** add one alternative data signal to the strategy toolkit. Pick one source, do it well.
**Duration:** 3–4 weeks.
**Candidate sources** (pick ONE):
- **Chinese-language financial news sentiment.** Sources: cnyes.com, anue, MoneyDJ. Models: FinBERT-Chinese or similar pre-trained.
- **PTT / Mobile01 discussion mining.** Volume, sentiment, mention count.
- **Insider transactions** (公司內部人持股異動). Filed regularly with TWSE.
- **Foreign investor flows** (外資買賣超). Daily-published.
- **Margin balance** (融資融券). Sentiment proxy.

**Builds:**
- Ingestion pipeline for the chosen source
- Daily aggregation aligned to trading days
- Feature engineering and integration with Phase 6 ML pipeline
- A/B comparison: ML strategy with vs without the new signal
- Statistical test on the lift

**Exit when:** the new signal is integrated, evaluated, and a clean writeup explains whether it adds value (statistically and economically).

**Key decisions at kickoff:**
- Which alternative source. Lock this in early; don't change midway.
- Storage: how often, where, what schema?

**Pitfalls:**
- **Scope creep.** "I'll do news AND PTT AND insider trades" — no, you won't. Pick one.
- **Chinese NLP pitfalls.** Tokenization, traditional vs simplified, finance-specific vocabulary. Use a finance-tuned model.
- **Backfill problems.** Many alt-data sources don't have historical archives. You may only get forward-looking data, limiting your validation.

**Hiring signal:** very high if NLP route — combines DS/ML + NLP + finance + Chinese-language work, a rare and differentiated combination.

---

### Phase 8 — Productionization + Live Broker
**Goal:** the project goes from "side project that runs on my laptop" to "monitored system on a server." Live broker (Fubon Neo) integration starts here.
**Duration:** ongoing (initial pass: 3–4 weeks).
**Builds:**
- Move execution to a small VPS or always-on machine
- Scheduling (cron / systemd / Airflow) for nightly data pulls and model retraining
- Monitoring: heartbeat, error alerts (e.g. via Discord/Telegram webhook), daily P&L reports
- Live dashboard showing positions, P&L, recent trades
- Fubon Neo API adapter implementing the same broker interface as the simulator
- Risk controls: kill-switch, daily loss limit, position-size guardrails
- Logging and audit trail
- **Soft launch:** smallest possible real position size for 1+ month before scaling
- Documentation suitable for someone else to read

**Exit when:** system runs unattended for 30 days with no missed trading days, and live results are reconciled against backtest expectations weekly.

**Key decisions at kickoff:**
- Hosting: cheapest VPS, home machine + UPS, or cloud?
- How to handle broker outages, exchange holidays, system crashes mid-day?
- Real-money sizing path: start with what fraction of NT$1M?

**Pitfalls:**
- **The first live bug will cost money.** Have aggressive kill-switches and small initial position sizes.
- **Live ≠ backtest.** Slippage, latency, partial fills, rejections — all of which the simulator should approximate but never perfectly.
- **Broker certificate management.** Fubon Neo requires a `.pfx` certificate. Store carefully, never commit.

**Hiring signal:** very high — a working production system is the difference between "did a project" and "shipped a product."

---

### Phase 9+ — Future Expansions

Not committed; placeholders for after Phase 8 stabilizes:

- Intraday horizon (minute bars, intraday strategies)
- Universe expansion beyond Top 50 (TWSE all + TPEx)
- Futures and options
- Portfolio optimization (mean-variance, risk parity, Black-Litterman)
- Event-driven strategies (earnings announcements, ex-dividend)
- Reinforcement learning approaches
- Cross-market signals (US futures overnight, etc.)

---

## 4. Cross-Cutting Concerns

Things that span all phases and don't belong to any single one:

**Testing.** Every phase ships with tests. Tests that hit network/disk are marked `@pytest.mark.integration` and run separately from the fast unit-test suite.

**Honest evaluation.** No phase is "done" without an honest writeup. Wins and losses both reported. The journal log is part of the deliverable.

**Cost discipline.** Total project budget should be <NT$1000/month for data + hosting through Phase 7. Phase 8 may add VPS costs.

**Survivorship bias.** Acknowledged in every backtest report until Phase 9 expands universe handling.

**Document hygiene.** `CLAUDE.md`, this file, and per-phase specs stay in sync. When in doubt, this document is the source of truth for *intent*; `CLAUDE.md` is the source of truth for *current state*.

---

## 5. How to Use This Document in Future Chats

Three usage patterns:

### Pattern A — Phase planning
At the start of each phase, paste sections 1, 2, and the relevant Phase N section into a fresh chat with this prompt:

```
I'm about to start Phase N of this project. Here's the master plan
[paste sections 1, 2, and Phase N].

Help me think through the "Key decisions at kickoff" and produce a
detailed PHASE_N.md spec ready to hand to Claude Code, similar in
shape to PHASE_0.md.
```

### Pattern B — Mid-phase blockers
When stuck on something specific within a phase, paste only sections 1, 2, and the current phase section. Ask the specific question.

### Pattern C — Post-phase review and update
At end of each phase, paste this whole document plus the phase's deliverables and journal entries:

```
I just finished Phase N. Here's the master plan, the PHASE_N spec,
and my journal entries from the phase.

Help me: (1) identify what should be updated in this master plan
based on what I learned, (2) produce a brief retrospective, and
(3) prep the kickoff for Phase N+1.
```

---

## 6. Progress Tracker

| Phase | Status | Started | Ended | Notes |
|---|---|---|---|---|
| 0 — Foundation | Done | 2026-05-10 | 2026-05-11 | Repo, tooling, conventions, universe, abstract `DataSource`, journal scaffold all in place |
| 1 — Data Pipeline | Done | 2026-05-11 | 2026-05-17 | 50/50 tickers backfilled; 111 tests passing; all exit criteria met. Retro: `journal/phase_1_retro.md`. Open: FU-05 (pre-TWSE prices for 6669/7769/6919) must be resolved before Phase 2 starts. |
| 2 — Dashboard | Done | 2026-05-20 | 2026-05-21 | All exit criteria met; 31 new tests (160 total); all 50 tickers render in <2s warm. Retro: `journal/phase_2_retro.md`. |
| 3 — Backtester | Done | 2026-05-30 | 2026-06-13 | Custom thin engine; T+1 execution; Taiwan cost model; `WalkForwardSplit`; SMA crossover reference strategy; full tear sheet. Retro: `journal/phase_3_retro.md`. |
| 4 — Technical strategies | Done | 2026-06-14 | 2026-06-22 | 6 single-asset strategies + CrossSectionalMomentum; portfolio runner (ADR 0005/0006); ~310 tests passing. Retro: `journal/phase_4_retro.md`. Pre-Phase-5: ADR 0007 stock split fix. |
| 5 — Paper sim | Pending | — | — | — |
| 6 — ML strategies | Pending | — | — | — |
| 7 — Alt data | Pending | — | — | — |
| 8 — Production + live | Pending | — | — | — |

Update this table at every phase transition.
