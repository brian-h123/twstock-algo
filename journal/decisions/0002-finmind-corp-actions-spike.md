# 0002 — FinMind Corporate-Actions Coverage (Phase 1 Spike)

**Date:** 2026-05-11
**Status:** Accepted

## Context

Before writing any Phase 1 production code, the spec required a one-day investigation
of FinMind's corporate-actions endpoints to validate coverage and understand the data
shape. The goal was to hit three decision gates: coverage solid → proceed; spotty older
data → expand TwseSource scope; broken entirely → stop and escalate.

The spike was run in `notebooks/spike_finmind_corp_actions.ipynb` (not committed).
Ticker 2330 (TSMC) was used as the primary test case; 3711 (ASE Tech) as the
thin-history ticker.

## Findings

### Authentication

FinMind v4 API uses `Authorization: Bearer {token}` header. Token is not passed as a
query parameter. Loaded from `FINMIND_TOKEN` env var via `python-dotenv`.

### OHLCV — `TaiwanStockPrice`

- Coverage confirmed back to at least 2016-01-04 (244 trading days for 2016)
- Column mapping required when ingesting into our schema:

| FinMind column     | Our schema column | Notes                                      |
| ------------------ | ----------------- | ------------------------------------------ |
| `stock_id`         | `ticker`          |                                            |
| `max`              | `high`            |                                            |
| `min`              | `low`             |                                            |
| `Trading_Volume`   | `volume`          |                                            |
| `Trading_money`    | `value`           | NTD turnover                               |
| `spread`           | —                 | Drop; daily price change, not needed       |
| `Trading_turnover` | —                 | Drop; trade count, not needed              |
| `date`             | `date`            | Comes as `str`; must parse to `datetime64` |

- Pre-listing requests handled gracefully: FinMind returns data from the listing date
  with no error, no special handling needed in our fetch logic.

### Corporate actions — two relevant datasets

**`TaiwanStockDividend`** — declared dividend table

- One row per annual dividend declaration; covers 2016–2026 for 2330 (31 rows)
- Cash and stock dividends are separate columns in the same row, not separate rows
  with an action type
- `date` field is the **record date** (基準日), NOT the ex-date
- Actual ex-dates are in separate columns: `CashExDividendTradingDate` (除息交易日)
  and `StockExDividendTradingDate` (除權交易日)
- Total cash dividend per share = `CashEarningsDistribution` + `CashStatutorySurplus`
- Total stock dividend per share = `StockEarningsDistribution` + `StockStatutorySurplus`

**`TaiwanStockDividendResult`** — ex-date reference price table (preferred for adjustment)

- One row per ex-dividend event; covers 2016–2026 for 2330 (31 rows)
- `date` field IS the ex-date directly
- `stock_or_cache_dividend` indicates type: `息` = cash, `權` = stock, `權息` = both
- All 31 events for 2330 are `息` (cash-only); TSMC does not issue stock dividends
- Back-adjustment factor computable directly: `reference_price / before_price`
- `reference_price` (減除股利參考價) = theoretical ex-dividend price; identical to
  `after_price` in all observed rows — use `reference_price` as it is more semantically
  precise

**Payout cadence shift:** TSMC paid annually before 2019, quarterly from 2019 onwards.
The adjustment function must handle multiple events per year correctly.

### Capital reductions — `TaiwanStockCapitalReductionReferencePrice`

- Empty for 2330 over the full 10-year window — TSMC has had no capital reductions
- This dataset covers 減資 (capital reduction with cash refund to shareholders), not
  stock splits in the Western sense
- Adjustment factor for capital reductions = `PostReductionReferencePrice / ClosingPriceonTheLastTradingDay`
- Dataset is queryable and works correctly — no events for 2330, but will be needed
  for other Top 50 tickers

### Thin-history ticker (3711 — ASE Tech)

- Listed 2018-04-30; pre-listing request returns data from listing date, no error
- 1954 rows through 2026-05-11; 8 dividend events (all `息`)
- Same column structure as 2330 — no special-case handling needed

## Decision

**Gate outcome: coverage solid → proceed as planned.**

FinMind provides sufficient coverage for all three corporate action types:

- Cash dividends: via `TaiwanStockDividendResult` (primary) and `TaiwanStockDividend`
- Stock dividends: via same datasets; `股_or_cache_dividend = 權` or `權息`
- Capital reductions: via `TaiwanStockCapitalReductionReferencePrice`

No expansion of `TwseSource` scope is required at this time.

**Implementation choices recorded here for `finmind.py` and `adjustments.py`:**

1. Use `TaiwanStockDividendResult` as the primary corporate-actions fetch for
   back-adjustment. Use `TaiwanStockDividend` only if raw declared amounts are needed
   (e.g. for display or audit).
2. Adjustment factor = `reference_price / before_price` from `TaiwanStockDividendResult`.
3. Map `stock_or_cache_dividend` values (`息`, `權`, `權息`) to our schema's
   `action_type` (`cash_dividend`, `stock_dividend`, mixed handling TBD).
4. Parse `date` from string to `datetime64` in the FinMind adapter layer; downstream
   code never sees raw string dates.
5. Apply OHLCV column renames in `FinMindSource.get_daily_ohlcv`; drop `spread` and
   `Trading_turnover`.

## Consequences

- `TwseSource.get_corporate_actions` remains `NotImplementedError` for now; FinMind
  coverage is sufficient.
- `adjustments.py` can use `reference_price / before_price` directly rather than
  reconstructing the factor from raw dividend amounts.
- Capital reduction handling must be included in `adjustments.py` even though 2330
  has no events — other Top 50 tickers may.
- The `stock_or_cache_dividend` → `action_type` mapping needs a small lookup in the
  FinMind adapter; confirm exact values when stock dividend or `權息` case is encountered
  (not observed in 2330 or 3711 data).
