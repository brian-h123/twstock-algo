# Macd — MACD Signal-Line Crossover (12/26/9)

**Type:** Single-asset, trend-following / momentum  
**Parameters:** `fast=12, slow=26, signal=9` (canonical "textbook" MACD)  
**Period:** 2020-01-01 → 2025-06-17 | 49 tickers (7769 skipped — insufficient data)  
**Benchmark:** 0050 Buy-and-Hold (Sharpe 0.69, CAGR 2.18%, Max DD 5.89%)

---

## Logic

Long when the MACD line (EMA12 − EMA26) is above the signal line (EMA9 of the MACD line); flat otherwise. The dense signal emits 1 or 0 every bar after warmup (first `slow + signal − 2 = 33` bars are NaN). Unlike the crossover strategies, this fires whenever the two lines cross — which can happen many times per year, making it the highest-turnover strategy in the roster.

The histogram (MACD line − signal line) is algebraically redundant with the crossover condition and is not used separately.

---

## Results (distribution across 49 tickers)

| Metric | Value |
|---|---|
| Median net Sharpe | 0.31 |
| Median net CAGR | ~0.48% |
| Median max drawdown | ~4.8% |
| Hit rate (TSMC rep.) | 42.0% |
| Turnover (TSMC rep.) | ~92% / yr |
| Median fees / ticker | ~NT$30,800 |
| Beats own B&H | 16/49 (32.7%) |
| Beats 0050 B&H | 11/49 (22.4%) |

Sharpe IQR: −0.05 – 0.63. Best: 6919 (3.65), 2603 (1.55), 3231 (1.06). Worst: 1301 (−0.61), 1216 (−0.58), 4904 (−0.54).

---

## Assessment

MACD is the cost story of the Phase 4 roster. With ~50 trades per ticker over 5.5 years (versus ~10 for SmaCrossover and ~8 for RsiReversion), the 12/26/9 crossover fires frequently enough to turn over nearly 92% of capital per year on TSMC alone. At the canonical commission rate plus Taiwan's 0.3% sell tax, each round-trip costs roughly 0.585% in total friction. At 50 trades, that is ~NT$30,800 in fees per ticker — four times what the SMA strategy pays.

The gross signal is not bad: MACD correctly times momentum on many names and a 42% hit rate with W/L ratio 2.6 for TSMC suggests the underlying cross has some predictive content. But the net extraction after costs is mediocre: the median Sharpe of 0.31 is the second-lowest in the roster, ahead only of RSI.

The outliers illustrate the same pattern seen in BollingerBreakout: 6919 and 2603 are names with strong directional episodes where the frequent crossovers still captured net positive alpha despite high costs. The median ticker does not have that profile.

The 92% turnover figure should anchor any Phase 5 evaluation of MACD. If a paper-sim shows MACD performing well, the first question is whether the cost model was engaged.

**Verdict:** High-cost strategy whose gross signal quality is eroded by the sell tax at MACD's natural turnover rate. Not viable at the standard TaiwanCostModel without either higher alpha or a frequency filter that suppresses whipsaw trades.

---

*Universe is a 2026-05-08 snapshot; survivorship bias not corrected. Parameters are canonical (not optimised for selection).*
