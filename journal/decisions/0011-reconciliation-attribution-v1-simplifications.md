# 0011 — Reconciliation Waterfall v1 Simplifications

**Date:** 2026-07-23
**Status:** Accepted

## Context

`generate_reconcile_report` (`broker/reconcile.py`, Phase 5 build-sequence step
14) implements `ADR 0008`'s attribution reconciliation (D2/D3) as a single-call
comparison: one `SimResult` against one frictionless `BacktestResult` reference,
matching the locked demonstrator in `PHASE_5.md §1`
(`generate_reconcile_report(sim=replay, reference=reference)`, `replay` being
the pessimistic run under `VolumeParticipationFiller` + `NettingSettlement`).

Designing the waterfall (`PHASE_5.md §7`) surfaced two sub-decisions that
`ADR 0008`/`0010` don't fully specify:

1. How to compute the `raw_vs_adjusted` tripwire line when every friction
   (liquidity, slippage, settlement lag, corporate actions) is entangled in a
   single pessimistic sim run, rather than isolated one at a time.
2. How to cause-tag a `PARTIAL_FILL` event's underfill in the order-level
   audit. `simulator.py::_resolve_order` computes the executed quantity as
   `min(volume_ceiling, cash_ceiling, remaining)`, but neither ceiling is
   recorded in the `PARTIAL_FILL` payload — so a partial fill's *reason*
   (illiquid market vs. insufficient buying power) isn't currently
   recoverable from the event log.

## Decision

**D1 — `raw_vs_adjusted` is computed as a residual bucket, not a clean
isolation.**

```
total_gap        = sim_return − reference_return
raw_vs_adjusted  = total_gap − liquidity_underfill − float_reliance
```

`liquidity_underfill` and `float_reliance` are the two lines we can measure
directly (from the fill model's volume cap and the settlement ledger's
dated-cash-flow lag, respectively, by re-folding the sim's own event log).
Whatever remains once those are subtracted out is bucketed under
`raw_vs_adjusted`. This is an honest catch-all, not proof of causal
attribution — an unmeasured bug anywhere would also surface here, not only
corporate-action mishandling. The tolerance band is loose (~5bp of initial
equity, per `PHASE_5.md §7.3`) specifically because of this; a genuine
corporate-action bug (the `ADR 0007`-class ~7% back-adjustment error) is an
order of magnitude larger than the band, so the check retains teeth despite
being a residual rather than an isolation.

*Rejected alternative:* comparing the sim's own `DIVIDEND`/`SPLIT` event
totals directly against the raw corporate-actions source table. This is a
real, buildable check, but it validates the sim's internal bookkeeping
correctness against ground truth — which is already what oracles O8/O9 test
(on hand-built fixtures). Building it into `reconcile.py` as well would
mostly duplicate that coverage on real multi-year data rather than add a new
signal, for a meaningfully more complex implementation.

**D2 — `PARTIAL_FILL` underfill defaults to `"liquidity"` cause in the order
audit.**

`VolumeParticipationFiller` is the canonical Phase 5 preset (`CLAUDE.md` D10,
`PHASE_5.md §1`), so the volume ceiling is expected to dominate as the
practical cause of most partial fills over the cash ceiling. Distinguishing
the two exactly would require either extending the `PARTIAL_FILL` payload
with the binding ceiling, or independently recomputing both ceilings inside
the audit from stored bar volume and account buying-power history — deferred
as unnecessary complexity for v1. `REJECT` events are unaffected by this: they
already carry an exact `RejectionReason` in their payload and need no
defaulting.

## Consequences

- `generate_reconcile_report`'s per-line rigor is intentionally bounded: it
  answers "does the simulator's pessimistic total cost reality-check against
  the frictionless backtester," not "exactly how much did each friction axis
  cost, in isolation." That stricter question is a different, heavier
  analysis (see below), not something this function's single-call signature
  is trying to answer.
- The cleaner, controlled-variable version of this analysis is available
  **without changing `reconcile.py`'s signature or `ReconciliationReport`'s
  shape**: because `BrokerSimulator`'s frictions (`fill_model`, `settlement`)
  are already independently swappable per `ADR 0008` D1, calling
  `generate_reconcile_report` repeatedly across an ablation matrix of
  presets — each reconciled against the same frictionless `reference` —
  isolates each friction's contribution one at a time (e.g. `FullFill` +
  `InstantSettlement` vs `reference` isolates raw-vs-adjusted/implementation
  noise alone; `VolumeParticipationFiller` + `InstantSettlement` adds
  liquidity alone; etc.). This was discussed and intentionally deferred
  rather than built now — a natural fit for `scripts/phase_5_reconcile.py`
  (build-sequence step 15) if the stricter analysis is ever needed.
- If reconciliation is ever found to mask a real bug behind a too-loose
  `raw_vs_adjusted` band, or `PARTIAL_FILL` cause accuracy becomes
  load-bearing for a later report, revisit via: (a) the ablation-matrix
  technique above, and/or (b) adding a `binding_ceiling` field to the
  `PARTIAL_FILL` payload (`simulator.py::_fill`) so cause-tagging becomes
  exact instead of defaulted.

## Related

- Parent: `ADR 0008` (shared execution core — D2 independent oracles, D3
  reconciliation as attribution, D1 swappable presets).
- Sibling: `ADR 0010` (order lifecycle — defines `PARTIAL_FILL` and the
  `RejectionReason` taxonomy this ADR tags).
- `PHASE_5.md §4.10`, `§7` (reconciliation report and attribution waterfall
  spec).
