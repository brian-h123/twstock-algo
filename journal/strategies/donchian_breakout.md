# DonchianBreakout — Donchian Channel Breakout (20-day)

**Type:** Single-asset, trend-following breakout  
**Parameters:** `window=20`  
**Period:** 2020-01-01 → 2025-06-17 | 49 tickers (7769 skipped — insufficient data)  
**Benchmark:** 0050 Buy-and-Hold (Sharpe 0.69, CAGR 2.18%, Max DD 5.89%)

---

## Logic

Enter long when today's close exceeds the *prior* day's 20-day rolling high; exit when today's close falls below the *prior* day's 20-day rolling low. Asymmetric breakout thresholds: entry fires on a new high, exit fires on a new low — creating a wide grey zone between the two channels where the position is held without re-signalling.

The one-bar lag on the channel (using `upper.shift(1)` and `lower.shift(1)`) is not an approximation — it is structurally required. Today's rolling high already includes today's own high, making `close > upper_today` mathematically impossible in valid OHLCV data. The shift sets the threshold from fully-known prior data, making the breakout genuinely causal.

---

## Results (distribution across 49 tickers)

| Metric | Value |
|---|---|
| Median net Sharpe | 0.47 |
| Median net CAGR | ~1.0% |
| Median max drawdown | ~5.0% |
| Hit rate (TSMC rep.) | 60.0% |
| Turnover (TSMC rep.) | ~27% / yr |
| Median fees / ticker | ~NT$9,000 |
| Beats own B&H | 23/49 (46.9%) |
| Beats 0050 B&H | 12/49 (24.5%) |

Sharpe IQR: 0.23 – 0.69. Best: 6919 (2.01), 2357 (1.08), 2382 (0.94). Worst: 1301 (−0.69), 6505 (−0.77), 2880 (−0.25).

---

## Assessment

DonchianBreakout posts the best single-asset results alongside EmaCrossover (median Sharpe 0.47 vs 0.49). Its IQR is relatively tight (0.23 – 0.69), suggesting more consistent performance across the universe than the wide-dispersion breakout strategies (Bollinger, MACD).

The 60% hit rate for TSMC is the highest of any trend-following strategy in the roster. Donchian breakouts on a 20-day channel capture genuine new-high momentum events while the channel-floor exit provides a sensible stop. The asymmetric threshold means the strategy stays in a good trade (above the rolling high) without whipsawing on mean-reversion noise as long as the low channel isn't breached.

Turnover (~27% per year, ~14 trades over 5.5 years on TSMC) sits between the crossover strategies and Bollinger, generating moderate fees (~NT$9,000 per ticker). Not cheap enough to be "cost-neutral" against buy-and-hold, but more fee-efficient than the other breakout strategies.

The strategy's weakness is the same as all single-asset trend-followers here: in a broadly rising market, a static 20-day breakout doesn't add enough above passive. Only 25% of tickers beat 0050 B&H. The 47% that beat their own B&H is the best beat-rate among the breakout/reversion family, but still below 50%.

**Verdict:** Strongest of the non-crossover single-asset strategies. A natural baseline for any enhanced breakout system in Phase 5 or 6 (e.g., paired with cross-sectional momentum filtering to select the universe). As a standalone, it does not beat 0050 consistently.

---

*Universe is a 2026-05-08 snapshot; survivorship bias not corrected. Parameters are canonical (not optimised for selection).*
