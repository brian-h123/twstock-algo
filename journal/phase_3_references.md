# Phase 3 — Backtester Reference Study

> **Status: final.** Step 0 of the Phase 3 build sequence was targeted reading
> of backtrader and vectorbt source as architectural references. Step 15 (this
> update) adds the retrospective: which engine choices were directly shaped by
> what we read, which backtrader pieces to revisit in Phase 5, and what from
> vectorbt turned out not to matter.

## Why we read these libraries

The custom event-driven engine is locked (ADR 0004 — vectorbt rejected as the
backtester, build custom instead). We did **not** read these to reconsider that
decision; we read them as references:

- **backtrader** is the close architectural sibling — event-driven, bar-by-bar,
  with a real broker/order state machine. We read it to (a) validate the
  `execution="next_open"` decision against a mature implementation, and (b) prep
  the Phase 5 paper-sim broker and order-state work.
- **vectorbt** is the architectural opposite — fully vectorized, the paradigm we
  rejected. We read it narrowly: steal the tear-sheet metric list, verify the
  Sharpe/Sortino/drawdown formulas, and pin down *why* its default execution is
  unsafe — which is the concrete justification for ADR 0004.

## Headline finding — the lookahead-safety spectrum

The single most useful thing from both reads is that the two most popular
libraries sit at opposite ends of a spectrum, and our engine sits past both:

- **backtrader — lookahead is opt-*in*, and loudly labeled.** Safe by default:
  the run loop processes the broker *before* the strategy each bar, so an order
  created on bar T cannot fill before T+1. To get same-bar execution you must
  set a flag literally named **`cheat`** (`cheat_on_open`, `coc`/cheat-on-close).
- **vectorbt — lookahead is opt-*out*, and silent.** `Portfolio.from_signals`
  fills a signal on bar T at bar T's own price by default. If that signal used
  T's close, you've used end-of-day information to trade at end-of-day — and
  nothing errors. Safety requires *you* to remember to shift the signal array.
- **ours — lookahead is not a choice.** `execution="next_open"` enforces the
  T→T+1 boundary structurally. No flag, nothing to remember; the fill price
  (T+1 open) strictly postdates all information the signal used (≤ T close).

The progression opt-in danger → opt-out danger → impossible is the cleanest
argument for why building custom was worth it, and why lookahead safety is the
headline Phase 3 feature.

---

# backtrader observations

### `cerebro.py` — the run loop
- Two execution paths: `_runonce` (vectorized, default; indicators precomputed)
  vs `_runnext` (event-driven, bar-by-bar). We are the `_runnext` model. The
  existence of two paths is itself the observation — vectorization is a
  bolted-on mode.
- `_runnext` within-bar order: data advances → (timers + optional `next_open`
  cheat hook) → `_brokernotify()` → `strat._next()`. **Data → broker →
  strategy.**
- The T/T+1 boundary is *emergent from call order*, not a parameter: broker
  processes pending orders before the strategy creates new ones. Our engine
  names the same property as an explicit contract instead of leaving it implicit
  in loop structure.
- `cheat_on_open` / `next_open()` runs the strategy *before* the broker,
  collapsing T/T+1 → T/T. The labeled escape hatch.
- Multi-strategy + parameter optimization are engine-core
  (`itertools.product`, list-of-lists, multiprocessing pool). We push both out.
- Live-trading plumbing is interleaved into the loop (`newqcheck`, blocking
  queue waits, `_datanotify`/`_storenotify`, `_event_stop`). Historical-only in
  Phase 3.
- Warmup gating: first strategy call withheld until indicators have enough bars
  — our `min_periods=window`.

### `strategy.py` — strategy lifecycle
- Optional `pass` hooks, *not* enforced abstract methods — a wide lifecycle
  can't force every hook. Ours is one enforced `@abstractmethod`; a narrow
  interface can. Interface width drives the enforcement choice.
- Stateful callback object vs our stateless function: backtrader's strategy
  persists across bars, holds position/orders/indicators, mutates itself, talks
  to the broker. Ours is a pure `bars → target positions` transform; state lives
  in Portfolio + Engine.
- Strategy emits intent vs executes: backtrader places its own orders
  (`buy`/`sell`/`close` via `self.broker`); ours emits `{-1,0,+1}` and the
  engine diffs target-vs-actual. Strategy stays broker-ignorant.
- `notify_order` / `notify_trade` are the execution-reaction seam we omit in v1
  (Phase 5 partial-fills/rejections will need it).
- Metaclass auto-wires indicators/params declared as class attributes —
  rejected, plain pandas.

### `order.py` — order data model
- Three orthogonal axes: Direction (`Buy`/`Sell`) · Execution type
  (`Market`/`Limit`/`Stop`/`StopLimit`) · Status (9-state lifecycle).
- Nine states split transient (`Created`/`Submitted`/`Accepted`/`Partial`) vs
  terminal (`Completed`/`Canceled`/`Expired`/`Margin`/`Rejected`). Rejection
  *reasons* are distinct states because `notify_order` consumers care why.
- `OrderData`/`OrderExecutionBit` model fills as a list of execution bits for
  partial fills at multiple prices. Phase 3 fills atomically (one order → full
  fill at T+1 open); this is Phase 5 prep.

### `broker.py` — broker interface
- `BrokerBase` is the abstract interface — the swap-point between backtest
  simulator and live brokers. Mirrors our Phase 5 (`BrokerSimulator`) / Phase 8
  (Fubon) plan exactly.
- `buy`/`sell` do **not** fill — they build an `Order` and queue it, returning
  immediately unfilled. This is the code-level reason an order can't fill on its
  creation bar.
- `next()` is where fills happen — the broker walks its pending list and decides
  what fills at what price. Called from `_brokernotify`.

### `bbroker.py` — the backtest broker (fill engine)
- Fill pipeline: `next()` → walk `pending` deque (expiry → active → `_try_exec`)
  → dispatch by exectype → `_try_exec_market` picks price, applies adverse
  slippage → `_execute` updates position, computes commission, fires `notify`.
- **The price line.** Sourced in `_try_exec`:
  ```python
  popen = getattr(data, 'tick_open', None)
  if popen is None:
      popen = data.open[0]      # ← historical fill price = this bar's open
  # ... phigh/plow/pclose similarly
  ```
  The open enters as `data.open[0]`, passed as `popen` into `_try_exec_market`,
  and under the default (`coc=False`) is the fill price. `next()` runs the bar
  *after* order creation ⇒ fill at **T+1 open** = `execution="next_open"`,
  empirically. (`tick_open` is the live-data path; inert for historical runs.)
- Two cheats, two layers: `cheat_on_open` (cerebro — strategy before broker) and
  `coc`/cheat-on-close (bbroker — fill at this bar's close). Safe is default;
  lookahead is opt-in and named "cheat."
- Slippage: applied to base price pre-execution, always adverse (buy up / sell
  down), range-aware (clamped to the bar's high/low). Matches our 5 bps model,
  same seam.
- Commission via `comminfo`, at fill time, inside the broker — strategy never
  sees cost. Our Taiwan cost model lives at this exact seam.
- `opened`/`closed` = direction decomposition of a single fill (how much closes
  existing exposure vs opens new), **not** partial-fill status. Realized P&L
  computes on `closed`.

### `comminfo.py` — the cost object
- `CommInfoBase` is a configurable cost object computed at fill — the pattern we
  borrow. Key param: a commission-type discriminator, `COMM_PERC` (percentage)
  vs `COMM_FIXED` (flat amount); `_getcommission ≈ abs(size) * commission`.
- Maps onto our mixed model: percentage commission (0.1425% × 6折) with a
  `COMM_FIXED`-style NT$20 *floor*, plus percentage sell-tax with a stock/ETF
  rate split. `interest`-on-short param is irrelevant (no shorting in Phase 3)
  but the seam exists.

### `position.py` — position bookkeeping
- `Position.update(size, price)` returns `(opened, closed)` with
  **`opened + closed == size`** — the fill decomposed into exposure-increasing
  and exposure-reducing parts.
- Average-price rules: increase → size-weighted new average; reduce → remaining
  size keeps its price; close → price nullified; reverse → price = fill price.
- This *is* our Portfolio cost-basis logic. The reversal case (long→short in one
  fill) is the edge case to test explicitly, and with `{-1,0,+1}` targets a
  direct reversal is possible.

---

# vectorbt observations

### `portfolio/base.py` — the tear sheet
- `stats_defaults` is vectorbt's *assembled default* tear sheet (curated subset
  of the full returns menu + trade-ledger metrics) — a better template than the
  raw `returns_acc_config` because it's the "what to actually show" decision.
- Four families: **run-metadata** (start/end/period/start_value/end_value) ·
  **returns-based** (total_return, benchmark_return, max_gross_exposure, max_dd,
  max_dd_duration, sharpe/sortino/calmar/omega) · **trade-based** (trade counts,
  win_rate, best/worst, avg_win/avg_loss + durations, profit_factor, expectancy,
  open-vs-closed split) · **cost** (total_fees_paid).
- `benchmark_return` is a default field → 0050 buy-and-hold belongs *in* the
  tear sheet header, side-by-side, not a separate report.
- Open/closed trade split mirrors `position.py`'s `opened`/`closed` — same
  realized/unrealized boundary surfacing at the reporting layer.

### `returns/accessors.py` — annualization
- vectorbt parameterizes `ann_factor = year_freq / freq` for multi-frequency
  support. It's a documented assumption, not a magic number.
- Our policy: daily-only, so `year_freq = 252` is a committed constant (not a
  user knob). Comparability of the ratio over a fixed convention beats matching
  each year's variable TWSE session count (~240–250).
- Split by metric type: **252 / √252** for vol·Sharpe·Sortino (scaling a
  per-period statistic, comparability dominates); **actual elapsed time** for
  CAGR (a realized quantity over a real window). `exchange_calendars` stays in
  the data/alignment layer and in any session-counted duration metric — not in
  the volatility annualization factor.

### `returns/nb.py` — formulas (verified, conventions pinned)
- **Sharpe** = `mean(r − rf) / std(r, ddof=1) × √252`, `rf = 0`. vectorbt uses
  sample std (Bessel, ddof=1) — adopt.
- **Sortino** = `mean(r − target) / downside_dev`, `target = 0`;
  `downside_dev = sqrt(mean over ALL periods of min(r−target,0)²) × √factor`.
  Confirmed: positive deviations are masked upstream (`adj_returns[adj_returns
  > 0] = 0`), and the denominator averages over **all** periods, not
  downside-only. This all-periods choice is the convention implementations
  disagree on — stating it explicitly is the rigor signal.
- **Max drawdown** = from the running peak: `dd = equity/expanding_max(equity) −
  1`, `max_dd = min(dd)`. `start_value=100` is base-invariant (drawdown is a
  ratio), cosmetic.
- Metrics are **stateless pure functions of a returns array** — confirms our
  TearSheet computes from the equity-curve artifact as pure functions, no engine
  coupling. (Third instance of the "pure function over a clean artifact" pattern
  — see borrow #11.)
- vectorbt hand-writes numerical primitives (`expanding_max_1d_nb`,
  `cum_returns_1d_nb`) because **Numba can't compile `pandas.cummax`** — a
  compatibility tax, not a design choice. We rejected Numba, so we use pandas
  built-ins and inherit only their *explicitness about NaN/warmup semantics*.

### `signals/factory.py` — signal vs execution
- vectorbt is fully vectorized: the *entire* signal array (all bars at once) is
  built in `signals/`, then `Portfolio.from_signals` executes the full array in
  one pass. No "current bar," no stepping.
- Signal-generation (boolean arrays, in `signals/`) is cleanly separated from
  execution (`Portfolio.from_signals`, in `portfolio/`). We already enforce this
  split (stateless `generate_signals` → engine executes) — confirm-only.
- `from_signals` is **same-bar by default** (signal on T fills at T's own price)
  and lookahead-safety is opt-out (user must shift). See headline finding.

---

# What we borrow

1. **Broker interface as the sim/live swap-point** (`BrokerBase`). One
   interface, swappable `BrokerSimulator` (Phase 5) and Fubon adapter (Phase 8).
2. **Submit-vs-process separation.** Order creation only *queues*; a separate
   per-bar processing step fills. This is what makes next-bar execution
   structural rather than a flag.
3. **Cost computed at fill, inside the broker, via a configurable cost object.**
   Borrow the `COMM_PERC`/`COMM_FIXED` discriminator shape for our Taiwan model
   (percentage commission + NT$20 fixed floor + stock/ETF tax-rate split). Keeps
   the strategy cost-blind.
4. **Adverse, pre-execution slippage** applied to the base price — our 5 bps
   model, same placement.
5. **`opened`/`closed` fill decomposition** (`opened + closed == size`) for
   realized vs unrealized P&L; realized computes on `closed` against the prior
   average price. Direct Portfolio bookkeeping borrow; test the reversal case.
6. **Small closed order-state set, transient/terminal split, reasons broken
   out** — the *shape* (not contents) for a Phase 5 TWSE-specific rejection
   taxonomy.
7. **Indicator-warmup gating** before first strategy call — our
   `min_periods=window`.
8. **The curated default tear-sheet metric list** (`stats_defaults`, four
   families) as our TearSheet template — the metric *menu*, not the
   implementation.
9. **Specific metric adds beyond the original PHASE_3 spec:** `max_dd_duration`
   (underwater duration, not just depth), `expectancy` (avg P&L/trade),
   `benchmark_return` in-header (0050 side-by-side), open-vs-closed trade P&L
   split. All cheap, all high-signal.
10. **Verified formula conventions, documented as our own choices:** Sharpe
    `ddof=1, rf=0`; Sortino downside-deviation over all periods, `target=0`;
    max-drawdown from running peak. Annualization 252/√252 for ratios, real
    elapsed time for CAGR.
11. **The "pure function over a clean artifact" pattern** — appears three times
    and is the spine of the design: stateless *strategy* (signals from prices),
    stateless *metrics* (tear sheet from equity curve), post-hoc *analyzers*
    (from the run-result artifact). All three are pure functions over a clean
    output, never coupled to engine internals.
12. **Explicit NaN/warmup semantics** — via pandas' NaN-aware methods + our
    `min_periods` discipline, matching vectorbt's explicitness without its
    custom primitives.

# What we reject

Each tagged **Additive** (buildable later as a new component without reworking
the core) or **Architectural** (reversing would require reworking the core).
See "On reversibility" below.

1. **Vectorized execution path (`_runonce`).** Unneeded at 50 tickers × 10
   years; event-driven is fast enough and far easier to reason about for
   lookahead. *(Architectural, but moot.)*
2. **Multi-strategy co-simulation.** One strategy per run; compare via separate
   runs + a table (Phase 4), not co-simulated allocation. *(Architectural.)*
3. **Parameter optimization in the engine core.** `WalkForwardSplit` and
   parameter choices live *outside* — an optimizer wraps the engine, not vice
   versa, which keeps out-of-sample reporting honest. *(Additive.)*
4. **Live-trading plumbing in the loop.** Historical-only in Phase 3; the
   real-time driver wraps the same pieces in Phase 5. *(Additive.)*
5. **Both cheats (`cheat_on_open`, `cheat_on_close`) — and vectorbt's same-bar
   default.** Lookahead is the biggest backtest failure mode and honest
   evaluation is the project's core value; we expose no opt-in at all.
   `execution` is `Literal["next_open"]`. *(Mechanically additive, but
   philosophically locked — preventing accidental reversibility is the point.)*
6. **Strategy↔broker coupling / strategy places its own orders.** Ours emits
   intent only; the engine owns execution. Cleaner boundary and a precondition
   for the lookahead audit. *(Architectural.)*
7. **Stateful callback strategy.** Ours is a stateless pure function — this is
   load-bearing: `assert_no_lookahead` perturbs future data and checks output is
   unchanged, which is only meaningful for a pure function. *(Architectural —
   the least reversible, by design; reversing voids the audit.)*
8. **Optional `pass`-hook lifecycle.** Ours is one enforced `@abstractmethod`;
   Phase 5 will widen the ABC with optional reaction hooks, which is
   backward-compatible. *(Additive.)*
9. **Metaclass / `IndicatorFactory` indicator+param auto-wiring.** Plain pandas
   (`rolling`/`expanding`) instead; no magic. *(N/A.)*
10. **Multiple execution types (limit/stop/stoplimit).** Market-on-next-open
    only in Phase 3; the borrowed order-state shape makes adding types clean
    later. *(Additive.)*
11. **Execution bits / partial fills.** Atomic, full-size fills in Phase 3.
    *(Additive — Phase 5 prep.)*
12. **Analyzer *framework* (in-loop).** A single `TearSheet` class instead — see
    reversibility note; extensibility is relocated, not lost. *(Additive.)*
13. **Numba/JIT and hand-written numerical primitives.** We use pandas built-ins
    (`cummax`, `rolling`, `expanding`). vectorbt's custom primitives exist to
    satisfy Numba's restricted subset; copying them would cargo-cult their
    *constraint* without sharing its *cause*. *(N/A — different toolchain.)*
14. **Configurable multi-frequency annualization.** We commit `year_freq = 252`
    as policy. The right framing is *not* "configurable → hardcoded" but
    "general-purpose parameter (vectorbt can't assume frequency) → single-purpose
    committed policy with rationale." *(N/A.)*

### On reversibility

The rejects split cleanly, and the split is reassuring. Most are **additive**:
limit/stop orders, partial fills, an optimizer wrapper, the live-trading driver,
a wider strategy ABC, more analyzers — all built *later* as new components
without touching the Phase 3 core. We are not painting ourselves into a corner;
we are declining to build Phase 5–8 features during Phase 3.

The few **architectural** rejects (stateless strategy, strategy/broker
separation, single-strategy core) are exactly the ones tied to the project's
thesis — lookahead safety and honest evaluation. Being locked on these is a
*feature*: they are the structural guarantees, reversed only by abandoning the
thesis.

A recurring theme unifies most rejects: **both libraries build for N use cases;
we build for one and bake in the decision.** General-purpose flexibility
(configurable annualization, multi-strategy cores, multiple execution types,
the analyzer framework) is complexity we trade away for a legible single-purpose
engine.

**The TearSheet → Analyzer case is the clearest example of relocated, not lost,
extensibility.** Rejecting the in-loop analyzer *framework* costs us nothing in
analyzer *extensibility* — provided the engine emits clean, complete output
artifacts (equity curve + trade ledger + daily positions). Any number of
analyzers are then post-hoc pure functions over those outputs. The only thing
the in-loop framework buys is *streaming* computation, which matters for Phase 8
live, not for backtests at our scale. So the prerequisite for that future is not
an engine rewrite — it's making the output contract clean *now* (see Future #6).

---

# Future things to consider

Deferred items noticed while reading, mostly Phase 5+:

1. **`notify_order`/`notify_trade` reaction channel.** Required once fills can be
   partial or rejected. Open: the minimal hook to add to the strategy interface
   without breaking statelessness.
2. **Range-capping slippage** to the bar's high/low — adopt backtrader's clamp,
   or accept that a slipped price can exceed the bar range?
3. **Stop orders.** Do any Phase 4 strategies (stop-loss exits) need a stop
   execution type, or do we model exits as signals?
4. **Open execution in the real-time paper-sim.** Is same-day-open fill ever
   desirable in Phase 5, or is next-open still the honest live default?
5. **Live-feed queue management.** backtrader's `newqcheck`/blocking-wait
   machinery resurfaces when Phase 5 pulls real-time data.
6. **Engine emits a complete, well-typed run-result object** (equity curve +
   trade ledger + daily positions). Treat as a *Phase 3 design requirement* — it
   is what preserves tear-sheet and future-analyzer reversibility (see "On
   reversibility").
7. **`deflated_sharpe_ratio`** (seen in vectorbt's returns menu) corrects Sharpe
   for multiple-testing/selection bias — the exact Phase 4 multiple-comparisons
   concern. Candidate metric for the Phase 4 strategy bake-off, not Phase 3.
8. **Sizer: inline vs separate object.** backtrader extracts position sizing into
   a `Sizer`. Our max-10%-per-position rule is a single constraint — lean inline
   for Phase 3, extract only if Phase 4 needs varied sizing schemes. Additive
   either way.

---

# Retrospective — what actually happened (added at step 15)

*Written after the engine was fully built (steps 2–14 complete, all exit criteria
green). The test below is: did the draft notes predict what mattered, or were
they post-hoc rationalization? Answer: mostly predictive; a few items turned out
simpler or not needed at this scale.*

## Design choices directly shaped by this reading

**`pending_orders` dict as the T→T+1 boundary.**
The most load-bearing engine insight came from `bbroker.py`: `buy()`/`sell()` do
not fill — they queue. `broker.next()` fills. Translating that into our stateless
engine produced the `pending_orders: dict[str, str]` pattern — orders scheduled
at the signal bar, consumed at the next bar's open. The code reads exactly like
the borrow description predicted; nothing had to be rethought during
implementation.

**Sortino downside-deviation convention.**
`nb.py`'s Sortino formula is the one formulation that implementations genuinely
disagree on. Verifying it from source resolved the ambiguity before it became a
bug: mask positives → compute mean of squared negatives over **all** periods →
take sqrt. The `tearsheet.py` implementation matches, and the code comment
(`# downside_dev averages over ALL n periods — borrow #10`) points directly back
here. Without having pinned the convention explicitly in the reference study,
we'd have had to reverse-engineer it from test failures.

**`_ANN_FACTOR = 252` as committed policy, not a parameter.**
vectorbt's multi-frequency `ann_factor = year_freq / freq` was studied precisely
to justify *why* we don't need it. The tearsheet commits `_ANN_FACTOR = 252` as
a module-level constant with a comment pointing to borrow #14. The right framing
— "single-purpose committed policy" vs "general-purpose parameter for a library
that can't assume frequency" — was clearest after seeing the vectorbt code.

**Stateless strategy + perturbation audit.**
The architectural decision that `generate_signals` must be a pure function of
`bars` (no mutable instance state) was not obvious from first principles — it's
easy to imagine a strategy that accumulates a position buffer across bars. Seeing
backtrader's stateful callback strategy (§`strategy.py`) and tracing *why* that
makes `assert_no_lookahead`-style verification impossible (you can't just
re-run the function with a perturbed copy if the function has external state)
crystallized the constraint. The note "reversing voids the audit" (Reject #7)
is precisely why `audit.py`'s perturbation approach is coherent: it only works
on a pure function.

**`max_drawdown_duration_days` added to the tear sheet.**
`stats_defaults` from vectorbt surfaced `max_dd_duration` as a standard metric
we had not originally spec'd. It's in the tear sheet (`max_drawdown_duration_days`)
because the reference study called it out (Borrow #9); without the reading it
would have been a Phase 4 addition.

**Cost at fill, strategy cost-blind.**
The `TaiwanCostModel` is called inside `Portfolio.buy()` / `Portfolio.sell()`,
not by the strategy or the engine loop. This placement — cost computed at the
fill boundary, never visible to the strategy — was a direct borrow from
`comminfo.py`'s `_getcommission` call inside `_execute`. It's a small point but
the right seam: the strategy can't accidentally branch on cost information it
shouldn't have.

## What turned out simpler than the notes predicted

**The `opened`/`closed` fill decomposition (Borrow #5).**
`position.py`'s `opened + closed == size` decomposition handles the reversal
case (long→short in one fill). We don't need it. Phase 3 is long-only, fills are
atomic, and round-trip P&L is computed in `_round_trips()` by pairing sorted buy
and sell records — a simpler calculation that doesn't need per-fill direction
tracking. The Phase 5 partial-fills case *will* need the decomposition; the notes
are correct that it's prep material, not current use.

**The order state machine (9 states across 4 dimensions).**
`order.py`'s `Created → Submitted → Accepted → Partial → Completed / Canceled /
Expired / Margin / Rejected` lifecycle exists because backtrader must handle live
broker acknowledgement latency, rejections, and partial fills. Our engine needs
none of it: `pending_orders` is a `dict[str, Literal["buy", "sell"]]`. Two
states (`pending` vs `not pending`), zero transition tracking. The notes correctly
flagged the shape of backtrader's model as Phase 5 prep (Borrow #6); the Phase 3
engine happily ignores all of it.

**Sizer: stayed inline, exactly as predicted (Future #8).**
Position size is computed with three lines in the engine loop:
`target_amount = portfolio.equity * self._max_position_pct`,
`spendable = min(target_amount, portfolio.cash)`,
`shares = math.floor(spendable / open_prices[ticker])`.
Extracting a `Sizer` object would have been premature.

**Range-capping slippage (Future #2).**
bbroker.py clamps slippage to the bar's high/low so a slipped fill price can't
exceed the bar range. We don't implement this. At 5 bps per side on TWSE Top-50
names, slippage is a rounding-error against the bar range, and the simplification
doesn't affect any result in the test suite. Revisit only if slippage grows or
the universe expands to less-liquid names.

## What from vectorbt turned out not to matter

**The vectorized signal pipeline (`signals/factory.py`).**
The whole `Entry`/`Exit` boolean-array infrastructure exists to feed
`Portfolio.from_signals` in one pass. We don't have a vectorized portfolio.
Reading it confirmed there was nothing to borrow architecturally — it's a
consequence of vectorbt's all-at-once execution model, which we rejected. The
notes correctly marked it "confirm-only" (see `signals/factory.py` observation).

**Numba primitives (`expanding_max_1d_nb`, `cum_returns_1d_nb`).**
Understood immediately as a Numba compatibility tax. pandas' `.cummax()` and
`.pct_change()` do exactly the same thing with less code. Rejecting Numba at the
dependency level made these irrelevant; noting *why* they exist prevents cargo-
culting them later.

**Multi-frequency annualization.**
The full `ann_factor = year_freq / freq` machinery in
`returns/accessors.py` exists because vectorbt supports minute, hour, day, and
week bars. We have one frequency, one factor, one line. The reading was useful
only to confirm we were right to commit the constant.

**`expectancy` metric.**
vectorbt's `stats_defaults` includes `expectancy = avg_win × hit_rate − avg_loss
× (1 − hit_rate)` — the expected value per trade. It was listed as a Borrow #9
candidate but didn't make the final `tear_sheet()`. The metric is meaningful but
adds nothing to Phase 3's SMA crossover evaluation that `avg_win`, `avg_loss`,
and `hit_rate` don't already supply. Candidate for Phase 4 when comparing
multiple strategies.

**`deflated_sharpe_ratio` (Future #7).**
Still deferred. Correct for Phase 4's multi-strategy comparison where
multiple-testing bias is the concern. Not relevant for a single-strategy
validation run.

## backtrader pieces to revisit in Phase 5

The order-level architecture from `order.py` + `bbroker.py` is the primary Phase
5 reference:

- **9-state lifecycle, split into transient / terminal.** `Created → Submitted →
  Accepted → Partial → Completed / Canceled / Expired / Margin / Rejected`.
  Phase 5's TWSE paper-sim will need a subset of this — likely
  `Pending → Filled / Rejected / Expired`. The exact Taiwan-broker rejection
  taxonomy (margin call, price-band violation, circuit breaker, insufficient lot
  size) is the Phase 5 design decision; backtrader's *shape* is the template.

- **`notify_order` / `notify_trade` reaction hooks.** Phase 5 strategies may want
  to react to fills (e.g. log a partial fill, adjust a stop). These hooks need
  to be added to the Strategy ABC without breaking the statelessness invariant —
  which means they fire after `generate_signals`, not inside it, and they don't
  mutate the signal-generation path. The backtrader design gets this right
  (callback, not a signal-path intrusion); the Phase 5 ABC extension should
  follow the same pattern.

- **Execution bits / partial fills.** `OrderExecutionBit` and the `opened +
  closed == size` accounting in `position.py` become relevant when the paper-sim
  routes orders through a real book. The bbroker.py read is the primary reference
  for how to decompose a fill into exposure-increasing and exposure-reducing
  components and compute realized P&L on the `closed` portion only.

- **`BrokerBase` as the sim/live swap-point (Borrow #1).** Not yet materialized —
  Phase 5 is where `BrokerSimulator` and the Fubon adapter both implement a
  shared interface. The abstract method set from backtrader's `BrokerBase`
  (`buy`, `sell`, `cancel`, `getposition`, `getvalue`, `getcash`, `next`) is the
  right starting menu; prune to what Fubon's API actually supports.

- **Live-feed queue machinery** (`newqcheck`, blocking queue waits,
  `_datanotify`/`_storenotify`, `_event_stop` in `cerebro.py`). Phase 5 paper-
  sim is still pulling from static Parquet; Phase 8 live will need a real-time
  feed queue. The cerebro.py live-mode sections are the reference for how an
  event loop integrates a blocking data queue without busy-waiting.

## Summary judgment

The reference study was more useful than the usual pre-implementation reading —
because it was *targeted*. We read `bbroker.py` to understand fill mechanics, not
to understand every cerebro feature. We read `nb.py` to verify one disputed
formula, not to understand Numba. The narrowness kept the volume manageable and
the borrows concrete.

The test of whether pre-code reading pays off is whether it changed any decision
made during implementation. It changed three:

1. The `pending_orders` pattern (directly from `bbroker.py`'s queue-then-fill
   split).
2. The Sortino all-periods denominator (directly from `nb.py`).
3. Adding `max_drawdown_duration_days` to the tear sheet (from
   `stats_defaults`).

Everything else was validation — confirming the planned design was consistent with
what mature libraries do, or confirming a rejection was justified. Validation is
not waste: it means the Phase 3 spec was well-designed before the code started.
