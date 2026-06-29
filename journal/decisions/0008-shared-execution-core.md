# 0008 — Shared Execution Core: Backtester and Simulator as Configurations of One Engine

**Date:** 2026-06-25
**Status:** Accepted

## Context

While drafting the Phase 5 settlement ADR (now `ADR 0009`), a foundational
question surfaced that the per-component ADRs cannot answer on their own: *what
is the boundary between the backtester and the paper-trading simulator, and do
we need both?* The settlement, fill-model, and order-lifecycle decisions all
inherit their meaning from that boundary — if it is fuzzy, every decision
downstream inherits the fuzz.

This ADR resolves the boundary, and is therefore the **parent** of the two
Phase 5 component ADRs: the settlement-model ADR (`ADR 0009`) and the
order-lifecycle ADR (`ADR 0010`) are both written against the architecture
decided here.

The question is sharp because two intuitions collide. The first: a backtester
and a simulator differ only in their *time window* (historical vs upcoming
data), so everything else should be identical — otherwise the backtest is a
fantasy detached from reality. The second: the existing backtester (`ADR 0005`)
runs an instant-settling cash pool with unconditional full fills, which is
visibly *not* how a real market behaves. Together they suggest the backtester
should be made "more realistic" by adopting the simulator's friction. This ADR
explains why that conclusion is wrong, what the real relationship is, and what
architecture follows.

## Findings

### The backtester and simulator answer different questions by fixing different variables

The difference between the two is **not** the time window. It is *what varies
and what is held fixed*, and that follows from their purposes:

- The **backtester** asks: *does the strategy's logic have edge?* To answer
  cleanly it **holds execution friction fixed and idealised**, so the variance
  observed is attributable to the *strategy*, not to execution noise. Idealised
  fills are a deliberate **experimental control**, not a naive oversight. Let
  orders randomly partial-fill and reject in the backtest, and an
  underperforming result becomes uninterpretable — you cannot tell whether the
  *idea* failed or the *execution model* ate it. The two variables are
  confounded.
- The **simulator** asks: *does the strategy survive contact with execution
  reality?* It **holds the strategy fixed and varies execution friction** —
  partial fills, settlement lag, rejections — precisely to measure what friction
  costs.

So the relationship is not "the backtester is a worse simulator." It is two
experiments that deliberately fix different variables, connected by the
reconciliation report: *backtest return − simulator return, decomposed by
friction source* is exactly what execution costs the strategy. The pipeline
**backtester → simulator → live** answers a different question at each stage.

### The idealised backtest is a prerequisite for measuring friction, not an inferior simulator

The reconciliation experiment can only run if the backtester is a **clean
control**. The idealised, frictionless configuration is the *reference frame*
the simulator's results are diffed against. Pollute the backtester with friction
and there is no baseline to diff — the attribution waterfall has no zero point
and collapses.

This inverts the naive intuition: making the backtester "more realistic" does
**not** bring it closer to truth; it destroys the frame that lets us *quantify*
the distance to truth. The idealisation is the feature. `ADR 0005` already
proved the corollary for cash specifically — for a long-only book within netted
means, the instant pool and a true-netted model resolve to the *identical
realised book* — so the instant pool is not a naive cash model but a *free*
control: simpler, and (by the equivalence) lossless at this scope.

### Correctness comes from independent oracles, not from the engine reproducing itself

A shared core has one tempting but false correctness check: run the simulator at
**zero pessimism** (full fills, instant settlement, no rejections) and confirm it
reproduces the backtester. Under two *independently built* engines that
agreement would be a real cross-check — the double-entry-bookkeeping virtue of
two routes meeting. Under **one** engine it is a tautology: the zero-pessimism
simulator *is* the backtester with the friction dials off, so "does the engine
reproduce itself?" is `x == x`. It cannot fail, so it cannot inform — and a check
that cannot fail is not a weak check, it is **not a check**. It is therefore
**removed**, not demoted.

This forces the real question into the open: how is the simulator's plumbing
verified, given friction makes it diverge from the backtester *on purpose*? The
answer is **independent oracles** — trusted answers computed by a wholly separate
route that shares no code, and therefore no bug, with the engine. The simulator's
ending cash (produced by the order machine, the settlement ledger, the
event-fold) is checked against a flat blotter sum
`initial − Σ buys − Σ costs + Σ sells + Σ dividends`; its share count against a
plain fill tally; a split's effect against a by-hand `shares × ratio`,
`basis ÷ ratio`; and accounting identities (value conservation,
settlement-transfer neutrality, per-order quantity conservation) that must hold
from first principles. These *can* fail, so they actually test something. This is
the discipline Phase 4 already used — the lot ledger validated against
`_round_trips`, a separately-derived quantity, never against itself.

The division of labour is therefore strict, and it is the architectural payoff of
this ADR:

- **Independent oracles answer "is the simulator correct?"** They are the
  correctness gate. (Enumerated in `PHASE_5.md`.)
- **The reconciliation report answers "given it is correct, what does friction
  cost?"** It is an **attribution** instrument — it diffs the simulator against
  the frictionless backtester (the legitimate use of the frictionless preset: a
  *reference point* for friction's effect) and decomposes the difference by
  cause. It assumes both sides correct and measures their *intended* divergence;
  it does **not**, and cannot, prove correctness, because the two share the
  engine.

Conflating these two was the latent error a shared core invites: leaning on
reconciliation for correctness. Kept separate, each instrument does the one job
it can actually do.

### The simulator plays two roles; the exchange role degrades to the filler at daily granularity

A precise account of "the simulator mimics a broker" requires separating two
real-world actors the simulator must stand in for:

- The **broker** is an *intermediary*. It relays an order to the exchange and
  relays status back. It does **not** match the order. This is the
  submit / cancel / query / event-stream API (the order-lifecycle ADR).
- The **exchange** *matches* the order against the central order book by
  price-time priority, against other participants' liquidity. A faithful paper
  simulator must stand in for this too — order matching is owned by the
  exchange, not the broker, and a paper simulation that only mimicked the broker
  would never decide whether an order fills.

The data ceiling resolves how much of the exchange role we can build. We have
daily OHLCV bars and **no order book** — no view of other participants' resting
liquidity. Real price-time matching is therefore impossible, and intra-book
allocation rules (FIFO, pro-rata) never enter, because those distribute fills
among *competing orders at one price* and we have exactly one order and no
visible book. The exchange role thus collapses, at daily granularity, to a
**volume-participation filler**: fill up to a fixed fraction of **trailing
average daily volume as of T** — the last bar knowable before the order
executes — at T+1's open plus slippage, the daily-data stand-in for matching
against the liquidity actually there. The cap uses *trailing* volume
deliberately, **not** T+1's realised full-day volume: an order fills at T+1's
open (the first print of the day), when the day's total volume does not yet
exist, and a filler that needed it could not run live — breaking the sim/live
symmetry. The trailing-vs-realised variance is accepted, exactly as the
close-to-open price gap is accepted in sizing: both are honest estimates fixed
from what is knowable at decision time, with the gap to realisation an
unavoidable cost, not an error to engineer away. The filler **is** the
exchange-matching model, deliberately degraded to what daily bars support; real
order-book matching is the Phase 9+ intraday extension behind the same seam.

### One execution core, three presets

It follows that the backtester and the simulator are not two systems but **two
configurations of one execution core** — the `SettlementModel`, the fill model,
and the order lifecycle — parameterised over three axes:

`(settlement_model, fill_model, bar_feed)`

The named presets:

| Preset | settlement_model | fill_model | bar_feed |
|---|---|---|---|
| **Backtester** (default control) | `InstantSettlement` | frictionless / full-fill | historical |
| **Replay-simulator** | `Netting` (or `SettledCashOnly`) | volume-participation filler | `ReplayFeed` (historical) |
| **Paper-simulator** | `Netting` (or `SettledCashOnly`) | volume-participation filler | `LiveFeed` (upcoming) |

"Paper trading on future data" is simply the third preset (pessimistic core +
live feed), exactly as the `step(bar)` / `BarFeed` design already established.
And critically, friction becomes an **opt-in dial on any preset**: a historical
backtest can be run under simulator-grade friction (settled-cash-only + partial
fills) as a *research / stress-test mode*, over the full multi-year window
rather than the 2-week forward run — more statistically powerful — while the
**default** backtest stays frictionless so the control, and therefore the
attribution *reference* the reconciliation diffs against, always exists.

### Mature backtesters validate the idealised-default-with-opt-in-friction pattern

This is the standard architecture in the field, not a novelty: vectorbt fills at
close by default; zipline and backtrader default to near-frictionless fills with
**opt-in** slippage and commission models. The idealised default with friction
as a configurable layer is the validated pattern. *How those libraries structure
friction as opt-in dials on a shared execution path* is a focused question to
bring to the Step 0 reading — referenced for the friction-configuration design,
not for a matching engine we cannot build.

### The shared core is the target architecture; the backtester's migration is deferred and non-blocking

The unification is a target *architecture and interface contract*, not a Phase 5
rewrite. The backtester exists, is audited (~310 tests), and Phases 3–4 closed on
it. "Share the same execution core" must **not** become "rebuild the backtester
now" — that silently reopens Phase 3 and blocks the simulator, the precise
scope-creep-via-good-idea the project guards against.

The honest split: Phase 5 builds the **new** simulator code *to the shared
interface* from the start. The existing backtester's **internal** migration onto
that interface is a separate, deferred, non-blocking work-item — it changes **no
results** (the equivalence guarantees it), so it sits outside the Phase 5
critical path and is sized only once the simulator has proven the interface. Its
eventual home (its own phase, a slice of Phase 8, or an inter-phase ticket) is
decided then, not now.

### Alternatives considered

- **Two independent systems** (backtester and simulator built and maintained
  separately) — rejected. The execution logic drifts, is duplicated, and there
  is no shared interface for the reconciliation to diff against. The whole
  reconciliation experiment presupposes a common core.
- **Make the backtester realistic by default** (adopt friction / netting as the
  default cash model) — rejected. Destroys the frictionless control that makes
  friction *measurable*, and by `ADR 0005`'s equivalence changes nothing about
  the canonical realised book anyway — pure cost, zero information, and it
  removes the reconciliation baseline.
- **Rebuild the backtester onto the shared core during Phase 5** — rejected.
  Reopens audited Phase 3 code, blocks the simulator, and changes no results.
  Deferred as a non-blocking work-item instead.
- **Build a real order-book matching engine in the simulator** — rejected. No
  order-book data exists at daily granularity; FIFO/pro-rata are inapplicable
  with a single order and no visible book. The volume-participation filler is the
  honest approximation; real matching is a Phase 9+ extension behind the same
  seam.

## Decision

**The backtester and the paper-trading simulator are configurations of one
execution core, parameterised over `(settlement_model, fill_model, bar_feed)`.
The frictionless configuration is the deliberate experimental control and the
attribution *reference* for the reconciliation report; correctness is verified
by independent oracles, not by the simulator reproducing the backtester. Friction
is an opt-in dial on any configuration. The fill model is the exchange-matching
approximation, degraded to daily-bar fidelity.**

Choices recorded for `PHASE_5.md` and the child ADRs:

1. **One execution core.** `SettlementModel` (`ADR 0009`), fill model and order
   lifecycle (`ADR 0010`), and the `BarFeed` are the shared, configurable
   components. Backtester / replay-simulator / paper-simulator are presets over
   them, not separate stacks.
2. **Frictionless default is the control.** The default backtest preset is
   `InstantSettlement` + full-fill + historical feed. This is preserved as the
   experimental control and the reconciliation reference frame, not "upgraded."
3. **Friction is opt-in on any preset.** A historical run may be configured with
   simulator-grade friction for long-window stress-testing; this is a research
   mode, and the frictionless default is always available alongside it.
4. **The fill model is the exchange role.** It stands in for order matching,
   degraded to a volume-participation model by the daily-data ceiling. Real
   order-book matching is the Phase 9+ intraday extension behind the same
   interface.
5. **Phase 5 builds the new simulator to the shared interface.** The existing
   backtester's internal migration onto the core is **deferred and
   non-blocking**, changes no results, and is sized after the interface is
   proven. ML strategies remain Phase 6; the migration does not pre-empt them.
6. **Parent/child relationship.** `ADR 0009` (settlement) and `ADR 0010`
   (order lifecycle) are children of this ADR and are written against this core.
7. **Correctness via independent oracles; reconciliation via attribution.**
   Simulator correctness is gated by independent-oracle tests (flat-sum cash and
   share reconstruction, cost reconstruction, and the value-conservation,
   settlement-neutrality, and order-quantity-conservation identities), enumerated
   in `PHASE_5.md`. The reconciliation report is an attribution instrument that
   diffs the simulator against the frictionless backtester and decomposes the
   difference by cause; it does not prove correctness. **Zero-pessimism
   reproduction of the backtester is not a correctness check and is not used** —
   under one engine it is tautological.

## Implementation Notes (Phase 5)

- **The interface contract is the deliverable, not a rebuild.** Phase 5 defines
  and builds, in `src/twstock/broker/`, the shared seams: the `SettlementModel`
  ABC (`ADR 0009`), the fill-model / order-lifecycle interfaces (`ADR 0010`),
  consuming the `BarFeed` abstraction. The simulator is the first concrete
  consumer.
- **A run is a preset, not a class.** Prefer a single parameterised entry point
  configured by `(settlement_model, fill_model, bar_feed)` over divergent
  backtester/simulator classes, so the presets cannot drift.
- **The backtester is not touched in Phase 5.** It keeps its existing path; its
  migration onto the shared interface is a tracked deferred item with the trigger
  "shared interface proven by the Phase 5 simulator."
- **Step 0 reading gains a question:** how do vectorbt / zipline / backtrader
  structure opt-in slippage and commission as configurable layers on a shared
  fill path — to inform the friction-dial design here.

## Consequences

- **The reconciliation report is an attribution instrument, not a correctness
  proof.** The frictionless backtester is its *reference point* for measuring
  friction's effect, and the attribution waterfall (`ADR 0009` consequences)
  decomposes the simulator-vs-backtester difference by cause. Correctness is
  established separately, by independent oracles — because under one engine the
  simulator reproducing the backtester at zero pessimism is tautological and
  proves nothing.
- **`ADR 0005` is recontextualised, not reverted.** Its instant pool is now named
  as the *experimental control* of the backtester preset, justified by
  simplification-with-equivalence, not by being the "real" cash model. The
  framing changes; the code does not.
- **`ADR 0009` and `ADR 0010` are children of this ADR** and inherit the
  shared-core framing; each is written as a configurable component of the core,
  not as simulator-only machinery.
- **The backtester migration is a tracked deferred work-item**, explicitly
  outside the Phase 5 critical path, sized later. ML strategies stay at Phase 6.
- **Friction-as-dial unlocks long-window stress-testing** of any strategy
  without sacrificing the control — the genuine capability improvement behind the
  original question, delivered without reopening the audited engine.
- **Intraday real matching has a home.** When order-book data arrives (Phase 9+),
  a real matching model replaces the volume-participation filler behind the same
  fill-model seam, with no change to settlement, lifecycle, or the preset
  structure.
- **No backtester result changes in Phase 5.** The migration changes no results
  by construction; existing Phase 3/4 tear sheets are unaffected.
