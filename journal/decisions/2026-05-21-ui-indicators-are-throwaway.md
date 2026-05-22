# ADR: UI indicator functions are throwaway — do not promote to a shared module

**Date:** 2026-05-21
**Status:** Accepted
**Decision ID:** D1 (Phase 2)

## Context

`src/twstock/ui/charts.py` contains two indicator functions:

- `compute_sma(close: pd.Series, window: int) -> pd.Series`
- `compute_bollinger(close: pd.Series, window: int, k: float) -> tuple[pd.Series, pd.Series, pd.Series]`

These were written in Phase 2 to compute overlays on the inspection dashboard. They are correct implementations of SMA and Bollinger Bands for display purposes.

Phase 3 will build a proper indicator module (`src/twstock/indicators/`) that the backtesting engine, strategy layer, and paper-trading simulator will all depend on. The natural impulse when reaching Phase 3 is to reuse the existing implementations: they already work, they have tests, and copying them into a shared module seems like the obvious path.

**This decision records why that impulse must be resisted.**

## Why the Phase 2 implementations are not suitable for reuse

### 1. Lookahead safety is not implemented

The Phase 2 `compute_sma` and `compute_bollinger` functions compute indicators over the full input series without any lookahead guard. For dashboard display this is correct — you want the full smoothed line. For backtesting it is a fatal flaw: a strategy that uses `compute_sma(df["close"], 20)` on the full backtest DataFrame has access to future prices at every bar, producing meaningless results that cannot be detected by inspection.

The Phase 3 indicator module must enforce lookahead safety as a structural invariant, typically by computing indicators in a rolling/expanding fashion that is provably causal. The Phase 2 functions have no infrastructure for this.

### 2. The calling convention is incompatible with vectorbt

vectorbt expects indicator inputs as numpy arrays or DataFrames with specific index alignment. The Phase 2 functions return `pd.Series` with the same index as the input, which works for Plotly line traces but may not align with vectorbt's expected shapes for portfolio-level computation across multiple tickers.

### 3. NaN edge handling is minimal

`compute_sma` uses `pd.Series.rolling().mean()`, which silently returns NaN for windows shorter than `window` rows. For display this is acceptable. For a backtester that allocates capital based on indicator values, an unguarded NaN at the start of history can propagate into position-sizing logic and produce incorrect results. The Phase 3 module needs explicit `min_periods` policy and NaN-propagation documentation.

### 4. They live in the wrong module

`src/twstock/ui/` is a presentation-only layer. Per `CLAUDE.md` §3, it may not be imported from by any strategy, backtest, or simulator module. If `compute_sma` is promoted to `twstock/indicators.py`, either (a) `charts.py` starts importing from outside `ui/`, which violates D7, or (b) `ui/charts.py` keeps its own copy — at which point both copies exist and drift apart.

## Decision

`compute_sma` and `compute_bollinger` in `src/twstock/ui/charts.py` are **permanently scoped to the dashboard**. They are not a prototype of the Phase 3 indicator module. They will not be moved, imported from outside `ui/`, or used as the starting point for Phase 3 implementations.

Phase 3 builds `src/twstock/indicators/` from scratch with lookahead-safe, backtester-compatible implementations. The Phase 2 display functions may be consulted as a reference for the mathematical formula only.

## Enforcement

- `CLAUDE.md` §7 guardrails includes the explicit instruction: "Do not extract `compute_sma` / `compute_bollinger` from `src/twstock/ui/charts.py` into a shared module during Phase 2. They are intentionally throwaway; Phase 3 builds the indicator module from scratch."
- This ADR is linked from `journal/phase_2_retro.md` §5.
- The `ui/` import boundary (D7) mechanically prevents downstream modules from importing `charts.py` even if someone tries.

## Alternatives considered

**Extract to `ui/charts.py` → `twstock/indicators/display.py`, keep backtesting indicators separate.**
This creates a situation where `twstock/indicators/display.py` and `twstock/indicators/backtest.py` have duplicated formulas with subtle behavioral differences. Two sources of truth for SMA is worse than one throwaway copy and one proper copy. Rejected.

**Use the Phase 2 implementations as-is in Phase 3 and add lookahead-safety wrappers later.**
"Add lookahead safety later" is how lookahead bias enters backtesting code. The cost of rewriting two functions from scratch is lower than the cost of a latent lookahead bug in Phase 3 results. Rejected.
