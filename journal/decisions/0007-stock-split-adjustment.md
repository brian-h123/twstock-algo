# 0007 — Stock Split Support in the Adjustment Pipeline

**Date:** 2026-06-19
**Status:** Accepted

## Context

While running `scripts/phase_4_evaluate.py` during Phase 4 strategy evaluation,
the 0050 Buy-and-Hold benchmark produced a CAGR of approximately −0.35% and a
Sharpe of −0.012 over 2020–2025 — results that contradicted historical reality.
Investigation traced the cause to 0050's 4:1 stock split (股票分割) on
2025-06-18, the first-ever split for this ETF, which resulted in a raw price
drop from NT$188.65 to NT$47.16 (−75%).

The architecture (established in Phase 1) stores raw OHLCV separately from
corporate actions and applies back-adjustment on read via `adjust()` in
`src/twstock/data/adjustments.py`. That pipeline already handles five
`action_type` values — `cash_dividend`, `stock_dividend`,
`cash_and_stock_dividend`, `rights_issue`, and `capital_reduction` — sourced
from FinMind's `TaiwanStockDividend` and
`TaiwanStockCapitalReductionReferencePrice` endpoints. Stock splits are a
distinct corporate action class in Taiwan; they do not appear in
`TaiwanStockDividend`. With no split factor reaching `adjust()`, the 75% raw
price drop on 2025-06-18 is treated as genuine market movement, corrupting both
the equity curve and all return-based metrics for any backtest that crosses that
date.

This ADR records the analysis, the design decision, and the deferred
implementation plan. A temporary workaround for Phase 4 is documented at the
end.

## Findings

### FinMind TaiwanStockSplitPrice endpoint

A direct API probe confirmed that FinMind exposes a `TaiwanStockSplitPrice`
(台股分割後參考價) dataset. The response for ticker `0050` with
`start_date=2020-01-01`:

```
date:         2025-06-18
stock_id:     0050
type:         分割
before_price: 188.65   ← closing price on the last pre-split trading day
after_price:   47.16   ← post-split theoretical reference price
max_price:     51.85   ← post-split daily limit-up
min_price:     42.45   ← post-split daily limit-down
open_price:    47.16
```

The adjustment factor is `after_price / before_price = 47.16 / 188.65 ≈ 0.25`,
consistent with a 4:1 split. This structure is directly analogous to
`TaiwanStockCapitalReductionReferencePrice`, which provides
`(ClosingPriceonTheLastTradingDay, PostReductionReferencePrice)` — the same
two-price form from which a single ratio is derived.

### Scope of affected tickers

A universe-wide probe of `TaiwanStockSplitPrice` across all 50 Top 50
constituents plus the 0050 benchmark, from 2018-01-01 onwards, found three
events:

| Ticker | Name    | Date       | before_price | after_price | Ratio  | Split |
|--------|---------|------------|--------------|-------------|--------|-------|
| 0050   | 元大台灣50  | 2025-06-18 | 188.65       | 47.16       | 0.2500 | 4:1   |
| 2327   | 國巨      | 2025-08-25 | 546.00       | 136.50      | 0.2500 | 4:1   |
| 6919   | 新唐科技    | 2025-07-21 | 1215.00      | 121.50      | 0.1000 | 10:1  |

All three events occurred in 2025. No splits were found in the 2018–2024
window for any Top 50 constituent or the 0050 benchmark. Tickers 2327 and 6919
are Top 50 constituents; 0050 is the benchmark-only instrument.

### Price adjustment vs share count adjustment

Two mechanisms can preserve economic consistency across a split event in a
backtesting context:

**Price adjustment** — back-scale all pre-split OHLCV prices by `ratio =
after / before`, so the series is continuous. Indicators (SMA, RSI, Bollinger
bands, Donchian channels) that look across the split boundary compute on a
homogeneous price scale. Portfolio accounting follows automatically: a position
entered pre-split at an adjusted price of NT$47 with 4,000 shares carries the
same capital commitment as the real-world NT$188 × 1,000 shares, and after the
split the post-split raw price equals the adjusted price, so no further
correction is needed.

**Share count adjustment** — leave prices raw; when the split event fires,
multiply the position's share count by the split ratio. Total equity is
preserved at the split bar, but indicator calculations that span the boundary
mix pre-split (NT$188) and post-split (NT$47) prices on incompatible scales —
a 20-period SMA straddling 2025-06-18 averages numbers that cannot be compared.
This breaks every technical strategy in Phase 4 even if the portfolio accounting
is correct.

Price adjustment is the standard approach for historical backtesting and is what
the existing `adjust()` pipeline already implements for all other corporate
action types. Share count adjustment is the correct Phase 5 / Phase 8 model:
real brokers update account share counts when a split occurs, and the live
position ledger should defer to the broker's share count as source of truth
rather than trying to maintain its own adjustment.

**Decision: price adjustment.** Indicator signals are the primary concern for
Phase 4; the live-trading share count question belongs to Phase 5 and must not
be conflated with historical backtesting.

One reported concern: does adjusting pre-split prices down by the split ratio
cause the engine to allocate 4× more shares pre-split than real trading would,
potentially overstating position size? The answer is no. The `max_position_pct`
cap is applied in adjusted price space at order time — `4,000 adjusted shares ×
NT$47 = NT$188,000` — and the engine marks positions to market in the same
adjusted space throughout. The adjusted-share count (4,000) happens to match
the post-split real-world count, and the P&L arithmetic is exact: a cross-split
round trip from adjusted NT$25 to NT$50 produces the same return as buying 1,000
raw shares at NT$100, receiving 3,000 via the split, and selling 4,000 at NT$50.

### Schema extension vs new table

Four `action_type` values already store numeric fields in `ratio`: `stock_dividend`,
`cash_and_stock_dividend`, `rights_issue`, and `capital_reduction`. Stock splits
require only `ratio = after_price / before_price`; `cash_amount` and
`subscription_price` are `NaN`. No new column is needed.

A separate storage table for splits would require new paths in `storage.py`, a
new read function, a new argument to `adjust()`, and a new validation call —
all for data that shares columns with what is already stored. The existing
dedup key `(ticker, ex_date, action_type)` already uniquely identifies a split
row. Extending the `action_type` enum is the minimal, self-consistent change.

### Adjustment math

The `capital_reduction` case in `_compute_factor` already handles a ratio that
is the direct adjustment multiplier: `return action["ratio"]`. For capital
reductions the ratio is typically greater than 1 (cash is returned, so the
theoretical post-event price is higher than the pre-event price adjusted for
the cash out). For stock splits the ratio is always less than 1 (more shares
at lower price). The formula is identical; the direction of the factor differs
by data, not by code. A single new `elif` branch handles stock splits with no
duplication.

### Alternatives considered

- **Map splits to `stock_dividend` with `ratio = N − 1`** — a 4:1 split
  would be stored as `ratio = 3` and use the `1 / (1 + ratio)` formula. This
  produces the correct factor (0.25) but conflates two semantically distinct
  events in the stored data, making it harder to understand the ledger later.
  Rejected; a distinct `action_type` costs one schema enum value and gains
  clear semantics.

- **New `STOCK_SPLITS_SCHEMA` and separate storage table** — clean separation
  but adds `read_stock_splits()`, a new `data/stock_splits/` directory, and a
  merge step inside `adjust()`. No benefit justifies the complexity given that
  the column set is identical. Rejected.

- **Static reference table (hard-coded in source)** — a `dict` of known splits
  keyed by `(ticker, date)`. Safe for the current three events but manually
  maintained and invisible to the backfill script. Rejected; the FinMind
  endpoint is authoritative and already covers the known universe.

- **Share-count adjustment only, no price adjustment** — correct for portfolio
  equity accounting at the split bar, but leaves indicators operating on
  discontinuous prices. Rejected for Phase 4; deferred to Phase 5 as the
  live-trading path.

## Decision

**Represent stock splits in the existing `CORPORATE_ACTIONS_SCHEMA` with
`action_type = "stock_split"`, using `ratio = after_price / before_price` — the
same convention and the same `_compute_factor` formula as `capital_reduction`.**

**Source the split data from FinMind's `TaiwanStockSplitPrice` dataset, via a
new `_build_stock_splits()` method in `FinMindSource`, wired into the existing
`get_corporate_actions()` call chain.**

**Implementation is deferred to the post-Phase-4 / pre-Phase-5 boundary.** The
Phase 4 evaluation window ends before all three known split events; the
workaround (below) is adequate until Phase 4 closes. The fix becomes
load-bearing when any backtest end date is extended past 2025-06-17.

## Implementation Plan (deferred)

The following steps constitute the full fix. They are recorded here rather than
in a separate spec because the scope is narrow enough to execute without a
dedicated planning session.

**Step 1 — Schema (`src/twstock/data/schemas.py`)**

Add `"stock_split"` to the `action_type` `isin()` check in
`CORPORATE_ACTIONS_SCHEMA`. No new columns; the schema cross-checks (`cash_amount`
required for cash actions, `ratio` required for stock/ratio actions) do not need
updating because `stock_split` carries only `ratio` (NaN cash, NaN sub price),
which the existing nullable handling already covers.

**Step 2 — Adjustment math (`src/twstock/data/adjustments.py`)**

Add one `elif` branch in `_compute_factor`:

```python
elif action_type == "stock_split":
    return action["ratio"]   # identical logic to capital_reduction
```

Update the docstring on `adjust()` to list `stock_split` among the five handled
action types.

**Step 3 — Data source (`src/twstock/data/sources/finmind.py`)**

Add `_build_stock_splits(split_data: list[dict]) -> pd.DataFrame` to
`FinMindSource`. Map `date → ex_date`, `stock_id → ticker`,
`after_price / before_price → ratio`; set `cash_amount = NaN` and
`subscription_price = NaN`; set `action_type = "stock_split"`. The dataset
appears to emit one row per event (no dedup needed, but sort by `date` for
safety).

In `get_corporate_actions()`, add a third `_request` call for
`TaiwanStockSplitPrice` alongside the existing `TaiwanStockDividend` and
`TaiwanStockCapitalReductionReferencePrice` calls. Wrap in the same
`NoDataError` guard. Append the result to `parts` before the final `pd.concat`.

**Step 4 — Data refresh**

Re-run the backfill script for the three affected tickers to pull the new split
rows into `data/corporate_actions/`:

```bash
uv run python scripts/backfill.py --tickers 0050 2327 6919
```

Because `write_corporate_actions` deduplicates on `(ticker, ex_date,
action_type)`, running the backfill is idempotent; existing dividend and
reduction rows are preserved.

**Step 5 — Tests (`tests/data/test_adjustments.py`)**

Add a `_stock_split` helper and two unit tests mirroring the existing
`_capital_reduction` tests: one that confirms pre-split prices are scaled by
the ratio, and one that confirms the ex-date bar is unchanged.

Optionally add a `test_schemas.py` case that validates a `stock_split` row
against `CORPORATE_ACTIONS_SCHEMA`.

**Step 6 — Revert the Phase 4 workaround**

In `scripts/phase_4_evaluate.py`, remove the `end=date(2025, 6, 17)` cap that
was set to avoid the 0050 split. Update the docstring to reflect that the full
2020–2025 window is once again valid.

**Step 7 — ADR 0004 / CLAUDE.md update (minor)**

In `CLAUDE.md §6` Phase 3 public API comment for `adjust()` and in any Phase 5
architecture that touches live position accounting, add a note that the
Phase 5 paper-trading share count must be sourced from the broker/simulator,
not from the back-adjustment pipeline.

## Phase 4 Workaround

The default `end` date in `scripts/phase_4_evaluate.py` has been set to
`2025-06-17` — one trading day before 0050's split. Because all three known
splits fall after this date (0050: June 18, 6919: July 21, 2327: August 25),
the Phase 4 evaluation window is clean: no adjusted price series is corrupted,
and the 0050 Buy-and-Hold benchmark reports correctly.

The workaround is intentional and documented here. **Do not extend the end date
past 2025-06-17 before completing the implementation plan above.** Doing so
will silently corrupt the 0050 benchmark and any strategy result for 6919 or
2327.

## Consequences

- No engine, portfolio, or strategy file requires change. The fix is entirely
  within the data pipeline (`schemas.py`, `adjustments.py`, `sources/finmind.py`)
  and the backfill script.
- Once implemented, 0050, 2327, and 6919 will have smooth adjusted price series
  across their split dates. Any strategy or benchmark that spans these dates will
  produce correct results automatically, with no code changes outside the data
  layer.
- The lot-consuming FIFO ledger (`build_round_trips`) is unaffected. Round trips
  are computed from adjusted prices at entry and exit; the split adjustment is
  transparent below the strategy layer.
- Phase 5's paper-trading simulator should not apply the back-adjustment `ratio`
  to live positions. When a real broker reports a split, it updates the account's
  share count directly. The simulator should replicate that event model, not
  the back-adjustment model, to avoid double-counting.
- The `TaiwanStockSplitPrice` endpoint returned one row per event with no
  duplicate-row pattern (unlike `TaiwanStockCapitalReductionReferencePrice`,
  which emits two rows per event and requires deduplication in
  `_build_capital_reductions`). If future probes show duplicates, add a
  sort-and-dedup step to `_build_stock_splits` consistent with the reduction
  builder.
