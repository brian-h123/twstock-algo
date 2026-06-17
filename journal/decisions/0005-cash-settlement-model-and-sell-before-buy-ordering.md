# 0005 — Phase 4 Portfolio Engine: Cash-Settlement Model and Sell-Before-Buy Ordering

**Date:** 2026-06-16
**Status:** Accepted

## Context

`PROJECT_PLAN.md §3 Phase 4` extends the engine from single-asset strategies to multi-asset portfolio strategies (the "4b" block). The Phase 3 retro flagged multi-ticker portfolio semantics as the deliberately deferred open question; this ADR resolves the part of it that concerns cash.

Phase 3 (`ADR 0004` decision 5) explicitly deferred "multi-asset portfolio constraints" and "partial fills" to later phases. Phase 4 picks up the multi-asset path while keeping the all-or-nothing, long-only, market-order, daily constraints from Phase 3 in force. The existing `Portfolio` (`src/twstock/backtest/portfolio.py`) already holds a `dict[str, Position]` against a single shared `_cash` pool, and already credits sells / debits buys at fill time — so the data structures for a real portfolio exist. What Phase 4 must decide is the *semantics* that were never exercised when behaviour was dominated by one position at a time: when a single decision both sells and buys on the same bar, (a) what cash-availability model the engine assumes, and (b) in what order those sells and buys execute.

These two questions are recorded in one ADR because they are causally linked: the sell-before-buy ordering is only valid under the cash model chosen here. The supporting evidence — TWSE settlement mechanics and the dominance argument — is summarised in Findings; this ADR is the decision.

## Findings

### TWSE settles net, not trade-for-trade

The naive model is trade-for-trade settlement, where each trade settles independently and a buy needs its full cash in hand regardless of any same-day sell. TWSE does not work this way.

- TWSE operates a **T+2 Delivery-versus-Payment cycle with daily multilateral net settlement** (TWSE clearing documentation, `twse.com.tw/en/clearing`). The clearinghouse nets each account's funds and shares receivable/payable for a settlement date.
- Investors complete settlement by **10:00 (securities) / 11:00 (funds) on T+2**, and there is **no general prefunding regulation**, though individual brokers may impose one to limit default risk.
- Consequence: selling A and buying B on the same trade day produces a single **net** cash obligation settled on T+2. The proceeds of A fund the purchase of B *through the netting* — cleared cash from the sale of A on trade day is not required to buy B.

This is why same-day rotation is normal and fundable in TWSE, and it is the real-world fact that makes the modelling choice below honest rather than generous.

### The core distinction — settlement is *assumed*, not *modelled*

The engine does not implement T+2. `Portfolio.sell` runs `self._cash += total_cash_in` the instant a sell fills, and the next buy on the same bar may spend it. The Phase 4 model is therefore an **instant-settling shared cash pool** — there is no settlement lag anywhere in the code.

T+2 net settlement is not what the engine reproduces; it is what *justifies* the instant model. For a long-only strategy that spends only within its netted means, the netted real-world outcome and the instant-model outcome resolve to the **same realised book**. The settlement plumbing is invisible to P&L precisely because the strategy never depends on the gap between "cash I hold" and "cash I am owed." Modelling the lag would add machinery and change nothing about Phase 4 results — so it is deferred, not built.

The instant-settling pool bundles five assumptions, each safe in Phase 4 and each named so later phases know what they inherit:

1. **Immediate usable proceeds** — a fill's cash is available to other fills on the same bar (justified by netting).
2. **No prefunding** — netted/unsettled funds can be deployed freely.
3. **Every trade settles** — no failed delivery or payment; no settlement risk.
4. **Free T+2 float** — no financing cost for money owed but not yet received.
5. **Single fill point per bar** — all of a bar's trades occur "at once" at T+1's open, so intra-day settlement ordering is moot.

### Sell-before-buy is weakly dominant, not a risk tradeoff

The question "always sell before buy, or only when cash is tight?" dissolves under the Phase 4 assumptions, because selling first is provably never worse:

- Every order on the bar fills at one reference price (T+1 open), and per-trade costs are fixed fractions of notional independent of execution order. The only coupling between orders is the shared cash pool.
- Executing all sells first makes the cash available to the buy phase exactly `initial_cash + Σ(sell proceeds)` — its maximum possible value. Buy size is monotonic in available cash (`shares = floor(min(target, cash) / price)`), so every buy receives at least as many shares as under any other interleaving. The final book is therefore at least as close to target, and never further.
- When cash is not binding, every ordering produces the identical book.

So "always" and "only-when-tight" are **outcome-equivalent** — when cash is not tight the order is irrelevant anyway — and "always" is the simpler, deterministic, more testable of two equivalent rules.

The concrete failure mode of *not* ordering sells first, in this codebase specifically: the engine sizes buys as `spendable = min(target_amount, portfolio.cash)` (`engine.py`), so a cash-starved buy does not raise — it silently buys fewer shares, and leftover cash from a later sell sits idle for the rest of the bar. On a rotation (name X leaves the held set, name Y enters), buying Y before selling X **silently under-invests**. That is worse than an exception, because it corrupts the result with no signal. Sell-before-buy removes the failure entirely.

### Where the assumptions break

The instant model and the sell-before-buy rule are valid only under the five assumptions above. Each is shed in a specific later phase; this is the canonical list those phases cite when they revisit:

- **Prefunding / settled-cash-only accounts** (Phase 5 simulator, Phase 8 live) — if cleared cash is required before buying, assumption 2 fails and same-day proceeds cannot fund a same-day buy. The simulator should offer a pessimistic "settled-cash-only" mode to measure how much a strategy leaned on netting.
- **True T+2 cash lag / cash management** (Phase 5) — modelling the account as genuinely T+2 means proceeds are unspendable for two days; a same-day rotation cannot self-fund without a cash buffer. Assumption 1 fails. This is real cash-management modelling that belongs in the simulator.
- **Non-fills, limit-down, settlement failure** (Phase 8 live) — Taiwan's ±10% daily price limit means a name can sit limit-down with no liquidity, so a counted-on sell may not execute and the dependent buy overdraws the account. Assumptions 1 and 3 fail. Cannot arise in a fill-at-open backtest; must be modelled live.
- **Partial fills** (Phase 5) — a partially filled sell frees only part of its cash, degrading "sell all, then buy all" into "buy what realised proceeds allow." Assumption 5 (and the precondition for clean sell-before-buy) fails. This is exactly the case where a pre-computed order sequence becomes only approximately optimal.
- **Intraday timing + float financing** (Phase 9) — at minute bars, sell-before-buy becomes a *temporal* fact (a 09:05 sell cannot fund a 09:02 buy), and at scale the T+2 float begins to carry a financing cost. Assumptions 4 and 5 fail.

### Alternatives considered

- **Model strict T+2 settled cash in Phase 4** — rejected. For a long-only strategy spending within netted means it produces the identical book, so it adds machinery and a cash buffer that real TWSE rebalancing does not require, while yielding no new information at this phase. Belongs in the Phase 5 simulator as a pessimistic mode.
- **"Only-when-tight" conditional sell ordering** — rejected. Outcome-equivalent to always-sell-first, but adds a state-dependent branch and a definition of "tight," costing testability and determinism for zero benefit.
- **Buy-first / insertion-order execution (the Phase 3 status quo)** — rejected. On rotations it causes silent under-investment via `min(target, cash)` buy sizing.
- **Trade-for-trade (gross) settlement assumption** — rejected. Does not match TWSE net settlement; would force a cash buffer that the real market nets away.

## Decision

**The Phase 4 portfolio engine models an instant-settling shared cash pool, and executes all sells before all buys on any bar that does both.**

Implementation choices recorded here for `PHASE_4.md`:

1. **Cash model: instant-settling single pool.** A sell credits cash at fill, and that cash is immediately available to same-bar buys. No settlement lag, no prefunding requirement, no settlement-failure modelling in Phase 4. This ratifies the existing `Portfolio` behaviour as the deliberate Phase 4 portfolio semantics, not an incidental artifact of the single-asset implementation.
2. **Execution order: sells before buys.** On any bar that executes orders, partition the pending orders and execute all sells, then all buys. Valid under all-or-nothing instant fills at a single reference price (T+1 open). This replaces the current insertion-order execution in `engine.py`.
3. **The cash model is justified by, not modelled on, TWSE T+2 net settlement.** The justification holds only for long-only positions spending within netted means; recorded so later phases know the boundary.
4. **Scope.** This ADR governs the daily, long-only, all-or-nothing, market-order portfolio path only. It does not authorise partial fills, short positions, limit orders, or intraday execution — all remain deferred, consistent with `ADR 0004` decision 5.
5. **Deferral is explicit.** The five assumptions and the phase that breaks each are listed in Findings. Introducing settled-cash/prefunding modelling, partial-fill-aware ordering, live non-fill handling, or intraday timing each requires a new ADR (or a documented extension of this one) — the same gate `ADR 0004` placed on the execution model.

## Consequences

- `Portfolio`'s cash mechanics need no change for 4b. The engine's order-execution block does: it must partition sells-before-buys instead of iterating pending orders in insertion order.
- Backtest results for rebalancing strategies are marginally more optimistic than a strict settled-cash account would produce. The gap is exactly the free T+2 float; it is documented and bounded rather than hidden.
- **More reversible than the Phase 3 contracts.** Unlike the `Strategy` ABC (`ADR 0004`), this is an internal modelling choice, not a public contract downstream strategies depend on. Swapping the instant pool for a settled-cash model later changes results but breaks no strategy file — which is precisely why the per-phase deferral approach is safe here.
- Phase 5's simulator inherits this cash model as its **optimistic baseline**; the pessimistic modes (settled-cash-only, partial fills, rejections) are added there, and each diverges from this baseline in a known direction — making the baseline a useful reference rather than a hidden assumption.
- The tear sheet must surface **turnover and total tax paid**, so the cost the instant model does not penalise (the float) and the cost it does (the 0.3% sell-side tax on every rebalance) are both visible.
- Sell-before-buy fires only on order-executing bars; which bars those are is set by the rebalance cadence, decided in `ADR 0006`. The two interlock but are recorded separately.
