# Phase 3 Retrospective — Backtesting Engine

**Branch:** `phase-3`
**Opened:** 2026-06-08
**Steps completed:** 0–15 (all)
**Tests at close:** 161 passed, 5 skipped (integration; `@pytest.mark.integration` requires
local Parquet store) — 0.93 seconds excluding integration
**Integration test:** green (`test_sma_crossover_integration.py` on real 2330 data, 2020–2025)

---

## Wins

**Lookahead safety is structural, not incidental.** The three-layer model — indicator
causality, strategy causality, execution timing — holds without any special-casing. The
engine never slices bars per-bar (O(n²) avoided); the audit utility (`assert_no_lookahead`)
catches violations post-hoc; `execution: Literal["next_open"]` makes same-bar execution
structurally impossible. Reference study confirmed this is strictly stronger than backtrader
(opt-in danger via `cheat_on_open`) and vectorbt (opt-out danger, silent by default).

**`BacktestResult` as the reversibility guarantee.** D10 paid off immediately: every
downstream report — `tear_sheet()`, `equity_curve_figure()`, `trades_table()` — is a
pure function over the artifact. The engine was never opened for a "just add one more
metric" request; the artifact completeness absorbed all of them. This will pay dividends
in Phase 6 when ML model evaluation needs arbitrary post-hoc analyzers.

**TaiwanCostModel statutory citations.** Embedding the primary-source citations directly
in the module docstring made cost-model review easy and eliminates the class of bug where
you look up the tax rate years later and can't remember where it came from. The 0.3% sell
tax and 0.1% ETF tax are correctly asymmetric in the type signature: `buy_costs` has no
tax return value, `sell_costs` returns `(fill, commission, tax)`. That asymmetry in the
return type prevents conflation at the call site.

**Front-loading the spec compressed implementation time.** The step 0–1 documentation
commits (references writeup + ADR) before any code landed meant that every subsequent
step had a clear module spec to implement against. No mid-step scope decisions, no
backtracking to re-design interfaces. Steps 9–12 (audit, SmaCrossover, tearsheet,
walkforward) were built in a single session, each to a clean commit.

**`WalkForwardSplit` built in Phase 3.** Adding the iterator now cost eight lines of
logic; adding it in Phase 6 would have been a breaking change to the Phase 3 public API.
This is the clearest example of "build the interface at the earliest phase that defines
it, not the earliest phase that uses it heavily."

---

## Slips

**`BuyAndHold` emitted `0` instead of `NaN` for non-entry bars.** This was present in
the initial `feat(backtest): add Strategy ABC and BuyAndHold` commit and caught during
engine test writing. A `BuyAndHold` emitting `[1, 0, 0, …]` enters at bar 0 and
immediately sells at bar 2's open because the engine sees a long→flat transition at bar 1.
Corrected in a `fix(backtest): correct BuyAndHold to emit NaN after entry` commit before
the PR. The `0 vs NaN` distinction was already documented in the spec (§4.2); the bug
was a transcription slip, not a design gap.

**Boolean signal fragility in the audit detection test.** `_LookaheadStrategy` uses
`close.shift(-1) > close` — a boolean comparison. With a small `perturbation_start=-20`,
only one safe-window bar depends on scrambled data, and the direction relative to `close[T]`
may not flip, so no `LookaheadError` is raised. The default window of `-50` is large enough
to be reliable in practice. Documented in the weekly journal; the test was restructured to
test the unconditional property (no false positives on causal strategies) rather than the
probabilistic detection guarantee.

**Bitwise `&` precedence bug in `win_loss_ratio`.** `len(wins) > 0 & len(losses) > 0`
parses as `len(wins) > (0 & len(losses)) > 0`, which is always `False`. `win_loss_ratio`
silently returned `nan` for any non-empty trade set. Caught by code review, not by tests;
the test checked the NaN case, which is correct regardless. Fixed to `and`. Lesson:
boolean short-circuit operators (`and`, `or`) belong in guard conditions; `&`/`|` belong
in vectorized pandas operations.

**`Trade.total_cost` does not include slippage.** Slippage is embedded in `fill_price`;
`total_cost` captures only commission + tax. Summing `trades["total_cost"]` for a "total
fees" metric would silently omit slippage. The engine pre-aggregates
`total_slippage_cost` separately on `BacktestResult`. Documented in tearsheet.py;
discovered during tearsheet metric implementation.

---

## Surprises

**The buy-sizing test revealed an undocumented design choice.** The test was initially
written assuming fill-price sizing (999 shares at 5 bps slippage) but the engine uses
open-price sizing (1000 shares). This is not a bug — it is the correct choice, because
slippage is a post-sizing cost applied inside `portfolio.buy()`, not a price known at
sizing time. But the design decision was not explicitly stated anywhere. A well-written
behavioral test revealed an assumption in the implementation that belonged in the spec.
Added to the docstring.

**`pending_orders.clear()` ordering before the force-close loop is non-negotiable.**
If a buy is pending on the last bar and the force-close loop runs first, the portfolio
sees no open positions to close. This ordering is in the spec (§7.5: "Pending orders are
dropped on the final day, not executed"), but easy to reverse when writing the loop from
scratch. The behavioral test `test_pending_order_dropped_on_last_bar` caught it.

**`pythonpath = ["."]` required for conftest helper imports.** pytest auto-injects
conftest fixtures but does not add `tests/` to `sys.path`. `from tests.backtest.conftest
import make_synthetic_bars` silently fails without the `pyproject.toml` addition. One
line; easily reverted; but not something the spec mentioned.

**`itertuples()` on a typed DataFrame produces `Iterable[Any]`.** Attribute access on
`Any` rows (`row.fill_price`) produces Pylance warnings even after explicit casting.
Replaced with `.to_numpy()` extraction before the loop. An ergonomics detail, but worth
knowing before Phase 5's portfolio accounting has more complex iteration patterns.

---

## Decisions to Revisit

**`_round_trips` in tearsheet.py assumes whole-position FIFO (Phase 5 blocker).** It
pairs the i-th buy with the i-th sell per ticker after sorting by timestamp. This is
correct under Phase 3's constraints: all-or-nothing position sizing, long-only, no
partial fills. When Phase 5 introduces partial fills or Phase 6 allows short positions,
`_round_trips` must be replaced with a proper FIFO queue tracking remaining shares per
lot. Track as a Phase 5 pre-work item.

**`WalkForwardSplit` uses calendar-year arithmetic, not trading-day-aware splits.**
`train_end - relativedelta(years=3)` counts calendar years; some folds will have
slightly different numbers of trading days depending on where weekends and holidays fall.
Trading-day-aware splits are a Phase 6 enhancement if validation is sensitive to fold-size
variance.

**The `0 vs NaN` signal convention will be the most common Phase 4 strategy-author
mistake.** Both the spec (§4.2) and `generate_signals` docstring now document the
distinction, and `BuyAndHold` is the canonical example of getting it right. Phase 4
will add more strategies; each new strategy author will encounter this. Consider adding
an explicit check in `Engine.run()` that warns when a strategy emits `0` on bar 0 (before
any position is open), since `0` as a warmup proxy is the canonical footgun.

**The 0.3% sell tax is the dominant cost for any medium-to-high-turnover strategy.**
The SMA(20)/SMA(60) crossover on 2330 over 2020–2025 passes the `gross_return >
net_return` exit criterion comfortably. But strategies with higher turnover — RSI
mean-reversion, Bollinger band touch — will be killed by the 0.585% per-round-trip
cost floor. This is not a flaw; it is the point. Phase 4 strategies should have their
costs scrutinized before claiming any alpha.

---

## Prep Notes for Phase 4

**The public API is locked and tested.** The symbols in `CLAUDE.md §6` (Phase 3
exposures) are the stable surface. Phase 4 imports `Strategy`, `Engine`,
`TaiwanCostModel`, `BacktestResult` without modification. Adding Phase 4 strategies
means adding files under `src/twstock/strategies/`; the `backtest/` package is not
touched.

**Multi-ticker strategies require explicit design.** The Phase 3 engine loops tickers
independently and aggregates positions; it has no cross-ticker signal routing. Phase 4
strategies that rank tickers (top-N momentum, pairs) will need a multi-ticker
`generate_signals` signature or a universe-level wrapper. This is the main interface
extension the engine will need. Spike the design before writing any cross-sectional
strategy.

**Phase 4 integration test template exists.** `test_sma_crossover_integration.py`
is the template: run on real 2330 data, assert non-NaN metrics, assert net ≠ gross,
check fill timing, pass the audit. Every Phase 4 strategy should get an analogous
`@pytest.mark.integration` test before being called "done."

**backtrader's order state machine is Phase 5 reading.** The `broker.py` and
`order.py` files were de-prioritized for the Phase 3 engine (which has no order state
machine — just pending buy/sell dicts). Phase 5 paper-sim needs bracket orders, partial
fills, and T+2 settlement. Re-read `backtrader/broker.py` when starting Phase 5;
notes are in `journal/phase_3_references.md §backtrader`.

**Survivorship bias is still present and still not fixed.** Every tear sheet includes
the one-line caveat. The universe is the 2026-05-08 snapshot; backtests from 2016 fish
in a pond that didn't exist yet. Fixing this would require historical constituent data
for 0050, which FinMind does not expose for free. Defer to Phase 9+ or accept as a
known limitation in every Phase 4 result.
