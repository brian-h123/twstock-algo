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

## `LiveSession` should mirror `PaperSession`'s execution flow, diverging only at broker connection and price sourcing

Raised: 2026-07-20 (Phase 5, step 10, designing `PaperSession`'s constructor and run loop).

The point of paper trading is to rehearse what live trading will actually look
like, so a future `LiveSession`'s `decide → submit → advance → drain → fold →
mark` spine should be the same shape as `PaperSession`'s, not a parallel
design. `BrokerSimulator` and `PaperSession` were deliberately built to mimic
real execution as closely as possible for exactly this reason — the closer
the two shapes match, the more the Phase 5 simulator actually validates before
real capital is at risk.

Two things are genuinely different for Phase 8 and need their own design
there, not inherited wholesale from `PaperSession`:

1. **Broker connection.** `PaperSession` constructs its own `BrokerSimulator`
   internally — capital, cost model, fill model, settlement are all pure
   config, nothing to bootstrap. A real broker adapter needs credentials, a
   `.pfx` certificate, and an authenticated connection: external resources
   that can't be wired up inside a constructor the same way. `LiveSession`
   will need to *receive* an already-connected `BrokerBase` implementation
   rather than build one, and a decision is needed on how a user actually
   creates and starts a live session — what constructs the adapter, where
   credentials come from, what the entry point looks like.
2. **Price data source.** `PaperSession` reads OHLCV from a pre-built
   `BarFeed` (`ReplayFeed`/`FixtureFeed`/`LiveFeed`, sourced from local
   storage or a FinMind EOD poll). `LiveSession` will need to query the
   broker API directly for prices instead of going through any `BarFeed` — a
   different data path entirely, not just a different feed implementation.
3. **Session ID uniqueness.** `PaperSession._next_session_seq` (a `ClassVar`
   counter) only guarantees a unique `session_id` within one running process —
   deliberately, since nothing today constructs `PaperSession`s concurrently
   across processes. If `LiveSession` or any batch/parallel orchestration
   introduces genuinely concurrent processes, revisit whether `uuid4()` (or a
   composite counter + UUID) is warranted instead.

**Natural trigger:** Phase 8 kickoff, once the real Fubon/Shioaji adapter's
actual constraints are known and `LiveSession` can be designed (or extracted
from `PaperSession`) directly against them, rather than guessed at now.
