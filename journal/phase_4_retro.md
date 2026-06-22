# Phase 4 Retrospective — Technical Strategies

**Branch:** `phase-4`
**Planning:** 2026-06-14 → 2026-06-17
**Code started:** 2026-06-18
**Steps completed:** 1–15 (Step 16 — close-out docs — in progress)
**Tests at close:** ~310 passed (unit); integration tests green
**Integration tests:** per-strategy on real 2330 data; CrossSectionalMomentum end-to-end on full universe

---

## What Shipped

**4a — Single-asset strategies (Steps 1–8)**

Six strategies fully evaluated across 49 tickers (7769 excluded — insufficient history):
`SmaCrossover` (Phase 3 reference), `EmaCrossover`, `RsiReversion`, `BollingerBreakout`,
`Macd`, `DonchianBreakout`. Each with a unit test suite, an integration test on real 2330 data,
a `assert_no_lookahead` pass, and a one-page writeup in `journal/strategies/`.

`macd()` and `donchian_channels()` added to `indicators/core.py`. The lot-consuming FIFO ledger
(`build_round_trips` in `backtest/trades.py`) replaced `_round_trips` in tearsheet.py, with a
regression invariant confirmed: FlatToFlat + FIFO reproduces the old metrics on whole-position
single-asset results. `scripts/phase_4_evaluate.py` runs any strategy across all 50 tickers and
emits the distribution (median/IQR net Sharpe, % beating own B&H, % beating 0050).

**4b — Portfolio runner (Steps 9–14)**

`CrossSectionalStrategy` ABC + `CrossSectionalAdapter` in `backtest/cross_sectional.py`.
`TieBreakPolicy` (`LiquidityTieBreak`, `HoldAll`) in `backtest/tiebreak.py`.
`EqualWeightTopN` allocator in `backtest/allocator.py`.
`RebalanceSchedule` (`Monthly`, `Weekly`, `Daily`, `Quarterly`) in `backtest/rebalance.py`.
`reconcile()` in `backtest/reconciler.py`.
Panel-aware `assert_no_lookahead` in `backtest/audit.py`.
`Engine.run()` dispatches both paths from one execution core.
`CrossSectionalMomentum` (native `CrossSectionalStrategy`) evaluated end-to-end.

**Step 15 — Writeups and comparison table**

Seven strategy writeups in `journal/strategies/`; consolidated `comparison.md` with honest
reporting: all single-asset strategies lose to 0050 B&H; CrossSectionalMomentum is the only
one that clears the benchmark on risk-adjusted terms.

---

## Wins

**The planning week was not wasted time — it compressed everything after it.**

Phase 4's kickoff took four days (June 14–17) before a single line of code was written.
The output was ADR 0005, ADR 0006, and a finalized PHASE_4.md with a 14-step sequence and
full module specs. Steps 1–6 (all five new single-asset strategies) shipped in a single
session because each was a substitution into a known shape, not a design decision. Steps 9–12
(cross-sectional interface, tiebreak, allocator, rebalance) also landed in one session.
The front-loaded spec was doing the compression. In Phase 3, the same pattern held.
If anything, Phase 4 makes the argument more forcefully: the 4b architecture (three causally
linked open questions — interface shape, cash model, trade accounting) could not have been
designed incrementally without mid-phase backtracking.

**Stateless purity is a property you have to earn, not just declare.**

The three-question bundle (interface shape, cash model, trade accounting) turned out to be
causally linked in a non-obvious way: the interface shape determines what the strategy can
express; the cash model determines whether same-day rotations are fundable; the trade ledger
determines whether re-trims corrupt round-trip metrics. Getting the interface wrong forces
the cash model to compensate; getting the cash model wrong breaks trade accounting. All three
had to be resolved together, in the planning session, before any module was written. The
planning investment is what made the implementation mostly transcription.

**`_round_trips` replacement was overdue and the lot ledger is correct.**

The lot-consuming FIFO ledger (`build_round_trips`, Step 7) replaced a known Phase 3
technical debt. `_round_trips` assumed whole-position entries and exits — correct for Phase 3's
constraints, but wrong the moment re-trims appear. Phase 4 paid this debt before the portfolio
runner needed it, so the canonical FlatToFlat + FIFO path was available immediately for
`CrossSectionalMomentum`. The regression test (old metrics reproduced on all-or-nothing
single-asset results) provided the acceptance proof.

**Honest reporting is part of the deliverable, not a note at the bottom.**

Every writeup names survivorship bias, multiple comparisons, and the canonical-parameter
convention explicitly. The comparison table includes losers. The CrossSectionalMomentum
writeup explicitly qualifies the 17.82% CAGR as overstated due to survivorship bias,
before giving the Sharpe number. A reviewer reading these writeups sees that the methodology
is disciplined even where the results look good. That is the project's value proposition.

---

## Slips

**The allocator/tiebreak build sequence was specified in the wrong order.**

PHASE_4.md §5 originally had Step 10 (allocator) before Step 11 (tiebreak), but
`EqualWeightTopN` takes a `TieBreakPolicy` as a constructor argument — tiebreak must be built
first. Caught at the spec level (before any code existed) and fixed with a spec commit.
A minor slip: the dependency direction should be readable from the import graph.

**The `FILL_TO_FILL` branch was not fully migrated during the gross→net transition.**

The original `_round_trips` used raw `fill_price` as both entry basis and exit price —
gross reporting. When `build_round_trips` switched to Option B (net reporting, cost-adjusted
basis and exit), `FLAT_TO_FLAT` was correctly updated to read cost-adjusted slice values.
`FILL_TO_FILL` was left reading `row.fill_price` directly — still gross. The two branches
silently diverged on the same formula; on zero-cost fixtures the values are numerically
identical, so no existing test caught the discrepancy. Only a cost-aware test exposed it.
Lesson: when migrating a formula across two branches, write a test that explicitly verifies
both branches agree on a non-trivial input before closing the change.

**`InsufficientCashError` during the first cross-sectional integration test.**

The reconciler computes target shares using equity at T's close and fills at T+1's open with
slippage. On a growing portfolio, new entries need funding at the higher equity level, but
exiting underperformers free up only their smaller actual values. The NT$20 minimum commission
floor makes small residual orders disproportionately expensive. A cash-cap fix was applied to
`engine.py _run_cross_sectional` (mirroring the single-asset path's `min(target, cash)` clamp),
but this is a patch: it processes buys in list order (first-come-first-served) rather than
spreading available cash proportionally. The correct solution is a Phase 5 task.

---

## The Architectural Tensions — Questioning Earlier Decisions

This theme deserves its own section because it recurred more than once during Phase 4, and
the way it was handled is worth recording explicitly.

**The stateless purity contract forced the sibling strategy shape — and that was the right trade.**

When designing `CrossSectionalStrategy`, the question was: why not a unified method
(`next(self, panel)`) like backtrader's universal `next()`? Backtrader's design is
genuinely clean and ergonomic — a single entry point handles both single-asset and
multi-asset strategies. The reason it was not available here is that backtrader's strategies
are *stateful callbacks*: `self.datas`, `self.buy()`, `self.sell()` are instance state.
The Phase 3 stateless purity contract (ADR 0004 D3) was the constraint: a stateless
`generate_signals` is the only reason `assert_no_lookahead` can permute future bars and
check that past signals are unchanged. A stateful callback makes that audit mechanically
impossible.

The key insight from this moment: the earlier decision was not wrong, it was *deliberate*.
The trade was ergonomics (universal method) for auditability (provable no-lookahead). Once
that trade was stated clearly, the sibling ABC shape was the only correct answer, not a
workaround. The constraint was real, the justification was sufficient, and the design held.

**`_round_trips` genuinely constrained what Phase 4 could do — and had to be replaced.**

Unlike the stateless purity constraint (which was not wrong), `_round_trips` was a known
Phase 3 simplification that became load-bearing when re-trims entered the picture. The
Phase 3 tearsheet notes explicitly flagged it as a Phase 5 blocker. Replacing it in Step 7
was not questioning the original decision — it was *executing the deferred plan*. The lot
ledger is what `_round_trips` was always supposed to become.

The distinction matters: some earlier decisions are structural (stateless purity — don't
challenge without a very good argument); others are tactical simplifications made with a
known expiration date (`_round_trips` — replace as scheduled). Treating the former like
the latter wastes planning capital; treating the latter like the former delays necessary work.

**The buy-sizing assumption from Phase 3 did not extend cleanly to the cross-sectional path.**

The Phase 3 engine's buy-sizing clamp (`spendable = min(target_amount, portfolio.cash)`)
worked for single-asset because there is only one pending buy at a time and the cash state
is exact before the buy executes. The cross-sectional engine queues multiple buys and
executes them sequentially; by the third or fourth buy, the cash estimate from the reconciler
may be stale by the costs of the earlier buys. This is not a Phase 3 mistake — single-asset
truly does not have this problem. It is the signature of a new problem class that only
surfaces when multiple simultaneous orders share a cash pool.

The honest observation here is that **the transition from single-asset to multi-asset
introduces a category of interaction effects that simply do not exist in the single-asset
case**. Budget arithmetic that is exact for one order becomes approximate for ten sequential
orders drawing from the same pool. The Phase 4 fix handles it adequately for the current
constraint set (long-only, market orders, no simultaneous settlement); Phase 5 will need to
handle it properly.

**The general principle that emerged:**

When an earlier decision feels constraining, the first question should be: *is this constraint
justified, or is it a tactical simplification?* If justified, argue from the ground up — find
the design session, read why the trade was made, and check whether the argument still holds.
If the constraint is tactical, execute the planned replacement on schedule. And if neither
applies — if the constraint is genuinely blocking something the original design did not
anticipate — write an ADR before changing anything. The stock split gap (below) is an example:
the data pipeline architecture was not wrong, it just hadn't yet handled a corporate action
class that had never appeared in the training period.

---

## Key Findings from Evaluation

**No single-asset strategy reliably beats buy-and-hold.**

The best single-asset strategy (EmaCrossover) beats its own B&H at the median only 51% of
tickers — barely above random. Only 27–29% of tickers beat 0050 B&H for even the best
single-asset strategies. This is an honest result: unconditional technical rules applied
uniformly across a universe of large-cap stocks in a broadly bullish period will not
systematically outperform passive.

**Turnover is the decisive factor, not signal quality.**

MACD produces directional accuracy broadly similar to the crossover strategies but at ~92%
annual turnover versus ~18% for SMA. The difference in fees (median ~NT$30k vs ~NT$7k)
explains most of the Sharpe gap. The 0.3% sell tax is not a nuisance cost — it is the
dominant performance driver for any strategy that rebalances frequently.

**Cross-sectional ranking amplifies alpha; within-name filtering does not.**

CrossSectionalMomentum (Sharpe 0.779 vs 0050's 0.690) is the only Phase 4 strategy that
clears the benchmark. The ranking step — holding only the top 10 names in the universe —
systematically avoids the worst-performing names, not just the ones below their own moving
average. That is the fundamental difference between a single-asset signal and a
cross-sectional one.

**The max drawdown gap is real and not captured by Sharpe alone.**

CrossSectionalMomentum's max drawdown is 37.82% — six times the 0050 benchmark's 5.89%.
The Sharpe improvement (0.779 > 0.690) does not fully represent the risk profile of a
concentrated 10-stock equal-weight portfolio. Phase 5+ reporting should track Sortino and
Calmar ratios alongside Sharpe.

**Survivorship bias is material, especially for the momentum result.**

The universe is a 2026-05-08 snapshot. Applying it back to 2020 includes stocks that had
not yet grown into the top-50 at that time. The 17.82% CAGR for CrossSectionalMomentum is
the number most likely overstated as a result. A rolling-constituent universe is the correct
fix; it is a Phase 9 task.

---

## The Stock Split Gap — A Post-Phase-4 Bridge

During Step 8's first real-data run, the 0050 Buy-and-Hold benchmark produced a CAGR of
approximately −0.35% — contradicting historical reality. Investigation traced the cause to
0050's 4:1 stock split on 2025-06-18 (NT$188.65 → NT$47.16), which the data pipeline
treated as a genuine 75% price drop because no split-adjustment logic existed.

This surfaced a real architectural gap in the data pipeline (not in the backtest engine or
strategies): `adjust()` handled five corporate action types, all dividend or reduction flavors,
but never a stock split because no top-50 constituent had ever split before 2025. The fix is
narrow and well-scoped: one new `action_type = "stock_split"` in the schema, one `elif` branch
in `_compute_factor`, and a new `_build_stock_splits()` method in `FinMindSource` — identical
in structure to the existing `capital_reduction` path. Three tickers are affected (0050, 2327,
6919), all split in mid-to-late 2025. The full seven-step implementation plan is in
**ADR 0007** (`journal/decisions/0007-stock-split-adjustment.md`).

The Phase 4 workaround — capping `end` at `2025-06-17` in `phase_4_evaluate.py` — keeps the
evaluation window clean. The fix is a **pre-Phase-5 prerequisite**: any strategy or benchmark
that spans past mid-June 2025 will silently corrupt its price series until the split adjustment
is in place. Phase 5's paper-trading evaluation window will certainly extend past that date.

The stock split episode is a good illustration of how real-world market events reveal
architectural gaps that no amount of upfront design anticipates. The right response is to
stop, document the gap completely (ADR), implement a bounded workaround, and schedule the
proper fix — which is exactly what happened here.

---

## Decisions to Carry Forward

**Pre-Phase-5 prerequisite: implement ADR 0007 (stock split adjustment).**
The seven-step plan in ADR 0007 is self-contained and does not require architectural decisions.
It must complete before any Phase 5 evaluation window extends past 2025-06-17.

**Cash management in the cross-sectional engine (Phase 5 scope).**
The Phase 4 cash-cap fix to `engine.py _run_cross_sectional` is a minimum-viable patch.
Phase 5 must address: (1) underfill tracking in the tear sheet, (2) proportional buy
allocation when cash is short (not first-come-first-served), and (3) T+2 net settlement
modeling in the paper-trading simulator. The root-cause fix — calling `mark_to_market` at
T+1 open after sells but before buys — would eliminate the price-gap mismatch entirely.

**The `CrossSectionalAdapter` is expected to fade.**
It exists to let binary single-asset strategies appear in portfolio results for Phase 4.
Phase 6 ML strategies will be inherently cardinal and native `CrossSectionalStrategy`.
The adapter should not grow new capabilities; strategies that need magnitude must be native.

**Max drawdown and concentration risk must be surfaced more prominently.**
CrossSectionalMomentum's 37.82% max drawdown is buried in the results table alongside Sharpe.
A concentrated equal-weight top-10 portfolio is a different risk instrument from a diversified
ETF. Phase 5+ reporting should ensure Calmar and Sortino are first-class metrics, not footnotes.

**Cadence, not signal quality, is often the deciding variable.**
Phase 4 demonstrated this clearly: the MACD signal is not inherently worse than SMA, but 92%
annual turnover at 0.3% sell tax destroys net performance. Any Phase 5 or 6 strategy that
rebalances frequently must account for this arithmetic before claiming alpha at the gross level.

---

## Prep Notes for Phase 5

**The Phase 3 stateless constraint was the right call and will continue to apply.**
The audit machinery — `assert_no_lookahead` for single-asset, the panel variant for
cross-sectional — depends on strategies being stateless pure functions. Phase 5 paper-trading
adds an order state machine, but that machine lives in the broker simulator, not inside
`generate_signals` or `generate_scores`. Do not conflate the broker state machine with
strategy state. The audit must remain valid after Phase 5.

**Phase 5 introduces a qualitative jump in complexity.**
The shift from Phase 3 (single-asset backtester) to Phase 4 (cross-sectional portfolio runner)
was already non-trivial — it required a planning week and three new ADRs. The shift from
Phase 4 (backtester) to Phase 5 (paper-trading simulator with live data, partial fills,
T+2 settlement, order state machine, and broker integration) is larger. Expect more
architectural questioning sessions. The lesson from Phase 4 is not that those sessions are
wasteful — they are where the real decisions get made. Budget time for them and front-load
the ADR work before writing code.

**Re-read `journal/phase_3_references.md §backtrader` before designing the order state machine.**
The backtrader `broker.py` and `order.py` study was deferred from Phase 3 as Phase 5 reading.
That pre-reading is now due. The order state machine (pending, partially filled, rejected,
expired) is the most stateful and bug-prone piece of Phase 5; the reference study will
identify the patterns worth borrowing and the ones to reject.

**The survivorship bias problem will not get easier.**
Every phase after Phase 4 inherits the fixed-universe limitation. Until historical
constituent data is obtained (Phase 9+ task), every backtest result — especially
cross-sectional ones — should carry the survivorship caveat prominently. Phase 5
performance comparison against Phase 4 backtests must flag this explicitly: the live
universe will not match the backtested universe for names that graduated or left the
top-50 during the backtest window.
