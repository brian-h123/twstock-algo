# 0014 — The `raw_vs_adjusted` Residual Reflects Engine/Sim Design Intent, Not Unmeasured Friction

**Date:** 2026-07-25
**Status:** Accepted

## Context

After landing the binding-ceiling fix (`ADR 0011` D2 follow-up), the
`liquidate_at_end`/`unliquidated_residual_cost` line, and the
`read_corporate_actions` date-bounding fix, both demonstrator reconciliations
still failed the `raw_vs_adjusted` tripwire, at very different magnitudes:

- **Single-asset** (`SmaCrossover(20,60)` on 2330, 2021–2025): +0.368%, an
  order of magnitude past the 5bp band. The order audit is completely
  empty — no `PARTIAL_FILL`, no `REJECT` — ruling out liquidity, cash-ceiling,
  and rejection-driven friction entirely.
- **Cross-sectional** (`CrossSectionalMomentum`, Top-50, monthly rebalance,
  same window): -6.7%, over an order of magnitude larger still.

### Single-asset: raw-vs-adjusted price basis

Dissecting `sim.trades` against `reference.trades` side by side surfaced the
cause. Every trade lands on the **identical date** in both (signal timing
matches exactly — both compute signals from the same `get_adjusted_history`
call), but the **fill prices and share counts differ substantially** even on
matching dates. First trade, both 2021-06-21: sim buys 165 shares @ 590.30;
reference buys 184 shares @ 542.66.

The root cause: `Engine._run_single_asset` fetches `get_adjusted_history`
once and executes directly off that **adjusted** series for both signal and
fill (`engine.py:46,64` — `open_prices` is read straight from `bars`, the
adjusted DataFrame). `PaperSession`'s `ReplayFeed`, by contrast, calls
`get_adjusted_history` only to drive the strategy's signal timing, but reads
`read_ohlcv` — **raw, unadjusted** — prices for `Bar.open`/`Bar.close`, which
is what `BrokerSimulator._resolve_order` actually fills against
(`feed.py:49`). Cash dividends are credited to the sim's account directly as
`CashMutation` events (13 payments totaling NT$6,682, 0.668% of capital, over
the window) rather than being folded into the price series.

This one divergence compounds through the whole run: every round trip that
spans a dividend ex-date shows a measurably different raw-price return vs
the reference's adjusted-price return on the identical entry/exit dates
(e.g. the 2023-11-06→2024-08-19 leg, spanning three ex-dividend dates: +75.05%
raw vs +77.46% adjusted), and because position sizing at each re-entry
depends on the account's *current* equity (which differs from the reference's
by then), the two paths diverge further with each subsequent trade rather
than staying a fixed offset.

### Cross-sectional: the same root cause, amplified by rebalancing

Repeating the trade-level dissection on the cross-sectional run confirmed
the same underlying mechanism, but with a materially larger and structurally
different manifestation. Rebalance *dates* match exactly across all 46
months (the `Monthly()` schedule is calendar-driven and unaffected). But the
ticker *sets* actually held after each rebalance mismatch on **29 of 46
dates (63%)**.

The cause traces to `reconciler.py::reconcile()`:

```python
target_shares = math.floor(portfolio.equity * target_weight / price)
delta = target_shares - current_shares
if delta == 0:
    continue  # no trade
```

`target_weight` (the Top-10 momentum selection) is a pure function of the
shared adjusted-price panel and should be identical in both engines. But
`portfolio.equity` and `price` are not: the sim's `_current_weights` is
computed from raw close prices and the sim's own (already price-basis-
diverged) account state, while the reference's equity and price are
adjusted-basis throughout. This is an **exact, zero-tolerance integer
check** — a small, continuous equity/price gap doesn't shrink the trade
size proportionally the way it does in single-asset; it flips `delta`
between `0` and `±1`, converting a modest price gap into a binary
trade-or-no-trade decision. Because each month's ending holdings become the
next month's starting `current_shares`, a flipped decision in one month
changes the reconcile inputs for every subsequent month — the divergence is
path-dependent and compounds across all 46 rebalances rather than resetting
each period. That compounding, not a larger per-ticker effect, is why the
cross-sectional gap is over an order of magnitude bigger than single-asset's.

No second, independent cause was found — the cross-sectional gap is
consistent with amplification of the single-asset mechanism, not a distinct
bug.

## Decision

**The `raw_vs_adjusted` residual — in both the single-asset and
cross-sectional shapes — is treated as a meaningful design-intent signal,
not a bug to reconcile toward zero, and no attempt is made to tune the
tripwire tolerance to accommodate it.**

`ADR 0011` D1 defined `raw_vs_adjusted` as "an honest catch-all... not proof
of causal attribution," with a loose band chosen so a real corporate-action
bug would still trip it. That framing implicitly assumed the residual would
generally trend toward zero once measured frictions were subtracted. This
investigation revises that assumption for any real, dividend-paying,
multi-trade run — single-asset or cross-sectional: the residual will not
trend toward zero, because it isn't friction at all. It is the structural,
unavoidable consequence of two engines with genuinely different and equally
legitimate design intents:

- **`Engine` (Phase 4)** is the frictionless "perfect world" reference. It
  trades a back-adjusted synthetic price series specifically because that
  series is *designed* to make historical returns comparable across
  corporate actions — it bakes in a dividend-reinvestment assumption by
  construction. That's the right choice for a tool whose job is isolating
  strategy signal quality. Its `reconcile()`-driven rebalancing inherits the
  same basis for cross-sectional strategies.
- **`PaperSession`/`BrokerSimulator` (Phase 5)** is the "next step toward
  reality" layer. A real broker will never execute an order — or size a
  rebalance — at a synthetic back-adjusted price; only at the real, raw,
  traded price, with dividends arriving as discrete cash on `pay_date` rather
  than an instantaneous reinvestment into more shares. Trading raw and
  crediting dividends as discrete cash events is the *more correct* behavior
  for what this layer exists to model, not a simplification to fix.

Given both behaviors are individually correct for what each engine is for,
reconciling them to near-zero is not a reasonable goal, and no tolerance
change is made to `raw_vs_adjusted`'s existing band — a real bug should
still be able to trip it; this decision changes how the *residual itself* is
interpreted, not the threshold.

**Fixing `Engine`'s pricing basis is deferred to the future backtester
migration, not actioned now.** Switching `Engine` to size positions and
rebalances off raw prices (with corporate actions credited directly, the way
the sim already does) is the natural test of this ADR's diagnosis: if the
sim/Engine reconciliation gap collapses once both sides share a price basis,
that confirms the effect is fully explained by this mechanism with no
remaining unknown cause. That migration is out of scope for Phase 5 —
`Engine` is closed Phase 4 infrastructure, every existing Phase 3/4 tear
sheet was produced on the adjusted basis, and changing it now would be a
scope violation of `CLAUDE.md`'s phase-ordering guardrail, not a Phase 5 fix.

## Consequences

- This surfaces an open question about `Engine` (Phase 4) rather than a
  Phase 5 defect: sizing positions (and, for cross-sectional strategies,
  rebalance rotations) off adjusted prices may make backtested performance
  measurably more optimistic than a real fill ever could be, since the
  adjusted series is systematically lower in the past for a dividend-paying
  stock, buying more shares per dollar than reality would allow, and can
  select an entirely different rotation path once compounded over many
  rebalances. Revisit when the backtester migration is scheduled: rerun both
  demonstrators against a raw-priced `Engine` and check whether
  `raw_vs_adjusted` shrinks to the friction-only residual this ADR argues it
  should.
- A new waterfall line to cleanly *isolate* (name and measure) the
  single-asset price-basis effect — mirroring how `unliquidated_residual_cost`
  isolates the forced-liquidation asymmetry — remains a candidate but is
  **not yet designed or implemented**, and is now understood to only cover
  the single-asset shape of the problem.
- The cross-sectional shape does **not** reduce to the same "reprice the
  trade sequence" isolation technique: because the ticker sets traded
  diverge, there is no shared trade sequence left to reprice by the second
  or third rebalance. Isolating it would need a different construct — e.g.
  measuring the rate of `delta`-sign flips at the reconcile boundary, or
  tracking cumulative holdings-set divergence — and is left as an open
  design question, not resolved by this ADR.
- Given the note above, whether a cross-sectional-specific waterfall line is
  worth building at all before the backtester migration is itself an open
  question: if the migration is expected to resolve the root cause directly,
  a bespoke isolation line for the interim may not be worth the design and
  implementation cost. Revisit once the migration is scheduled.

## Related

- Amends: `ADR 0011` D1's framing of `raw_vs_adjusted` as a residual expected
  to trend toward zero after subtracting measured friction lines.
- Sibling: `ADR 0008` (shared execution core — frictionless reference vs
  pessimistic sim as the reconciliation's whole premise); `ADR 0007` (stock
  split / adjustment conventions this ADR's price-basis analysis relies on);
  `ADR 0006` (portfolio runner architecture — `reconciler.py::reconcile`'s
  floor-based share sizing, the amplification mechanism for the
  cross-sectional case).
- Discovered while dissecting both demonstrators' `raw_vs_adjusted` tripwire
  failures (`scripts/phase_5_paper_run.py`, build-sequence step 15).
