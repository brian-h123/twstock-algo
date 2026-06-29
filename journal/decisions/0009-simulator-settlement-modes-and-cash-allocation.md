# 0009 — Simulator Settlement Modes and Cash-Shortfall Allocation

**Date:** 2026-06-25
**Status:** Accepted

## Context

`PROJECT_PLAN.md §3 Phase 5` builds the paper-trading simulator. `ADR 0005`
fixed the Phase 4 portfolio engine on an **instant-settling shared cash pool**
with **sell-before-buy** ordering, and explicitly deferred two things to the
Phase 5 simulator: a pessimistic *settled-cash-only* mode, and *true T+2 cash
lag* modelling. `ADR 0005` also pre-authorised the mechanism for picking them
up — "introducing settled-cash/prefunding modelling … requires a new ADR (or a
documented extension of this one)." This ADR is that new ADR.

This ADR is a **child of `ADR 0008` (Shared Execution Core)**: the
`SettlementModel` decided here is one of the configurable components of the
shared execution core, and the backtester's `InstantSettlement` and the
simulator's `Netting` / `SettledCashOnly` are presets over that core rather than
properties of two separate systems.

Two questions are resolved here together because they are causally linked:

1. **What settlement model(s) does the simulator run, and which is canonical?**
2. **When target buys exceed available buying power, how is the shortfall
   allocated across orders?**

They are linked because "available buying power" is *defined by* the active
settlement model — the allocation rule in (2) cannot be specified until (1)
fixes what counts as spendable cash on a given bar.

A third, narrower matter rides along: the simulator processes corporate actions
as position events on raw prices (the raw-plus-events `ReplayFeed` decision),
rather than reading pre-adjusted prices as the backtester does. The **cash**
component of a dividend therefore needs a settlement schedule of its own, which
is a settlement-model concern and is decided here. The **share and basis**
components are deterministic position arithmetic with no modelling alternatives;
they are specified in `PHASE_5.md`, not in an ADR.

This ADR records the analysis and the decision. Implementation lands in Phase 5;
the build notes are at the end rather than as a deferred patch, because unlike
`ADR 0007` this is new construction, not a fix to existing code.

## Findings

### The Phase 4 cross-sectional cash bug was an estimation-and-allocation failure, not a settlement-timing failure

The first cross-sectional integration test raised `InsufficientCashError`. The
reconciler sized all buys at T's close (`floor(equity × weight / close)`) and
executed them one-by-one at T+1's open + slippage. Three causes compounded:

- **Stale-price sizing** — close-based sizing under-estimates cost when the fill
  is at open + slippage; each buy costs slightly more than budgeted.
- **Unbudgeted commissions** — ten sequential buys accrue 10 × the NT$20 minimum
  (NT$200+) that the reconciler never reserved against the pool.
- **Rotation asymmetry** — on a rotation bar, sells free proceeds at open net of
  costs, which are smaller than the T-close marks used to size the buys; the new
  book targets the higher equity, so required cash exceeds arrived cash.

The failing orders were **full-sized rotation buys (NT$50–100k notional), not
NT$20-floor dust.** The pool was structurally depleted by the time the 7th–8th
buy executed.

Critically, **every one of these causes occurred under instant settlement** —
there was no settlement lag anywhere in the code when the crash happened. None
is a settlement-*timing* error. Implementing T+2 would not have prevented the
crash, and keeping instant settlement forever does not block the fix. The bug
and the settlement model are **orthogonal**, and the retro's grouping of "T+2
modelling" with "proportional allocation" as one bundle of cash work conflates
them.

`ADR 0005` proves the orthogonality from the other side: for a long-only
strategy spending within netted means, the instant model and a true-netted-T+2
model resolve to the *identical realised book*. T+2 modelling therefore changes
nothing about the canonical strategy's returns; its value is **honesty about
float reliance**, which is additive and separate from correctness.

### TWSE settles net; the realistic funding rule is netting

`ADR 0005` established, with TWSE clearing documentation, that TWSE runs a **T+2
DvP cycle with daily multilateral net settlement** and **no general prefunding
regulation**, so same-day sale proceeds fund same-day purchases *through the
netting*. This fixes the canonical funding rule for the simulator:

- **Netting (canonical).** Buying power on a bar = settled cash + the proceeds
  of same-bar (same settlement-cycle) sells. Same-bar rotations self-fund, as
  they do in real TWSE trading. This is the realistic baseline and the canonical
  reporting mode.
- **Settled-cash-only (pessimism).** Buying power = matured (settled) cash only;
  same-bar sale proceeds are unavailable until they settle on T+2. A fully
  invested rotation cannot self-fund and must lag. This is `ADR 0005`'s deferred
  "settled-cash-only" mode and measures exactly how much a strategy leaned on
  the netting float.

Individual brokers *may* impose prefunding to limit default risk (`ADR 0005`
notes this); that is a per-broker live concern for the Phase 8 adapter, not a
Phase 5 default.

### One dated ledger supports both modes; settled-cash-only is what justifies building it

Both modes ride a single substrate: a **dated settlement ledger** holding
`settled_cash` (spendable now) plus a queue of dated movements
(`amount`, `settlement_date`, `kind`) that mature into `settled_cash` when the
simulation date reaches their settlement date. The funding policy on top
computes buying power from this ledger.

Note the asymmetry that justifies the machinery: the **netting** policy barely
needs more than `ADR 0005`'s instant pool — for a long-only book within netted
means it reproduces the instant model's realised book (the equivalence above).
It is **settled-cash-only** that *requires* the dated queue, because it must know
each sell's settlement date to withhold its proceeds. Building the ledger is
justified by the pessimism mode, not by the canonical one. This is why both are
built: the canonical mode alone would not need the ledger, and the pessimism
mode is the deliverable that proves the ledger correct.

The ledger carries **two maturation schedules**: trade settlement at
**T+2 trading days** (XTAI calendar), and dividend cash at its **pay-date**
(below).

### Proportional allocation preserves cross-sectional intent

When `Σ target_buy_notional > buying_power`, the shortfall must be shared. Four
candidate rules:

- **First-come-first-served** (the Phase 4 patch). Fill in list order until the
  pool drains; the tail starves. The "first" is dict-iteration order — arbitrary
  and strategy-meaningless, so it silently injects a selection signal nobody
  chose. This is the `TieBreakPolicy` alpha-line violation in another form:
  execution order encoding an outcome. Rejected.
- **Equal-haircut / largest-first.** Cut equal share counts, or cut the biggest
  orders first. Distorts the weight *shape* — the thing a cross-sectional book
  actually targets — to optimise a cost detail. Wrong priority. Rejected.
- **Rank-priority.** Fund the highest-score names first. Most aligned with intent,
  but requires the cash handler to consume the strategy's *scores*, not just its
  *target weights* — punching through the stateless seam and re-introducing alpha
  into the execution layer. Rejected on the same guardrail as FCFS.
- **Proportional.** Each buy gets `(buying_power / Σ target_notional) ×
  its_target`. Preserves the relative weight vector (scaled down), which *is* the
  cross-sectional strategy's intent; the book stays maximally close to the
  intended shape. **Order-independent** — the book is a pure function of
  `(weights, buying_power)`, regardless of loop order. **Chosen.**

Proportional is correct *because of what a cross-sectional target means*: the
weight vector is the alpha, the gross is incidental, so scaling preserves intent
and any other rule distorts shape or smuggles alpha. The desire underneath
rank-priority — concentrate in high-conviction names when capital-constrained —
is legitimate but belongs **upstream in the allocator** as a score-tilted
weighting scheme. Proportional shrinkage of a score-tilted vector already
concentrates in high-conviction names *for free*, with no alpha in execution,
`assert_no_lookahead` still confined to the strategy, and the `TieBreakPolicy`
guardrail intact. Score-tilting the allocator is a separate, optional decision
recorded as out of scope here.

This rule is a property of the **cross-sectional / portfolio path only**. The
single-asset strategies hold one position and have no shared-pool contention;
their sizing path is untouched.

### Cost-budgeting removes the over-commitment at the source

Proportional allocation handles a *genuine* shortfall fairly, but two of the
three bug causes (stale-price residual, unbudgeted commissions) are
**over-commitment**, not genuine shortfall — the pool was never as large as the
reconciler assumed. The lookahead-safe fix is to size against cost-adjusted
buying power: reserve estimated commission + securities-transaction tax +
slippage, computed from **known rates** (not future prices), before allocating.

`buying_power_net = buying_power − Σ estimated_costs`, where per-order
`estimated_cost = max(commission_min, notional × commission_pct) +
(sell ? notional × tax_rate : 0) + notional × slippage_bps`.

This is the salvageable kernel of a rejected redesign (reconcile-at-T+1-open,
see Alternatives): it achieves that redesign's *goal* — stop over-filling the
pool — using only T-close prices and fixed rates, touching no future data and
requiring no order type a real TWSE broker cannot submit. It does **not** attempt
to eliminate the close→open price gap: that gap is real slippage every trader
eats, correctly captured by sizing on T-close and filling at the open, and
surfaced honestly as a small partial fill on the residual rather than engineered
away.

### Corporate-action cash settles on its own schedule

The simulator processes dividends as position events on raw prices. A cash
dividend therefore enters the dated ledger as a **receivable maturing on its
pay-date**, sourced from FinMind `TaiwanStockDividend.CashDividendPaymentDate`.
Crediting at pay-date is strictly more honest than crediting at ex-date and uses
the same dated-queue machinery as trade settlement.

That FinMind's `CashDividendPaymentDate` is reliably the actual credit date is a
**documented modelling assumption**, not an asserted fact; a Phase 5 validation
task spot-checks several known 2016–2026 dividends against actual pay timing.

Taiwan Top 50 names commonly pay **combined cash-plus-stock dividends on one
ex-date** (`ADR 0007` notes the `cash_and_stock_dividend` first-class type).
Such an event fans out across the position's state variables on **different
schedules**: the **cash** component is a pay-date receivable on this ledger; the
**share multiplication and basis reduction** happen at **ex-date** and are
deterministic (specified in `PHASE_5.md`). This is the proof that the simulator
must decompose a combined event even though the schema names it one type — the
components demonstrably settle on different dates, so no single atomic adjustment
can represent it. (The backtester never hits this: its adjusted prices are
timeless, applying both components at ex-date, so the pay-date/ex-date
distinction is invisible to it. The simulator is the layer where it becomes
real.)

Consequence for reconciliation: on a combined-dividend **ex-date** bar, the
backtester has already priced in the cash the simulator still holds as a
receivable. The reconciliation dividend line therefore expects a gap equal to
the **accrued-but-unpaid cash dividend, closing to zero on the pay-date** — a
*predicted, accepted* divergence, not a correctness tripwire.

### Alternatives considered

- **Model strict T+2 settled-cash-only as the canonical mode** — rejected. It
  contradicts TWSE net settlement (`ADR 0005`), would forbid normal same-day
  rotations the real market funds via netting, and would make the canonical sim
  diverge from the backtester for a reason that is not real. Kept as the
  *secondary pessimism* mode instead, which is its correct role.
- **Keep `ADR 0005`'s instant pool unchanged in the simulator with no settled
  ledger** — rejected. Cannot measure float reliance and provides no pessimism
  mode; `ADR 0005` explicitly asked the simulator to add these. The dated ledger
  is the minimum needed to make settled-cash-only expressible.
- **Reconcile order share-counts at T+1 using the open price** — rejected.
  Directly violates the no-lookahead invariant (the execution bar's own price
  feeding the order sized for that bar). The target-notional variant (commit cash
  amounts at T, convert to shares at the open) avoids the lookahead but is **not
  submittable on a real TWSE broker** — both Fubon Neo and Shioaji order objects
  are quantity-based and TWSE trades share/lot quantities; there is no notional
  order primitive, so live the user would convert notional→shares pre-open
  anyway, collapsing back to a share count fixed from T-close. Breaks the sim/live
  symmetry that is the point of the phase. Its safe goal is captured by
  cost-budgeting.
- **Rank-priority cash-shortfall handler** — rejected; re-introduces alpha into
  the execution layer. The intent it serves is relocated to a score-tilted
  allocator upstream.
- **First-come-first-served / dict-order allocation** (the Phase 4 patch) —
  rejected; `TieBreakPolicy` violation, unfair, silent underfill.
- **Credit dividend cash at ex-date** — rejected as the default; pay-date is more
  honest and the ledger already supports dated maturation. Retained as a
  documented fallback if pay-date validation fails.

## Decision

**The simulator runs a dated settlement ledger with a swappable settlement
model. Netting is the canonical reporting mode; settled-cash-only is a mandatory
secondary pessimism mode. The backtester retains `ADR 0005`'s instant model
unchanged.**

**Cross-sectional cash shortfalls are allocated proportionally to target
notional, against cost-budgeted buying power. Conviction concentration, if
desired, is an allocator weighting scheme, never execution-layer logic.**

Implementation choices recorded for `PHASE_5.md`:

1. **`SettlementModel` abstraction**, three implementations sharing the
   configurable-interface / locked-default pattern:
   - `InstantSettlement` — `ADR 0005`'s model; backtester default; no dated
     ledger. Ratifies existing behaviour, unchanged.
   - `NettingSettlement` — simulator **canonical**; dated ledger; buying power =
     settled cash + same-bar sell proceeds. Reproduces the instant model's
     realised book for long-only-within-netted-means (`ADR 0005` equivalence),
     differing only by tracking `settled_cash` and exposing the float.
   - `SettledCashOnly` — simulator **pessimism**; dated ledger; buying power =
     matured settled cash only.
2. **Dated ledger.** `settled_cash` + a queue of `(amount, settlement_date,
   kind)`. Trades settle at **T+2 trading days** (XTAI). Dividend cash settles at
   **pay-date**. Each bar matures all entries with `settlement_date ≤ today`.
3. **Proportional allocation** on the cross-sectional path: when
   `Σ target_buy_notional > buying_power_net`, scale every buy by
   `buying_power_net / Σ target_buy_notional`, then apply the existing
   share/lot sizing. Orders that scale below the minimum economic size (cannot
   cover the NT$20 commission floor) are dropped and recorded as
   `cash_shortfall` underfill. Order-independent.
4. **Cost-budgeting** in the reconciler: size against
   `buying_power − Σ estimated_costs`, costs from fixed rates and T-close prices
   only. Lookahead-safe.
5. **Corporate-action cash** flows through the ledger as a pay-date receivable;
   share/basis mutations are ex-date deterministic arithmetic specified in
   `PHASE_5.md`. Combined `cash_and_stock_dividend` events decompose across the
   two schedules.
6. **Scope.** Daily, long-only path. This ADR governs cash modelling and
   allocation only; the order state machine, fill model, partial-fill seam, and
   rejection taxonomy are the order-lifecycle ADR. The two interlock — this ADR
   produces fair *intent* (sized, allocated orders); the order-lifecycle ADR
   enforces *reality* (the broker partial-fills or rejects against actual
   liquidity and funds) — exactly as `ADR 0005` interlocks with `ADR 0006` on
   cadence.
7. **Score-tilted allocator weighting is deferred** as a separate optional
   decision, recorded here only as the correct home for conviction concentration.

## Implementation Notes (Phase 5)

Narrow enough to hand to `PHASE_5.md` directly.

- **`src/twstock/broker/settlement.py`** — `SettlementModel` ABC and the three
  implementations; the dated-ledger data structure; `mature(today)`,
  `record_fill(...)`, `buying_power(today)`.
- **Allocator / reconciler** — add proportional scaling against cost-budgeted
  buying power on the cross-sectional path; emit `cash_shortfall` underfill
  events for dropped/scaled orders into the JSONL event log (event-sourced
  logging decision). Single-asset path untouched.
- **Corporate-action handler** — route the cash component of
  `cash_dividend` / `cash_and_stock_dividend` to
  `SettlementModel.record_dividend(amount, pay_date)`; share/basis mutations
  applied at ex-date per the `PHASE_5.md` handler spec.
- **Tear sheet / reconciliation** — surface the settlement-float attribution line
  (≈0 under netting, signed under settled-cash-only) and the intended-vs-achieved
  deployment stat broken down by underfill cause.
- **Validation task** — spot-check FinMind `CashDividendPaymentDate` against
  actual pay timing for several known events; if it fails, fall back to ex-date
  crediting with a documented note.

## Consequences

- **`ADR 0005` stands, layered not overturned.** The backtester keeps the instant
  pool; the simulator adds modes that each diverge from it in a known direction —
  the "optimistic baseline" `ADR 0005` anticipated. This is the new ADR `ADR 0005`
  pre-authorised.
- **Sell-before-buy survives as ordering; its justification downgrades.** `ADR
  0005`'s dominance proof relied on assumption 5 (single fill point per bar).
  Partial fills break that (`ADR 0005` says so under "Partial fills"), so
  sell-before-buy becomes *approximately optimal with realised proceeds feeding
  buy sizing*, not provably dominant. Behaviour unchanged; the claim weakens
  honestly.
- **The reconciliation report gains predicted lines.** Settlement float is a
  tripwire-adjacent line: ≈0 under netting (a non-zero netting-vs-backtester
  float is a bug signal), signed and meaningful under settled-cash-only (the
  float-reliance number). Dividend timing is an accepted divergence: an
  ex-date-to-pay-date gap equal to accrued unpaid cash, closing at pay-date.
- **The canonical cross-sectional demo must run in both settlement modes**, so
  settled-cash-only is exercised and the float-reliance comparison is produced,
  rather than building the pessimism mode and never showing it does work.
- **Later phases inherit a clean extension point.** Per-broker prefunding (Phase
  8 live adapter) is a fourth funding policy on the same ledger; intraday float
  financing cost (`ADR 0005` assumption 4, Phase 9) attaches to the dated queue.
  Neither requires reopening this ADR.
- **No backtester result changes.** The backtester's `InstantSettlement` path is
  untouched; only the simulator gains the ledger. Existing Phase 3/4 tear sheets
  are unaffected.
