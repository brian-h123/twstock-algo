# 0013 — Deterministic Ticker Ordering in `reconcile()` and `budget_weights()`

**Date:** 2026-07-24
**Status:** Accepted

## Context

Validating the Phase 5 demonstrator's cross-sectional reconciliation
(`scripts/phase_5_paper_run.py`, build-sequence step 15) surfaced a
reproducibility bug that predates Phase 5 entirely. Running the exact same
frictionless `Engine` call — same strategy, universe, dates, allocator,
schedule — across three separate process invocations produced three
different final-equity values (NT$3,155,079 / NT$3,219,447 / NT$3,160,438
on a NT$1,000,000 start; a ~2% spread, far beyond rounding noise).

**Root cause:** `backtest/reconciler.py`'s `reconcile()` (line 108) and
`budget_weights()` (line 42) both build their working ticker set via Python
`set()` union/intersection over ticker-symbol strings:

```python
all_tickers = set(portfolio.positions.keys()) | set(clean_weights.index)
```

Python randomizes string hashing per process by default (`PYTHONHASHSEED`
unset), so `set` iteration order over these strings differs between
process invocations — while staying stable *within* a single process,
which is why isolated same-process test runs looked internally consistent
and the bug went unnoticed. Pinning `PYTHONHASHSEED=0` across two separate
processes reproduced identical results, confirming the mechanism directly.

**Why it mattered financially, not just cosmetically:** `reconcile()`'s
`all_tickers` order directly determines the returned `Order` list's
sequence. `sells`-before-`buys` as a *group* was already guaranteed and
tested (`test_all_sells_precede_all_buys`), but order *within* each group
was not. Both `Engine._run_cross_sectional` (Phase 4) and `PaperSession`'s
broker (Phase 5) apply/resolve orders sequentially in that exact list
order. When multiple same-bar orders compete for a shared, limited cash
pool, whichever order is resolved first — determined purely by that
process's arbitrary hash seed — gets priority; others are proportionally
squeezed (`budget_weights`) or partially filled/rejected (the broker's
cash ceiling). This is an accidental first-come-first-served effect
leaking into results through a completely unaudited side channel — the
same class of problem `ADR 0009` D6 ("cash-shortfall allocation =
proportional... FCFS/rank-priority encode alpha in the execution layer")
was written to prevent, except here it arrived through an implementation
accident rather than a deliberate design choice.

**This function is shared by both engines.** `engine.py`'s cross-sectional
path imports and calls `reconcile()` directly (`pending_orders =
reconcile(...)`, applied in list order with no independent reordering of
its own), and `PaperSession` submits to the broker in the same returned
order. So this bug has been live since Phase 4's cross-sectional portfolio
runner was built (`ADR 0006`) — it is not new to Phase 5, only discovered
here because Phase 5's reconciliation work was the first thing to actually
compare the same cross-sectional call across multiple process invocations.

## Decision

**Wrap the existing `set()` expressions in `sorted()`** in both
`budget_weights()` and `reconcile()`. Ticker symbols are fixed-width
numeric strings, so lexicographic sort coincides with numeric sort — a
simple, unambiguous, one-line change at each call site.

This tie-break carries no correlation with any strategy's score or
signal, so it introduces no return-predictive alpha into the execution
layer — consistent with `ADR 0006`'s prohibition on alpha-encoding
tiebreaks/allocators/cadence. Some sequential-processing order is
unavoidable in a single-threaded simulator; the goal is not to eliminate
order effects (impossible without true concurrency) but to make the order
deterministic, auditable, and provably uncorrelated with the signal.

*Rejected alternative:* pin `PYTHONHASHSEED` in the run environment
(`.env`, CI config, etc.). This would make the arbitrary order
reproducible but does nothing to make it *meaningful* — the fix would live
outside the code, in an environment variable anyone could forget to set,
silently reintroducing the bug. A self-contained code fix is strictly
better and was preferred without much debate.

## Consequences

- Both `Engine`'s cross-sectional backtests and `PaperSession`'s
  paper-trading runs are now exactly reproducible across process
  invocations for the same strategy/parameters/data — verified directly:
  three separate process runs of the same `Engine` call now return
  bit-identical final equity, versus three different values before the fix.
- Any cross-sectional strategy result generated before this fix (e.g.,
  `journal/strategies/` writeups touching `CrossSectionalMomentum`, or
  `comparison.md` figures) is not necessarily reproducible if rerun today.
  By explicit decision, those write-ups are **not** being retroactively
  corrected — they are left stale rather than revisited now.
- New tests (`test_orders_are_sorted_by_ticker_within_each_side`,
  `test_shortfalls_are_sorted_by_ticker`) close the coverage gap:
  `test_all_sells_precede_all_buys` only ever asserted sells-as-a-group
  precede buys-as-a-group, never order within each group, so this was
  invisible to the suite despite passing consistently within any one
  process.
- **Forward note:** `ADR 0008`'s deferred backtester-migration-onto-the-
  shared-core item (triggered when "interface proven by the Phase 5 sim")
  should re-verify results against this fix when that migration happens —
  pre-fix non-determinism could have distorted any historical comparison
  between the two engines that relied on a single run of either side.

## Related

- Sibling: `ADR 0006` (portfolio runner architecture — D6 prohibits
  alpha-encoding tiebreaks/allocators/cadence; this bug is the same class
  of concern, arising from an implementation accident rather than a design
  choice).
- Sibling: `ADR 0009` (settlement modes + cash allocation — D6, proportional
  not FCFS cash-shortfall allocation; this fix protects that same invariant
  at the execution-order level, one layer below where D6 operates).
- Sibling: `ADR 0008` (shared execution core — the eventual backtester
  migration should re-verify against this fix; see Consequences).
- Discovered while validating `scripts/phase_5_paper_run.py`'s
  cross-sectional reconciliation (build-sequence step 15).
