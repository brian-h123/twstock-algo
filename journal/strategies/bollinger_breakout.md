# BollingerBreakout — Bollinger Band Breakout (Momentum Variant)

**Type:** Single-asset, momentum breakout  
**Parameters:** `window=20, k=2.0`  
**Period:** 2020-01-01 → 2025-06-17 | 49 tickers (7769 skipped — insufficient data)  
**Benchmark:** 0050 Buy-and-Hold (Sharpe 0.69, CAGR 2.18%, Max DD 5.89%)

---

## Logic

Enter long when the close breaks above the upper Bollinger band (SMA + k·σ); exit when the close falls back below the middle band (SMA). This is the **momentum** interpretation of Bollinger Bands: a breakout above 2σ signals the start of a strong upward trend, not a mean-reversion short opportunity. The middle band serves as a trailing stop.

The middle band exit is intentionally permissive — it allows the position to ride a trend as long as the close stays above the SMA, rather than cutting on the first pullback.

---

## Results (distribution across 49 tickers)

| Metric | Value |
|---|---|
| Median net Sharpe | 0.28 |
| Median net CAGR | ~0.33% |
| Median max drawdown | ~3.9% |
| Hit rate (TSMC rep.) | 34.8% |
| Turnover (TSMC rep.) | ~42% / yr |
| Median fees / ticker | ~NT$14,500 |
| Beats own B&H | 14/49 (28.6%) |
| Beats 0050 B&H | 11/49 (22.4%) |

Sharpe IQR: 0.03 – 0.55. Best: 6919 (2.57), 2383 (1.20), 3231 (0.96). Worst: 6505 (−1.16), 1301 (−1.01), 3045 (−0.35).

---

## Assessment

The distribution is the widest of any single-asset strategy: IQR 0.03 – 0.55, with extreme outliers in both directions (6919 Sharpe 2.57, 6505 −1.16). This wide dispersion is characteristic of a breakout strategy that genuinely captures strong moves on a handful of names while suffering frequent false signals on the rest.

The 34.8% hit rate on TSMC is the lowest in the roster — the strategy is explicitly designed around the idea that most breakouts fail, but the wins are large enough to cover losses. The W/L ratio of 3.6 for TSMC confirms the asymmetry is real, but only on names where sustained momentum follows a breakout. On range-bound names, the hit rate falls further while the W/L ratio collapses.

Turnover is high at ~42% per year (2330 representative), generating ~NT$14,500 in fees per ticker — nearly double the crossover strategies. The sell tax of 0.3% bites on every exit from a winning trade as well as losing ones. A false breakout that quickly falls back below the middle band generates a round-trip of ~0.585% cost for a small loss.

The best results appear on names with discrete momentum episodes: 6919 (3 trades over 5.5 years, caught a specific run), 2383 (semiconductors, sustained 2020-2024 uptrend). Broad application without a momentum pre-filter is not effective.

**Verdict:** Wide dispersion, negative-median outcome when fees are applied uniformly. Works for a small minority of strongly-trending names; punishes the rest. Not viable as a universe-wide strategy without a momentum filter to reduce false breakout exposure.

---

*Universe is a 2026-05-08 snapshot; survivorship bias not corrected. Parameters are canonical (not optimised for selection).*
