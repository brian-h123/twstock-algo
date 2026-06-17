# 0006 — Phase 4 Portfolio Runner: Cross-Sectional Strategies, Allocation, Cadence, and Trade Accounting

**Date:** 2026-06-17
**Status:** Accepted

## Context

Phase 4 (the "4b" block) builds the multi-asset portfolio path. `ADR 0005` resolved the cash/settlement and execution-ordering half of the deferred "multi-ticker portfolio semantics" question flagged in the Phase 3 retro. This ADR resolves the remainder: how a cross-sectional strategy expresses its signals, how those signals become target weights, when the book is rebalanced, and how the resulting multi-position, partially-rebalanced book is reported.

Two prior locks constrain the design. `ADR 0004` decision 3 locked the single-asset `Strategy` ABC (`generate_signals(bars) -> pd.Series` in {-1, 0, +1}) as the integration contract for Phases 4–6; it must not change. `ADR 0004` decision 5 deferred "multi-asset portfolio constraints" to later phases; this ADR is where that lands for Phase 4, under the long-only, all-or-nothing, market-order, daily constraints still in force. The portfolio path is therefore **additive** — a sibling to the single-asset path, never an edit to it. As Findings sets out, the deeper reason for a sibling rather than a unified universal method is the stateless-strategy decision (`ADR 0004` §3), not merely the ABC lock.

The design was informed by a targeted read of how established engines structure these layers (backtrader, zipline, QuantConnect Lean, bt); the observations are summarised in Findings. As with 0004, the reference reading is the *comparison*; this ADR is the *decision*.

Scope boundary: this ADR governs cross-sectional **rank-and-select** strategies (momentum, cross-sectional mean-reversion) and the runner that executes them. Relational strategies that require shorting (pairs trading) remain deferred to Phase 5 and are explicitly out of scope. The interfaces here must not *block* shorts, but they are not designed around them.

This ADR deliberately bundles five coupled pieces (strategy interface, allocation, cadence, tie-breaking, trade accounting) because they form one subsystem and none is meaningful read alone. The fracture lines, should it ever be split, are recorded in Consequences.

## Findings

### Strategy interface — a sibling, not a second method, not a fused class

Two established patterns exist for cross-sectional strategies, and the choice between them turns on statelessness, not on the ABC lock:

- **backtrader** exposes every instrument to a single `next()` via `self.datas`; single-asset is the degenerate one-feed case. One universal method, no single-vs-multi distinction.
- **zipline** separates cross-sectional factor computation (the Pipeline — `rank`, `zscore`, `top(n)` over the universe) from the per-bar order logic (`handle_data`). Its docs are explicit that the Pipeline separates alpha-factor computation from order placement. Cross-sectional ranking is its own component.
- No established engine puts two parallel signal methods on one strategy class.

The deciding factor is **not** the ABC lock — it is statelessness. backtrader's universal method is ergonomic *because its strategy is a stateful callback*: `next()` is invoked once per bar, the strategy carries state across calls, and it places its own orders. `ADR 0004` decision 3 deliberately rejected that model, making `Strategy` a stateless pure function (`bars → signals`, no cross-bar memory, no order placement) precisely because statelessness is what makes `assert_no_lookahead` a valid test — the audit perturbs future bars and checks that past signals are unchanged, which a stateful callback would void. So the universal method's main advantage does not transfer to this engine: copying backtrader's *shape* while having rejected its *mechanism* is not available to us. The sibling split is a **consequence of choosing a testable no-lookahead guarantee over the cleaner-but-stateful universal method** — not a workaround forced by an earlier lock. (This is the project's central trade laid bare: backtrader's universal `next()` is a genuinely clean design, and it is given up deliberately to keep the no-lookahead property *provable* rather than merely promised.)

A second, independent reason: the two signals are different *types*. A single-asset signal is a directional state per bar (long / flat); a cross-sectional signal is a real-valued score per name, meaningless in isolation, whose purpose is to be ranked against the others. The single-asset `Strategy.generate_signals(bars) -> Series` emits only {-1, 0, +1} and structurally cannot carry a magnitude. Forcing both through one method would unify their return types and push that difference into runtime branches; two honest types express it where the reader and the type system can see it. The zipline-style separation (a distinct cross-sectional component) is the fit: a **sibling ABC**, leaving the single-asset contract untouched and un-renamed.

The output must be a *rankable signal*, not necessarily a magnitude. Three score grades: **cardinal** (a real magnitude with meaningful spacing — momentum's trailing return), **ordinal** (rank only), **binary** (eligible / not — an SMA golden cross). A strategy supplies whichever grade its logic honestly justifies. Fabricating a magnitude where the strategy has none is dishonest and is forbidden by the tie-handling guardrail (decision 6).

Classification rule (decides which base a strategy extends):

- **Binary** per-ticker yes/no → single-asset `Strategy`, optionally lifted to the panel by `CrossSectionalAdapter`.
- **Cardinal or relational** → native `CrossSectionalStrategy`. Cardinal because the single-asset contract cannot emit a float; relational because the score needs the other names to compute at all. Momentum is **native** (cardinal): each name's trailing return is computable alone, but the magnitude *is* the signal, so it cannot be expressed through the binary adapter.

### Allocation is a holdings-agnostic scores→weights map

Lean and bt both separate "produce a target portfolio" from "trade toward it." bt makes it a pipeline (Select → Weigh → Rebalance, gated by a Run cadence algo); Lean separates the Portfolio Construction Model (Insights → PortfolioTargets) from the Execution Model (targets → orders). The shared invariant: the weighting step does **not** see current holdings — diffing the target against the book is the executor's job.

That boundary is precisely what makes allocation rules interchangeable. An allocator that peeked at current positions could not be swapped without re-reasoning execution. So the allocator is a pure `scores → target weights` function, and the reconciler owns the diff.

### Cadence is a gate, and generalises to three schools

Across the established tools, cadence falls into three schools — calendar, threshold/tolerance-band, and event/signal-driven. bt's gate-algo model (a predicate at the front of the pipeline that short-circuits the rebalance) is the cleanest fit for a daily-bar engine; Lean folds cadence into a single "next rebalance time" function. The gate must receive a *context* (date + current/target weights + change flags), not a bare date, so that one signature satisfies all three schools: calendar rules read the date, threshold rules read the drift between current and target, event rules read the change flags. Phase 4 ships calendar only; the richer context is what keeps threshold and event additive later without reshaping the calendar rules.

### Trade accounting — lot-consuming, two orthogonal axes

QuantConnect's Lean `TradeBuilder` (`Common/Statistics/TradeBuilder.cs`) is this exact problem in production. It is **not** a single FIFO-vs-average choice; it is two independent knobs:

- **Fill matching** — FIFO vs LIFO: which entry lot an offsetting fill closes against.
- **Fill grouping** — FillToFill / FlatToFlat / FlatToReduced: which sequence of fills counts as one reportable trade.

It consumes lots from a per-position queue and emits realised P&L per closed slice, which handles partial reductions natively. A partial *fill* (Phase 5 liquidity) is structurally identical to a partial *reduction* (a Phase 4 re-trim) — both are simply "fewer shares than the lot holds" — so building the ledger now makes Phase 5 require no round-trip rework.

The current `_round_trips` pairs the i-th buy with the i-th sell, assuming 1:1 whole-position entries/exits. Any re-trim breaks that pairing and silently corrupts `hit_rate`, `avg_win`, `avg_loss`, and `n_trades`. The lot ledger replaces the pairing primitive.

Two distinct questions, two layers (recorded explicitly because the terminology caused confusion during planning):

- *"What is the book worth each day?"* — the equity curve marks **open** positions to market, needing each position's running average cost (`Position.avg_cost`, already implemented). Drives Sharpe, drawdown, CAGR, total return.
- *"When a trade closed, did it win and by how much?"* — the trade metrics pair **closed** exits against entries, needing the lot ledger. Drives hit-rate, average win/loss, trade count.

`avg_cost` and the lot ledger are not alternatives. They answer different questions at different layers, and both are kept.

### A configurability principle

To keep "make it configurable" from becoming a reflex that erodes the honesty thesis, knobs are tiered:

- **Pure-reporting knobs** change only how a run is *described*, never its P&L. Cheap and safe to expose; lock a canonical default for cross-run comparability.
- **Return-changing policy knobs** change actual P&L. Configurable *interface*, but one locked canonical for every headline writeup, never tuned across runs to pick winners (the overfitting trap, `PROJECT_PLAN.md §3 Phase 4`).
- **Locked contracts** are structural correctness/honesty invariants and are not configurable at all (an ADR to change).

Trade-accounting grouping/matching are pure functions over the immutable `BacktestResult` (D10) — tier 1, freely configurable with a locked canonical. Allocation rule, tiebreak, and cadence are tier 2 — configurable interface, locked canonical. The cost model, `next_open`, sell-before-buy, and the strategy ABCs are tier 3.

## Decision

**Build the Phase 4 portfolio runner as a layered pipeline — cross-sectional scores → cadence gate → holdings-agnostic allocation → tie resolution → reconciliation to sells-then-buys → lot-based trade accounting — sharing the Phase 3 `Portfolio`, cost model, and T+1 execution timing. The single-asset path is unchanged.**

Implementation choices recorded here for `PHASE_4.md`:

1. **`CrossSectionalStrategy` sibling ABC.** `generate_scores(panel: dict[str, pd.DataFrame]) -> pd.DataFrame` (index = dates, columns = tickers, float scores; NaN = not eligible / abstain). Stateless, same purity contract as `Strategy` (0004 decision 3). The locked single-asset `Strategy` is neither modified nor renamed.

2. **`CrossSectionalAdapter` — a tightly-scoped Phase 4 convenience.** Lifts a single-asset `Strategy` to binary panel scores by applying it per ticker and reading its non-NaN long signal as `1.0`, NaN otherwise. **Binary only** — it cannot manufacture a magnitude; a strategy whose edge is a magnitude (e.g. ranking by *how* oversold the RSI is, not merely whether RSI < 30) must be a native `CrossSectionalStrategy`. Its value is concentrated in Phase 4: it lets the classical single-asset technical strategies appear in portfolio results with zero duplicated logic and, crucially, **zero re-auditing** — the binary signal was already verified lookahead-safe in single-asset form, and the adapter only reshapes it. That value is expected to fade after Phase 4: Phase 6 ML strategies are inherently cardinal (a predicted return or rank per name) and will all be native `CrossSectionalStrategy`, for which the adapter is useless. It is therefore built deliberately thin, documented as a classical-binary convenience, and load-bearing for nothing — if Phase 6 leaves it unused it is a small file deleted with no structural ripple. Centralising the binary lift in one audited place is the reason to prefer a named adapter over re-deriving the lookahead argument in an ad-hoc inline wrapper each time.

3. **Panel-aware lookahead audit.** `assert_no_lookahead` gains a panel variant that perturbs future rows across the panel and checks earlier scores are unchanged — valid only because `generate_scores` is stateless (mirrors 0004 decision 4).

4. **`Allocator` ABC, holdings-agnostic.** `allocate(scores_at_T: pd.Series, *, max_position_pct: float) -> pd.Series` of target weights. It does **not** receive current positions. Phase 4 ships `EqualWeightTopN(n)`: rank scores descending, take the top n, weight `1/n`, clamp each to `max_position_pct`. The constructor validates `n * max_position_pct >= 1.0` and raises otherwise (a configuration that can never fully invest is a config error, not a silent half-cash run). Fewer eligible names than n holds cash; it never errors.

5. **`RebalanceSchedule` with `should_rebalance(ctx) -> bool`.** `ctx` carries the date, current weights, proposed target weights, and signal-change flags. Phase 4 ships calendar implementations (`Monthly`, `Weekly`, `Daily`, `Quarterly`) resolved against `exchange_calendars` XTAI; the canonical schedule is **monthly, first XTAI trading day**. "Should I rebalance?" and "compute the target" are kept as separable steps so threshold rules (which need a target to measure drift against) drop in later. Cadence applies to the portfolio path only; the single-asset path stays event-driven and has no cadence.

6. **`TieBreakPolicy` — and alpha may not live here.** *(Load-bearing; numbered prominently like 0004 decision 3.)* A pluggable policy resolving "more eligible names than slots, with equal scores." Phase 4 ships `LiquidityTieBreak` (canonical — prefer the most-traded among the tied names) and `HoldAll` (honest reference baseline — hold all tied names, accepting more than n positions and sub-target weights). **Guardrail: a tiebreak may break ties only on grounds the strategy never claimed as signal (liquidity, hold-all, deterministic order). It must not encode alpha.** A tiebreak that prefers names on any return-predictive basis is a second trading signal that bypasses `generate_scores` and therefore the lookahead audit — unaudited alpha. If a tiebreak is constructed to move returns on purpose, that intent *is* alpha and belongs in the strategy's cardinal score, not the allocator. This is the same boundary 0004 drew around the stateless contract: the audit is meaningful only if all signal lives where it is audited.

7. **Reconciler: target weights + current book → orders, sells before buys.** Diffs the target against the held book, converts weights to shares as `floor(equity * weight / open_price)`, and emits all sells before all buys per `ADR 0005`. Odd-lot (零股) sizing is carried forward from Phase 3: round-lot-only would make most Top-50 names untradeable at NT$1M (one 1,000-share lot of a NT$1,000 name is the entire per-position budget), so any-integer-share sizing is retained, with the caveat that real odd-lot liquidity is thinner — a Phase 5 pessimism gap. This block replaces the fixed-fraction buy logic currently in `engine.py`.

8. **Dispatch shares one execution core.** `Engine.run` dispatches on strategy type: a single-asset `Strategy` uses the existing transition logic; a `CrossSectionalStrategy` uses cadence → allocate → tie-break → reconcile. Both share `Portfolio`, `TaiwanCostModel`, T+1 scheduling, mark-to-market, and `BacktestResult` construction. There is no second engine.

9. **Lot-based trade accounting, configurable, canonical FlatToFlat + FIFO.** Replace `_round_trips` with a lot-consuming ledger parameterised by grouping (FillToFill / FlatToFlat / FlatToReduced) and matching (FIFO / LIFO). The canonical for every Phase 4 writeup is **FlatToFlat grouping** (one round-trip per position lifecycle, so monthly re-trims do not inflate the trade count) **+ FIFO matching**. It remains a pure function over `BacktestResult.trades` (D10); any lens recomputes from a single run. `Position.avg_cost` is retained unchanged for the open-position basis and equity curve.

## Consequences

- The portfolio path is purely additive. `Strategy`, the engine's single-asset branch, `Portfolio`'s cash mechanics, and `avg_cost` are all unchanged. New surface: `CrossSectionalStrategy`, `CrossSectionalAdapter`, `Allocator` / `EqualWeightTopN`, `RebalanceSchedule`, `TieBreakPolicy`, the reconciler, and the lot ledger.
- `src/twstock/strategies/` gains cross-sectional strategies alongside single-asset ones, and the new runner modules need a home plus an import-boundary entry in `CLAUDE.md §7` (the `strategies/` boundary is currently undefined; Phase 4 must define it, mirroring the `backtest/` boundary).
- **Mixed reversibility — the natural fracture line if this ADR is split:**
  - The lot ledger (decision 9) is pure reporting; it can change without touching execution — the most independently reversible piece, and the most plausible candidate to spin out later.
  - The allocator and tiebreak (decisions 4, 6) are swappable behind their ABCs; reversible without engine changes.
  - The `CrossSectionalStrategy` contract (decision 1) becomes the Phase 6 integration point for ML cross-sectional scores. Changing it after Phase 4 closes is a breaking change to every cross-sectional strategy file — the least reversible decision here, exactly as the single-asset ABC is in 0004.
- The alpha-line guardrail (decision 6) is a **standing constraint**, not a one-time choice: every future allocator, tiebreak, and cadence rule is checked against it. It is the portfolio-path analogue of 0004's "grep for `shift(-`" review check.
- Phase 5 inherits the lot ledger with zero round-trip rework (partial fills reuse the partial-reduction path) and the cadence/allocator/tiebreak interfaces unchanged; only the broker implementation differs between backtest, paper-sim, and live.
- The `should_rebalance(ctx)` signature and `RebalanceSchedule` make the threshold and event cadence schools additive in later phases without reshaping the calendar rules.
- The strategy interfaces are horizon-agnostic: `generate_signals(bars)` and `generate_scores(panel)` are unaffected by bar frequency, as are the allocator, cadence, reconciler, and ledger. Intraday (Phase 9) is therefore a **performance** concern, not an interface one. A stateless strategy recomputes from full history each bar — trivial at ~2,500 daily rows, costly at ~100,000 minute rows — which is the price paid for rejecting stateful incremental updates. The fix is vectorised indicator precompute over the series (the strategy stays pure) or the vectorbt plug-in path `ADR 0004` already left open; the contracts survive intraday unchanged, only their implementation needs an optimisation pass. This is a flagged Phase 9 cost, not a redesign.
- Pairs trading (relational + shorts) remains out of scope. The interfaces do not block it, but are not designed for it; a later ADR introduces shorting and the relational-strategy treatment.
