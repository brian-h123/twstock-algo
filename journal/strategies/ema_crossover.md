# EmaCrossover — EMA(20)/EMA(60) Crossover

**Type:** Single-asset, trend-following  
**Parameters:** `fast=20, slow=60` (canonical, not optimised)  
**Period:** 2020-01-01 → 2025-06-17 | 49 tickers (7769 skipped — insufficient data)  
**Benchmark:** 0050 Buy-and-Hold (Sharpe 0.69, CAGR 2.18%, Max DD 5.89%)

---

## Logic

Long when the 20-day exponential moving average is above the 60-day EMA; flat otherwise. Signal computed at T's close on causal data; fills at T+1's open. NaN during warmup. Structurally identical to SmaCrossover but with EMAs: the exponential weighting means recent prices have more influence, so crossovers occur sooner after a trend shift than with SMAs. In theory this reduces lag; in practice it also increases sensitivity to short-term noise.

---

## Results (distribution across 49 tickers)

| Metric | Value |
|---|---|
| Median net Sharpe | 0.49 |
| Median net CAGR | ~1.3% |
| Median max drawdown | ~5.7% |
| Hit rate (TSMC rep.) | 50.0% |
| Turnover (TSMC rep.) | ~18% / yr |
| Median fees / ticker | ~NT$6,800 |
| Beats own B&H | 25/49 (51.0%) |
| Beats 0050 B&H | 13/49 (26.5%) |

Sharpe IQR: 0.27 – 0.70. Best: 2881 (1.01), 2330 (0.87), 2603 (0.96). Worst: 2207 (−0.38), 1301 (−0.37), 3008 (−0.35).

---

## Assessment

The median Sharpe of 0.49 is marginally better than SmaCrossover's 0.48, and the 51% beat-own-B&H rate just crosses the majority threshold. Neither difference is meaningful — they are within the noise of a 49-ticker sample evaluated over one historical period.

EMA's faster reaction to recent price changes does not reliably help in the TWSE top-50 universe over this period. Large-cap Taiwanese names tend to trend on multi-month cycles driven by earnings and macro events; the slightly faster EMA crossover fires at similar turning points to the SMA and does not capture materially different entries or exits.

The higher sensitivity cuts both ways: on a few choppy names, EMA generates slightly more whipsaw trades than SMA (e.g., 2301 had 15 trades with EMA vs 14 with SMA), but the difference is negligible in aggregate.

Fewer trades per ticker on average compared to SMA (EMA: ~11 trades on TSMC; SMA: ~10 trades) — EMA's persistence means the slow EMA doesn't reverse as readily once established, slightly reducing churn on smooth trends.

**Verdict:** Marginal improvement over SmaCrossover, not statistically distinguishable. Both are unsuitable as standalone strategies without a universe filter selecting for high-momentum names. The crossover family as a whole is not competitive against 0050 buy-and-hold in this period.

---

*Universe is a 2026-05-08 snapshot; survivorship bias not corrected. Parameters are canonical (not optimised for selection).*
