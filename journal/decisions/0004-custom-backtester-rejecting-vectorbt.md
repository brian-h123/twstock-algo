# 0004 — Phase 3 Backtester: Custom Engine, Not vectorbt

**Date:** 2026-05-27
**Status:** Accepted

## Context

`PROJECT_PLAN.md §2` recorded the backtester as "vectorbt (provisional, confirm at Phase 3 kickoff)." The Phase 2 retro (`journal/phase_2_retro.md §8`) flagged the specific blocker: "Verify the vectorbt version against the current Python 3.12 / pandas 2.x stack and confirm compatibility before committing to it."

At Phase 3 kickoff the compatibility question still needed checking, but the deeper question was whether vectorbt is the right tool for this project's stated thesis — a portfolio piece for data science / ML engineering job applications where "value comes from rigorous engineering and honest evaluation, not claimed alpha" (`PROJECT_PLAN.md §1`).

The decision had to be made before any Phase 3 code was written because it determines the shape of `src/twstock/backtest/` and the contracts that Phases 4, 5, and 6 depend on. The comparison that informs it — a targeted read of backtrader and vectorbt source — is recorded separately in `journal/phase_3_references.md`. That document is the *comparison*; this ADR is the *decision*. The load-bearing sub-decisions surfaced by the reading (no same-bar execution, stateless strategy) are recorded here so downstream files have a canonical record to cite, not just an explanatory note in the references doc.

## Findings

### Compatibility — the original blocker is no longer a blocker

- vectorbt v0.28.4 (released Aug 2024) migrated the codebase to pandas 2.0 and broadened Python version support (PR #824).
- vectorbt v1.0.0 shipped on PyPI 2026-04-22 with explicit support up to Python 3.12.
- A local install/import was not performed for this ADR; the release-note evidence was sufficient to retire the compatibility concern before making the decision.

Compatibility is no longer the deciding factor. The question shifted from "is vectorbt feasible?" to "is vectorbt right for this project?"

### Performance scope — vectorbt's strength is irrelevant at our scale

Universe: 50 tickers × ~10 years × ~252 trading days ≈ 125,000 rows total.

At this scale a naive pandas backtester runs a single strategy in well under one second. vectorbt's speed advantage materializes when sweeping ~10,000 parameter combinations across thousands of tickers — the situation it was designed for. `PROJECT_PLAN.md §3 Phase 4` explicitly favors fixed canonical parameters over grid search to avoid the overfitting-via-search problem, so even parameter sweeps will stay modest.

The primary value proposition vectorbt offers does not apply to this project.

### Lookahead safety — the deciding argument

This is the core of the decision. The reference reading (`journal/phase_3_references.md`, "Headline finding") surfaced that the two most popular libraries sit at opposite ends of a lookahead-safety spectrum, and a purpose-built engine sits past both:

- **backtrader — lookahead is opt-*in*, and loudly labeled.** Safe by default: the `_runnext` loop processes the broker *before* the strategy each bar (data → broker → strategy), so an order created on bar T cannot fill before T+1. Confirmed in source — under the default `coc=False`, the market fill price resolves to `data.open[0]` of the bar that runs *after* order creation, i.e. T+1's open. Same-bar execution exists, but reaching it requires setting a flag literally named **`cheat`** (`cheat_on_open` in cerebro; `coc`/cheat-on-close in the broker).
- **vectorbt — lookahead is opt-*out*, and silent.** `Portfolio.from_signals` fills a signal on bar T at bar T's own price by default. A signal computed from T's close that fills at T's price has used end-of-day information to trade at end-of-day — and nothing errors. Safety requires the caller to remember to `.shift()` the signal array. This is the single most common retail backtesting bug in vectorbt projects, and it is exactly the failure mode `PROJECT_PLAN.md §3 Phase 3 Pitfalls` names as the #1 risk for this phase.
- **ours — lookahead is not a choice.** `execution="next_open"` enforces the T→T+1 boundary structurally. There is no flag and nothing to remember: the fill price (T+1 open) strictly postdates every piece of information the signal could have used (≤ T close).

The progression — **opt-in danger → opt-out danger → impossible** — is the cleanest justification for building custom, and it reframes the decision. This is not only "reject vectorbt." Neither popular library makes lookahead *structurally* impossible: adopting backtrader would leave a labeled escape hatch in the codebase; adopting vectorbt would leave a silent one. Structural impossibility is the single property this project's honest-evaluation thesis most needs, and a thin custom engine is the only option on the spectrum that delivers it — at the cost of writing the engine.

### Cost model — Taiwan's asymmetric tax does not fit vectorbt's API

Taiwan's securities transaction tax is 0.3% (0.1% for ETFs), applied on the sell side only. vectorbt's `Portfolio.from_signals(..., fees=...)` takes a single per-side commission rate, symmetric across buys and sells.

Expressing Taiwan's asymmetric tax cleanly in vectorbt requires dropping down to `Portfolio.from_orders` and writing custom per-order logic, which forfeits most of the library's ergonomic advantage. A custom cost model expresses the real-world rules directly:

```python
@dataclass(frozen=True)
class TaiwanCostModel:
    stock_sell_tax: float = 0.003
    etf_sell_tax: float = 0.001
    commission_cap: float = 0.001425    # statutory maximum, per side
    commission_discount: float = 0.6    # 6折 durable online baseline
    slippage_bps_per_side: float = 5.0
    etf_tickers: frozenset[str] = frozenset({"0050"})
```

### Phase 5 reuse — the architectural win

Phase 5 builds a paper-trading broker simulator with a stateful order-management interface (place / cancel / query orders, partial fills, rejections). vectorbt's notion of a "broker" is a vectorized fill simulator over signal columns, not a stateful object — it cannot be extended into a Phase 5 broker without writing the broker from scratch anyway.

A custom Phase 3 engine that defines a `Strategy` ABC and a small order-handling surface lets Phase 5 inherit those interfaces directly. The same `Strategy` subclass runs in backtest, paper-sim, and (eventually) live execution; only the broker implementation changes between modes. Net effort across Phases 3 and 5 is lower than the vectorbt path would have produced.

### Alternatives considered

- **backtrader** — the close architectural sibling for an event-driven daily engine, and the reference whose broker/order state machine most directly informs Phase 5. The project appears unmaintained as a runtime dependency, but `cerebro.py`, `strategy.py`, `broker.py`, `bbroker.py`, and `order.py` were read as references for the engine design and the Phase 5 broker state machine. Its safe-by-default-with-a-named-cheat model is the upper end of the lookahead spectrum above. Studied, not imported.
- **zipline-reloaded** — maintained fork of Quantopian's zipline. Complex setup, designed for batch simulation rather than rapid iteration. Overkill for the scope here.
- **bt** (Philippe Morissette) — simpler than vectorbt, similar tradeoffs without vectorbt's speed advantage. No reason to prefer over either custom or vectorbt.

## Decision

**Build a thin custom backtesting engine in `src/twstock/backtest/`. No vectorbt, no backtrader, no zipline as a runtime dependency.**

Implementation choices recorded here for `PHASE_3.md`:

1. Execution model is `next_open` only: signal computed at T's close, fill at T+1's open. The engine's `execution` parameter is a `Literal["next_open"]`; adding any other mode requires a new ADR.
2. `TaiwanCostModel` is always on. Tax rates (0.3% stocks / 0.1% ETFs) are hard-coded as dataclass fields; commission and slippage are configurable but never zero by default.
3. **`Strategy` is a stateless pure function**, exposed as an ABC with a single method `generate_signals(bars: pd.DataFrame) -> pd.Series`. It maps bars to target positions and holds no cross-bar state; all execution state lives in `Portfolio` and `Engine`. This is not merely a choice of interface shape — it is load-bearing for the lookahead audit (decision 4). `assert_no_lookahead` works by perturbing future bars and checking that earlier signals are unchanged, which is only a meaningful test when the strategy is a pure function of its input. A stateful callback strategy — backtrader's model, where the strategy persists across bars, mutates itself, and places its own orders — would void that audit. Reversing this decision means abandoning the audit, which is why it is the least reversible decision in the phase, by design. The same stateless contract is reused by Phase 4 (manual strategies), Phase 5 (paper-trading runner), and Phase 6 (ML strategies wrapped as `Strategy` subclasses).
4. **Lookahead safety is layered, and no opt-in cheat is exposed at any layer.** Three layers: indicator causality (no `.shift(-k)` for positive k), strategy-author responsibility (no use of future bars in `generate_signals`), and execution-layer enforcement (T+1 fills only). `assert_no_lookahead` verifies the middle layer by perturbing future data — valid precisely because the strategy is stateless (decision 3). Unlike backtrader (`cheat_on_open`/`coc`) and vectorbt (the same-bar `from_signals` default), the engine exposes no same-bar path at any layer. Removing structural impossibility — adding any escape hatch — requires a new ADR, the same gate as decision 1.
5. Hard Phase 3 scope: market orders, daily bars, single-asset strategies, long-only by default. Limit orders, partial fills, multi-asset portfolio constraints, and intraday support are explicitly deferred to later phases.
6. vectorbt and backtrader are studied as architectural references; observations recorded in `journal/phase_3_references.md` (drafted as step 0 of the Phase 3 build sequence; finalized at step 15 with retrospective insights).

## Consequences

- No new runtime dependencies for the backtester. `pandas`, `numpy`, `plotly`, and stdlib are sufficient.
- ~500–800 lines of engine code plus comparable test coverage, fitting the Phase 3 ~2-week budget.
- The `Strategy` ABC defined in Phase 3 becomes the integration contract for Phases 4, 5, and 6. Changing it after Phase 3 closes is a breaking change to every strategy file written downstream.
- The `BacktestResult` artifact is the *reporting* contract (see `PHASE_3.md` D10): the tear sheet and every future analyzer is a pure function over a complete run-result object, never coupled to engine internals. Keeping that artifact complete in Phase 3 is what keeps analyzer choices reversible later without an engine rewrite.
- Phase 5's broker simulator extends rather than replaces the order/portfolio interface defined in Phase 3.
- Not a one-way door. The public data API surface (`get_adjusted_history`, `get_universe`, etc.) is library-agnostic. If Phase 6 ML work ever needs large-scale parameter sweeps that exceed custom-engine performance, vectorbt can be plugged in for that specific use case without touching the data layer or the `Strategy` contract.
- Code review for every Phase 3+ commit adds an explicit check: search for `shift(-` in any new file. The mechanical `import-linter` enforcement of the `backtest/` import boundary remains a Phase 8 ticket.
- `TwseSource` and `FinMindSource` are unaffected by this decision. The 0050 backfill required for the benchmark (Phase 3 build sequence step 7) uses the existing FinMind path with no API changes.
