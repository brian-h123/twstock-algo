# ADR: Corporate-actions ingestion overhaul

- **Date:** 2026-05-18
- **Status:** Accepted
- **Phase:** 1 (follow-up)
- **Supersedes:** initial spike conclusion to use `TaiwanStockDividendResult` as
  the primary dividend source

## Context

While building stock-dividend cross-check anchors for the adjustment function,
we discovered a latent bug in the Phase 1 corporate-actions ingestion. The
diagnosis cascaded into a broader rework: the chosen FinMind source dataset is
too lossy to handle Taiwan's combined cash+stock dividend convention
(除權息合一) correctly, and additional event types (rights issues, capital
reductions) need cleaner modeling.

### Findings

**Taiwan dividend convention.** Pure stock-dividend events (no cash on the same
ex-date) are essentially nonexistent for TWSE Top 50 post-2015. Large-caps
universally distribute cash + stock together on a single ex-date. Confirmed
across 2317 (Hon Hai), 1101 (Taiwan Cement), 1216 (Uni-President), 2892 (First
Financial).

**Current ingestion is wrong for combined events.** The existing code maps
FinMind's `權息` rows to `action_type=cash_dividend` with
`cash_amount = stock_and_cache_dividend` (the collapsed total). For a combined
event this produces an adjustment factor of `Y / (Y + cash + stock_face_value)`
instead of the correct `Y/(Y+cash) × 1/(1+stock_ratio)`. For 1101's 2022-07-21
event the error is ~7% on the back-adjustment factor at that ex-date, applied
multiplicatively to all earlier prices. Systematic, silent, distorts every
downstream consumer of the adjusted-price layer.

**`TaiwanStockDividend` is significantly richer than `TaiwanStockDividendResult`.**
It exposes the four-component breakdown (`StockEarningsDistribution`,
`StockStatutorySurplus`, `CashEarningsDistribution`, `CashStatutorySurplus`),
separate ex-dates for the stock and cash legs, and rights-issue fields all in
the same row. The spike's "use Result for simplicity" choice traded away the
breakdown we now need.

**Rights issues are in `TaiwanStockDividend`, not a separate dataset.** A row
with all four dividend components zero and `TotalNumberOfCashCapitalIncrease >
0` is a rights issue. Cash dividend AND rights issue can co-occur on the same
source row at the same ex-date (e.g. 3312 2022-07-18: NT$2 cash + 25M new
shares at NT$17). The original assumption that they were mutually exclusive at
the row level was wrong.

**`CashIncreaseSubscriptionRate` is not the quantity we need.** Empirically it
appears to be the existing-shareholder subscription rate after 公司法 §267
employee carve-outs (10–15%) and any public-underwriter allocation. Total
dilution to existing shareholders — what the adjustment math requires — must
be derived from share counts:
`TotalNumberOfCashCapitalIncrease / ParticipateDistributionOfTotalShares`. We
ignore `CashIncreaseSubscriptionRate` entirely for adjustment purposes.

**`date` column gotcha.** In `TaiwanStockDividend`, `date` is the record date
(權利分派基準日), not the ex-date. The current code works only because it
reads from `Result` (where `date` is the ex-date). Source switch requires
reading from `CashExDividendTradingDate` / `StockExDividendTradingDate`.

**Existing `action_type="split"` is mislabeled.** Current mapping is from
`TaiwanStockCapitalReductionReferencePrice`, which is capital reduction (減資),
not stock split (股票分割). Math path is similar but the events are
conceptually distinct.

**Two unused FinMind datasets exist for share-count events:**
`TaiwanStockSplitPrice` (actual splits) and `TaiwanStockParValueChange` (par
value changes). Both rare in TWSE Top 50 within the backfill window. Deferred
to Phase 9+.

**ESOP per-share adjustment is already baked into FinMind output.** TSMC's
NT$3.49978969 vs headline NT$3.50 (2024-06-13) appears directly in
`CashEarningsDistribution`. Employee-stock fields (`TotalEmployeeStockDividend`,
`TotalEmployeeStockDividendAmount`, `TotalEmployeeCashDividend`,
`RatioOfEmployeeStockDividend*`) are informational and not consumed by the
adjustment math.

## Decision

1. **Primary source for dividend / rights-issue ingestion:**
   `TaiwanStockDividend`. Switch from `TaiwanStockDividendResult`.

2. **`TaiwanStockDividendResult` is a cross-validator**, used only in an
   integration test to guard against silent upstream changes (cash totals,
   stock totals, reference price reconciliation).

3. **Action-type enum** for `CORPORATE_ACTIONS_SCHEMA`:

   - `cash_dividend`
   - `stock_dividend`
   - `cash_and_stock_dividend` (new — the common Taiwan combined case)
   - `rights_issue`
   - `capital_reduction` (renamed from `split`)

   Deferred to Phase 9+ and dropped from the current enum: `split`,
   `par_value_change`.

4. **Schema columns** in `CORPORATE_ACTIONS_SCHEMA`: `ex_date`, `ticker`,
   `action_type`, `cash_amount` (nullable), `ratio` (nullable),
   `subscription_price` (nullable, new typed column). Per-type validation:
   | action_type | cash_amount | ratio | subscription_price |
   |---|---|---|---|
   | cash_dividend | required | — | — |
   | stock_dividend | — | required | — |
   | cash_and_stock_dividend | required | required | — |
   | rights_issue | — | required | required |
   | capital_reduction | — | required | — |

5. **Multi-row emission.** When a `TaiwanStockDividend` source row has both
   dividend components AND rights-issue fields populated, emit two output rows
   sharing the same ex-date. Keeps action types orthogonal; avoids combinatorial
   action-type explosion.

6. **Ex-date column policy:**

   - `cash_dividend` → `CashExDividendTradingDate`
   - `stock_dividend`, `cash_and_stock_dividend`, `rights_issue` →
     `StockExDividendTradingDate`
   - For `cash_and_stock_dividend`: assert at ingestion that both ex-date
     columns match; warn and skip on mismatch.

7. **Rights-issue ratio derivation:**
   `ratio = TotalNumberOfCashCapitalIncrease / ParticipateDistributionOfTotalShares`.
   `subscription_price = CashIncreaseSubscriptionpRrice`. Ignore
   `CashIncreaseSubscriptionRate`.

8. **Ignore employee-stock fields.** ESOP dilution is already in the per-share
   dividend numbers FinMind reports.

9. **Skip rows where `year == '不適用'` AND no rights-issue fields populated.**
   Those are non-standard cycles with no actionable corporate event.

10. **Adjustment-factor formulas** the adjustment function must implement
    (multiplicatively applied to all pre-ex-date prices for that ticker; `Y` =
    raw close on ex-date):
    - `cash_dividend`: `Y / (Y + cash_amount)`
    - `stock_dividend`: `1 / (1 + ratio)`
    - `cash_and_stock_dividend`: `Y/(Y+cash_amount) × 1/(1+ratio)`, applied as a
      single combined factor — not chained
    - `rights_issue`:
      `(Y + ratio × subscription_price) / (Y × (1 + ratio))`
    - `capital_reduction`: `after_price / before_price` (direct multiplier from
      `TaiwanStockCapitalReductionReferencePrice`)

## Consequences

**Migration.** Any Phase 2 inspection screenshots, hand-checks, or journal
entries generated against the old adjustment math need re-verifying after the
fix lands. None expected — Phase 2 hasn't started — but flagging for cleanliness.

**Test surface grows.** Cross-validator integration test against
`TaiwanStockDividendResult` is new. Anchor fixture extended to cover combined
events.

**Open / parking.** `CashIncreaseSubscriptionRate` exact units left
unresolved. Documented as "appears to be existing-shareholder rate after Art.
267 carve-outs; we derive dilution from share counts instead." Not blocking.
Whether the date-mismatch warning in `cash_and_stock_dividend` ingestion
should be a hard error or a skip-and-log: starting with skip-and-log since no
Top 50 backfill event is expected to trigger it.

## References

- FinMind docs: <https://finmind.github.io/tutor/TaiwanMarket/Fundamental/>
- 公司法 §267 (employee subscription right in new-share issuances)
- 1101 2022-07-21 distribution (TSC AGM resolution, TWSE MOPS announcement)
