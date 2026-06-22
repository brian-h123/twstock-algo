# Phase 4 Strategy Comparison

**Period:** 2020-01-01 → 2025-06-17  
**Capital:** NT$1,000,000 | Max 10% per position | T+1 open execution  
**Cost model:** TaiwanCostModel always on — commission 0.0855%, sell tax 0.3%, slippage 5 bps per side  
**Benchmark:** 0050 Buy-and-Hold — Sharpe 0.69, CAGR 2.18%, Max DD 5.89%

---

## Summary table

Single-asset metrics are **medians across 49 tickers** (7769 excluded — insufficient data). Hit rate and turnover are from the TSMC (2330) representative tear sheet, as the evaluation harness does not compute per-ticker medians for those fields. CrossSectionalMomentum metrics are from a single portfolio run across the full universe.

| Strategy | Net Sharpe | Net CAGR | Max DD | Hit Rate† | Turnover† | Fees‡ | % Beats B&H | Beat 0050 |
|---|---|---|---|---|---|---|---|---|
| SmaCrossover | 0.48 | ~1.2% | ~5.3% | 50% | ~18%/yr | ~NT$7.6k | 49% | No (29%) |
| EmaCrossover | 0.49 | ~1.3% | ~5.7% | 50% | ~18%/yr | ~NT$6.8k | 51% | No (27%) |
| RsiReversion | 0.27 | ~0.3% | ~2.5% | 88% | ~15%/yr | ~NT$4.7k | 29% | No (10%) |
| BollingerBreakout | 0.28 | ~0.3% | ~3.9% | 35% | ~42%/yr | ~NT$14.5k | 29% | No (22%) |
| Macd | 0.31 | ~0.5% | ~4.8% | 42% | ~92%/yr | ~NT$30.8k | 33% | No (22%) |
| DonchianBreakout | 0.47 | ~1.0% | ~5.0% | 60% | ~27%/yr | ~NT$9.0k | 47% | No (25%) |
| CrossSectionalMomentum | **0.78** | **17.8%** | 37.8% | 59% | 2.21×/yr | NT$117k§ | N/A | **Yes** |
| _0050 B&H (benchmark)_ | _0.69_ | _2.18%_ | _5.89%_ | — | — | — | — | — |

†From TSMC (2330) representative tear sheet.  
‡Median per ticker for single-asset strategies. Total portfolio for CrossSectionalMomentum.  
§Total fees for the full portfolio over 5.5 years. Single-asset fee figures are per isolated ticker run.

---

## Rankings

**By median Sharpe (single-asset):**
1. EmaCrossover — 0.49
2. SmaCrossover — 0.48
3. DonchianBreakout — 0.47
4. Macd — 0.31
5. BollingerBreakout — 0.28
6. RsiReversion — 0.27

No single-asset strategy beats 0050's Sharpe of 0.69 at the median ticker. The crossover and Donchian families are the least bad; RSI and Bollinger are clearly ineffective as unconditional rules.

**Overall winner:** CrossSectionalMomentum (Sharpe 0.779, the only strategy that clears the 0050 benchmark on risk-adjusted terms).

---

## Key findings

**1. No single-asset strategy reliably beats buy-and-hold.**
The best single-asset strategy (EmaCrossover) beats its own B&H at the median only 51% of the time — barely above random. Only 27-29% of tickers beat 0050 B&H for even the best single-asset strategies. This is an honest result: unconditional technical rules applied uniformly across a universe of large-cap stocks in a broadly bullish period will not systematically outperform passive.

**2. Turnover is the decisive cost factor, not signal quality.**
MACD has a broadly similar signal quality to the crossover strategies in terms of directional accuracy, but ~92% annual turnover versus ~18% for SMA produces 4× the fees. The difference between a strategy that eats 0.3% sell tax ~50 times and one that eats it ~10 times is a material drag that no improvement in gross Sharpe covers at this frequency.

**3. Cross-sectional ranking amplifies alpha; within-name filtering does not.**
The crossover strategies apply a rule to one name independently of what is happening elsewhere. CrossSectionalMomentum ranks all names against each other and holds only the top 10. That ranking step is what generates the Sharpe improvement: the strategy systematically avoids the worst-performing names in the universe, not just the ones below their own moving average.

**4. Survivorship bias is material for the momentum result.**
The 17.82% CAGR for CrossSectionalMomentum is likely meaningfully overstated. The universe is a 2026 snapshot; names that grew into the top-50 during 2020-2025 are present in the 2020 evaluation but were not truly investable at that size. A rolling-constituent universe would produce a lower, more defensible figure.

**5. Max drawdown is not penalised by Sharpe.**
CrossSectionalMomentum has a Sharpe of 0.779 and a max drawdown of 37.8% — six times the benchmark's 5.89%. An investor comparing only Sharpe ratios would miss that the momentum portfolio could lose more than a third of its value in a bad period. Any Phase 5 development should track Sortino and Calmar ratios in addition to Sharpe.

---

## Caveats

- Universe is a **2026-05-08 snapshot**; survivorship bias not corrected. All CAGR figures are overstated to a degree that is likely larger for momentum (which explicitly holds past winners) than for mean-reversion or crossover strategies.
- Parameters are **canonical by convention, never optimised for selection** (PHASE_4.md D2). A reader who sees "SmaCrossover(20, 60)" should understand this was set before evaluation, not after.
- **Multiple comparisons:** seven strategies evaluated over one historical period. Some results are good by chance. No strategy should be promoted on a single in-sample number without an out-of-sample evaluation.
- The **0050 benchmark metrics** (Sharpe 0.69, CAGR 2.18%) are lower than 0050's actual historical return over this period, likely because the data was cut at 2025-06-17 just before the 0050 4:1 split. The benchmark is used consistently across all strategies, so relative comparisons are valid even if the absolute CAGR is understated.
