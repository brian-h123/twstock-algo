# RsiReversion — RSI(14) Mean Reversion

**Type:** Single-asset, mean-reversion  
**Parameters:** `period=14`, entry threshold RSI < 30, exit threshold RSI > 50  
**Period:** 2020-01-01 → 2025-06-17 | 49 tickers (7769 skipped — insufficient data)  
**Benchmark:** 0050 Buy-and-Hold (Sharpe 0.69, CAGR 2.18%, Max DD 5.89%)

---

## Logic

Buy when RSI(14) crosses below 30 (oversold); exit when RSI(14) rises above 50 (recovered). Asymmetric thresholds create a wide grey zone (30-50) where the strategy holds without re-signalling, and a tight exit zone above 50. NaN during the first 14 bars. Sparse signal: emits only on threshold crossings, so many bars have no opinion.

---

## Results (distribution across 49 tickers)

| Metric | Value |
|---|---|
| Median net Sharpe | 0.27 |
| Median net CAGR | ~0.3% |
| Median max drawdown | ~2.5% |
| Hit rate (TSMC rep.) | 87.5% |
| Turnover (TSMC rep.) | ~15% / yr |
| Median fees / ticker | ~NT$4,700 |
| Beats own B&H | 14/49 (28.6%) |
| Beats 0050 B&H | 5/49 (10.2%) |

Sharpe IQR: 0.10 – 0.46. Best: 2059 (1.15), 3653 (1.03), 2345 (0.95). Worst: 2408 (−0.60), 4904 (−0.23), 2885 (−0.40).

---

## Assessment

The 87.5% hit rate on TSMC looks impressive but is the wrong number to watch. The win/loss ratio for TSMC is 0.79 — losses (avg −8.2%) outweigh wins (avg +6.5%). The strategy wins most of the time but loses badly when it is wrong: a name that drops through RSI 30 and keeps falling inflicts the full drawdown before the RSI recovers above 50 to trigger the exit.

The low median CAGR (0.3%) explains the outcome: most trades produce small recoveries, but capital sits idle between signals. In a 2020-2025 Taiwan market that broadly trended upward, RSI rarely dips below 30 on the large-cap names. When it does, the setup often reflects a deteriorating fundamental story rather than a temporary oversold condition — exactly the cases where mean-reversion fails.

Max drawdown is the lowest of any single-asset strategy (~2.5% median), which is the one genuine feature: the strategy is in-market infrequently, so portfolio-level exposure is limited. But capital not deployed earns nothing, so low drawdown here reflects low exposure, not good risk management.

Only 10% of tickers beat 0050 B&H — the worst result in the roster.

**Verdict:** The weakest single-asset strategy by Sharpe and beat-rate. Mean-reversion logic applied uniformly across a trending universe in a bull period is structurally disadvantaged. A sector-filtered or volatility-regime-conditioned version might be defensible; the unconditional RSI(14) < 30 rule is not.

---

*Universe is a 2026-05-08 snapshot; survivorship bias not corrected. Parameters are canonical (not optimised for selection).*
