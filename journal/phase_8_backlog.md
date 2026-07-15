# Phase 8 backlog

One-line tickets raised mid-phase per `PHASE_5.md §10`'s slip-handling rule:
things that are out of scope for the current phase but shouldn't be silently
forgotten. Not a design doc — just pointers to revisit when the named phase
starts.

## Live adapter needs a wall-clock provenance timestamp, additive to `ts: date`

Raised: 2026-07-11 (Phase 5, step 9, designing `BrokerSimulator.submit`/`cancel`).

`BrokerEvent.ts` (and `Order.decided_at`) are `date`-typed and mean "the
simulated-calendar date this happened on" — used directly by settlement
maturation (`DatedLedger.mature`), T+2 XTAI arithmetic (`NettingSettlement`),
and price-limit reference tracking (`DerivedPriceLimits`). That meaning has
to stay `date`-granularity forever, even once live trading arrives, because
TWSE settlement and XTAI sessions are inherently date-level concepts, not
sub-second ones.

Live trading will still want a genuine wall-clock timestamp somewhere, for
latency/audit/provenance purposes (e.g. "when did the broker actually
receive/ack this order" vs. "what date does this pertain to"). The two are
the same instant in Phase 5's synchronous, no-transport-latency simulator,
which is why `Order.decided_at` and any broker-side "now" collapse to one
value today — but they're conceptually distinct and will diverge once a real
network round-trip exists.

**The fix is additive, not a migration.** Don't widen `ts: date` to
`datetime` across the module — that would force sub-second granularity onto
settlement/calendar logic that doesn't want it and was never broken. Instead,
add a separate, live-only field (e.g. `received_at: datetime` on the future
live order/event model) purely for provenance, alongside the existing
`ts: date` used for everything calendar-driven.

## Unify single-asset and cross-sectional order sizing under one `Allocator` shape

Raised: 2026-07-14 (Phase 5, step 9, deciding where `max_position_pct` should
live on the simulator/session boundary).

`Engine._run_single_asset` sizes positions inline (`equity × max_position_pct`
capped by cash) and never touches an `Allocator` at all — `Strategy` and
`CrossSectionalStrategy` are deliberate siblings, not a unified interface
(ADR 0004/0006), so single-asset and cross-sectional runs carry two separate
sizing code paths. `PaperSession` (step 10) mirrors this same split for
Phase 5, for consistency with the already-shipped, audited `Engine` behavior.

A more centralized design — every strategy shape sizing through one
`Allocator`, even if single-asset uses a trivial one-ticker variant — would
remove the duplication and give one place to reason about position-cap
enforcement. Not attempted now: it means reopening the ADR 0004/0006 sibling
architecture and `Engine`'s already-shipped single-asset path, well outside
Phase 5's scope.

**Natural trigger:** the backtester-migration-onto-shared-core work already
tracked as deferred/non-blocking (ADR 0008, `PHASE_5.md §10`) — if `Engine`
is ever rebuilt onto the shared execution core, that is the moment to also
revisit unifying sizing under one `Allocator` shape, since both changes touch
the same `Strategy`/`CrossSectionalStrategy` dispatch boundary.
