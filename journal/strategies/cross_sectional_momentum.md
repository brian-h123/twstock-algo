# CrossSectionalMomentum — 12-1 Month Trailing Return

**Type:** Cross-sectional portfolio strategy  
**Parameters:** `lookback=252, skip=21` | `EqualWeightTopN(n=10)` | `Monthly()` rebalance  
**Period:** 2020-01-01 → 2025-06-17 | Full Top-50 universe (49 eligible tickers)  
**Benchmark:** 0050 Buy-and-Hold (Sharpe 0.69, CAGR 2.18%, Max DD 5.89%)

---

## Logic

At each rebalance bar (first XTAI trading day of the month), score every eligible ticker by its total return over the prior 252 trading days, skipping the most recent 21 days. The skip (the "−1" in the standard 12-1 month momentum convention) avoids the short-term reversal that contaminates the trailing 1-month window. Rank scores descending; hold the top 10 names at equal weight (10% each). Rebalance monthly.

This is the only native `CrossSectionalStrategy` in the Phase 4 roster. The score is cardinal (magnitude matters for ranking), so it cannot be adapter-lifted from a binary `Strategy` — the allocator needs the raw score to rank correctly.

---

## Results (single portfolio run, 2020-2025)

| Metric | Value |
|---|---|
| Net Sharpe | 0.779 |
| Net CAGR | 17.82% |
| Max drawdown | 37.82% |
| Max DD duration | 335 days |
| Hit rate (round-trips) | 59.1% |
| Turnover | 2.21× / yr |
| Total fees | NT$117,229 |
| Total trades | 105 |
| vs 0050 Sharpe | **0.779 > 0.690 → beats benchmark** |

---

## Assessment

This is the only Phase 4 strategy that beats 0050 buy-and-hold on a risk-adjusted basis. The Sharpe of 0.779 versus 0050's 0.690 is a real improvement, and the cross-sectional momentum factor is well-documented in the academic literature (Jegadeesh-Titman 1993 and decades of replications), so this is not a surprising result.

**What to trust:** The Sharpe improvement and the directional result (momentum works in the Taiwan large-cap universe over this period) are meaningful.

**What to discount:**

- **Survivorship bias.** The universe is a 2026-05-08 snapshot. Applying it back to 2020 means some stocks that have since grown into the top-50 are present in the 2020 universe when they were not yet there in reality. The 17.82% CAGR is likely overstated as a result. A rolling-constituent universe would produce lower, more honest CAGR figures.

- **Max drawdown is severe.** At 37.82%, this is 6× worse than the 0050 benchmark's 5.89%. A concentrated 10-stock equal-weight portfolio has substantially higher idiosyncratic risk than the diversified ETF. The strategy's Sharpe improvement over 0050 comes partly from higher expected returns and partly from a high-variance equity curve that the Sharpe ratio alone does not fully capture.

- **Turnover cost.** At 2.21× annual turnover over 5.5 years, total fees reach NT$117,229 — the highest of any Phase 4 strategy. Monthly rebalancing with 10 positions and a 30-40% rotation rate generates approximately 20-24 round-trips per year. At 0.585% round-trip cost (commission + sell tax + slippage), that is ~NT$21k per year in friction. A lower-frequency rebalance (quarterly) would meaningfully improve net performance.

- **Cash management.** The engine applies a buy-side cash cap (Phase 4 fix) when sells' T-close proceeds fail to cover T+1-open buy costs. A small number of buy orders are therefore underfilled on rebalance bars. Phase 5 should track these underfills explicitly.

**Verdict:** The cross-sectional momentum factor is real and detectable in the TWSE top-50 universe. The raw backtest number (17.82% CAGR) overstates the true expected return due to survivorship bias. The strategy deserves further development in Phase 5-6: rolling-universe constituents, quarterly rebalance, proper short-component, and out-of-sample period to validate the factor genuinely.

---

*Universe is a 2026-05-08 snapshot; survivorship bias not corrected. Capital NT$1,000,000, max 10% per position. Net of Taiwan cost model (commission 0.0855%, sell tax 0.3%, slippage 5 bps per side).*
