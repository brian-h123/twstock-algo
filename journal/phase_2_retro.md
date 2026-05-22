# Phase 2 Retrospective — Inspection Dashboard

**Written:** 2026-05-21
**Phase duration:** 2026-05-20 → 2026-05-21 (~2 calendar days)
**Estimated duration:** 1–2 weeks
**Exit criteria check:** all criteria met as of this writing

---

## 1. What was built

All modules from `PHASE_2.md` §3 are implemented and tested:

| Module | Status | Tests |
|---|---|---|
| `ui/names.py` | Done | `test_names.py` |
| `ui/data_loader.py` | Done | `test_data_loader.py` |
| `ui/charts.py` | Done | `test_charts.py` |
| `ui/app.py` | Done | `test_smoke.py` |

Suite total: **160 tests collected, 3.59 s** (up from 129 at Phase 1 exit; 31 new tests).
Pre-commit clean.

Build sequence followed exactly:
1. `chore: scaffold ui module`
2. `feat(ui): add ticker name dictionary`
3. `feat(ui): add cached data loader`
4. `feat(ui): add chart builders`
5. `feat(ui): wire ticker and date range controls`
6. `feat(ui): render candlestick chart`
7. `feat(ui): add raw/adjusted price toggle`
8. `feat(ui): add SMA and Bollinger overlays`
9. `feat(ui): add info panel`
10. `feat(ui): add corporate action markers`
11. `perf(ui): verify <2s warm render` — no code changes needed; all tickers passed manually

---

## 2. Exit criteria — final status

- [x] `uv run streamlit run src/twstock/ui/app.py` launches with no errors
- [x] Any Top 50 ticker renders candlestick + volume with SMA(20) and ex-date markers in <2s warm
- [x] Adjusted → raw toggle shows visible price gap at 2330 2024-07-18; toggling back closes it
- [x] Ex-date markers appear at correct dates for 3+ spot-checked tickers; hover shows type and amount
- [x] Info panel shows reasonable values for 3+ spot-checked tickers
- [x] Short-history tickers (6919, 7769) load without error; date picker clamps to listing date
- [x] `uv run pytest tests/ui/` passes; total suite runs in < 5 s
- [x] Pre-commit clean
- [x] All Phase 2 commits follow the convention in `CLAUDE.md` §9
- [x] ADR written: `journal/decisions/2026-05-21-ui-indicators-are-throwaway.md`
- [x] `CLAUDE.md` updated: current phase → Phase 3, §6 Current State updated
- [x] `README.md` has a "Run the dashboard" section
- [x] `PROJECT_PLAN.md` progress tracker updated (Phase 2 row → Done)
- [x] `journal/phase_2_retro.md` — this document

---

## 3. What went well

**Build sequence held.** All 10 implementation steps were completed in order without backtracking. The spec was detailed enough that each step had a clear exit condition.

**Phase completed far under estimate.** The 1–2 week estimate was set conservatively for someone learning Streamlit and Plotly; with the spec fully designed upfront, the actual implementation was ~2 days. The phase was genuinely narrow in scope — the spec's resistance to scope creep paid off.

**No critical bugs.** The dashboard rendered correctly on first attempt for both long-history (2330) and short-history (6919, 7769) tickers. The Phase 1 data layer was solid enough that the UI never encountered surprising data.

**Performance passed without any adjustments.** Step 11 was a no-op. The two known perf traps (rangeslider off, 2-year default range instead of 10-year) were already in place from the spec; no profiling or tuning was needed.

**Commit convention landed cleanly.** The Conventional Commits format from `CLAUDE.md` §9 produced a clear, scannable git log for the first time in this project. All Phase 2 commits are independently readable with no phase-number references or vague subjects.

**Taiwan candlestick convention was easy to get right.** The spec's explicit call-out (red for up, green for down) prevented the default US-convention mistake. A reviewer familiar with TWSE charts would not notice anything wrong.

---

## 4. What was harder than expected

### 4.1 `st.session_state` clamping must happen before the keyed widget renders

**The issue:** Streamlit reads `session_state[key]` *first* when rendering a keyed widget, ignoring the `value=` argument on all reruns after the first. The clamping pattern in `PHASE_2.md` §7.2 — mutating `session_state.start_date` and `session_state.end_date` *before* calling `st.date_input` — is not intuitive. It is easy to write the clamp after the widget and wonder why the date picker still shows the unclamped value after a ticker switch.

**Resolution:** followed the spec's pattern literally. Works correctly. Worth documenting explicitly so a future reader of `app.py` doesn't refactor the clamp below the widget call.

### 4.2 The selectbox returns the display label, not the ticker

**The issue:** `st.selectbox` returns the selected item from the `options` list as-is. Since `options` is `[display_label(t) for t in universe]` — e.g. `"2330 — 台積電"` — the returned value is the label string, not the 4-digit ticker. Passing the label directly to any loader or `get_available_range` produces an empty DataFrame without any error, making this a silent failure that only shows up as a blank chart.

**Resolution:** `ticker = selected_label.split(" — ")[0]` before any downstream call. This is noted in `PHASE_2.md` §9 pitfalls, but easy to overlook.

---

## 5. Decisions made during the phase

All Phase 2 decisions were pre-locked in `PHASE_2.md` §2. No new decisions were made during the build. The only decision requiring a formal ADR is D1 (indicators computed on-the-fly, not extracted to a shared module), recorded in `journal/decisions/2026-05-21-ui-indicators-are-throwaway.md`.

| Decision | Where recorded |
|---|---|
| D1: indicators computed on-the-fly in `ui/charts.py`, not extracted | `journal/decisions/2026-05-21-ui-indicators-are-throwaway.md` |
| D2–D7: all pre-locked at phase kickoff | `PHASE_2.md` §2 |

---

## 6. Open follow-ups entering Phase 3

None from Phase 2 itself. Inherited open items from Phase 1 (FU-01, FU-02, FU-05, FU-06 all resolved before or during Phase 1 exit):

| ID | Description | Priority before Phase 3 |
|---|---|---|
| FU-03 | `get_corporate_actions` `end` param unused in `FinMindSource` | Low — apply Option 1 (post-fetch filter) the first time a Phase 3 caller passes a real `end` date |
| FU-04 | Backfill resume never skips on consecutive runs (always full re-fetch) | Low — performance only, not correctness |

---

## 7. Honest assessment

**On pace:** completed in ~2 days against a 1–2 week estimate. The primary reason is that the dashboard was designed to be minimal from the start — the guiding constraint was "build the minimum needed to verify adjustments, ex-dates, and basic overlays." With that constraint held, there were only 4 source files to write and the scope never expanded. The spec being fully designed upfront and the Phase 1 data layer being stable were secondary contributors; the minimal-by-design philosophy is what made those advantages count.

**On scope discipline:** the spec's explicit out-of-scope list was used. Two items almost crept in: (1) the impulse to move `compute_sma` to `twstock/indicators.py` since "Phase 3 will need it anyway" — resisted, per D1 and the ADR; (2) a custom date-range preset button (1M/3M/1Y/Max) — added as a Phase 8 ticket in `journal/phase_8_backlog.md` instead.

**On the dashboard's purpose:** it works as a sanity-check tool. The raw/adjusted toggle on 2330 2024-07-18 visually confirms the adjustment math closes the dividend gap exactly as intended. That is the primary Phase 1 deliverable validated.

**On `compute_sma` / `compute_bollinger`:** these are intentionally throwaway. They are correct implementations but lack lookahead-safety helpers, proper NaN edge-case handling, and vectorbt-compatible signatures that Phase 3 will need. Do not promote them. The ADR locks this decision in writing.

**On hiring signal:** Phase 2 is explicitly low-signal per `PROJECT_PLAN.md`. The dashboard is a sanity-check tool, not a TradingView clone. Time spent here was appropriate — the features that matter for Phase 3 (clean data layer, verified adjustment math) were already done in Phase 1.

---

## 8. Phase 3 kickoff notes

1. **Confirm vectorbt at kickoff.** `PROJECT_PLAN.md` §3 Phase 3 marks vectorbt as provisional. The kickoff decision should verify the vectorbt version against the current Python 3.12 / pandas 2.x stack and confirm compatibility before committing to it.

2. **Build `twstock/indicators/` from scratch.** Do not copy `compute_sma` / `compute_bollinger` from `ui/charts.py`. The Phase 3 indicator module needs lookahead-safe versions with explicit `min_periods` handling and a calling convention compatible with vectorbt's expected input shapes.

3. **Taiwan cost model is critical.** The 0.3% sell-side securities transaction tax is non-negotiable. Any backtest that omits it will show inflated returns. Hard-code this as a named constant in `config.py`; never let it be optional.

4. **Buy-and-hold on 0050 as the benchmark.** Every strategy tearsheet must include the 0050 benchmark return for the same period. If a strategy can't beat passive 0050 after costs, say so clearly.
