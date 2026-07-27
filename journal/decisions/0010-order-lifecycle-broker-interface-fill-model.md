# 0010 — Order Lifecycle, Broker Interface, and Fill Model

**Date:** 2026-06-26
**Status:** Accepted (Step 0 reading complete; all `[CONFIRM IN STEP 0]` markers cleared — see `journal/phase_5_references.md`)

## Context

`PROJECT_PLAN.md §3 Phase 5` builds the paper-trading simulator. This ADR owns
the **order side** of it: the broker interface, the order state machine, the fill
model, the time-in-force semantics, and the rejection taxonomy. The Phase 4 retro
flagged the order state machine as the most stateful and bug-prone piece of the
phase, so its design is front-loaded here before any code.

This ADR is a **child of `ADR 0008` (Shared Execution Core)** — the order
lifecycle and the fill model are configurable components of the shared core, not
simulator-only machinery, and correctness is verified by the independent oracles
`ADR 0008` mandates, never by the simulator reproducing the backtester. It is a
**sibling of `ADR 0009`** (settlement and cash): the two interlock at different
layers — `ADR 0009` produces fair *intent* (proportional, cost-budgeted target
orders), and this ADR enforces *reality* (the broker partial-fills or rejects
against actual liquidity and funds). Neither subsumes the other.

The status is **Accepted**: the architectural decisions below were locked by this
session's reasoning, and every claim that depended on the *exact* shape of a real
API or of backtrader's source has been confirmed by the Step 0 reading (backtrader
`broker.py` / `order.py` / `fillers.py` read directly; Fubon Neo and Shioaji order
surfaces and the TWSE/MOPS suspension sources investigated). Each former
`[CONFIRM IN STEP 0]` point is now annotated inline with its outcome and recorded
in full in `journal/phase_5_references.md`; the one item carrying residual work
(item 7, suspension) is design-resolved with a bounded enrichment build, not an
open architectural question. The reading-checklist subsection below is retained as
the record of what was confirmed.

## Findings

### The broker interface is an event stream, not a blocking call — and that is the sim/live seam

Both real-broker targets are fire-and-forget submission plus a separate
asynchronous event channel. `submit` returns an acknowledgement immediately
(Shioaji's `place_order` returns a `Trade` that may be `PendingSubmit`; Fubon's
`placeOrder` returns and order/fill/event reports arrive on their own streams);
the actual fills and rejections arrive *later* as events. A **synchronous**
interface — one where `submit` returns a fill — would force the live adapter to
block and poll until the deal callback fires, which is leaky and fragile. A pure
**event stream** fits both worlds: `submit` only acknowledges; events
(partially-filled / filled / rejected / expired / cancelled) are emitted by
`process_bar(bar)` in the simulator and by the exchange callback live. Same event
shapes, different timing of when entries appear.

The `BrokerBase` seam is therefore `submit` / `cancel` / `query-state` /
*drain-events*. `drain-events` is the deliberate inversion of backtrader's model:
backtrader *pushes* — the broker calls into the strategy (`notify_order`),
deciding who hears about a fill — whereas our broker only *collects* events into a
buffer, and the loop *pulls* them (`drain_events()` returns everything since the
last drain and clears the buffer) and routes them to the log and account itself.
The broker thus has one outward surface — an event stream — and holds no reference
to the account, the log, or any strategy. This intermediary is what keeps the
broker a sealed unit and the live adapter thin: without it, routing responsibility
falls onto whoever owns the broker (backtrader's coupling), which live would force
the adapter to wire exchange callbacks straight into account code. The simulator
and the Phase 8 Fubon adapter both implement this seam; it is the single strongest
portfolio artifact of the phase ("one broker interface, simulator and live broker
swappable behind it").
**Step 0 confirmed.** backtrader's `BrokerBase` core seam is `submit(order)` /
`cancel(order)` / `getcash()` / `getvalue()` / `getposition(data)` with `next()`
as the per-bar hook (= our `process_bar`); it has no `drain_events` (it pushes via
a `notify` / `get_notification` deque — the coupling we invert). Fubon registers
four separate callbacks (`set_on_event` / `set_on_order` / `set_on_order_changed` /
`set_on_filled`) where Shioaji registers one (`set_order_callback`), confirming the
adapter-normalises-delivery design (N callbacks → one fold). Order construction
(`buy` / `sell`) is kept upstream, not in the broker.

The **fold** — the function that applies one event to the local mirror of order
and account state — is written **order-independent and idempotent**, so the *same
fold* serves the in-order simulator and the out-of-order live adapter (Shioaji
documents that a deal event may arrive before its order event due to message
priority). This is the same fold the event-sourced persistence layer uses to
rehydrate state, so the lifecycle, the live adapter, and persistence all meet at
one event stream.

### The state set, validated against a real broker

The order states are `{Pending, PartiallyFilled, Filled, Rejected, Expired,
Cancelled}`, an extensible set.

This was validated against Shioaji's actual status enum `{Cancelled, Filled,
PartFilled, Failed, PendingSubmit, PreSubmitted, Submitted}` and backtrader's
`{Created, Submitted, Accepted, ...}`: **both** real systems carry three pre-fill
states, which **collapse to our single `Pending`**. The justification is
principled, not convenient: the three states encode *transport round-trips* —
`Created` (in our process), `Submitted` (in flight to the exchange), `Accepted`
(resting in the order book) — each a real, separately-timed event as the order
travels machine → broker → exchange. **The simulator has no such transport**: it
plays broker and exchange at once, so acceptance is instantaneous and the three
sub-states are simultaneous. `Pending` is therefore the *correct* representation,
not a lossy compression. The collapse lives in the simulator (no transport); the
**re-expansion lives in the Phase 8 live broker adapter**, which faces the real
exchange and maps the broker's sub-states back. Note the Phase 5 boundary: Phase 5
"live" mode is the `LiveFeed` (real EOD *data*) feeding the **simulator** — no
live broker adapter exists yet and no order reaches a real exchange. The three
pre-fill states become real only in Phase 8, with the live broker adapter.
`PartFilled` → `PartiallyFilled`, `Filled` → `Filled`, `Cancelled` → `Cancelled`,
and Shioaji's `Failed` is our `Rejected`. The one state real brokers do **not**
name is `Expired` (backtrader does name it, validating our choice): a ROD order's
unfilled remainder simply ends at session close — so `Expired` is ours to model
explicitly.
**Step 0 confirmed.** backtrader's `order.py` has nine statuses — `Created,
Submitted, Accepted, Partial, Completed, Canceled, Expired, Margin, Rejected`. Its
three pre-fill states (`Created` / `Submitted` / `Accepted`) independently
corroborate the `Pending` collapse (matching Shioaji's three); `Expired` is named
first-class (validating ours). We drop `Margin` (its docstring confirms it =
insufficient cash) and fold it into `Rejected` + `INSUFFICIENT_BUYING_POWER`. Our
set is backtrader's minus `Margin`, plus explicit illegal-edge guards (next
finding).

### The transition table is the contract; coverage is enumerable

The state machine is the *grammar* of legal transitions; the fill model (below)
is the *decision-maker* whose events drive transitions. The legal transitions are
finite, which gives an exact completeness criterion for testing — **every legal
edge tested at least once, every illegal edge asserted to raise** — rather than a
vague "stress as much as possible."

Legal transitions:

| From | To | Trigger |
|---|---|---|
| `Pending` | `PartiallyFilled` | first deal, `deal_qty < order_qty` |
| `Pending` | `Filled` | complete fill in one event |
| `Pending` | `Rejected` | submit-time refusal (see taxonomy) |
| `Pending` | `Cancelled` | cancel before any fill |
| `Pending` | `Expired` | bar-end, zero fill (no available volume) |
| `PartiallyFilled` | `PartiallyFilled` | subsequent deal, cumulative `< order_qty` |
| `PartiallyFilled` | `Filled` | remainder completes |
| `PartiallyFilled` | `Expired` | bar-end, remainder unfilled *(retro-flagged: trickiest)* |
| `PartiallyFilled` | `Cancelled` | cancel after partial fill |

Terminal states (no outgoing transition): `Filled`, `Rejected`, `Expired`,
`Cancelled`. Illegal transitions that must raise: any event targeting a terminal
order; any return to `Pending`; and **`PartiallyFilled → Rejected`** — rejection
is a submit-time / pre-fill refusal, so once any quantity has filled the
remainder can only `Expire` or be `Cancelled`, never be rejected.

`cancel-while-partially-filled` and `expire-the-remainder` are **first-class
transitions** with dedicated table rows and tests, not edge notes, because they
are the most bug-prone cells.

Boundary: this table covers the **single-order lifecycle plus the partial-fill
self-loop plus the illegal-transition guards**. Cross-order interactions
(shared-pool contention, allocation) are `ADR 0009`; cross-bar resting orders are
deferred (they become meaningful only at intraday — see TIF below).

**Step 0 confirmed.** backtrader's `alive()` = {Created, Submitted, Partial,
Accepted} makes a `Partial` order alive, so `Partial → Cancel` and
`Partial → Expire` are legal there too — matching our first-class rows. backtrader
sets status via methods without hard-guarding illegal edges (it trusts the broker
to call the right one); we *add* explicit illegal-transition guards
(`PartiallyFilled → Rejected` raises), making our table a strict, guarded subset.

### Partial fills propagate into the lot ledger incrementally

A fill event ("deal") carries `(order_id, fill_price, fill_quantity, ts)`. The
broker accumulates deals against the order; the status flips
`PartiallyFilled → Filled` when cumulative deal quantity reaches order quantity.
Shioaji's model is the template — an order accumulates a list of
`Deal(seq, price, quantity, ts)` — and maps one-to-one to **one incremental
lot-ledger update per deal**. The Phase 4 lot ledger (`trades.py`) was built for
exactly this partial-reduction path (`ADR 0006 §9`, "fewer shares than the lot
holds"), so it is reused, not forked; the account adds settlement and buying
power, nothing about lots.

The **NT$20 commission minimum accrues per order**, recognised on the order's
*first* fill and **not** re-applied on subsequent partials of the same order —
otherwise an order filling in N pieces would be charged the floor N times. (The
proof: a broker could otherwise split an order into N deals and collect N × the
minimum.)

**Step 0 confirmed.** backtrader's `OrderBase.execute()` appends an
`OrderExecutionBit` to an `exbits` deque per deal (structurally Shioaji's `Deal`
list); `_execute` accumulates the bit and updates the position incrementally via
`position.pseudoupdate(size, price)`, with the `partial()` / `completed()` status
flip decided separately by the broker on remaining size — accumulation decoupled
from status-setting, exactly our model (one lot-ledger update per deal). Fubon's
official fee page confirms the NT$20 minimum is per-order (per 委託 / 單筆), charged
when the amount falls short; commission and tax are floored after the decimal
(adopt in the cost model).

### The fill model is the exchange-matching approximation (inherited from `ADR 0008`)

Per `ADR 0008`, the simulator plays both the broker role (this event API) and the
**exchange** role (order matching), and the exchange role degrades — given daily
bars and no order book — to a **volume-participation filler**:

`fillable_at_open = min(remaining, floor(participation_rate × trailing_ADV_as_of_T))`,

priced at **T+1's open + worse-side slippage** from the existing Taiwan cost
model. The cap uses **trailing ADV as of T**, the last bar knowable before the
order executes, **not** T+1's realised full-day volume — that volume does not
exist at the open and a filler needing it could not run live (the lookahead /
sim-live-symmetry argument from `ADR 0008`). `participation_rate` has a locked
canonical default of `0.05`, configurable per run.

The fill model is the swappable component `ADR 0008` names: the **full-fill /
frictionless** filler is the backtester preset; the **pessimistic
volume-participation** filler is the simulator preset; the name is descriptive
(e.g. `VolumeParticipationFiller`), never a time-in-force.

**Step 0 confirmed.** backtrader's filler is a swappable callable
`__call__(order, price, ago) -> size`; `FixedBarPerc` = `(volume[ago] * perc) //
100` capped at `remsize` is exactly our volume-participation model, and `filler is
None → size = remsize` (full fill) is literally `ADR 0008`'s frictionless preset on
one path. Slippage is separate (broker `slip_open` / `slip_match` / `slip_limit` on
*price*; the filler only sizes), matching our size/price split. **Rejected:**
backtrader's filler reads the *execution bar's own* `volume[ago]`, which is
lookahead for our at-open fill — hence trailing ADV (see the execution-timing seam
below).

There is a second, coupled seam worth naming: the **execution-timing
assumption**. Phase 5 fills at a single instant — **T+1's open** — which is why
the volume cap must use *trailing* ADV (the open instant precedes the day's
volume). An alternative assumption, **fill-across-the-bar**, treats the order as
participating in trading over the whole bar, under which the bar's *own* volume
becomes a legitimate (contemporaneous) cap — this is backtrader's default model,
and the reason its execution-bar-volume filler is not lookahead *for it*. The
volume source is therefore **downstream of the timing assumption**: at-open ⇒
trailing ADV; across-bar ⇒ same-bar volume. Phase 5 locks **fill-at-open +
trailing ADV** as canonical; **fill-across-bar** is the deferred intraday
extension, where same-bar volume becomes valid again. The fill model is built so
the timing assumption is swappable, not hard-coded — the frequency-agnostic seam
of `ADR 0008` applied to execution timing.

### Time-in-force: IOC canonical, ROD identical at daily granularity, FOK forbidden cross-sectionally

Time-in-force answers one question — *what happens to the unfilled remainder?* —
and is **orthogonal** to the fill model (which answers how much fills, at what
price, and whether to reject). The standard TWSE types `ROD` / `IOC` / `FOK`
appear on both brokers' order objects; we name the field after the standard and
keep the fill model separately named. Canonical Phase 5 = **IOC time-in-force +
pessimistic volume-participation filler**.

- **IOC** — fill what this bar allows, expire the remainder within the bar.
- **ROD** — the remainder rests for the rest of the day. At **daily** granularity
  the bar *is* the day, so ROD and IOC **collapse to the same behaviour** (expire
  the remainder at bar-end). ROD becomes behaviourally distinct from IOC only at
  **intraday** frequency, where a remainder can rest across intrabars — the
  frequency-agnostic seam `ADR 0008` protects. This is correct-by-collapse,
  documented so the identical daily behaviour is not mistaken for a bug.
- **FOK** — all-or-nothing, immediately. FOK is mechanically well-defined and
  harmless for an **isolated single order**, but in a **shared cash pool** it
  makes the cross-sectional book **order-dependent**: the first FOK buy that
  fills consumes cash and can kill a later FOK buy that would otherwise have
  filled wholly. That is the same alpha-in-execution sin as first-come-first-
  served (the `TieBreakPolicy` violation `ADR 0009` rejects). FOK is therefore a
  valid enum value the machine and live adapter can represent, but the
  **cross-sectional path rejects it with a loud error** ("FOK is not a valid TIF
  for portfolio rebalancing; use IOC"). This is an *active guard*, not passive
  deferral — it converts a silent future order-dependent-book bug into a
  documented boundary, the `ADR 0007` lesson applied prospectively. FOK's real
  home is a future pairs-trading execution ADR (multi-leg atomicity), if and when
  pairs trading lands.

### Rejection taxonomy: three daily-derivable causes, extensible by construction

Reason codes are an extensible enum; the v1 set is the three that actually bite a
long-only daily book on TWSE (the third a detection with an evidence-gated
refinement), with a deliberate distinction between **submit-time refusal**
(`Rejected`) and **accepted-but-unfilled** (`Expired`):

- `INSUFFICIENT_BUYING_POWER` — refused at submit; funding short under the active
  settlement model (`ADR 0009`).
- `PRICE_LIMIT_LOCK` — T+1's open is at the ±10% 漲跌停 band against the order's
  side, so a market order cannot fill there → zero fill.
- `NO_MARKET_DATA`, **refined to** `SUSPENSION` — these are **not peer codes from
  peer sources**; one is the detection, the other an evidence-gated refinement of
  it. **`NO_MARKET_DATA` is the detection and the gate**: XTAI says a bar should
  exist on a valid trading day, the feed (FinMind) lacks it → the simulator cannot
  fill (no price) and rejects. It is *always derivable* and is an honest claim
  about *our own data* — it asserts no market event. **`SUSPENSION` ⊆
  `NO_MARKET_DATA`**: within the only population that matters (missing bars on
  valid trading days) every full-day 暫停交易 halt is necessarily a missing bar, so
  `SUSPENSION` is the **evidence-gated upgrade** that re-labels a missing bar as a
  confirmed halt *only* on positive evidence — a **suspension calendar** built from
  the TWSE after-trading historical halt records (**TWTAWU**), which rides the same
  batch/append dual-feed pattern as the OHLCV (ReplayFeed batch-loads history;
  LiveFeed appends each EOD poll, since TWTAWU publishes day D's record by D's
  close, before the ~17:30 poll). Absent that evidence the bar stays
  `NO_MARKET_DATA` — we never invent a halt we cannot prove. Conceptually this is a
  per-ticker refinement of the market-wide trading calendar ("is the *market*
  open?" → "is *this ticker* tradeable?"). Out of scope: 處置 disposition (the bar
  still prints) and intraday halts (an open still prints). **MOPS 即時重大訊息**
  (live advance disclosure) was investigated and **dropped** — its only niche is a
  real-time-at-open pre-screen, empty across the roadmap (replay is historical;
  LiveFeed is EOD-batch, TWTAWU covers it; Phase 8 reads halt status from the
  broker API). **Build order:** `NO_MARKET_DATA` detection is foundational and
  always built; the TWTAWU `SUSPENSION` calendar is an enrichment layered after,
  validated against an independent source (the Yahoo suspended calendar), with
  **graceful degradation** to `NO_MARKET_DATA`-only if the pull is not clean.

  *Step 0 resolved* (see journal item 7): the earlier "TWSE OpenAPI endpoint /
  ~2-hour bound" framing is superseded — the source is the TWTAWU historical
  suspension calendar (evidence-gated enrichment, graceful degradation), not a
  live endpoint; MOPS investigated and dropped; the Phase 8 live-halt source is a
  separate broker-API endpoint still to be verified — *not* `SymbolQuote.status`,
  which is short-sale/day-trade eligibility.

Price limits come through a small **price-limit provider** abstraction so the
rejection logic is source-agnostic: in **replay** they are derived from
raw + corporate-action events (prior close ±10%, tick-rounded, off the *adjusted*
reference on ex-days); **live**, they are read from the broker (Shioaji's contract
`limit_up`/`limit_down`/`reference`; Fubon's symbol-quote fields). The derived and
published bands should match closely — itself a Phase 8 cross-check.

**Step 0 confirmed.** Fubon's `SymbolQuote` exposes `limitup_price` /
`limitdown_price` / `reference_price` — the analogues of Shioaji's
`limit_up` / `limit_down` / `reference`; the live-reads branch is confirmed on
both brokers. (Fubon's `status` bitmask is short-sale/day-trade eligibility, *not*
a halt signal — see journal item 6.)

### Stateless purity and the lookahead audit are preserved

The order state machine lives **entirely** in the simulator and account — never
in `generate_signals` / `generate_scores`. `assert_no_lookahead` (single-asset and
panel) remains valid and is re-run for the demonstrator. No order or fill state is
ever passed back into a strategy: events route to the **log and account only**.
backtrader's `notify_order` callback couples broker state *into* the strategy;
this simulator must consciously **not** do that.

**Step 0 confirmed.** backtrader's `BackBroker.notify(order)` appends
`order.clone()` to a `notifs` deque that Cerebro pulls and routes to
`strategy.notify_order` — broker state pushed into the strategy, exactly the
coupling we forbid. We borrow the immutable-snapshot discipline (notify a *clone*)
but route events to the log/account only, never a strategy hook.

This is a deliberate scope choice, not a blind omission. Some strategies *do*
legitimately react to order state — iceberg/TWAP slicing, "if this leg fills, send
the next," re-quoting a partial, placing a stop after a fill, retrying a rejection.
But every such case is **execution logic** (how to work an order already decided),
not **alpha** (what to buy) — and our purity boundary draws exactly that line.
Fill-reaction therefore has a proper future home: an **executor** (execution-policy)
stage inserted into the pipeline as `allocator → reconciler → executor → broker`,
*below* the strategy seam, never a `notify_order` hook bolted onto a strategy.
Phase 5 has no executor stage (market orders, no slicing); it is a recorded
extension point so the absence is a choice, and the purity audit stays intact when
it is eventually added.

### Alternatives considered

- **Synchronous broker interface** (`submit` returns a fill) — rejected. Forces
  the live adapter to block-and-poll; leaky. The event stream fits both sim and
  live with one fold.
- **Reconcile-and-clamp as the canonical execution path** (pre-size orders down,
  the Phase 4 patch) — rejected. Silently absorbs underfill and hides intent.
  Submit-and-partial-fill preserves intent and surfaces underfill through the
  event log. The allocator's proportional pre-sizing (`ADR 0009`) and the
  broker's partial-fill enforcement compose at *different layers*; a naive
  full-size allocator mode is retained only for adversarial stress-testing.
- **FOK as a usable cross-sectional time-in-force** — rejected; order-dependent
  book, `TieBreakPolicy` violation. Guarded loudly, not silently deferred.
- **Cross-bar resting orders in v1** — rejected; at daily granularity a resting
  remainder is barely distinguishable from re-emitting from tomorrow's signal —
  the worst bug-to-value ratio in the phase. Deferred to intraday, where ROD
  becomes meaningful.
- **Conflating `NO_MARKET_DATA` with `SUSPENSION`** — rejected; phantom
  rejections corrupt the reconciliation. Distinct codes; suspension only from a
  positive source.
- **`notify_order`-style coupling of broker events into strategies** — rejected;
  breaks stateless purity and the lookahead audit.

### Step 0 reading outcomes

The reading is complete; each former checklist item and its outcome (full detail in
`journal/phase_5_references.md`):

1. **`BrokerBase` method set / callbacks** — *confirmed.* Core seam matches; Fubon's
   four callbacks vs Shioaji's one both normalise to our single fold.
2. **Order status constants and transitions** — *confirmed.* Our set = backtrader's
   minus `Margin` (folded into `Rejected`), plus explicit illegal-edge guards;
   three-pre-fill collapse doubly corroborated.
3. **Partial-fill propagation** — *confirmed.* Per-deal `exbits` accumulation
   decoupled from the status flip; one lot-ledger update per deal.
4. **Commission rule** — *confirmed.* NT$20 minimum per order, charged on shortfall;
   commission and tax floored after the decimal.
5. **Filler / slippage seam** — *confirmed.* Swappable callable; size/price split;
   `filler=None → full fill`. *Rejected* backtrader's execution-bar volume →
   trailing ADV.
6. **Price-limit fields** — *confirmed.* Fubon `limitup_price`/`limitdown_price`/
   `reference_price` = Shioaji's; live-reads branch confirmed on both.
7. **Suspension data** — *design-resolved.* `NO_MARKET_DATA` detection foundational;
   `SUSPENSION` an evidence-gated enrichment from the TWTAWU historical suspension
   calendar (graceful degradation); MOPS dropped; the Phase 8 live-halt broker
   endpoint is a separate to-verify item.
8. **`notify_order` coupling** — *confirmed* as the anti-pattern; we route events to
   the log/account only.

## Decision

**The simulator implements an event-stream `BrokerBase` — the sim/live swap point
— with an order state machine whose legal transitions are an enumerable contract.
The canonical configuration is IOC time-in-force with a pessimistic
volume-participation filler on trailing ADV as of T. Rejections are an extensible
reason-coded taxonomy distinguishing submit-time refusal from accepted-but-
unfilled. FOK is represented but forbidden on the cross-sectional path. The order
state machine lives wholly in the simulator; strategies stay pure.**

Implementation choices recorded for `PHASE_5.md` (signatures and transition cells
confirmed in Step 0):

1. **`BrokerBase` event-stream seam** — `submit` / `cancel` / `query` /
   drain-events (`process_bar` in the sim); one order-independent, idempotent fold
   shared by simulator, live adapter, and persistence.
2. **State set** `{Pending, PartiallyFilled, Filled, Rejected, Expired,
   Cancelled}`; real brokers' submission sub-states collapse to `Pending`;
   `Expired` is modelled by us; the set is extensible.
3. **Transition table** as above, tested to the enumerable completeness criterion
   (every legal edge exercised, every illegal edge raises);
   `cancel-while-partially-filled` and `expire-the-remainder` are first-class.
4. **Fill model** — `VolumeParticipationFiller`, trailing ADV as of T, T+1 open +
   worse-side slippage, canonical `participation_rate` default, configurable;
   swappable with the frictionless filler per `ADR 0008`.
5. **Time-in-force** — IOC canonical; ROD supported and identical at daily
   granularity (distinct only at intraday); FOK represented but rejected on the
   cross-sectional path with a loud error.
6. **Rejection taxonomy** — extensible reason codes; v1 =
   `INSUFFICIENT_BUYING_POWER`, `PRICE_LIMIT_LOCK`, and the missing-bar cause
   `NO_MARKET_DATA` with its evidence-gated refinement `SUSPENSION`
   (`SUSPENSION` ⊆ `NO_MARKET_DATA`). `NO_MARKET_DATA` detection (XTAI ∧
   ¬FinMind-bar) is foundational; the `SUSPENSION` upgrade is sourced from the
   TWTAWU historical suspension calendar, graceful-degrading to `NO_MARKET_DATA`
   if unavailable. Price limits via a source-agnostic provider (replay derives,
   live reads).
7. **Stateless purity** — events route to the log and account only, never to
   strategies; `assert_no_lookahead` re-run for the demonstrator.
8. **Scope** — daily, long-only, market orders. Limit/stop order types,
   short-borrow and tick-rule rejections (the shorting path), cross-bar resting,
   and real order-book matching are deferred to their named later phases.

## Implementation Notes (Phase 5)

- **`src/twstock/broker/`**: `base.py` (`BrokerBase` ABC), `order.py` (`Order`,
  `OrderStatus`, the transition table and its guards), `simulator.py`
  (`BrokerSimulator` implementing `BrokerBase`; `process_bar` applies the fill
  model and emits events), `fill.py` (`FillModel` ABC + `VolumeParticipationFiller`),
  rejection reason-code enum + the price-limit provider. Lot bookkeeping reuses
  `trades.py`.
- **Events** are appended to the JSONL event log and folded into the account;
  they are **never** routed to a strategy.
- **Tests**: transition-table coverage (every legal edge, every illegal edge
  raises); the independent-oracle suite (`ADR 0008` / `PHASE_5.md`); the
  adversarial `FixtureFeed` catalogue (forced partial fills, limit-lock,
  cash starvation, missing-bar-is-not-suspension, split-on-held-name, out-of-order
  fold). The FOK cross-sectional guard has a dedicated test asserting the loud
  error.

## Consequences

- **`BrokerBase` is the Phase 8 Fubon swap point.** The live adapter implements
  the same interface; its acceptance is the sim/live-seam test plus the
  independent oracles, not a zero-pessimism reproduction (`ADR 0008`).
- **One fold, three consumers.** The order-independent fold is shared by the
  simulator, the live adapter (out-of-order events), and the event-sourced
  persistence layer — written once, defensively.
- **The FOK guard prevents a future silent bug.** A cross-sectional run that
  flips to FOK fails loudly rather than producing an order-dependent book.
- **The fill model's seam hosts intraday matching.** When order-book data arrives
  (Phase 9+), a real matching model replaces the volume-participation filler
  behind the same seam, with no change to the lifecycle, settlement, or interface.
- **Interlocks with `ADR 0009`, child of `ADR 0008`.** `ADR 0009` sizes fair
  intent; this ADR enforces reality. Both are configurable components of the
  shared core; correctness is by independent oracles.
- **Step 0 complete; Accepted.** The decisions and rationale were final on
  drafting; the method signatures, transition-table cells, and the eight checklist
  facts have been confirmed by the Step 0 reading (recorded in
  `journal/phase_5_references.md`). The one residual is item 7's suspension
  *enrichment* — a bounded build with a `NO_MARKET_DATA` floor, not an open
  architectural question — and the Phase 8 live-halt broker endpoint, deferred to
  Phase 8 by design. This ADR is **Accepted**.
