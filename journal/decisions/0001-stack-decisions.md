# 0001 — Initial Stack Lock-ins

**Date:** 2026-05-09
**Status:** Accepted

## Context

Before writing any code, a planning session was used to settle all major tool and architecture choices for the project. Making these decisions up front prevents mid-project churn and gives a clear basis for future ADRs that change or extend the stack.

## Decision

The following choices were locked in and recorded in `CLAUDE.md` section 2:

| Area | Decision | Rationale |
|---|---|---|
| Time horizon | Daily bars only | Intraday data is expensive and noisy; daily is sufficient for the strategies targeted in Phases 4–6 |
| Universe | TWSE Top 50 (0050 ETF constituents) | Liquid, well-covered by free data sources; manageable scope for a first build |
| Capital assumption | NT$1,000,000; max 10% per position | Round number that reflects realistic retail scale; 10% cap limits single-stock blow-up risk |
| Python | 3.12 | Current stable release; good typing support |
| Dependency manager | uv | Fast, reproducible, modern replacement for pip + venv |
| Lint / format | ruff | Single tool replacing flake8 + isort + black; fast |
| Tests | pytest | Standard; broad plugin ecosystem |
| Pre-commit | ruff + pytest smoke test | Catches format issues and import-level errors before they reach CI |
| Data in-memory | pandas | Ubiquitous; well-understood by target audience (data science / ML hiring managers) |
| Data on disk | Parquet | Columnar, compressed, fast for time-series slices |
| Data queries | DuckDB over Parquet | SQL interface without a running server; integrates well with pandas |
| Primary data source | FinMind (free tier) | Free, covers TWSE daily OHLCV and corporate actions; Python-friendly API |
| Fallback data source | TWSE official | Source of truth for corporate actions when FinMind lags |
| Frontend | Streamlit + Plotly | Fast to build; familiar to Python data scientists |
| Backtester | vectorbt | Vectorized, pandas-native; final evaluation deferred to Phase 3 kickoff |
| Broker (paper) | Self-built simulator | Avoids external dependency for paper trading; full control over fill logic |
| Broker (live) | Fubon Neo API | Has a Python SDK; account opening in progress |
| License | MIT | Permissive; appropriate for a public portfolio project |

## Consequences

- These choices are in effect for all phases unless explicitly superseded by a later decision record.
- Any deviation (e.g., switching backtester, adding a second data source) requires a new ADR.
- Streamlit, Plotly, vectorbt, and broker SDKs are intentionally deferred — install only when the relevant phase begins.
