# Phase 3 — Backtesting Engine

> **Purpose.** A thin, custom, event-driven daily backtester that produces an honest tear sheet for any rule-based strategy on the Top 50 universe. The engine is the spine of Phases 4, 5, and 6 — every strategy, every paper-trading run, every ML model evaluation flows through it. Correctness over features: lookahead-safe by construction, Taiwan-cost-aware, exhaustively tested. No vectorbt, no backtrader as a dependency; both studied as references.

**Status:** Ready to start (spec finalized)
**Last updated:** 2026-05-30
**Estimated duration:** ~2 weeks (hard cap; pull stretch items to Phase 8 tickets)
**Predecessor:** Phase 2 (complete)

---

## 1. Goal

After Phase 3, the following invocation returns a tear sheet for both a reference strategy and a buy-and-hold benchmark, with the strategy's net return correctly below its gross return due to Taiwan-specific costs:

```python
from twstock.backtest import Engine, TaiwanCostModel, BuyAndHold
from twstock.strategies.sma_crossover import SmaCrossover

engine = Engine(
    capital=1_000_000,
    cost_model=TaiwanCostModel(),
    max_position_pct=0.10,
    execution="next_open",
)

result = engine.run(
    strategy=SmaCrossover(fast=20, slow=60),
    tickers=["2330"],
    start=date(2020, 1, 1),
    end=date(2025, 12, 31),
)

benchmark = engine.run(
    strategy=BuyAndHold(),
    tickers=["0050"],
    start=date(2020, 1, 1),
    end=date(2025, 12, 31),
)

print(result.tear_sheet())
print(benchmark.tear_sheet())
```

Specifically, after Phase 3 the project has: (a) a lookahead-safe execution model with T-close signals and T+1-open fills, (b) a Taiwan-specific cost model with 0.3% sell-side tax and configurable commission, (c) a reference SMA(20)/SMA(60) crossover strategy, (d) a 0050 buy-and-hold benchmark, (e) tear-sheet output with all standard metrics, and (f) a configurable `WalkForwardSplit` iterator ready for Phase 6.

---

## 2. Locked Decisions

These are settled. Do not relitigate without an ADR.

| # | Decision | Rationale |
|---|---|---|
| D1 | **Custom thin engine.** No vectorbt, no backtrader, no zipline as a dependency. Both vectorbt and backtrader are studied as architectural references and notes recorded in `journal/phase_3_references.md`. | Performance is not a constraint at 50 tickers × 10 years. Lookahead safety is structural in a custom engine and structurally fragile in vectorbt. Phase 5's broker simulator reuses the same interfaces this phase defines. |
| D2 | **Execution model: signal at T close, fill at T+1 open.** No same-bar execution under any circumstance. | Industry standard for daily strategies. Makes lookahead bias on execution impossible by construction. |
| D3 | **Slippage: fixed 5 bps per side, configurable.** No volume-based slippage in Phase 3. | Top 50 are highly liquid; max position size (NT$100k) is rounding-error against daily volume. Revisit if universe expands in Phase 9+. |
| D4 | **Cost model: 0.3% sell-side securities transaction tax (0.1% for ETFs), 0.1425% statutory commission cap × 6折 durable online discount (0.0855% effective per side), NT$20 round-lot per-order minimum, 5 bps per-side slippage; always on.** Cap and discount stored as separate fields on `TaiwanCostModel` so promotional rates can be modeled by overriding the discount. T+2 settlement documented but not modeled in Phase 3. All rates verified against the Securities Transaction Tax Act and TWSE references (citations in `cost_model.py` docstring). | Taxes are legally mandated and asymmetric (sell only). Commission cap is statutory; 6折 is the durable online-order baseline across Fubon, Yuanta, and Cathay. Always-on prevents accidental "no-cost" backtests. |
| D5 | **Indicators: plain pandas, vectorized, `min_periods=window`.** Lookahead-safety enforced at the engine layer (T → T+1 separation), not at the indicator layer. | Falls out of D1. `rolling(window).mean()` at index T uses values up to and including T; that is fine because execution happens at T+1. |
| D6 | **`WalkForwardSplit` iterator, configurable: `train_years`, `test_years`, `step`, `anchored` (bool).** Built in Phase 3, used minimally (one in/out split for the SMA crossover stability report). Full usage deferred to Phase 6. | Single API covers anchored vs rolling and retraining cadence. Building it now means Phase 6 inherits a tested abstraction. |
| D7 | **0050 backfilled as the benchmark instrument.** `data/by_ticker/0050.parquet` added via existing backfill script. `universe.py` is unchanged (`get_top50_universe()` still returns 50 constituents); 0050 is benchmark-only. | The Top 50 are 0050's constituents, not 0050 itself. Benchmarking against the actual ETF (not an equal-weight proxy) is more honest. One-line addition to the data layer. |
| D8 | **Strategy ABC.** All strategies subclass `twstock.backtest.Strategy` and implement `generate_signals(bars: pd.DataFrame) -> pd.Series`. Signals are target positions in {-1, 0, +1} for the v1 spec (long-only enforced by the engine when configured). | Same interface used by Phase 4 (manual strategies), Phase 6 (ML models wrapped as strategies), and Phase 5 (paper-trading runner). Locking it now is the single most important integration decision. |
| D9 | **`backtest/` import boundary.** `src/twstock/backtest/*` may import from `twstock.data.api`, `twstock.data.storage`, `twstock.universe`, `twstock.indicators`, and `twstock.config`. It must not import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or `twstock.ui`. | Mirrors the `ui/` boundary rule. Keeps the backtester decoupled from data internals and from presentation. Enforced by code review; `import-linter` is a Phase 8 ticket. |
| D10 | **The engine emits one complete, well-typed run-result artifact** (`BacktestResult`: equity curve + full trade ledger + daily positions + cost totals). All reporting — the tear sheet, the equity/drawdown figure, and any future analyzer — is a pure function over this artifact, never coupled to engine internals. | This completeness is the reversibility guarantee. Rejecting backtrader's in-loop analyzer *framework* (see `journal/phase_3_references.md`, "On reversibility") costs nothing in analyzer *extensibility* — provided the artifact is complete *now*: any number of post-hoc analyzers become pure functions over it, with no engine rewrite. The only thing the in-loop framework buys is *streaming* computation, which matters for Phase 8 live, not for backtests at this scale. |

---

## 3. Deliverables (modules)

```
src/twstock/
├── indicators/
│   ├── __init__.py
│   └── core.py              # sma, ema, bollinger_bands, rsi (NEW)
├── backtest/
│   ├── __init__.py
│   ├── strategy.py          # Strategy ABC + BuyAndHold reference impl (NEW)
│   ├── cost_model.py        # TaiwanCostModel dataclass (NEW)
│   ├── portfolio.py         # Portfolio: positions, cash, equity (NEW)
│   ├── engine.py            # Engine: main loop, T+1 execution (NEW)
│   ├── result.py            # BacktestResult dataclass (NEW)
│   ├── tearsheet.py         # Metrics + Plotly equity curve (NEW)
│   ├── walkforward.py       # WalkForwardSplit iterator (NEW)
│   └── audit.py             # Lookahead-bias test utility (NEW)
└── strategies/
    ├── __init__.py
    └── sma_crossover.py     # Reference SMA(20)/SMA(60) crossover (NEW)

tests/
├── indicators/
│   ├── __init__.py
│   └── test_core.py
└── backtest/
    ├── __init__.py
    ├── test_strategy.py
    ├── test_cost_model.py
    ├── test_portfolio.py
    ├── test_engine.py
    ├── test_engine_lookahead.py    # dedicated lookahead-bias tests
    ├── test_result.py
    ├── test_tearsheet.py
    ├── test_walkforward.py
    ├── test_audit.py
    └── test_sma_crossover_integration.py
```

**Total: 11 source files + 11 test files** across three new top-level packages (`indicators`, `backtest`, `strategies`).

`CLAUDE.md` is updated once during Phase 3 (locked decisions §2, repo structure §4, guardrails §7, module list §6). A new ADR records the vectorbt-rejection decision. `scripts/backfill.py` gains 0050 in its default ticker list (or via one explicit invocation, recorded in §5).

---

## 4. Module specifications

### 4.1 `indicators/core.py`

Pure functions over `pd.Series`. No state, no side effects, no I/O. Each function takes a Series and returns a Series (or tuple of Series) of the same length, NaN-padded at the start to respect `min_periods=window`.

```python
def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average. Value at index T uses close[T-window+1:T+1]."""

def ema(close: pd.Series, window: int) -> pd.Series:
    """Exponential moving average using pandas' adjust=False convention
    (matches standard trading-platform definition)."""

def bollinger_bands(
    close: pd.Series, window: int = 20, k: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (lower, middle, upper). Middle = sma(close, window).
    Bands at middle ± k * rolling_std(window, ddof=0)."""

def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI. Returns values in [0, 100], NaN for the first
    `window` rows."""
```

Implementation notes:
- **Causality is structural.** `rolling(window, min_periods=window).mean()` at index T uses values from T-window+1 to T inclusive. It never uses T+1 or later. This is the foundation of lookahead safety in the indicator layer.
- **`min_periods=window`** (not the pandas default of 1). Return NaN until the warmup window is filled. The engine ignores NaN signal rows.
- **No `fillna` inside indicators.** Callers decide how to handle warmup NaNs.
- **Type signatures**: input is `pd.Series` with a `DatetimeIndex` and `float64` dtype; output matches the input's index. Reject other inputs with `TypeError`.
- **No `Phase 2 ui/charts.py` import.** The throwaway `compute_sma` / `compute_bollinger` in the dashboard stays in place; Phase 3 builds these from scratch per the ADR.

### 4.2 `backtest/strategy.py`

The contract every strategy implements. Lives at the top of the package because Phase 4 imports it, Phase 5 wraps it, and Phase 6 inherits it.

```python
class Strategy(ABC):
    """Abstract strategy interface.

    A strategy maps OHLCV bars to target positions. Signals at bar T
    must be computable using only bars[:T+1] (data up to and including T).
    Execution is performed by the Engine at bar T+1 open.
    """

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        """Given a date-indexed OHLCV DataFrame, return a Series of
        target positions in {-1, 0, +1} indexed by the same dates.
        NaN means 'no signal — preserve previous state'.

        Implementations must use only causal operations. Any use of
        bars.iloc[T+k] for k > 0 to compute signal[T] is lookahead bias.
        """

    @property
    def name(self) -> str:
        """Human-readable name for tear sheet labeling. Default: class name."""
        return type(self).__name__


class BuyAndHold(Strategy):
    """Reference strategy: enter at bar 0 (executed at bar 1 open) and
    hold until end. Used for the 0050 benchmark and as a smoke test."""

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        signals = pd.Series(dtype=float, index=bars.index)  # all NaN = preserve state
        signals.iloc[0] = 1
        return signals
```

Implementation notes:
- **Single-ticker scope for v1.** `generate_signals` takes one ticker's bars and returns one Series. Multi-asset / portfolio strategies (e.g. top-N momentum across the universe) are a Phase 4 extension; the engine loop handles per-ticker invocation for v1.
- **Signal semantics in v1:** +1 = full long position (size determined by engine's `max_position_pct`); 0 = flat; -1 = short (raised to the engine but for v1 the engine rejects short orders unless `allow_short=True`). NaN = preserve previous state. Position changes are state transitions; the engine diffs successive signals to derive orders.
- **`0` and `NaN` are not interchangeable.** `0` is an instruction — "go flat; close this position." `NaN` is silence — "I have no opinion; leave the position alone." A `BuyAndHold` that emits `[1, 0, 0, …]` would enter at bar 0 and immediately sell at bar 2's open, because the engine sees a long→flat transition at bar 1. The correct implementation emits `[1, NaN, NaN, …]`: enter once, then hands off.
- **Long-only by default.** Engine constructor takes `allow_short: bool = False`. Short signals on a long-only engine become 0 with a warning logged once.

### 4.3 `backtest/cost_model.py`

Taiwan-specific. Tax rates and the commission cap are statutory; the commission discount, minimum, and slippage are configurable. All rates and rules verified against primary sources (Securities Transaction Tax Act / 證券交易稅條例, Securities and Exchange Act / 證券交易法, TWSE and major broker references); citations embedded in the module docstring.

```python
@dataclass(frozen=True)
class TaiwanCostModel:
    """Cost model for Taiwan equities.

    All rates verified against primary sources (May 2026):

    - Securities transaction tax: Securities Transaction Tax Act Art. 2
      (https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340078).
      0.3% on sells of stocks (Art. 2(1), 千分之三),
      0.1% on sells of ETFs incl. 0050 (Art. 2(2), 千分之一).
      Sell-side only; buys are tax-free. Statutory, not negotiable.

    - Broker commission: Securities and Exchange Act Art. 85 sets a
      0.1425% per-side statutory maximum. Brokers may discount freely;
      the durable online-order baseline across major Taiwanese brokers
      (Fubon 富邦, Yuanta 元大, Cathay 國泰) is 6折 = 0.0855% effective.
      Stored as cap × discount so promotional rates (e.g. 1.8折 for
      Fubon new-account offers) are modeled by overriding the discount.

    - Minimum commission: broker-set, conventionally NT$20 per order for
      round-lot trades. Many brokers cut this to NT$1 for odd-lot orders.

    - Slippage: modeling assumption, 5 bps per side. Brackets a typical
      1-tick half-spread on TWSE Top-50 names (2.5–12.5 bps depending
      on price tier per the TWSE tick-size table).

    Documented simplifications (NOT modeled in Phase 3, see §9 Pitfalls):
    - T+2 cash settlement (普通交割). The Phase 3 engine treats cash as
      immediately available post-trade. Real settlement is two business
      days later, which can bind on high-turnover or fully-invested
      strategies. Phase 5 paper-sim will model this explicitly.
    - Day-trading tax halving (0.15% per Art. 2-2, extended through
      2027-12-31). Not relevant to a long-only daily-bar strategy.
    - Odd-lot trading rules (separate auction schedule, no margin/short,
      T+1-earliest sell of odd-lot buys). Phase 3 assumes round-lot
      trades. The odd_lot_commission_min_twd field is exposed on the
      dataclass for forward-compat with Phase 5 but is unused in v1.
    - TWSE exchange handling fee (0.0065%, Art. 132). Paid by the broker
      out of commission revenue, not separately billed to the investor;
      already inside `commission_cap`.
    """

    # Securities transaction tax (statutory, asymmetric, always on)
    stock_sell_tax: float = 0.003        # Art. 2(1), 千分之三
    etf_sell_tax: float = 0.001          # Art. 2(2), 千分之一

    # Broker commission (statutory cap × broker discount)
    commission_cap: float = 0.001425     # statutory maximum, both sides
    commission_discount: float = 0.6     # 6折 durable online baseline
    commission_min_twd: int = 20         # round-lot per-order minimum
    odd_lot_commission_min_twd: int = 1  # odd-lot minimum (Phase 5+)

    # Slippage (modeling assumption)
    slippage_bps_per_side: float = 5.0

    # ETF classification (drives tax-rate selection)
    etf_tickers: frozenset[str] = frozenset({"0050"})

    # Documentation metadata (not used in calculation)
    settlement: str = "T+2"

    @property
    def commission_pct(self) -> float:
        """Effective commission rate per side = cap × discount."""
        return self.commission_cap * self.commission_discount

    def apply_slippage(self, reference_price: float, side: str) -> float:
        """Return the worse-side fill price for buy/sell."""

    def buy_costs(
        self, ticker: str, shares: int, reference_price: float,
    ) -> tuple[float, float]:
        """Returns (fill_price_after_slippage, commission_paid).
        Commission = max(notional × commission_pct, commission_min_twd).
        No tax on buys."""

    def sell_costs(
        self, ticker: str, shares: int, reference_price: float,
    ) -> tuple[float, float, float]:
        """Returns (fill_price_after_slippage, commission_paid, tax_paid).
        Tax rate = etf_sell_tax if ticker in etf_tickers else stock_sell_tax.
        Commission = max(notional × commission_pct, commission_min_twd)."""
```

Implementation notes:

- **Always on.** There is no `cost_model=None` path. A free backtest is a lie.
- **Asymmetric tax, structurally.** Tax is sell-side only — buys never pay tax. The `buy_costs` return signature has no tax field for this reason; conflating buy and sell cost calculations is exactly the class of bug that's worth preventing in the type signature, not just in tests.
- **Cap × discount, not effective rate.** The dataclass stores the statutory cap (`0.001425`) and the broker discount (`0.6`) separately, with `commission_pct` as a derived property. This is the structure most useful for honest engineering: the cap is law, the discount is market practice, and conflating them in a single number hides which is which. Promotional rates are modeled by overriding `commission_discount` (e.g., `0.18` for Fubon's 1.8折 new-account offer).
- **Minimum commission, always applied.** `commission = max(notional × commission_pct, commission_min_twd)`. At a NT$100k position size (10% of NT$1M capital) the percentage rate produces NT$85.5 commission, comfortably above NT$20 — so the minimum rarely binds in normal operation. It does bind on small partial closes and trim trades, so it's modeled rather than dropped.
- **Odd-lot fields exposed but unused in v1.** Phase 3 assumes round-lot trades only; `odd_lot_commission_min_twd = 1` is included for forward-compat with Phase 5's paper-sim, which will introduce lot-size routing logic. Adding the field now and leaving it dormant is cheaper than a future migration.
- **Day-trading tax discount** (現股當沖, 0.15% under Art. 2-2, sunset 2027-12-31) is not modeled. Daily long-only strategies don't day-trade by definition, and ETFs don't qualify regardless.
- **TWSE exchange handling fee (0.0065%, 經手費)** is paid by the broker out of its commission revenue under Securities and Exchange Act Art. 132, not separately billed to the investor. Already inside `commission_cap`; not modeled as a separate cost line.
- **Settlement as metadata.** `settlement = "T+2"` is a frozen documentation string, not used in calculation. The Phase 3 engine treats cash as immediately available post-trade. Phase 5's paper-sim will model T+2 explicitly. Documented in §9 Pitfalls.
- **Equality and hashability.** `frozen=True` and `frozenset` for `etf_tickers` make the model hashable for caching purposes downstream.

### 4.4 `backtest/portfolio.py`

Tracks positions, cash, equity. Stateful; one instance per backtest run. Lives below the engine in the dependency graph — the engine instantiates and drives it.

```python
@dataclass
class Position:
    ticker: str
    shares: int
    avg_cost: float           # cost basis per share, post-slippage, post-commission

@dataclass
class Trade:
    timestamp: pd.Timestamp
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    fill_price: float
    commission: float
    tax: float
    notional: float           # shares * fill_price
    total_cost: float         # commission + tax + slippage_cost_implied

class Portfolio:
    def __init__(self, initial_capital: float, cost_model: TaiwanCostModel): ...

    def buy(self, ticker: str, shares: int, reference_price: float,
            timestamp: pd.Timestamp) -> Trade: ...

    def sell(self, ticker: str, shares: int, reference_price: float,
             timestamp: pd.Timestamp) -> Trade: ...

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """Update positions' marks, return current total equity
        (cash + sum of position values at given prices)."""

    @property
    def cash(self) -> float: ...

    @property
    def equity(self) -> float: ...

    @property
    def positions(self) -> dict[str, Position]: ...
```

Implementation notes:
- **Integer shares only.** Taiwan trades in lots of 1000 shares (`張`) traditionally, but odd-lot trading (`零股`) is now common. We allow any integer share count, defer lot-size rules to Phase 5 paper-sim (more realistic) and Phase 8 live broker (regulator-defined).
- **Cash can go negative on a buy** if `engine.max_position_pct` is misconfigured. The engine validates before placing the order; portfolio raises `InsufficientCashError` defensively.
- **No margin, no shorts, no derivatives in v1.** Engine enforces.
- **Equity curve is the daily mark-to-market series**, indexed by trading day. Built by the engine, not the portfolio (portfolio doesn't know what "today" is; engine drives the clock).

### 4.5 `backtest/engine.py`

The lookahead-safety enforcer. The single source of truth for execution timing.

```python
class Engine:
    def __init__(
        self,
        capital: float,
        cost_model: TaiwanCostModel,
        max_position_pct: float = 0.10,
        allow_short: bool = False,
        execution: Literal["next_open"] = "next_open",
    ): ...

    def run(
        self,
        strategy: Strategy,
        tickers: list[str],
        start: date,
        end: date,
    ) -> BacktestResult:
        """Run `strategy` on `tickers` from `start` to `end`.

        Algorithm:
        1. For each ticker, load adjusted bars via get_adjusted_history.
        2. For each ticker, call strategy.generate_signals(bars) ONCE
           upfront. The strategy receives the full bars frame; lookahead
           safety is the strategy author's responsibility (audited by
           backtest.audit).
        3. Walk the union of trading days chronologically. At each
           day T:
           a. Diff signal[T] vs current position to derive desired orders.
           b. Schedule those orders for execution at T+1's open.
           c. Execute orders scheduled at T (from T-1's signals) using
              T's open as the reference price.
           d. Mark-to-market at T's close. Append to equity curve.
        4. On the final day, force-close all positions at the last
           available open (or close if no next bar exists; document choice).
        5. Build BacktestResult and return.
        """
```

Implementation notes:
- **Strategy is called once with the full bars frame.** The contract is "you must use only causal operations." The engine does not slice per-bar (too slow). The `audit.assert_no_lookahead` utility validates by perturbing future data.
- **Single-ticker iteration in v1.** For multi-ticker strategies, the engine loops tickers, runs each independently, and aggregates results. Portfolio-level constraints (max position pct against total equity) are enforced at allocation time.
- **The "final day close-out"** is a deliberate choice — open positions at end-of-backtest are realized at the next available open. If `end` is the last bar in the data, force-close at the same day's close (with a documented small lookahead allowance for the final bar only). Tear-sheet labels this clearly.
- **Empty / short-history tickers.** If `get_adjusted_history(ticker, start, end)` returns <2 rows, log a warning and skip the ticker (don't crash). Tests verify this for 6919 and 7769.
- **Trading calendar.** Use `exchange_calendars` XTAI to identify trading days. The bars frame already excludes non-trading days via Phase 1's storage layer, so this is belt-and-suspenders.

### 4.6 `backtest/result.py`

Data container. No methods beyond convenience accessors; `tear_sheet()` lives in `tearsheet.py` and takes a `BacktestResult` as input (keeps result.py dependency-light).

```python
@dataclass(frozen=True)
class BacktestResult:
    strategy_name: str
    tickers: list[str]
    start: date
    end: date
    initial_capital: float
    final_equity: float
    equity_curve: pd.Series          # daily, indexed by trading day
    trades: pd.DataFrame             # one row per trade
    positions_daily: pd.DataFrame    # daily positions, indexed by date × ticker
    total_commission: float
    total_tax: float
    total_slippage_cost: float       # estimated; (fill - mid) × shares summed
```

Implementation notes:
- **This artifact is a Phase 3 design requirement, not an afterthought (D10).** Its completeness — equity curve *and* full trade ledger *and* daily positions *and* cost totals — is what makes every downstream report a pure function over the result. `tear_sheet()` (§4.7) takes a `BacktestResult` and returns metrics; it never reaches into the engine. Future analyzers (rolling-Sharpe plots, per-trade attribution, exposure heatmaps) are added the same way, with no engine change. This is the "relocated, not lost, extensibility" argument from `journal/phase_3_references.md`.
- **"Well-typed" means the container is typed and the frame schemas are documented.** `equity_curve` is a `pd.Series` (daily `DatetimeIndex`, `float64`). `trades` and `positions_daily` are DataFrames with a fixed, documented column contract — the `Trade` field set for `trades`; `date × ticker × {shares, avg_cost, market_value}` for `positions_daily`. The columns are part of the contract that Phases 4–6 depend on, so a column rename is a breaking change, treated the same as a method-signature change.
- **Frozen.** `frozen=True`; a result is the immutable record of one run. Re-running produces a new result.
- **Open (revisit at Phase 6): typed ledger vs DataFrame.** `trades`/`positions_daily` are DataFrames — the right shape for vectorized tear-sheet metrics, but it leaves "well-typed" resting on a documented column contract rather than the type system. If Phase 6's analyzers or feature pipeline want stronger guarantees, the options are a pandera schema on the two frames (consistent with the data-layer discipline in `CLAUDE.md §3`) or a `list[Trade]` ledger with a DataFrame view. Deliberately deferred — not worth the conversion cost for a single-run artifact in Phase 3. Origin: `journal/phase_3_references.md`, "well-typed run-result object."

### 4.7 `backtest/tearsheet.py`

Pure functions over `BacktestResult`. No state, no I/O.

```python
def tear_sheet(result: BacktestResult, benchmark: BacktestResult | None = None) -> dict:
    """Return a dict of metrics. Keys:
       total_return, cagr, sharpe, sortino, max_drawdown,
       max_drawdown_duration_days, hit_rate, avg_win, avg_loss,
       win_loss_ratio, turnover, total_fees, n_trades.
       If benchmark provided: alpha, beta, tracking_error, info_ratio."""

def equity_curve_figure(result: BacktestResult,
                        benchmark: BacktestResult | None = None) -> go.Figure:
    """Plotly figure: equity curve, drawdown subplot below."""

def trades_table(result: BacktestResult) -> pd.DataFrame:
    """Pretty-printed trades table for journal writeups."""
```

Implementation notes:
- **Sharpe annualization:** 252 trading days (Taiwan standard). Use daily log returns; multiply mean by 252 and std by √252.
- **Sortino:** same, but only downside std (target return = 0). Document this convention.
- **Max drawdown:** `(running_max - equity) / running_max`. Peak-to-trough percentage, reported as a positive number (a 30% drawdown is `0.30`, not `-0.30`).
- **Hit rate / avg win / avg loss:** computed per round-trip (buy → sell → close). Strategies that go from long to flat to long count as two trades; long to short is one trade if shorts allowed.
- **Turnover:** annualized fraction of equity traded. Sum of all trade notionals ÷ average equity ÷ years.
- **Survivorship bias caveat:** the tear sheet's `__repr__` includes a one-line note: "Universe is a 2026-05-08 snapshot; survivorship bias not corrected." Always visible.

### 4.8 `backtest/walkforward.py`

Configurable iterator. Phase 3 uses it minimally; Phase 6 uses it heavily.

```python
@dataclass
class WalkForwardSplit:
    train_years: int = 3
    test_years: int = 1
    step_years: int = 1
    anchored: bool = False

    def split(
        self, start: date, end: date,
    ) -> Iterator[tuple[date, date, date, date]]:
        """Yield (train_start, train_end, test_start, test_end) tuples.

        Rolling (anchored=False): train window slides forward by step_years.
        Anchored (anchored=True): train_start is fixed; train_end grows.

        Test windows are non-overlapping by default (step == test_years).
        Setting step < test_years produces overlapping test windows, useful
        for diagnostic plots but not for fold aggregation.
        """
```

Implementation notes:
- **Date arithmetic uses calendar years**, not trading days. A 3-year training window is `train_end - relativedelta(years=3)`. Trading-day-aware splits are a Phase 6 enhancement if needed.
- **Validates that** `end - start >= train_years + test_years` before yielding anything. Raises `ValueError` with a helpful message otherwise.
- **Yields `date` tuples**, not slices into a DataFrame. Caller is responsible for the slice; the iterator is data-independent.

### 4.9 `backtest/audit.py`

The lookahead-bias detector.

```python
def assert_no_lookahead(
    strategy: Strategy,
    bars: pd.DataFrame,
    perturbation_start: int = -50,
    tolerance: float = 1e-9,
) -> None:
    """Run strategy.generate_signals(bars) and compare to running it
    on a perturbed copy where bars after index `perturbation_start`
    have prices scrambled. The first len(bars) + perturbation_start
    signals must be identical between the two runs.

    Raises LookaheadError with a diff if any signal differs.
    """
```

Implementation notes:
- Used in tests (`test_engine_lookahead.py`) and as a CLI utility.
- The Phase 3 reference strategy (SMA crossover) must pass this audit.
- Phase 6 ML strategies will use this in their CI tests; cheap to leave in place.

### 4.10 `strategies/sma_crossover.py`

The reference strategy. Trivial, but exercises every layer of the engine.

```python
class SmaCrossover(Strategy):
    """Long when SMA(fast) > SMA(slow), flat otherwise.

    Signal at T uses SMA values computed on close[:T+1] (causal).
    Engine executes the position change at T+1's open.
    """

    def __init__(self, fast: int = 20, slow: int = 60):
        if fast >= slow:
            raise ValueError("fast must be strictly less than slow")
        self.fast = fast
        self.slow = slow

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        from twstock.indicators.core import sma
        fast_ma = sma(bars["close"], self.fast)
        slow_ma = sma(bars["close"], self.slow)
        return (fast_ma > slow_ma).astype(int)
```

Note: this is the only strategy file built in Phase 3. Other strategies (EMA crossover, RSI mean-reversion, etc.) are Phase 4.

---

## 5. Build sequence

Build in this order. Each step is a single commit following the convention in `CLAUDE.md §9`. Steps 0 and 1 are documentation commits that ground every subsequent step in a documented rationale before code starts; steps 2–14 are the engine build itself; steps 15–17 close out the phase.

### Pre-code (documentation)

0. **`docs: add phase 3 reference study notes`** — Spend ~half a day reading the four backtrader files (`cerebro.py`, `strategy.py`, `broker.py`, `order.py`) plus skimming vectorbt's portfolio API, indicator factory, and metrics code. Capture observations in `journal/phase_3_references.md` as a draft: what each library does in those areas, what is worth borrowing conceptually, what we reject. This file is finalized at step 15 with retrospective insights once the engine is built. Commit the draft.

1. **`docs: add planning ADR for custom backtester`** — Add `journal/decisions/0004-custom-backtester-rejecting-vectorbt.md` (drafted in the planning workspace, pasted in here). Locks D1 in the repo before any engine code is written.

### Engine build

2. **`chore: scaffold indicators, backtest, strategies modules`** — Create empty `src/twstock/{indicators,backtest,strategies}/__init__.py`. Create empty test directories. Verify `uv run pytest` still passes.

3. **`feat(indicators): add sma, ema, bollinger, rsi`** — `indicators/core.py` with the four functions. `test_core.py` covers each against hand-computed references on 30-row inputs. Verify NaN warmup behavior.

4. **`feat(backtest): add Strategy ABC and BuyAndHold`** — `backtest/strategy.py`. `test_strategy.py` verifies `BuyAndHold` produces the expected signal pattern.

5. **`feat(backtest): add TaiwanCostModel`** — `backtest/cost_model.py`. `test_cost_model.py` covers stock vs ETF sell tax, commission both sides, slippage direction, and the 0050 ETF case explicitly.

6. **`feat(backtest): add Portfolio`** — `backtest/portfolio.py`. `test_portfolio.py` covers buy/sell sequences, cash accounting, mark-to-market, position closing.

7. **`chore(data): backfill 0050 as benchmark instrument`** — Run `uv run python scripts/backfill.py --tickers 0050`. Update `scripts/backfill.py` default ticker list to include 0050 going forward. Parquet stays gitignored; the change committed here is the script edit.

8. **`feat(backtest): add Engine with T+1 execution`** — `backtest/engine.py` and `backtest/result.py`. `test_engine.py` covers the happy path: a 20-row hand-built bars frame, a deterministic strategy, expected trades and equity curve.

9. **`feat(backtest): add lookahead-bias audit utility`** — `backtest/audit.py` and `test_engine_lookahead.py`. Verify `assert_no_lookahead` catches a synthetic lookahead-violating strategy and accepts a synthetic causal one.

10. **`feat(strategies): add SmaCrossover reference strategy`** — `strategies/sma_crossover.py`. `test_engine.py` extended with an integration test running SmaCrossover on a 1-year synthetic bars frame; verify net return < gross return (costs working) and that audit passes.

11. **`feat(backtest): add tear sheet metrics and figures`** — `backtest/tearsheet.py`. `test_tearsheet.py` covers Sharpe, Sortino, max drawdown, hit rate against hand-computed values on a 50-row equity curve.

12. **`feat(backtest): add WalkForwardSplit iterator`** — `backtest/walkforward.py`. `test_walkforward.py` covers anchored vs rolling, edge cases (window larger than data, step ≠ test_years, exact-fit boundaries).

13. **`test(backtest): end-to-end integration test on real 2330 data`** — `test_sma_crossover_integration.py`. Run SmaCrossover on 2330's actual adjusted history (2020–2025), assert: (a) tear sheet returns non-NaN values for all metrics, (b) net return ≠ gross return, (c) all trade fills happened at T+1 opens, (d) audit passes. Marked `@pytest.mark.integration` — depends on local Parquet store.

14. **`feat(backtest): stability report for SMA crossover via WalkForwardSplit`** — A standalone script (`scripts/phase_3_stability_report.py`) that runs SmaCrossover on 2330 with one anchored 3y/1y split, prints per-fold tear sheets, and emits a brief markdown summary into `journal/phase_3_stability_report.md`. This exercises walk-forward end-to-end on real data.

### Close-out (documentation)

15. **`docs: finalize phase 3 references writeup`** — Take the step 0 draft and add retrospective insights: which design choices in the engine were directly informed by what was read, which parts of backtrader's order state machine will be revisited in Phase 5, what was ignored from vectorbt that turned out not to matter. This file is portfolio material; treat the writing accordingly.

16. **`docs: phase 3 retrospective`** — Write `journal/phase_3_retro.md`: wins, slips, surprises, decisions to revisit, prep notes for Phase 4.

17. **`docs: close phase 3 in CLAUDE.md and PROJECT_PLAN.md`** — Update `CLAUDE.md §6` to mark Phase 3 closed with a one-line summary, and `**Last updated**` + `**Current phase**` at the top. Update `PROJECT_PLAN.md` progress tracker (Phase 3 row → ended date). Add a "Run a backtest" section to `README.md` with a minimal code example from §1 of this spec.

**Slip-handling rule.** If step 13 isn't complete by the end of week 2, ship without the end-to-end integration test (defer to Phase 4 kickoff) — but **never** ship without the lookahead audit (step 9) or the cost model (step 5). Those are non-negotiable.

---

## 6. Test strategy

Phase 2 ended at 160 tests in 3.6 seconds. Phase 3 adds ~50–70 unit tests plus 1 integration test. Target: total project suite under 6 seconds (excluding integration).

### 6.1 What gets tested

| File | What it covers |
|---|---|
| `test_core.py` | Each indicator against a hand-computed reference Series on 30 rows. NaN warmup. Type-error rejection. Bollinger middle band exactly equals `sma`. RSI bounded in [0, 100]. |
| `test_strategy.py` | `BuyAndHold` returns 1 at index 0 and NaN for all subsequent bars. Strategy subclass without `generate_signals` raises `TypeError` at instantiation (ABC enforcement). |
| `test_cost_model.py` | Stock sell tax = 0.3% exactly; ETF sell tax = 0.1% for 0050; commission applied both sides; slippage applied in the worse direction; no tax on buys. |
| `test_portfolio.py` | Buy + sell sequence yields expected cash and equity. Insufficient cash raises `InsufficientCashError`. Mark-to-market with mixed positions. Selling shares not held raises. |
| `test_engine.py` | 20-row synthetic bars + deterministic signals produce the expected trades, fills at T+1 opens, equity curve shape. Empty / short-history tickers handled without crash. |
| `test_engine_lookahead.py` | `assert_no_lookahead` catches a strategy that uses `bars["close"].shift(-1)`. Accepts a causal strategy. Reference `SmaCrossover` passes the audit on a 100-row input. |
| `test_result.py` | Trivial: `BacktestResult` constructs cleanly; field types are as declared. |
| `test_tearsheet.py` | Sharpe, Sortino, max drawdown, hit rate against hand-computed reference values on a 50-row equity curve. Empty trade list handled. |
| `test_walkforward.py` | Anchored vs rolling; step < test_years produces overlapping folds; insufficient data raises `ValueError`; exact-fit boundary cases. |
| `test_audit.py` | (Same as test_engine_lookahead from a different angle if needed; otherwise merge.) |
| `test_sma_crossover_integration.py` | End-to-end on real 2330 data. Marked `@pytest.mark.integration`. |

### 6.2 What is explicitly NOT tested

- Plotly figure pixel output.
- Real-time / streaming data (Phase 5).
- Multi-ticker portfolio strategies (Phase 4).
- Live broker integration (Phase 8).
- Performance — the <6 second test suite target is verified manually, not in CI.

### 6.3 Fixture strategy

Inline `pd.DataFrame` construction in each test (10–50 rows). Reuse a small synthetic-bars helper for engine tests (`make_synthetic_bars(n_days, trend, vol, seed)` in `tests/backtest/conftest.py`). No new files under `tests/fixtures/` — the Phase 1 fixture set is for the data layer.

### 6.4 Pre-commit and lint

Existing `ruff` + `pytest` hooks apply unchanged. No new pytest markers needed (`@pytest.mark.integration` already exists).

---

## 7. Lookahead safety and execution model (DETAILED)

The single most important section of this phase. Get this wrong and every result in Phases 4, 6, 7 is meaningless. The protection is layered.

### 7.1 The execution invariant

> **At every point in time T, the only data used to decide what to trade is data with timestamp ≤ T. The fill price for any trade is at timestamp T+1's open.**

That sentence is the entire lookahead contract. Every line of `engine.py` exists to enforce it; every test in `test_engine_lookahead.py` exists to verify it; every guardrail in `CLAUDE.md` exists to prevent code that violates it.

### 7.2 The three layers

**Layer 1: indicator causality.** `rolling(window, min_periods=window).mean()` at index T uses values from T-window+1 to T inclusive. Never future. This is structural — pandas does the right thing. The indicator module never calls `.shift(-k)` for any positive k.

**Layer 2: strategy causality.** Strategies must not use `bars.iloc[T+k]` to compute `signal[T]`. The engine cannot prevent the strategy author from writing buggy code, but it can catch it after the fact. `assert_no_lookahead` does this by perturbing future data and verifying past signals are unchanged.

**Layer 3: execution timing.** Even with perfectly causal signals, executing at the same bar's close is a form of lookahead — at T's close, you can compute signal[T] but you cannot also execute at T's close without using data you didn't have when the signal fired. The engine enforces this by holding orders scheduled at T and only filling them at T+1's open price.

### 7.3 What this means in code

```python
# CORRECT: signal at T uses close[T] (known at end-of-day T),
# fill at T+1 open.
fast = sma(bars["close"], 20)    # fast[T] uses close[T-19..T]
slow = sma(bars["close"], 60)
signal = (fast > slow).astype(int)
# Engine executes signal[T] at bars["open"][T+1].

# WRONG: signal uses close[T+1].
signal = (bars["close"].shift(-1) > bars["open"]).astype(int)
# This is lookahead. Caught by assert_no_lookahead.

# ALSO WRONG: signal uses close[T], engine fills at open[T] same bar.
# Even though the indicator is causal, the execution is impossible —
# at open[T] you don't yet know close[T]. The engine structurally
# rules this out by always filling at T+1's open.
```

### 7.4 Why the engine doesn't slice per-bar

A simpler design would have the engine call `strategy.generate_signals(bars.loc[:T])` once per bar T. That's O(n²) and slow, but it makes lookahead impossible by construction at the engine layer.

We chose the faster design (call once with full bars, audit after) because (a) the strategy contract is explicit about causality, (b) the audit utility provides verification, and (c) Phase 6 ML strategies will need access to the full window anyway for refitting decisions. Honest tradeoff: a small amount of trust placed in the strategy author, in exchange for substantial speed and a cleaner Phase 6 integration.

### 7.5 The final-bar edge case

At the last bar of the backtest, there is no T+1. Open positions must be closed. Three options:

1. Force-close at the last bar's open (consistent with the T+1 rule, but means the strategy doesn't get to react to the final bar's signal).
2. Force-close at the last bar's close (allows the final signal to fire, but introduces a small same-bar execution at the boundary).
3. Don't close — report unrealized P&L separately.

We choose option 1: force-close at the last bar's open, applying full costs. Open positions on the final bar contribute their entry-to-final-open P&L. The tear sheet labels this clearly. This is the only place same-bar execution would have been tempting, and we explicitly avoid it.

**Pending orders are dropped on the final day, not executed.** If a signal at T-1 scheduled a buy for T and T turns out to be the last bar, that buy order is cleared without executing before the force-close runs. The alternative — execute the buy then immediately force-close at the same open — would be economically meaningless (net zero position change, double cost) and misleading in the trade ledger. Dropping pending orders on the final day is the correct choice.

**Stale orders for non-trading tickers are also dropped.** If a pending order from T-1 cannot be filled at T's open because the ticker has no data on T (suspended trading, holiday gap, etc.), the order is dropped rather than carried forward to T+1. Carrying stale orders risks filling at a price the signal never intended. Dropping is safe because: (a) the signal already moved `last_signal` to the new state, and (b) if the signal is still valid at T+1, the strategy will re-issue it at that bar's signal read. For the TWSE Top 50, suspension gaps are rare in practice.

**Tickers with no open price on the last day are not force-closed.** Their positions remain, mark-to-market uses the most recent stale close price from `last_close`, and the unrealized P&L is included in `final_equity`. This is a known limitation; Phase 5's paper-sim will handle it explicitly with exchange calendar lookups.

### 7.6 Cost-model symmetry

The 0.3% sell tax (or 0.1% for ETFs) is asymmetric — it applies only to sells. This is critical for round-trip economics: a round-trip on a stock pays commission twice (~0.185%) plus tax once (0.3%) plus slippage twice (0.10%), for a total of ~0.585% per round trip. A strategy that turns over its portfolio 100 times a year pays ~58.5% in costs annually. This is not a typo. Most retail strategies die at this hurdle, and that is the point — backtests that ignore the asymmetric tax are systematically optimistic.

---

## 8. Exit criteria

Phase 3 is done when **all** of the following hold:

- [ ] `from twstock.backtest import Engine, TaiwanCostModel` and `from twstock.strategies.sma_crossover import SmaCrossover` import without error
- [ ] Running `SmaCrossover(fast=20, slow=60)` on 2330 for 2020-01-01 to 2025-12-31 produces a tear sheet with non-NaN values for: `total_return`, `cagr`, `sharpe`, `sortino`, `max_drawdown`, `hit_rate`, `turnover`, `total_fees`, `n_trades`
- [ ] Running `BuyAndHold()` on 0050 for the same period produces a benchmark tear sheet
- [ ] For the SMA crossover, `gross_return > net_return` (costs are applied non-trivially)
- [ ] All trade fills in the trade log occurred at T+1's open relative to the corresponding signal bar (verified by audit)
- [ ] `BacktestResult.positions_daily` is non-empty and indexed by date × ticker for the 2330 run (run-result artifact complete per D10)
- [ ] `assert_no_lookahead(SmaCrossover(), bars)` passes for a 252-row sample of 2330
- [ ] Short-history tickers (6919, 7769) handled without crash when included in a multi-ticker run
- [ ] `WalkForwardSplit(train_years=3, test_years=1, anchored=False)` yields ≥3 folds on 2016–2026 and the SMA stability report (`journal/phase_3_stability_report.md`) is written
- [ ] `uv run pytest tests/indicators tests/backtest` passes; total project test suite (excluding integration) runs in <6 seconds
- [ ] `uv run pytest -m integration` passes (includes the 2330 end-to-end test)
- [ ] Pre-commit clean on a fresh clone
- [ ] All commits in the Phase 3 range follow the convention in `CLAUDE.md §9`
- [ ] One ADR written: `journal/decisions/0004-custom-backtester-rejecting-vectorbt.md` recording D1
- [ ] One references writeup: `journal/phase_3_references.md` documenting what was studied in vectorbt and backtrader and what was borrowed
- [ ] `CLAUDE.md` updated with: (a) D1–D9 in §2, (b) new modules in §4, (c) Phase 3 guardrails in §7, (d) any new commands in §10
- [ ] `README.md` has a "Run a backtest" section with a minimal code example
- [ ] `journal/phase_3_retro.md` written: wins, slips, surprises, decisions to revisit, prep notes for Phase 4
- [ ] `PROJECT_PLAN.md` progress tracker updated (Phase 3 row → ended date)

---

## 9. Pitfalls and known gaps

**Lookahead via `.shift(-k)`.** The single most common backtester bug. Any indicator or feature that uses `.shift(-1)` or `.shift(-k)` for positive `k` is wrong. Any strategy that does likewise is wrong. The audit utility catches this, but writing the buggy code in the first place is what we want to avoid. Code review check: search for `shift(-` in any new file before merging.

**Same-bar execution.** The temptation to "just fill at today's close since we computed the signal on today's close" is enormous and wrong. The engine's `execution="next_open"` is hard-coded for v1; there is no other option in the constructor's `Literal`. If a future phase needs same-bar execution (it shouldn't), it requires an ADR.

**Forgetting the 0.3% sell tax.** The biggest single source of backtest inflation in retail Taiwan-stock projects. `TaiwanCostModel` defaults to it on; do not provide a `tax_pct=0` path.

**T+2 settlement is not modeled.** Real Taiwan equities settle T+2 — sells provide cash two business days after the trade date, not the same day. The Phase 3 engine treats cash as immediately available post-trade. This is acceptable for daily-bar paper backtests of low-turnover strategies (the SMA crossover never gets close to fully invested, and the 10% per-position cap keeps a wide cash buffer), but it would silently overstate available capital on a high-turnover or fully-invested strategy. Documented in `TaiwanCostModel.settlement` as metadata; explicitly modeled in Phase 5 paper-sim.

**Short-history tickers.** 6919 (listed 2024-10-02) and 7769 (listed 2025-11-27) will return near-empty DataFrames for backtests spanning 2016–2024. The engine must skip with a logged warning, not crash. Tested in `test_engine.py`.

**Survivorship bias.** The universe is a 2026-05-08 snapshot. Backtesting from 2016 is fishing in a pond that didn't exist yet. Every tear sheet's `__repr__` notes this in plain language. Documented in journal; not fixed in Phase 3.

**0050 is not in the universe list.** `universe.get_top50_universe()` returns the 50 constituents, not 0050 itself. The benchmark uses 0050 directly via `get_adjusted_history("0050", ...)`. Anyone reading the code may be momentarily confused; the docstrings should be explicit.

**Sharpe annualization conventions.** 252 trading days for Taiwan. Some libraries default to 365 (calendar days) or 252 (US convention). We use 252 explicitly. Drift here makes cross-comparison with other public results misleading.

**The `Position.avg_cost`** is *post-slippage and post-commission* — i.e. the all-in cost basis per share. This is the realistic answer (you really did pay that much) but differs from some literature conventions that report fill price separately from costs. Document in the docstring.

**`Strategy.generate_signals` returns NaN for warmup rows.** The engine must skip NaN signal rows (treat as "no change"), not interpret them as 0. This is tested in `test_engine.py`.

**Mutation of `bars` inside `generate_signals`.** Tempting to do `bars["sma_20"] = sma(...)`. Don't — `bars` is the engine's reference copy. Strategies must either work with local variables or call `bars.copy()` first.

**Open follow-ups from prior phases that may surface in Phase 3:**
- `FU-03` (FinMind `get_corporate_actions` ignores `end` param) — may bite if Phase 3 calls `get_corporate_actions` with a real end date. Audit at integration-test time.

---

## 10. Out of scope (explicit)

To prevent scope creep, the following are **not** Phase 3:

- Multi-asset portfolio strategies (cross-sectional top-N momentum, pairs, etc.) — Phase 4
- Multi-strategy ensembles or signal blending — Phase 4
- Limit orders, stop orders, OCO, brackets — Phase 5
- Partial fills, rejections, order state machines — Phase 5
- Real-time / paper / live execution — Phase 5 / 8
- ML strategies, feature engineering pipelines — Phase 6
- Alternative data ingestion — Phase 7
- Multi-leg / spread / options — never within this project
- Intraday bars — Phase 9+
- Universe expansion beyond Top 50 + 0050 — Phase 9+
- Day-trading tax discount modeling — out of scope (we don't day-trade)
- Lot-size constraints (`張` vs `零股`) — Phase 5 paper-sim
- Margin / short selling enforcement beyond the boolean toggle — Phase 5
- Volume-based slippage — Phase 9+ if universe expands
- An `import-linter` config enforcing D9 — Phase 8 ticket
- A Streamlit UI for running backtests — Phase 8 ticket (the existing dashboard stays read-only)

If you find yourself building any of these mid-phase, stop, write a one-line ticket in `journal/phase_8_backlog.md`, and move on.

---

## 11. New dependencies

| Package | Why |
|---|---|
| (none required) | The indicator and backtest modules use only `pandas`, `numpy`, `plotly`, and stdlib. All already in `pyproject.toml`. |

**Studied but not added:** `vectorbt` and `backtrader`. Notes recorded in `journal/phase_3_references.md`. The decision to reject both as dependencies is captured in an ADR (see §8 exit criteria).

---

## 12. Kickoff checklist

Before pasting this into Claude Code, confirm:

- [ ] Phase 2 is fully complete (`PROJECT_PLAN.md §6` shows Phase 2 ended; `journal/phase_2_retro.md` exists)
- [ ] All 50 tickers' Parquet files are present in `data/by_ticker/` and `data/corporate_actions/`
- [ ] `quality.check_all()` reports `ok=True` for all 50 tickers (re-run if in doubt)
- [ ] `CLAUDE.md` has been replaced with the Phase 3 version produced in the planning workspace (covers §2 locked decisions, §3 conventions including the new `backtest/` boundary, §4 module list, §7 guardrails, §10 commands)
- [ ] The ADR rejecting vectorbt (`journal/decisions/0004-custom-backtester-rejecting-vectorbt.md`) has been drafted in the planning workspace and is ready to paste in at step 1
- [ ] You have ~half a day set aside for step 0 (targeted reading on vectorbt and backtrader) before any code is written
- [ ] You have ~2 uninterrupted weeks of bandwidth (or willingness to ship without the end-to-end integration test per the slip rule)
- [ ] You have read this spec end-to-end and have no open questions
