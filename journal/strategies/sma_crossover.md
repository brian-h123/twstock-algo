# SmaCrossover — SMA(20)/SMA(60) Crossover

**Type:** Single-asset, trend-following  
**Parameters:** `fast=20, slow=60` (canonical, not optimised)  
**Period:** 2020-01-01 → 2025-06-17 | 49 tickers (7769 skipped — insufficient data)  
**Benchmark:** 0050 Buy-and-Hold (Sharpe 0.69, CAGR 2.18%, Max DD 5.89%)

---

## Logic

Long when the 20-day simple moving average is above the 60-day SMA; flat otherwise. Signal is computed at T's close on causal data; the engine fills at T+1's open. NaN during the first 60 bars while the slow MA warms up. This is a classic trend-following filter: the short MA crossing the long MA up signals strengthening momentum; crossing down signals exhaustion.

It is also the Phase 3 reference strategy. Results here reflect a full 49-ticker distribution run — not the Phase 3 single-name analysis.

---

## Results (distribution across 49 tickers)

| Metric | Value |
|---|---|
| Median net Sharpe | 0.48 |
| Median net CAGR | ~1.2% |
| Median max drawdown | ~5.3% |
| Hit rate (TSMC rep.) | 50.0% |
| Turnover (TSMC rep.) | ~18% / yr |
| Median fees / ticker | ~NT$7,600 |
| Beats own B&H | 24/49 (49.0%) |
| Beats 0050 B&H | 14/49 (28.6%) |

Sharpe IQR: 0.20 – 0.73. Best: 2357 (1.06), 2382 (1.04), 3017 (1.00). Worst: 6505 (−0.48), 2207 (−0.30), 3008 (−0.20).

---

## Assessment

The strategy roughly splits the universe in half: 49% of tickers beat their own B&H on risk-adjusted terms, the remainder do not. That is close to a coin flip and consistent with what a pure trend-following rule should do when applied uniformly to a universe with mixed regime profiles.

Against 0050 the picture is clearer: only 29% of tickers beat the index B&H Sharpe. The 2020-2025 period was broadly favourable to long-only equities in Taiwan, so buy-and-hold was a high bar.

Where the strategy works: names that trend persistently over multi-month periods (2330 TSMC, 2382 Nien Made, 2357 TSMC supply chain, financials 2881/2882/2885). Where it fails: range-bound or mean-reverting low-beta names (banking, utilities) where 20/60 crosses whipsaw in sideways markets.

Turnover is low (~10 trades per ticker over 5.5 years), which keeps fees manageable (~NT$7,600 total). The signal quality, not cost, is the binding constraint here.

**Verdict:** Does not consistently beat buy-and-hold. Competitive on trending names; drag on the rest. Useful as a reference baseline, not as a deployable strategy without a universe filter.

---

*Universe is a 2026-05-08 snapshot; survivorship bias not corrected. Parameters are canonical (not optimised for selection).*
