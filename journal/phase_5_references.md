# Phase 5 — Step 0 Reference Reading Journal

**Purpose.** Focused source read against the `ADR 0010` Step 0 checklist, to
harden the Proposed decisions before implementation. Each entry records the
source read, the finding, an explicit **borrow / reject** judgement for the
`twstock-algo` design, and which `ADR 0010` marker it resolves. This journal is
the comparison record; the *decisions* live in the ADRs (strict separation, per
project convention).

**Sources.**
- backtrader `1.9.78.123` (pip), files: `broker.py`, `brokers/bbroker.py`,
  `order.py`, `fillers.py`. Read directly from installed source.
- Fubon Neo API official docs (`fbs.com.tw/TradeAPI`: Preparation, Reconnect,
  `query_symbol_quote` reference), Fubon retail fee page (`fbs.com.tw` 國內股票),
  Shioaji docs (earlier research), TWSE OpenAPI (`openapi.twse.com.tw`), FinMind
  dataset catalogue.

**Status:** **all eight** checklist items resolved. Items 1–6 and 8
hard-confirmed (item 1 from both the backtrader source and the real broker
callback surfaces; item 6 on both brokers with field names pinned). **Item 7
(suspension) is design-resolved**: `NO_MARKET_DATA` detection is foundational, the
TWTAWU historical suspension calendar is an evidence-gated enrichment with graceful
degradation, and MOPS was investigated and dropped — the remaining work is a
bounded enrichment build, not an architectural unknown.

---

## Item 1 — `BrokerBase` method set, signatures, and broker callback surfaces

**Read:** `broker.py::BrokerBase`, `brokers/bbroker.py::BackBroker`; Fubon Neo
docs (`Preparation`, `Reconnect`); Shioaji order/account surface (earlier).

**Found — backtrader.** The abstract surface is small: `submit(order)`,
`cancel(order)`, `getcash()`, `getvalue(datas=None)`, `getposition(data)`, plus
per-bar hook `next()` and order-construction convenience `buy(...)` / `sell(...)`
that build an order and submit it. There is **no `drain_events` method** —
backtrader instead *pushes* notifications onto a `collections.deque`
(`notify(order)` appends `order.clone()`; `get_notification()` pops left), which
Cerebro pulls and routes to the strategy (see item 8). Commission lives in the
broker (`comminfo`, `CommInfoBase`).

**Found — real brokers.** Fubon registers **four separate callbacks per event
type** — `set_on_event`, `set_on_order`, `set_on_order_changed`, `set_on_filled`
(each `(code, msg/data)`) — plus pull-style reports (`get_order_reports` 委託回報,
`get_filled_reports` 成交回報, `get_event_reports` 事件通知); `place_order(acc,
order)` is fire-and-forget; account queries are `accounting.inventories()`
(positions) and `accounting.bank_remain()` (cash); the order object carries
`time_in_force` as a `TimeInForce` enum (ROD/IOC/FOK). Shioaji, by contrast,
registers **one** callback (`set_order_callback`).

**Borrow.** (a) The `submit` / `cancel` / `getcash` / `getvalue` / `getposition`
core seam validates our `BrokerBase` shape; `next()` is the per-bar hook =
our `process_bar(bar)`. (b) Notifying with an *immutable clone* rather than a
live reference — our event records are immutable by construction. (c) **The
headline:** Fubon delivers events through **four** callbacks, Shioaji through
**one**, and both must normalise into our **single event stream / one fold** —
direct confirmation that the per-broker delivery shape is the *adapter's* problem,
not `BrokerBase`'s. `ROD/IOC/FOK` as an enum on the Fubon order object also
confirms the TIF decision against a second real broker.

**Reject / differ.** (a) backtrader bundles order *construction* (`buy`/`sell`)
into the broker; we keep construction upstream (allocator / strategy) and let the
broker receive only already-formed orders via `submit`. (b) We replace
push-to-strategy notification with **pull `drain_events` to the log + account
only** (item 8). (c) Our `BrokerBase` exposes **one uniform event stream**
regardless of whether the underlying broker delivers via 1 callback (Shioaji) or 4
(Fubon).

**Resolves marker 1.** Core seam, per-bar hook, and both brokers' callback
surfaces confirmed; the N-callbacks-to-one-fold normalisation is the adapter's
responsibility.

---

## Item 2 — Order status constants and legal transitions

**Read:** `order.py::OrderBase` status constants, transition methods, `alive()`.

**Found.** Nine statuses: `Created, Submitted, Accepted, Partial, Completed,
Canceled, Expired, Margin, Rejected`. `alive()` = `{Created, Submitted, Partial,
Accepted}` (non-terminal); terminals = `{Completed, Canceled, Expired, Margin,
Rejected}`. Each transition is a method that simply sets `status`
(`submit/accept/partial/completed/cancel/expire/reject/margin`); `reject()` is
idempotent. A `Partial` order is *alive*, so `Partial → Cancel` and
`Partial → Expire` are legal. backtrader does **not** hard-guard illegal
transitions at the Order level — it trusts the broker to call the right method.
The `Order` docstring confirms `Margin` = "not enough cash to execute," which can
fire at submission *or* on a later bar as cash is drawn down.

**Mapping to our set** `{Pending, PartiallyFilled, Filled, Rejected, Expired,
Cancelled}`:
- `Created` + `Submitted` + `Accepted` → **`Pending`** (3→1 collapse) — this
  *independently corroborates* Shioaji's three-submission-substate collapse. Two
  separate real systems fold their pre-fill states; our single `Pending` is
  well-founded.
- `Partial` → `PartiallyFilled`; `Completed` → `Filled`; `Canceled` →
  `Cancelled`.
- `Expired` → `Expired` — backtrader names it **first-class**, validating our
  decision to model it (Shioaji does *not* name it; backtrader does).
- `Margin` → folded into `Rejected` + `INSUFFICIENT_BUYING_POWER` reason code.

**Borrow.** The state set, the alive/terminal partition, the pre-fill collapse,
`Expired` as first-class, and `Partial → Cancel/Expire` as legal transitions.

**Reject / differ.** (a) We **drop `Margin` as a status** and capture its meaning
as a `Rejected` reason code — cleaner for our taxonomy than a parallel status.
(b) We **add hard illegal-transition guards** (e.g. `PartiallyFilled → Rejected`
raises) that backtrader leaves to broker discipline. Our table is a strict,
guarded subset of backtrader's.

**Resolves marker 2.** State set and transition legality confirmed; our table =
backtrader's set minus `Margin`, plus explicit illegal-edge guards.

---

## Item 3 — Partial-fill propagation into position bookkeeping

**Read:** `order.py::OrderBase.execute()` and `OrderData.exbits`;
`brokers/bbroker.py::_execute()`.

**Found.** Each fill is one `execute(dt, size, price, ...)` call, which appends an
`OrderExecutionBit` to the order's `exbits` deque — i.e. the **accumulating list
of deals**, structurally identical to Shioaji's `Deal` list. Accumulation is
**decoupled** from status-setting: `_execute` accumulates the bit and updates the
position incrementally via `position.pseudoupdate(size, price)`, while the broker
separately calls `partial()` or `completed()` based on remaining size. With **no
filler**, `_execute` takes `size = order.executed.remsize` — a full fill.

**Borrow.** (a) The per-deal accumulation model — one incremental update per deal,
mapping directly onto one lot-ledger update per deal in our reused `trades.py`.
(b) Accumulation decoupled from the status flip (status → `Filled` when cumulative
deal qty reaches order qty). (c) Incremental per-fill position update.

**Reject / differ.** backtrader's `exbits` track an `opened`/`closed` split for
futures/margin P&L; long-only FIFO needs only `(shares, price, cost)` per deal.
We keep the simpler shape. Commission floor is recognised **once per order on the
first deal**, not per deal (our rule; the Fubon fee-doc confirmation is item 4).

**Resolves marker 3.** Per-deal incremental accumulation confirmed; lot ledger
consumes one update per deal; status flip on cumulative completion.

---

## Item 4 — Commission rule (per-order NT$20 minimum)

**Read:** Fubon official retail fee page (`fbs.com.tw` 國內股票); corroborating
brokerage-review sources quoting Fubon's schedule.

**Found.** Taiwan stock/ETF/warrant commission is **0.1425% of executed value ×
discount** (old accounts 6折 = ×0.6; matches `CLAUDE.md`), **calculated
per-order** (逐筆/單筆), with **NT$20 charged when the amount falls short**.
Commission and the 0.3% sell-side transaction tax are **floored** (無條件捨去)
after the decimal. Independent corroboration of `ADR 0009`: settlement funds are
due by **10:00 on T+2** (T plus two business days, holiday-deferred), and the
**圈存** (earmarking) mechanism exists for pre-funded order types.

**Borrow / confirm.** (a) The NT$20 minimum is **per order, recognised once** —
confirms our rule and the broker-arbitrage reductio (a per-fill minimum would let
a broker split an order to multiply the floor). (b) **Floor** commission and tax
after the decimal — adopt in the cost model. (c) 6折 default confirmed.

**Differ / note.** The exact same-day aggregation of multiple 成交 (fills) under
one 委託 into a single minimum is the standard interpretation but a cent-level
detail to confirm against the official fee schedule / Fubon Neo SDK during Phase 8
if it ever becomes material; immaterial to Phase 5 mechanics.

**Resolves marker 4.** NT$20 per-order, recognised on first fill; floor rounding
added to the cost-model note.

---

## Item 5 — Filler / slippage seam

**Read:** `fillers.py` (`FixedSize`, `FixedBarPerc`, `BarPointPerc`);
`brokers/bbroker.py::_execute()` filler invocation.

**Found.** A filler is a **swappable callable** `__call__(order, price, ago) ->
size`. `FixedBarPerc` is `(volume[ago] * perc) // 100`, capped at `remsize` — i.e.
*exactly* our volume-participation model. Slippage is **separate** from the filler
(broker `slip_open` / `slip_match` / `slip_limit` params applied to *price*; the
filler only decides *size*). Critically, in `_execute`: `filler is None → size =
remsize` (full fill); `filler set → volume-capped size`. The filler reads
`order.data.volume[ago]` — **the execution bar's own volume**.

**Borrow.** (a) The filler-as-swappable-callable seam; our `FillModel` ABC mirrors
it. (b) `FixedBarPerc` as the direct template for `VolumeParticipationFiller`.
(c) The **size (filler) vs price (slippage) separation**, matching our fill-size /
fill-price orthogonality. (d) **`filler=None → full fill` is the same code path as
our frictionless backtester preset** — a direct, unplanned corroboration of
`ADR 0008`'s "friction is an opt-in filler on one shared path."

**Reject / differ.** backtrader's filler reads the **execution bar's own volume**
(`volume[ago]`). For our daily model — order fills at T+1's open, but T+1's
full-day volume is unknown at the open — that is **lookahead**. We substitute
**trailing ADV as of T**. This is the precise external confirmation of the
trailing-ADV decision in `ADR 0008` / `ADR 0010`: backtrader's own design would
introduce the exact lookahead we ruled out, *because* its fill timing differs from
ours (across-bar vs our at-open).

**Resolves marker 5.** Filler seam confirmed; `VolumeParticipationFiller` plugs in
at the same point; size/price split confirmed; trailing-ADV substitution justified
by the lookahead backtrader's execution-bar-volume would create in our fill timing.

---

## Item 6 — Price-limit fields

**Read:** Fubon Neo `query_symbol_quote` reference (`SymbolQuote` object,
fetched); Shioaji contract fields (earlier research).

**Found.** Fubon's `SymbolQuote` exposes **`limitup_price`**, **`limitdown_price`**,
and **`reference_price`** — the direct analogues of Shioaji's
`limit_up` / `limit_down` / `reference`. Both live brokers therefore publish the
band. For **Phase 5 replay** the price-limit provider still **derives** the band
from raw + corporate-action events (prior close ±10%, tick-rounded, off the
adjusted reference on ex-days); **live** reads it from either broker's quote.

**Note — `status` is NOT a halt signal (correction).** The same `SymbolQuote`
carries a **`status` bitmask**, but it encodes **short-sale and day-trade
eligibility**, not whether the stock is halted: per the field doc, `0` = 全禁
(none of *these* permissions), `1` = 平盤下可融券賣出, `2` = 平盤下可借券賣出,
`4` = 可先買後賣當沖, `8` = 可先賣後買當沖. So `status == 0` means "none of these
short-sale/day-trade exemptions apply" — the **normal** state for many perfectly
tradeable stocks — and is **not** a halt indicator. (An earlier draft wrongly read
全禁 as "trading forbidden.") What `status` *is* useful for is the **deferred
shorting / day-trade paths** (borrow-availability and tick-rule rejection codes),
not Phase 5, and not item 7. The actual Phase 8 live-halt source is a **separate,
still-unverified endpoint** — candidate: Fubon Neo `intraday/tickers` HTTP API — to
be confirmed in Phase 8; not Phase-5-blocking.

**Borrow / confirm.** The price-limit-provider abstraction stands: replay
derives, live reads — confirmed against **both** brokers, with field names pinned.

**Resolves marker 6.** `limitup_price` / `limitdown_price` / `reference_price`
confirmed on both brokers; replay derives, live reads.

---

## Item 7 — Suspension labelling (design resolved; enrichment is a build task)

**Read:** TWSE OpenAPI swagger (`openapi.twse.com.tw/v1/swagger.json`); a
follow-up investigation (user-supplied) of the TWSE after-trading endpoint
**TWTAWU** and the **MOPS 即時重大訊息** disclosure feed; FinMind catalogue.

**Definition (scoped precisely).** **Suspension = 暫停交易 / trading halt**: a
ticker's bar is **absent on a valid XTAI trading day** because trading was halted.
This is the `SUSPENSION`-vs-`NO_MARKET_DATA` question, and is **distinct from 處置
(disposition)** — under disposition the stock trades under special measures but the
**bar still prints**, so disposition is *out of scope* for the missing-bar
question. (Intraday halts also print an open and are out of scope.)

**Decisive axis.** The right source depends on **advance notice vs after-the-fact**
— but only for a workflow that must decide *before* T+1. `ADR 0010` locked
**submit-and-reject**, not pre-screening: the broker rejects at the *execution
moment*, so the simulator only needs the halt status **at T+1's open**, not the
night before. And a full-day halt is a **session *condition* in effect from the
open** (observable at the open; a live broker rejects you there) — **not** a
session *outcome* like volume that only exists after the close. So, unlike the
T+1-volume case (which forced trailing ADV), reading halt-status-at-open is **not
lookahead** — it is reference data, exactly like the trading calendar.

**Model — the suspension calendar.** Halt status is therefore a **ticker-level
refinement of the XTAI trading calendar**: the trading calendar answers "is the
*market* open on D?"; the suspension calendar answers "is *ticker X* tradeable on
D?" Both are reference data, consulted at any bar without lookahead. A missing bar
resolves cleanly: market-open ∧ ticker-suspended → `SUSPENSION`; market-open ∧
not-suspended → `NO_MARKET_DATA`.

**Source — TWTAWU, riding the OHLCV dual-feed pattern.** TWTAWU is a *historical*
record of which tickers were halted on which dates — ideal raw material for the
calendar. The MOPS investigation correctly noted TWTAWU "reports after the fact,"
but that only disqualifies it for a *real-time-at-open* pre-screen, which we do
not run:
- **ReplayFeed** (historical) — batch-load TWTAWU history into the calendar,
  exactly as historical OHLCV is batch-loaded.
- **LiveFeed** (EOD batch poll after ~17:30, **one completed day at a time** — not
  a real-time intraday stream) — TWTAWU publishes day D's halt record at D's
  *close*, which is already past when the 17:30 poll runs, so the record is
  available in the same cycle that processes D. MOPS's advance edge buys nothing
  here, because our execution is itself processed after the close.

So suspension data uses the **same batch/append dual-feed pattern as the OHLCV**:
one source, one calendar, both feeds, no special advance mechanism.

**MOPS — investigated and dropped (with reason).** MOPS 即時重大訊息 *is* the
correct live, advance disclosure feed, but its only niche is a real-time-at-open
pre-screen — a workflow that is **empty across the whole roadmap**: ReplayFeed is
historical, LiveFeed is EOD-batch (TWTAWU covers it), and Phase 8 live trading
reads halt status directly from the **broker API** (a dedicated halt/market-status
endpoint — candidate Fubon `intraday/tickers`, to be verified in Phase 8; *not*
the `SymbolQuote.status` bitmask, which is short-sale/day-trade eligibility — see
item 6). MOPS is also a heavy four-stage ETL (scrape a general disclosure feed →
filter to suspension announcements by parsing subject text → filter TWSE vs TPEx →
extract date + ticker). **Effort for a workflow we never run — dropped.**

**The label relationship (not peer codes).** `SUSPENSION` ⊆ `NO_MARKET_DATA`
within the only population that matters (missing bars on valid trading days): every
full-day halt is necessarily a missing bar. They are **not peer codes from peer
sources**. `NO_MARKET_DATA` is the **detection** — XTAI says a bar should exist,
FinMind lacks it — and is the gate that fires the rejection at all; it is always
derivable and is an honest claim about *our own data*. `SUSPENSION` is an
**evidence-gated refinement** — a positive claim about the *market* that requires
positive evidence (the calendar). Absent that evidence, we make the weaker, safer
claim (`NO_MARKET_DATA`) rather than invent a market event we cannot prove — the
same epistemic discipline as the evidence-gated rights-issue / capital-reduction
handling and the independent-oracle rule. This also lands correctly in
reconciliation: a `NO_MARKET_DATA` rejection is attributed to *our data fidelity*,
a `SUSPENSION` to a *real, modelled halt* — erring toward the former under-claims
correctly-modelled behaviour rather than over-claiming it.

**Build order (decided).**
1. **`NO_MARKET_DATA` detection — foundational, must-have.** XTAI ∧ ¬FinMind-bar,
   implemented first; it is the gate.
2. **TWTAWU suspension calendar — enrichment, layered after.** Build if the
   historical pull is clean, **validated against an independent source** (the
   Yahoo Finance suspended calendar — a source-independent cross-check, per project
   discipline). If the pull is not clean, **graceful degradation**: everything
   stays `NO_MARKET_DATA`, nothing breaks.

**Calibration.** Low-stakes for Phase 5: full halts on Top 50 are rare, the
14-day LiveFeed will almost certainly see zero, so the calendar's value is confined
to the ReplayFeed's multi-year history (a handful of halts). The
calendar-vs-floor outcome rides only on how clean the TWTAWU pull is.

**Resolves marker 7 (design resolved).** `NO_MARKET_DATA` detection (XTAI ∧
¬FinMind-bar) is foundational; the TWTAWU historical **suspension calendar** —
riding the OHLCV dual-feed pattern — is an **evidence-gated enrichment** that
upgrades confirmed halts to `SUSPENSION`, with graceful degradation if the pull is
not clean; **MOPS investigated and dropped** (its only niche is empty across the
roadmap). Remaining work is a bounded enrichment build, not an architectural
unknown.

---

## Item 8 — `notify_order` coupling (the anti-pattern to avoid)

**Read:** `brokers/bbroker.py::notify()` / `get_notification()` and the `notifs`
deque.

**Found.** `notify(order)` appends `order.clone()` to a `notifs` deque;
`get_notification()` pops it; Cerebro routes it to the strategy's
`notify_order(order)`. So broker/order state is **pushed to the strategy** — the
strategy directly receives the order object on every status change.

**Borrow.** The immutable-snapshot discipline (it pushes a *clone*, not a live
reference). Our event records are immutable, consistent with this.

**Reject / differ.** We must **not** route events to any strategy hook.
backtrader's `notify_order` is exactly the coupling `ADR 0010`'s stateless-purity
section names as forbidden: it lets broker/fill state reach strategy code, which
would invalidate the `assert_no_lookahead` audit and the stateless-`Strategy`
contract (`ADR 0004`). Our `drain_events` flow to the **log + account only**;
strategies never see order or fill state.

**Resolves marker 8.** Confirmed backtrader couples broker → strategy via
`notify_order`; we deliberately avoid it. Events to log/account only; purity and
the lookahead audit preserved.

---

## Cross-cutting note — unplanned corroboration of `ADR 0008`

The backtrader read produced one finding beyond the checklist: backtrader
implements "frictionless vs friction" as **one execution path with a swappable
filler** (`filler=None → full fill`; `filler set → volume-capped`), not as two
separate engines. This is precisely the shared-execution-core architecture of
`ADR 0008` (friction as an opt-in dial on one core), arrived at independently by a
mature backtester. Recorded as external validation of the parent ADR; no change
required.

---

## Marker summary (for the `ADR 0010` hardening pass)

| Marker | Outcome |
|---|---|
| 1 — `BrokerBase` set / callbacks | **Resolved.** Core seam matches; Fubon 4-callback / Shioaji 1-callback both normalise to one fold (adapter's job) |
| 2 — status set / transitions | **Resolved.** Our set = backtrader's minus `Margin`, plus hard illegal-edge guards; 3-pre-fill collapse doubly corroborated |
| 3 — partial-fill propagation | **Resolved.** Per-deal accumulation decoupled from status flip; lot ledger consumes one update per deal |
| 4 — commission rule | **Resolved.** NT$20 per-order once; floor commission + tax |
| 5 — filler / slippage seam | **Resolved.** Swappable filler, size/price split; **reject** execution-bar volume → trailing ADV |
| 6 — price-limit fields | **Resolved.** Both brokers (`limitup_price`/`limitdown_price`/`reference_price`); replay derives, live reads |
| 7 — suspension data | **Resolved (design).** `NO_MARKET_DATA` detection (XTAI ∧ ¬FinMind) foundational; TWTAWU historical **suspension calendar** = evidence-gated `SUSPENSION` enrichment (graceful degradation); MOPS dropped (niche empty across roadmap) |
| 8 — `notify_order` coupling | **Resolved.** Confirmed as the anti-pattern; events to log/account only |

**All eight markers resolved.** `ADR 0010` can be promoted from **Proposed** to
**Accepted**. Marker 7 carries a bounded **enrichment build task** (build the
TWTAWU suspension calendar, validate against the Yahoo suspended calendar; graceful
degradation to `NO_MARKET_DATA`-only if the pull is not clean) — not an open
architectural question.
