# Phase 4 — Technical Strategies

> **Purpose.** Implement and honestly evaluate a roster of classical technical strategies on the Phase 3 backtester, and extend the engine from single-asset to multi-asset portfolio strategies. The deliverable's value is rigorous engineering and honest evaluation — strategies that lose to buy-and-hold after costs are reported as such, with analysis of why. The phase has two blocks: **4a** (six single-asset strategies, no new engine) and **4b** (the portfolio runner plus cross-sectional momentum, the first strategy that needs it). Architectural rationale lives in ADRs 0004/0005/0006; this spec is the *how* and the *order*.

**Status:** Ready to start (spec finalized)
**Last updated:** 2026-06-17
**Estimated duration:** ~2–3 weeks
**Predecessor:** Phase 3 (complete, closed 2026-06-13)
**Governing ADRs:** `0004` (custom engine, stateless `Strategy`, lookahead layering), `0005` (instant-settlement cash model, sell-before-buy), `0006` (portfolio runner: cross-sectional interface, allocator, cadence, tie-breaking, trade accounting)

---

## 1. Goal

After Phase 4, the following runs: a single-asset strategy evaluated per ticker, and a cross-sectional momentum strategy evaluated across the universe through the new portfolio runner, both benchmarked against 0050 buy-and-hold.

```python
from datetime import date
from twstock.backtest import Engine, TaiwanCostModel, BuyAndHold
from twstock.backtest.allocator import EqualWeightTopN
from twstock.backtest.tiebreak import LiquidityTieBreak
from twstock.backtest.rebalance import Monthly
from twstock.strategies.rsi_reversion import RsiReversion
from twstock.strategies.cross_sectional_momentum import CrossSectionalMomentum
from twstock.universe import get_top50_universe

engine = Engine(capital=1_000_000, cost_model=TaiwanCostModel(), max_position_pct=0.10)

# 4a — single-asset: pure signal, run per ticker (full capital), aggregate the distribution
single = engine.run(
    strategy=RsiReversion(period=14),
    tickers=["2330"],
    start=date(2020, 1, 1), end=date(2025, 12, 31),
)

# 4b — cross-sectional momentum: signal is the strategy; allocation + cadence are run config
momentum = engine.run(
    strategy=CrossSectionalMomentum(lookback=252, skip=21),
    tickers=get_top50_universe(),
    allocator=EqualWeightTopN(n=10, tiebreak=LiquidityTieBreak()),
    schedule=Monthly(),
    start=date(2020, 1, 1), end=date(2025, 12, 31),
)

benchmark = engine.run(
    strategy=BuyAndHold(), tickers=["0050"],
    start=date(2020, 1, 1), end=date(2025, 12, 31),
)
```

Specifically, after Phase 4 the project has: (a) six single-asset strategies with tear sheets and writeups, evaluated across all 50 tickers as a distribution; (b) a portfolio runner that takes cross-sectional scores → target weights → orders under monthly rebalancing, sharing the Phase 3 `Portfolio`, cost model, and T+1 execution; (c) a cross-sectional top-N momentum strategy evaluated through it; (d) a lot-based trade-accounting ledger that survives partial reductions; and (e) a consolidated comparison table reporting which strategies, if any, beat buy-and-hold net of costs.

---

## 2. Locked Decisions

Settled. Architectural decisions already recorded in an ADR are summarized in one line with a pointer; do not relitigate them here.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Strategy roster.** Six single-asset (`SmaCrossover` done; `EmaCrossover`, `RsiReversion`, `BollingerBreakout`, `Macd`, `DonchianBreakout` new) + one cross-sectional (`CrossSectionalMomentum`). Pairs trading deferred to Phase 5. | Seven strategies against a ≥6 exit bar. Pairs is relational *and* needs shorts — both Phase 5. Six single-asset strategies clear the bar with zero engine risk; momentum is the differentiated 4b piece. |
| D2 | **Fixed canonical parameters, never optimized for selection.** Values per the table in §4.2 / §4.11. `WalkForwardSplit` may be used only as a stability diagnostic, never to pick a winning parameter. | Grid-search-and-pick makes in-sample Sharpe meaningless (`PROJECT_PLAN.md §3 Phase 4`). Conventional parameters stated "by convention, not fit" is the defensible, honest choice. |
| D3 | **Single-asset evaluation = per-ticker runs + distribution.** Run the engine once per ticker (full capital, single ticker) → 50 results → report median/IQR net Sharpe, fraction beating own B&H, fraction beating 0050. `2330` is the named equity-curve example. A secondary adapter-portfolio view (single-asset signal run through the 4b runner) may be reported but the distribution is the locked primary. | The distribution isolates the *signal* from allocation effects and shows robustness, not luck on one name. A single representative ticker is cherry-picking; a shared-capital run conflates signal with allocation. |
| D4 | **Benchmark = buy-and-hold 0050.** `BuyAndHold` on the ETF. Single-asset writeups also compare to each ticker's own B&H. | Benchmarking against the actual ETF (not an equal-weight proxy) is honest. Already backfilled in Phase 3 (`ADR 0004` D7). |
| D5 | **Strategy interface** → `ADR 0006 §1–3`. Locked single-asset `Strategy` (untouched, un-renamed) + sibling `CrossSectionalStrategy` (panel → scores) + `CrossSectionalAdapter` (binary lift only). | Sibling, not a fused class, because statelessness (0004 §3) makes backtrader's universal-method shape unavailable, and the two signal types differ (directional state vs rankable score). |
| D6 | **Allocator** → `ADR 0006 §4`. Holdings-agnostic `Allocator` ABC; ships `EqualWeightTopN(n, tiebreak=…)`; validates `n × max_position_pct ≥ 1.0`; under-supply of eligible names holds cash. | A pure scores→weights map keeps allocation rules interchangeable; the constructor check rejects configs that can never fully invest. |
| D7 | **Rebalance cadence** → `ADR 0006 §5`. `RebalanceSchedule.should_rebalance(ctx)`; calendar rules resolved against `exchange_calendars` XTAI; canonical = monthly, first XTAI trading day. Portfolio path only. | Calendar rebalancing is the factor-strategy convention; monthly tames the sell-tax drag; one defensible choice, not a tuned one. |
| D8 | **Tie-handling** → `ADR 0006 §6`. Pluggable `TieBreakPolicy`; canonical `LiquidityTieBreak`; `HoldAll` baseline. Alpha-line guardrail: no return-predictive signal in a tiebreak. | A binary score gives no basis to choose among tied names; resolving that is an allocator concern. Alpha hidden in a tiebreak escapes `generate_scores` and the lookahead audit. |
| D9 | **Cash & execution ordering** → `ADR 0005`. Instant-settling shared cash pool; all sells before all buys on any order-executing bar. | Justified by TWSE T+2 net settlement; sell-before-buy weakly dominates and removes the silent under-investment in the old `min(target, cash)` buy path. |
| D10 | **Trade accounting** → `ADR 0006 §9`. Lot-consuming ledger replaces `_round_trips`; configurable grouping/matching; canonical FlatToFlat + FIFO; `Position.avg_cost` retained for the open-position basis. | Trade-pairing breaks on re-trims; a lot ledger handles partial reductions natively and inherits Phase 5 partial fills for free. FlatToFlat keeps one round-trip per position lifecycle. |
| D11 | **Honest reporting is part of the deliverable.** Gross *and* net metrics; survivorship caveat in every writeup; multiple-comparisons risk named; losers reported, not dropped; binary-in-portfolio results note they test the tiebreaker as much as the signal. | The portfolio piece's value to a reviewer is statistical honesty. "10 strategies all beat the market" is a red flag. |

---

## 3. Deliverables (modules)

```
src/twstock/
├── indicators/
│   └── core.py                      # MODIFIED — add macd(), donchian_channels()
├── backtest/
│   ├── strategy.py                  # UNCHANGED — locked Strategy ABC, BuyAndHold
│   ├── cross_sectional.py           # NEW — CrossSectionalStrategy ABC, CrossSectionalAdapter
│   ├── allocator.py                 # NEW — Allocator ABC, EqualWeightTopN
│   ├── tiebreak.py                  # NEW — TieBreakPolicy ABC, LiquidityTieBreak, HoldAll
│   ├── rebalance.py                 # NEW — RebalanceContext, RebalanceSchedule ABC, Monthly/Weekly/Daily/Quarterly
│   ├── reconciler.py                # NEW — reconcile(target_weights, portfolio, prices)
│   ├── trades.py                    # NEW — lot ledger: build_round_trips(trades, grouping, matching)
│   ├── engine.py                    # MODIFIED — dispatch on strategy type; cross-sectional run loop
│   ├── audit.py                     # MODIFIED — panel-aware assert_no_lookahead
│   └── tearsheet.py                 # MODIFIED — use trades.build_round_trips; _round_trips removed
└── strategies/
    ├── sma_crossover.py             # UNCHANGED (reference)
    ├── ema_crossover.py             # NEW (single-asset)
    ├── rsi_reversion.py             # NEW (single-asset)
    ├── bollinger_breakout.py        # NEW (single-asset)
    ├── macd.py                      # NEW (single-asset)
    ├── donchian_breakout.py         # NEW (single-asset)
    └── cross_sectional_momentum.py  # NEW (native CrossSectionalStrategy)

tests/backtest/         # +test files mirroring each new module (see §6)
tests/strategies/       # +per-strategy unit + integration tests
journal/strategies/     # one writeup per strategy + comparison.md
scripts/                # phase_4_evaluate.py (per-ticker distribution harness)
```

`CLAUDE.md` is updated once at phase close (D5–D11 in §2, new modules in §4/§6, the new `strategies/` import boundary in §7, Phase 4 guardrails). Three ADRs (0005, 0006 plus 0004 already present) are referenced, not re-derived.

**`strategies/` import boundary** (currently undefined; add to `CLAUDE.md §7`, mirroring `backtest/`): `strategies/` may import from `twstock.backtest`, `twstock.indicators`, `twstock.data.api`, `twstock.universe`, `twstock.config`. It must **not** import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or `twstock.ui`.

---

## 4. Module specifications

### 4.1 `indicators/core.py` (additions)

Same contract as the Phase 3 indicators: pure functions over `pd.Series`, causal, `min_periods=window`, NaN-padded warmup, no `fillna`.

```python
def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram).
    macd_line = ema(close, fast) - ema(close, slow);
    signal_line = ema(macd_line, signal); histogram = macd_line - signal_line."""

def donchian_channels(high: pd.Series, low: pd.Series, window: int = 20
                      ) -> tuple[pd.Series, pd.Series]:
    """Returns (lower, upper). upper = high.rolling(window).max();
    lower = low.rolling(window).min(). Value at T uses bars[T-window+1:T+1]."""
```

Notes: `donchian_channels` takes `high`/`low`, not `close` — the only indicator so far that needs more than the close series. Causality is unchanged: a rolling max/min at T never reads T+1.

### 4.2 `strategies/` — single-asset (4a)

All five subclass the locked `Strategy` ABC and follow `SmaCrossover` exactly: state-based, dense signals after warmup, **explicit NaN during warmup** (never `0` as a warmup proxy), causal-only. Canonical parameters and signal logic:

| Strategy | Params | Long when | Flat when |
|---|---|---|---|
| `EmaCrossover` | fast=20, slow=60 | `ema(fast) > ema(slow)` | otherwise |
| `RsiReversion` | period=14 | `rsi < 30` (enter) | `rsi > 50` (exit) |
| `BollingerBreakout` | window=20, k=2.0 | `close > upper` | `close < middle` |
| `Macd` | 12/26/9 | `macd_line > signal_line` | otherwise |
| `DonchianBreakout` | window=20 | `close > prior upper` | `close < prior lower` |

`RsiReversion` and `DonchianBreakout` are trigger-ish (asymmetric enter/exit thresholds) — emit a non-NaN value only on a state change, NaN otherwise, to avoid spurious flat signals. Each strategy validates its params (`fast < slow`, etc.) in `__init__`.

### 4.3 `backtest/cross_sectional.py`

```python
class CrossSectionalStrategy(ABC):
    @abstractmethod
    def generate_scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Date-indexed, ticker-columned float scores. NaN = not eligible.
        Higher = higher rank. Stateless pure function of `panel`; no holdings,
        no cross-bar state. Magnitude only where honestly produced; ties are
        permitted and resolved by the allocator's TieBreakPolicy."""

    @property
    def name(self) -> str:
        return type(self).__name__


class CrossSectionalAdapter(CrossSectionalStrategy):
    """Lifts a single-asset Strategy to BINARY panel scores: applies it per
    ticker, maps a non-NaN long signal to 1.0, else NaN. Cannot manufacture a
    magnitude — a strategy whose edge IS a magnitude must be native."""
    def __init__(self, strategy: Strategy) -> None: ...
```

Notes:
- **Binary only.** This is the load-bearing limitation: the single-asset contract emits {-1, 0, +1}, so the adapter can express "eligible / not," never "how strongly." Documented as a Phase-4 convenience expected to fade once Phase 6 ML strategies arrive native (`ADR 0006 §2`).
- **Stateless, like its parent.** The adapter only reshapes; it adds no state, so the panel audit (§4.10) remains valid.

### 4.4 `backtest/allocator.py`

```python
class Allocator(ABC):
    @abstractmethod
    def allocate(self, scores_at_T: pd.Series, *, max_position_pct: float) -> pd.Series:
        """One bar's scores -> target weights (sum ≤ 1.0). Holdings-agnostic."""

class EqualWeightTopN(Allocator):
    def __init__(self, n: int, tiebreak: TieBreakPolicy = LiquidityTieBreak()) -> None: ...
    # rank scores desc; take top n (tiebreak resolves equal-score ties at the cut);
    # weight 1/n; clamp each to max_position_pct.
    # Raises if n * max_position_pct < 1.0 (a config that can never fully invest).
```

Notes:
- **Holdings-agnostic.** `allocate` never sees current positions — diffing against the book is the reconciler's job (§4.7). This boundary is what makes allocators swappable.
- **Under-supply holds cash.** Fewer than `n` eligible names → hold those names at `1/n` each, leave the rest in cash. Never force a fill into an un-scored name; never error.
- **Tiebreak lives here.** The policy is a constructor arg because ties surface during ranking, which is the allocator's step. The alpha-line guardrail (§4.5) constrains it.

### 4.5 `backtest/tiebreak.py`

```python
class TieBreakPolicy(ABC):
    @abstractmethod
    def select(self, tied: pd.Index, n_slots: int, panel_at_T: dict) -> pd.Index:
        """Choose among equally-scored names competing for the last slots.
        MUST NOT use return-predictive information."""

class LiquidityTieBreak(TieBreakPolicy):   # canonical: prefer the most-traded tied names
class HoldAll(TieBreakPolicy):             # honest baseline: return all tied (may exceed n)
```

Notes:
- **Alpha-line guardrail (`ADR 0006 §6`).** A tiebreak may break ties only on grounds the strategy never claimed as signal (liquidity, hold-all, deterministic order). A tiebreak that prefers names on a return-predictive basis is unaudited alpha — it belongs in `generate_scores`. This is a code-review check, not a unit test.
- `HoldAll` can return more than `n` names; the allocator then weights `1/len(selected)`. It is the most honest reference for a binary strategy with many ties.

### 4.6 `backtest/rebalance.py`

```python
@dataclass(frozen=True)
class RebalanceContext:
    date: pd.Timestamp
    current_weights: pd.Series
    target_weights: pd.Series
    signal_changed: bool

class RebalanceSchedule(ABC):
    @abstractmethod
    def should_rebalance(self, ctx: RebalanceContext) -> bool: ...

class Monthly(RebalanceSchedule): ...   # first XTAI trading day of the month
class Weekly(RebalanceSchedule): ...    # Quarterly, Daily likewise
```

Notes:
- **Context, not a bare date.** Calendar rules read only `ctx.date`; the richer fields exist so threshold rules (read drift between current/target) and event rules (read `signal_changed`) drop into the same signature later without reshaping the calendar rules (`ADR 0006 §5`).
- **XTAI-resolved.** "First trading day of the month" is computed from the `exchange_calendars` XTAI session list, never naive month-start — January 1 and Lunar New Year close the exchange, so the month's first *trading* day floats.
- **Separable steps.** The engine asks `should_rebalance` and *separately* computes the target; do not hard-wire "gate before computing target," since threshold rules need a target to measure against.

### 4.7 `backtest/reconciler.py`

```python
def reconcile(target_weights: pd.Series, portfolio: Portfolio,
              prices: dict[str, float]) -> list[Order]:
    """Diff target vs current book -> orders, ALL SELLS BEFORE ALL BUYS.
    shares = floor(equity * weight / price). Odd-lot sizing (ADR 0006 §7)."""
```

Notes:
- **Sells before buys (`ADR 0005`).** Partition the order list; emit sells first so freed cash funds same-bar buys. This is what removes the silent under-investment of the old `min(target, cash)` path on rotations.
- **Odd-lot sizing.** Any integer share count (零股), carried forward from Phase 3 — round-lot-only would make most Top-50 names untradeable at NT$1M. Real odd-lot liquidity is thinner; a Phase 5 pessimism gap.
- **Pure function.** Takes the target and the book, returns orders; the engine schedules them for T+1 and executes via `Portfolio.buy/sell`. No state of its own.

### 4.8 `backtest/trades.py`

```python
class Grouping(Enum): FILL_TO_FILL; FLAT_TO_FLAT; FLAT_TO_REDUCED
class Matching(Enum): FIFO; LIFO

def build_round_trips(trades: pd.DataFrame,
                      grouping: Grouping = Grouping.FLAT_TO_FLAT,
                      matching: Matching = Matching.FIFO) -> pd.DataFrame:
    """Consume per-ticker lot queues; emit realised P&L per closed slice.
    Replaces tearsheet._round_trips. Pure function over result.trades (D10)."""
```

Notes:
- **Lot-consuming, not trade-pairing.** Each buy pushes a `(shares, basis)` lot; each sell consumes lots (FIFO oldest-first) and emits one closed round-trip per lot slice it touches, with realised P&L `(exit − basis) × slice_shares`. A 300-share sell against a 500-share position closes 300 and leaves 200 open.
- **Partial reductions are native** — the re-trim case now, and Phase 5 partial fills later, are the same "fewer shares than the lot holds" path.
- **Canonical FlatToFlat + FIFO.** One round-trip per position lifecycle (re-trims do not inflate `n_trades`). Other settings exist for diagnostics; the canonical is locked for every writeup.
- **Regression invariant:** on all-or-nothing single-asset results (no partials), FlatToFlat + FIFO must reproduce the old `_round_trips` metrics exactly. This is the acceptance test for the rework.

### 4.9 `backtest/engine.py` (dispatch)

`Engine.run` gains optional `allocator` and `schedule` parameters and dispatches on strategy type:

```python
def run(self, strategy, tickers, start, end,
        allocator: Allocator | None = None,
        schedule: RebalanceSchedule | None = None) -> BacktestResult: ...
```

Notes:
- **Single-asset path unchanged.** A `Strategy` runs through the existing transition logic; `allocator`/`schedule` are ignored (warn if passed).
- **Cross-sectional path.** A `CrossSectionalStrategy` requires `allocator` and `schedule` (raise if missing). Per bar T: read `scores[T]`; if `schedule.should_rebalance(ctx)`, compute target via `allocator.allocate`, `reconcile` against the book, schedule sells-then-buys for T+1; execute T's pending orders at T's open; mark-to-market at T's close.
- **One execution core.** Both paths share `Portfolio`, `TaiwanCostModel`, T+1 scheduling, mark-to-market, and `BacktestResult` construction. No second engine, no `run_portfolio`.
- The fixed-fraction buy block (`target_amount = equity * max_position_pct; spendable = min(...)`) is replaced by the allocator + reconciler on the cross-sectional path.

### 4.10 `backtest/audit.py` (panel variant)

`assert_no_lookahead` gains a panel-aware form: perturb future rows across the panel and verify earlier `generate_scores` outputs are unchanged. Valid only because `generate_scores` is stateless (mirrors the single-asset audit, `ADR 0004 §4`).

### 4.11 `strategies/cross_sectional_momentum.py`

```python
class CrossSectionalMomentum(CrossSectionalStrategy):
    """Score = trailing total return over `lookback` days, skipping the most
    recent `skip` days (12-1 month convention; the skip avoids short-term
    reversal). NaN until lookback+skip history exists. Cardinal — must be
    native, not adapter-lifted."""
    def __init__(self, lookback: int = 252, skip: int = 21) -> None: ...
```

Notes: per-ticker score is computable alone, but it is a magnitude, so the strategy is **native** `CrossSectionalStrategy`, not an adapter case. Canonical config: `lookback=252, skip=21`, paired with `EqualWeightTopN(n=10)` and `Monthly()` at the run site.

---

## 5. Build sequence

One commit per step (Conventional Commits, `Co-Authored-By: Claude` trailer). Each strategy step bundles strategy file + unit test + integration test. **The 4a / 4b split is explicit below.**

### Setup

1. **`feat(indicators): add macd and donchian_channels`** — `indicators/core.py` + `test_core.py`. Causal, `min_periods=window`; hand-computed references; `donchian` takes high/low.

### 4a — Single-asset strategies (no new engine)

2. **`feat(strategies): add EmaCrossover`** — closest to `SmaCrossover`; validates the single-asset flow first. Unit + integration (`2330`, net ≠ gross, audit passes).
3. **`feat(strategies): add RsiReversion`** — first mean-reversion; asymmetric enter/exit; scrutinize net vs gross (high cost-drag risk).
4. **`feat(strategies): add BollingerBreakout`**.
5. **`feat(strategies): add Macd`**.
6. **`feat(strategies): add DonchianBreakout`** — *4a strategies complete (six single-asset).*

### Reporting rework (bridges 4a → 4b)

7. **`refactor(backtest): replace _round_trips with lot-based ledger`** — `trades.py`; rewire `tearsheet.py`; remove `_round_trips`. **Acceptance: FlatToFlat + FIFO reproduces the old metrics on all-or-nothing single-asset results.** Partial-sell unit tests added now (they exercise the path 4b will use).
8. **`feat(scripts): single-asset evaluation harness`** — `scripts/phase_4_evaluate.py` runs a strategy across all 50 tickers, collects results, emits the distribution (median/IQR net Sharpe, % beating own B&H, % beating 0050). Drives the 4a writeups.

### 4b — Portfolio runner

9. **`feat(backtest): cross-sectional strategy interface + adapter`** — `cross_sectional.py`; panel-aware `assert_no_lookahead` in `audit.py`. Validate plumbing with `CrossSectionalAdapter(SmaCrossover())` producing a scores panel (no runner yet).
10. **`feat(backtest): equal-weight top-N allocator`** — `allocator.py` + the `n × cap ≥ 1` validation + under-supply-holds-cash behaviour.
11. **`feat(backtest): tie-break policies`** — `tiebreak.py` (`LiquidityTieBreak`, `HoldAll`).
12. **`feat(backtest): rebalance schedules`** — `rebalance.py` (`RebalanceContext`, `Monthly/Weekly/Daily/Quarterly`), XTAI-resolved.
13. **`feat(backtest): portfolio reconciler and engine dispatch`** — `reconciler.py`; wire `Engine.run` to dispatch `CrossSectionalStrategy` through cadence → allocate → tie-break → reconcile, sells-before-buys. Integration-test with **adapter-lifted SMA** across the universe (known binary behaviour validates the whole path). Includes the rotation/under-investment regression test.
14. **`feat(strategies): add CrossSectionalMomentum`** — native cardinal strategy. Full integration test: panel `assert_no_lookahead`, non-NaN metrics, net ≠ gross, audit passes, vs 0050 B&H.

### Close-out (documentation)

15. **`docs(strategies): per-strategy writeups and comparison table`** — one 1-page writeup per strategy in `journal/strategies/`; consolidated `comparison.md` (see §7.3 for format). Losers included.
16. **`docs: close phase 4`** — `CLAUDE.md` (new modules, `strategies/` boundary, Phase 4 state, §6 close summary), `PROJECT_PLAN.md` progress tracker, `journal/phase_4_retro.md`.

**Slip-handling rule.** If 4b (steps 9–14) isn't complete by end of phase, ship 4a (six single-asset strategies + the lot-ledger rework + writeups) as a coherent partial deliverable — it already clears the ≥6 exit bar — and carry momentum + the runner into a short Phase 4b continuation. **Never** ship a strategy without its `assert_no_lookahead` test or without net-of-cost reporting.

---

## 6. Test strategy

Phase 3 ended at ~230 tests. Phase 4 adds per-strategy unit + integration tests and the interface-level suite. Integration tests are `@pytest.mark.integration` (depend on the local Parquet store).

### 6.1 What gets tested

| File | What it covers |
|---|---|
| `test_core.py` (extended) | `macd` against hand-computed EMA chain; `donchian_channels` rolling max/min; NaN warmup. |
| `test_<strategy>.py` (×5) | Signal correctness on a hand-built fixture; NaN during warmup (never `0`); the 0-vs-NaN convention; param validation in `__init__`. |
| `test_<strategy>_integration.py` (×6) | Run on real `2330` (or universe for momentum); non-NaN metrics; net ≠ gross; `assert_no_lookahead` passes. Template: `test_sma_crossover_integration.py`. |
| `test_cross_sectional.py` | `CrossSectionalAdapter` maps a known binary signal to a 1.0/NaN panel; panel shape (dates × tickers); stateless. |
| `test_allocator.py` | `n × cap < 1` raises; top-N selection correct; fewer eligible than n holds cash (no error, no forced fill); weights sum ≤ 1. |
| `test_tiebreak.py` | `LiquidityTieBreak` orders by volume; `HoldAll` returns all tied. |
| `test_rebalance.py` | `Monthly` lands on the first **XTAI trading day** (test around a Lunar New Year close and a 1st-of-month holiday). |
| `test_reconciler.py` | Sells emitted before buys; weight→shares floors; **rotation funds the buy from the same-bar sell** (guards the silent-under-investment regression). |
| `test_trades.py` | FlatToFlat + FIFO matches old `_round_trips` on whole positions; a partial sell yields correct realised P&L on the closed slice and leaves the remainder open; FIFO consumes oldest lot first. |
| `test_momentum_integration.py` | End-to-end momentum vs 0050; panel audit passes. |

### 6.2 What is explicitly NOT tested

- Short selling, partial fills, limit orders (Phase 5).
- The alpha-line guardrail — it is a code-review check, not a unit test.
- Plotly figure output; performance of the suite.
- Multi-strategy ensembles (Phase 6).

### 6.3 Fixture strategy

Inline `pd.DataFrame` construction per test; reuse the Phase 3 `make_synthetic_bars` helper. For panel tests, a small `make_synthetic_panel(tickers, n_days, …)` helper in `tests/backtest/conftest.py`. No new files under `tests/fixtures/`.

---

## 7. The portfolio rebalance loop and turnover economics (DETAILED)

The most important new section of this phase: get the rebalance loop or the cost accounting wrong and every 4b number is meaningless.

### 7.1 The rebalance loop, per bar T (cross-sectional path)

> On a rebalance bar T: read `scores[T]` (computed causally from data ≤ T) → `allocator.allocate(scores[T])` → `reconcile(target, book)` → schedule sells-then-buys for **T+1's open**. On a non-rebalance bar: do nothing. Always mark-to-market at T's close.

Cadence gates only the *action*; the strategy may compute a target every bar, but only rebalance bars trade to it. This composes cleanly with the T+1 execution invariant from Phase 3: cadence selects *which* T's emit orders; the T→T+1 fill timing is unchanged, so lookahead safety is preserved exactly as in single-asset runs.

### 7.2 Why weights drift, and what a re-trim is

Target weights are percent of total equity. With `EqualWeightTopN(10)`, each held name targets 10%. As prices move, a winner balloons past 10% and a laggard shrinks below it, even when the top-10 membership is unchanged. A rebalance therefore does two things: swap names that left/entered the top-N, **and** re-trim drifted names back to 10%. The re-trim is a *partial sell* of the winner — a full fill of a smaller order, not a Phase-5 liquidity partial fill — and it is exactly why `_round_trips` had to become a lot ledger (§4.8).

### 7.3 Turnover economics — the honest hurdle

The 0.3% sell tax is asymmetric and unavoidable; the round-trip cost floor is ~0.585% (commission ~0.171% + tax 0.3% + slippage 0.10%). Every rebalance that sells pays it. A monthly-rebalanced top-10 momentum book can turn over a large fraction of capital per year; at, say, 12 full turnovers that is ~7% of capital bled to costs before any alpha. **Cadence, not the signal, often decides viability** — and that is itself a finding worth reporting, not hiding. The tear sheet must surface `turnover` and `total_fees` so the cost of the chosen cadence is visible. A momentum strategy whose gross Sharpe is attractive but whose net Sharpe dies to turnover is the honest headline, not a failure to bury.

Comparison-table columns (one row per strategy, losers included): net Sharpe, net CAGR, max drawdown, hit rate, turnover, total fees, % of tickers beating B&H (single-asset), beat-0050 (yes/no).

---

## 8. Exit criteria

Phase 4 is done when **all** hold:

- [ ] ≥ 6 strategies fully evaluated with tear sheets and 1-page writeups in `journal/strategies/`; consolidated `comparison.md` built.
- [ ] Single-asset strategies evaluated across all 50 tickers; distribution (median/IQR net Sharpe, % beating B&H, % beating 0050) reported.
- [ ] `CrossSectionalMomentum` evaluated end-to-end through the runner, benchmarked against 0050 B&H.
- [ ] `Engine.run` dispatches both paths from one execution core; cross-sectional run returns a complete `BacktestResult`.
- [ ] `assert_no_lookahead` (single-asset and panel variants) passes for every strategy.
- [ ] Net return correctly below gross for every strategy (cost model engaged).
- [ ] Lot ledger reproduces old `_round_trips` metrics on whole-position single-asset results (regression acceptance).
- [ ] Rotation/under-investment regression test passes (sell-before-buy funds the same-bar buy).
- [ ] `EqualWeightTopN` raises on `n × cap < 1`; holds cash on under-supply.
- [ ] `Monthly` resolves to the first XTAI trading day across holiday boundaries.
- [ ] `uv run pytest tests/` green; `uv run pytest -m integration` green.
- [ ] Pre-commit clean; all commits follow `CLAUDE.md §9`.
- [ ] `CLAUDE.md` updated (D5–D11, new modules, `strategies/` boundary, Phase 4 guardrails); `PROJECT_PLAN.md` tracker updated; `journal/phase_4_retro.md` written.

---

## 9. Pitfalls and known gaps

**The 0-vs-NaN convention.** The most common strategy-author bug. `0` = "go flat, close the position"; `NaN` = "no opinion, preserve state." A strategy that emits `0` during indicator warmup generates a spurious sell if a prior position is open. Every new strategy emits NaN during warmup, like `SmaCrossover`. Tested per strategy.

**The 0.3% sell tax kills high-turnover strategies.** `RsiReversion` and `BollingerBreakout` (touch-based) are most at risk. Scrutinize net vs gross before claiming any edge; a beautiful gross Sharpe that dies net of costs is the disqualifying mistake for this portfolio piece, not a finding to soften.

**Silent under-investment on rotations.** The old engine sized buys as `min(target, cash)`, so a cash-starved buy bought fewer shares rather than erroring. The reconciler's sell-before-buy ordering fixes this; the regression test in `test_reconciler.py` guards it. Without sells-first, a rotation silently under-deploys.

**Binary strategies in a portfolio test the tiebreaker.** A binary signal (SMA via the adapter) with more eligible names than slots is mostly exercising `LiquidityTieBreak`, not the signal. Every such writeup states this plainly.

**Alpha smuggled into a tiebreak is unaudited.** A tiebreak that ranks tied names on a return-predictive basis bypasses `generate_scores` and the lookahead audit (`ADR 0006 §6`). Code-review check, like the Phase 3 `shift(-` grep.

**Survivorship bias.** Universe is a 2026-05-08 snapshot; backtesting from 2020 fishes in a pond that partly didn't exist. The `tear_sheet` note carries it; every writeup acknowledges it.

**Multiple comparisons.** Seven strategies, several parameterizations — some will look good by chance. Writeups name this; no strategy is promoted on a single in-sample number.

**Cadence is not modeled below daily.** Monthly rebalancing assumes the month's first XTAI trading day; intraday timing of the rebalance is a Phase 9 concern (`ADR 0006` consequences).

**Instant settlement (carried from Phase 3 / `ADR 0005`).** Cash from a sell is available to same-bar buys; real T+2 settlement and prefunding are Phase 5. Acceptable here for long-only spending within netted means.

---

## 10. Out of scope (explicit)

Not Phase 4:

- Short selling and shorts-based strategies (pairs trading) — Phase 5.
- Partial fills, rejections, order state machines — Phase 5.
- Limit/stop orders — Phase 5.
- Real T+2 settlement / prefunding modeling — Phase 5.
- Multi-strategy ensembles, signal blending — Phase 6.
- Parameter optimization for strategy selection — never (diagnostic walk-forward only).
- ML / cross-sectional learned scores — Phase 6 (they reuse `CrossSectionalStrategy`).
- Threshold / event-driven rebalance schedules — interface accommodates them; implementations are Phase 6+.
- Inverse-vol / risk-parity / mean-variance allocators — interface accommodates them; Phase 6+ / Phase 9.
- Intraday bars — Phase 9.
- Universe expansion beyond Top 50 + 0050 — Phase 9.

If you find yourself building any of these mid-phase, stop and write a one-line ticket in `journal/phase_8_backlog.md`.

---

## 11. New dependencies

| Package | Why |
|---|---|
| (none required) | New modules use only `pandas`, `numpy`, `plotly`, `exchange_calendars`, and stdlib — all already in `pyproject.toml` (`exchange_calendars` since Phase 1). |

---

## 12. Kickoff checklist

Before pasting this into Claude Code, confirm:

- [ ] Phase 3 is closed (`PROJECT_PLAN.md §6`; `journal/phase_3_retro.md` exists).
- [ ] ADRs `0005` and `0006` are committed under `journal/decisions/`.
- [ ] `CLAUDE.md` carries the Phase 4 decisions (§2), the new `strategies/` import boundary (§7), and the new module list (§4/§6).
- [ ] The Phase 3 public API imports cleanly (`Engine`, `TaiwanCostModel`, `BuyAndHold`, `WalkForwardSplit`, `assert_no_lookahead`, `tear_sheet`).
- [ ] All 50 tickers + 0050 Parquet files are present and `quality.check_all()` is green.
- [ ] You have read this spec end-to-end and have no open questions.
