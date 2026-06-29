# Phase 5 — Paper-Trading Simulator

> **Purpose.** Build the paper-trading layer that takes a Phase 4 strategy's signals and simulates execution with realistic Taiwan-market frictions — partial fills, rejections, costs, T+2 settlement, corporate actions — as a **pessimistic configuration of one shared execution core** (the backtester is the frictionless configuration of the same core). The deliverable's value is execution realism made *honest*: the simulator is deliberately pessimistic, its correctness is established by **independent oracles** (never by reproducing the backtester), and the gap between idealized backtest and realistic simulation is **attributed**, not hidden. Architectural rationale lives in ADRs 0008/0009/0010; this spec is the *how* and the *order*.

**Status:** Ready to start (spec finalized)
**Last updated:** 2026-06-27
**Estimated duration:** ~2–3 weeks (deeper than the original 1–2 week outline — the execution-realism layer is a full event-sourced engine)
**Predecessor:** Phase 4 (complete, closed 2026-06-22)
**Governing ADRs:** `0008` (shared execution core: configurations over `(settlement, fill, feed)`; correctness via oracles; reconciliation as attribution), `0009` (settlement modes + dated ledger; proportional cash allocation), `0010` (order lifecycle, event-stream `BrokerBase`, fill model, TIF, rejection taxonomy). **Inherited:** `0004` (stateless `Strategy`, lookahead layering), `0005` (instant-settlement baseline = the backtester control), `0006` (portfolio runner: allocator/cadence/tiebreak/reconciler), `0007` (corporate-action adjustment).

---

## 1. Goal

After Phase 5, the following runs: a Phase 4 strategy evaluated through the paper-trading simulator under a pessimistic execution preset over multi-year historical replay, reconciled against the frictionless backtester, plus a short live-data connectivity smoke test.

```python
from datetime import date
from twstock.broker import PaperSession, BrokerSimulator, VolumeParticipationFiller
from twstock.broker.settlement import NettingSettlement, SettledCashOnly
from twstock.broker.feed import ReplayFeed, LiveFeed
from twstock.broker.reconcile import reconcile
from twstock.backtest import Engine, TaiwanCostModel
from twstock.backtest.allocator import EqualWeightTopN
from twstock.backtest.rebalance import Monthly
from twstock.strategies.sma_crossover import SmaCrossover
from twstock.strategies.cross_sectional_momentum import CrossSectionalMomentum
from twstock.universe import get_top50_universe

# The simulator is the shared execution core under a PESSIMISTIC preset:
#   pessimistic fill model + netting (T+2) settlement + a bar feed.
broker = BrokerSimulator(
    capital=1_000_000,
    cost_model=TaiwanCostModel(),
    fill_model=VolumeParticipationFiller(participation_rate=0.05),  # trailing ADV as of T
    settlement=NettingSettlement(),                                 # dated T+2 ledger
    max_position_pct=0.10,
)
session = PaperSession(broker=broker)

# Primary gate: multi-year historical replay (single-asset)
replay = session.run(
    strategy=SmaCrossover(fast=20, slow=60),
    tickers=["2330"],
    feed=ReplayFeed(start=date(2021, 1, 1), end=date(2025, 12, 31)),
)

# Cross-sectional, run in BOTH settlement modes (canonical netting + the pessimism mode)
momentum = session.run(
    strategy=CrossSectionalMomentum(lookback=252, skip=21),
    tickers=get_top50_universe(),
    allocator=EqualWeightTopN(n=10), schedule=Monthly(),
    feed=ReplayFeed(start=date(2021, 1, 1), end=date(2025, 12, 31)),
)
momentum_settled = session.with_settlement(SettledCashOnly()).run(  # quantifies float reliance
    strategy=CrossSectionalMomentum(lookback=252, skip=21),
    tickers=get_top50_universe(),
    allocator=EqualWeightTopN(n=10), schedule=Monthly(),
    feed=ReplayFeed(start=date(2021, 1, 1), end=date(2025, 12, 31)),
)

# Reconciliation: ATTRIBUTION against the frictionless backtester, not a correctness check
reference = Engine(capital=1_000_000, cost_model=TaiwanCostModel(), max_position_pct=0.10).run(
    strategy=SmaCrossover(fast=20, slow=60), tickers=["2330"],
    start=date(2021, 1, 1), end=date(2025, 12, 31),
)
report = reconcile(sim=replay, reference=reference)   # per-line friction waterfall

# Connectivity smoke test ONLY (not a correctness/perf gate): ~14 sessions of live EOD data
live = session.run(strategy=SmaCrossover(fast=20, slow=60), tickers=["2330"], feed=LiveFeed())
```

Specifically, after Phase 5 the project has: (a) a `BrokerSimulator` implementing an event-stream `BrokerBase` — the crown-jewel sim/live swap seam; (b) an order-lifecycle state machine with an enumerable transition contract and hard illegal-edge guards; (c) a dated settlement ledger with three swappable modes (`Instant` control, `Netting` canonical, `SettledCashOnly` pessimism); (d) a volume-participation fill model on **trailing ADV as of T**, lookahead-safe at the T+1 open; (e) a rejection taxonomy whose missing-bar cause (`NO_MARKET_DATA`) is foundational and whose `SUSPENSION` refinement is an evidence-gated enrichment; (f) an **independent-oracle** test suite (O1–O10) that establishes correctness without reproducing the backtester; (g) an **attribution** reconciliation report that decomposes the sim-vs-backtest gap by friction cause; (h) an event-sourced JSONL log whose fold *is* the account state; (i) a `BarFeed` abstraction with `Replay`/`Fixture`/`Live` drivers; and (j) demonstrator runs (single-asset SMA + cross-sectional momentum in both settlement modes) plus a live connectivity smoke test.

---

## 2. Locked Decisions

Settled. Architectural decisions already recorded in an ADR are summarized in one line with a pointer; do not relitigate them here.

| # | Decision | Rationale |
|---|---|---|
| D1 | **One shared execution core.** → `ADR 0008`. Backtester and simulator are configurations of one core parameterized over `(settlement_model, fill_model, bar_feed)`. Frictionless = the experimental control *and* the reconciliation reference; friction is an opt-in dial. | Two systems would drift; one core with presets keeps "does it have edge?" (fix friction) and "does it survive execution?" (fix strategy) answerable on the same engine. |
| D2 | **Correctness via independent oracles, not engine-reproduces-engine.** → `ADR 0008`. The O1–O10 suite (§6.3); zero-pessimism reproduction is **deleted** as tautological. | Under one core, "sim reproduces backtester at zero friction" is `x == x` — it cannot fail, so it is not a check. Oracles compute the answer by an independent route and *can* fail. |
| D3 | **Reconciliation is attribution, not correctness.** → `ADR 0008`. `reconcile()` diffs sim vs the frictionless backtester and decomposes by friction cause; it never proves correctness. | The two share the engine, so agreement proves nothing about correctness; it measures the *intended* friction divergence. Correctness lives in the oracles (D2). |
| D4 | **Frequency-agnostic seams.** → `ADR 0008`/`0010`. Core advances on `process_bar(bar)` (not `step(date)`); the execution-timing assumption (at-open vs across-bar) is swappable; the fill model works per-bar. | ~80% reuse at intraday (a named post-Phase-8 phase). Phase 5 locks the daily/at-open preset but builds the seams. |
| D5 | **Settlement modes + dated ledger.** → `ADR 0009`. Swappable `SettlementModel`: `InstantSettlement` (backtester control), `NettingSettlement` (canonical), `SettledCashOnly` (mandatory pessimism mode). One dated ledger: `settled_cash` + a dated queue; trade settlement T+2 on the XTAI calendar; dividend cash at **pay-date**. | TWSE settles net (`ADR 0005`); netting reproduces the instant model's book for long-only-within-means (float line ≈ 0 is the honest expectation). `SettledCashOnly` quantifies float reliance and is what *justifies* the ledger. |
| D6 | **Cash-shortfall allocation = proportional; sizing is cost-budgeted.** → `ADR 0009`. Cross-sectional contention resolved proportionally; orders sized against `(buying_power − Σ estimated_costs)` from rates + T-close. | FCFS / rank-priority encode alpha in the execution layer (a `TieBreakPolicy` violation). Cost-budgeting from T-close is lookahead-safe; the close→open slippage is real cost, accepted not engineered away. |
| D7 | **Event-stream `BrokerBase` seam.** → `ADR 0010`. `submit` / `cancel` / `query` / `drain_events`; the broker *collects*, the loop *pulls* and routes to log + account; one order-independent, idempotent fold shared by simulator, live adapter, and persistence. | A blocking interface forces the live adapter to block-and-poll (leaky). The pull intermediary keeps the broker a sealed unit and the live adapter thin. This is the sim/live swap point. |
| D8 | **Order state machine + enumerable transition contract.** → `ADR 0010`. `{Pending, PartiallyFilled, Filled, Rejected, Expired, Cancelled}`; real brokers' three pre-fill states collapse to `Pending` (re-expanded in the Phase 8 live adapter); every legal edge tested, every illegal edge raises. | Confirmed against backtrader (minus `Margin`) and Shioaji. The collapse is correct because the simulator has no transport round-trips; hard guards make the table a contract, not a convention. |
| D9 | **Submit-and-partial-fill, not pre-screen/clamp.** → `ADR 0010`. The broker enforces reality (partial fill / reject) at the execution moment; it composes with the allocator's proportional pre-sizing (D6) at a different layer. | Reconcile-and-clamp silently absorbs underfill and hides intent. Submit-and-fill preserves intent and surfaces underfill in the event log. |
| D10 | **Fill model = `VolumeParticipationFiller` on trailing ADV.** → `ADR 0008`/`0010`. `min(remaining, floor(participation_rate × trailing_ADV_as_of_T))` at T+1 open + worse-side slippage; `participation_rate` locked default `0.05`, configurable; swappable, with `FullFill` as the frictionless preset. | Trailing ADV (not T+1's realized volume) keeps the at-open fill lookahead-safe and live-implementable — the same concession as sizing on T-close. backtrader's execution-bar volume was rejected for exactly this. |
| D11 | **Time-in-force.** → `ADR 0010`. IOC canonical; ROD supported and *identical to IOC at daily granularity* (distinct only intraday); FOK represented but **rejected on the cross-sectional path** with a loud error. | Shared-pool FOK makes the book order-dependent — the same alpha-in-execution sin as FCFS. The guard converts a silent future bug into a documented boundary. |
| D12 | **Rejection taxonomy: detection + evidence-gated refinement.** → `ADR 0010`. `INSUFFICIENT_BUYING_POWER`, `PRICE_LIMIT_LOCK`, and the missing-bar cause `NO_MARKET_DATA` (foundational: XTAI ∧ ¬feed-bar) with its refinement `SUSPENSION` (`⊆ NO_MARKET_DATA`), sourced from the TWTAWU historical suspension calendar with **graceful degradation**. Price limits via a source-agnostic provider (replay derives, live reads). | `SUSPENSION` is a positive claim about the *market* (evidence-gated); `NO_MARKET_DATA` is an honest claim about *our data* (always derivable). Absent evidence, make the weaker claim — never invent a halt. |
| D13 | **Event-sourced JSONL log; state = fold over the log.** → `ADR 0010`. Append-only immutable event records; the account (cash buckets, positions, order states) is a pure fold; DuckDB queries the JSONL. | Mirrors the live broker's event stream, makes the order-independent fold reusable by the live adapter, and matches the project's append-only ethos. SQLite rejected once the live run was demoted to a smoke test. |
| D14 | **`BarFeed` abstraction; raw OHLCV + corporate-action events.** → `ADR 0008`. `ReplayFeed` (primary gate), `FixtureFeed` (adversarial), `LiveFeed` (EOD-poll smoke test). Feeds emit **raw** OHLCV + action events, not pre-adjusted prices: the strategy reads adjusted prices (signal channel); the simulator reads raw prices for fills and action events for position bookkeeping. | Live-faithful and symmetric; exercises the corporate-action path on every run (closes the ADR-0007-class gap). |
| D15 | **Corporate-action handler → dated position mutations.** Reuses `ADR 0007`. Each event decomposes into dated mutations: cash dividend → pay-date receivable (ledger); shares-multiply + basis-reduce → ex-date. Combined 除權息 handled; splits always; rights/reductions evidence-gated with a loud assertion. | The norm in TWSE Top 50 is the combined event; modelling it as cash-only introduces ~7% back-adjustment error. Deterministic decomposition belongs in the spec, not an ADR. |
| D16 | **Stateless purity preserved.** → `ADR 0010`/`0004`. The order state machine lives wholly in the simulator/account; events route to the **log + account only**, never a strategy (no `notify_order` coupling). `assert_no_lookahead` re-run for the demonstrator. | Keeps `generate_signals`/`generate_scores` pure and the lookahead audit valid. Fill-reaction (iceberg/TWAP) is a future *executor* stage below the strategy seam, not a strategy hook. |
| D17 | **Scope: daily, long-only, market orders.** Shorts, pairs, limit/stop types, cross-bar resting, real order-book matching, and a live broker adapter are deferred to their named later phases. | Phase 5 is the paper simulator; each deferral adds an orthogonal axis (borrow/tick rejections, multi-leg atomicity, intraday) better added against a proven core. |

---

## 3. Deliverables (modules)

```
src/twstock/
├── broker/
│   ├── __init__.py            # NEW — public API: PaperSession, BrokerSimulator, fillers, feeds, reconcile
│   ├── base.py                # NEW — BrokerBase ABC (submit/cancel/query/drain_events) — the shared sim/live seam
│   ├── order.py               # NEW — Order, OrderStatus, TimeInForce, transition table + illegal-edge guards
│   ├── events.py              # NEW — immutable event records; EventLog (append-only JSONL); fold-to-state
│   ├── account.py             # NEW — Account: cash buckets + positions (reuses trades.py lots); mark-to-market
│   ├── settlement.py          # NEW — SettlementModel ABC; Instant/Netting/SettledCashOnly; dated ledger
│   ├── fill.py                # NEW — FillModel ABC; VolumeParticipationFiller (trailing ADV); FullFill
│   ├── rejection.py           # NEW — RejectionReason enum; PriceLimitProvider; SuspensionCalendar (+ NO_MARKET_DATA detection)
│   ├── corporate_actions.py   # NEW — decompose action events into dated position mutations
│   ├── feed.py                # NEW — BarFeed ABC; ReplayFeed, FixtureFeed, LiveFeed
│   ├── simulator.py           # NEW — BrokerSimulator(BrokerBase): process_bar applies fill/settlement/rejection, emits events
│   ├── session.py             # NEW — PaperSession: the run loop (feed → strategy → allocate → reconcile → submit → drain → fold → mark)
│   └── reconcile.py           # NEW — attribution reconciliation report (waterfall + order-level audit)
├── backtest/
│   ├── trades.py              # REFACTORED (step 0c) — LotLedger extracted; build_round_trips becomes thin wrapper; consumed one update per deal by account.py
│   ├── allocator.py           # REUSED — EqualWeightTopN; cost-budgeted sizing extension (D6)
│   ├── reconciler.py          # REUSED — Phase 4 target→orders reconciler (cross-sectional path)
│   └── engine.py              # UNCHANGED — frictionless backtester = reconciliation reference; shared-core migration DEFERRED (non-blocking, §10)

tests/broker/                  # +test files per module; the O1–O10 oracle suite; the adversarial FixtureFeed catalogue
journal/
├── phase_5_references.md      # DONE — Step 0 source-reading journal (backtrader + Fubon/Shioaji + TWSE/MOPS)
└── phase_5_retro.md           # NEW at close
scripts/
├── phase_5_paper_run.py       # NEW — replay demonstrator (SMA single-asset + momentum both modes) + live smoke
└── phase_5_reconcile.py       # NEW — emit the reconciliation report for a sim run vs its backtest reference
```

`CLAUDE.md` is updated once at phase close (D1–D17 in §2, the new `broker/` module list and import boundary, Phase 5 guardrails). ADRs 0008/0009/0010 are referenced, not re-derived.

**`broker/` import boundary** (add to `CLAUDE.md §7`, mirroring `backtest/`): `broker/` may import from `twstock.backtest` (cost model, `trades`, allocator, reconciler), `twstock.data.api`, `twstock.data.storage` (raw OHLCV + corporate-action reads for `ReplayFeed` and `LiveFeed` — the one exception to the adjusted-only `data.api` rule), `twstock.universe`, `twstock.config`, and `exchange_calendars`. It must **not** import from `twstock.data.sources`, `twstock.data.adjustments`, `twstock.data.quality`, or `twstock.ui`. The simulator never imports a strategy module (signals arrive as data through the session loop).

---

## 4. Module specifications

### 4.0 `backtest/trades.py` — `LotLedger` (pre-phase refactor, step 0c)

`LotLedger` is extracted from the internal `_Lot` deque inside `build_round_trips()`. The batch function becomes a thin wrapper; `account.py` (§4.4) depends on `LotLedger` directly for incremental per-deal tracking.

```python
class LotLedger:
    """FIFO per-ticker lot tracker. Extracted from build_round_trips()."""

    def __init__(self, matching: Matching = Matching.FIFO) -> None: ...

    def on_fill(self, side: Side, shares: int, price: float, cost: float) -> None:
        """side='buy'  → append a new lot (shares, entry_basis).
        side='sell' → consume lots FIFO; buffer closed slices; increment
        _generation when open_shares reaches 0 (marks a lifecycle boundary)."""

    @property
    def open_shares(self) -> int: ...        # Σ lot.shares across the open deque

    @property
    def avg_cost(self) -> float | None: ...  # weighted avg entry basis; None when flat

    def round_trips(
        self,
        grouping: Grouping = Grouping.FLAT_TO_FLAT,
    ) -> list[dict]:
        """Apply grouping to the buffered closed slices and return round-trip
        dicts (same schema as build_round_trips() output rows).
        FLAT_TO_FLAT groups slices by _generation (a counter incremented each
        time open_shares reaches 0); FILL_TO_FILL emits per sell event."""
```

`build_round_trips()` after the refactor:
```python
def build_round_trips(
    trades: pd.DataFrame,
    grouping: Grouping = Grouping.FLAT_TO_FLAT,
    matching: Matching = Matching.FIFO,
) -> pd.DataFrame:
    """Thin wrapper: feeds trade rows through per-ticker LotLedgers and
    collects round_trips(grouping). Public signature and output schema unchanged."""
```

Notes:
- **`on_fill` is the single entry point** for both sides (branches internally on `side`). No separate `on_buy`/`on_sell` — keeps the `Account` call site simple.
- **Closed slices are buffered** in `_closed_slices` with a `_generation` tag. Grouping is applied at read time, so the same `LotLedger` instance supports all three modes without being re-created.
- **`avg_cost` semantics** match `Position.avg_cost` in the existing backtester — a weighted average of the currently-open lots' entry bases.
- **Memory note:** `_closed_slices` grows with every sell. For a multi-year replay across 50 tickers this is bounded by the total trade count and is fine for Phase 5.
- **`build_round_trips()` public contract is unchanged.** All existing tests must stay green; new tests cover `LotLedger` directly (see step 0c).

### 4.1 `broker/base.py`

```python
class BrokerBase(ABC):
    @abstractmethod
    def submit(self, order: Order) -> OrderId: ...          # fire-and-forget; returns an ack id
    @abstractmethod
    def cancel(self, order_id: OrderId) -> None: ...        # request cancel; effect arrives as an event
    @abstractmethod
    def query(self, order_id: OrderId) -> OrderState: ...   # current mirror of one order
    @abstractmethod
    def drain_events(self) -> list[BrokerEvent]: ...        # pull all events since last drain; clears buffer
```

Notes:
- **The seam, not the simulator.** `BrokerSimulator` (§4.11) implements this; the Phase 8 Fubon adapter will implement the same interface. Nothing above `BrokerBase` knows which is behind it.
- **`process_bar` is deliberately NOT here.** Advancing the clock and generating fills is a *simulation* concern — `BrokerSimulator` adds `process_bar(bar)` (§4.11). Live, reality advances asynchronously and the adapter receives real fills via callbacks; there is no bar to "process." Putting `process_bar` in `BrokerBase` would force the live adapter to implement a meaningless method. The *advance* mechanism is therefore the session's concern (§4.11), and the shared seam stays minimal.
- **Pull, not push.** `drain_events` is the deliberate inversion of backtrader's `notify_order` push (D7). The broker holds no reference to the log, account, or any strategy; the session loop pulls and routes. (Live, `drain_events` returns the events the adapter has buffered from broker callbacks since the last call — same shape, different source.)
- **One fold.** The function that applies a `BrokerEvent` to the local mirror is order-independent and idempotent, so the same fold serves the in-order simulator and the out-of-order live adapter.

### 4.2 `broker/order.py`

```python
class TimeInForce(Enum):
    IOC = "ioc"   # canonical
    ROD = "rod"   # identical to IOC at daily granularity (distinct only intraday)
    FOK = "fok"   # represented; rejected on the cross-sectional path (loud)

class OrderStatus(Enum):
    PENDING = "pending"; PARTIALLY_FILLED = "partially_filled"; FILLED = "filled"
    REJECTED = "rejected"; EXPIRED = "expired"; CANCELLED = "cancelled"

@dataclass(frozen=True)
class Order:
    order_id: OrderId; ticker: str; side: Side; quantity: int
    tif: TimeInForce = TimeInForce.IOC
    # market orders only in Phase 5 (no limit price)

LEGAL_TRANSITIONS: frozenset[tuple[OrderStatus, OrderStatus]] = ...  # the §contract
def assert_legal_transition(src: OrderStatus, dst: OrderStatus) -> None:
    """Raise IllegalTransitionError if (src, dst) not in LEGAL_TRANSITIONS."""
```

Legal transitions (every other edge raises): `Pending → {PartiallyFilled, Filled, Rejected, Cancelled, Expired}`; `PartiallyFilled → {PartiallyFilled, Filled, Cancelled, Expired}`. Terminal: `Filled, Rejected, Expired, Cancelled`. **`PartiallyFilled → Rejected` is illegal** (rejection is pre-fill only; a partly-filled remainder expires or is cancelled).

Notes:
- **`cancel-while-partially-filled` and `expire-the-remainder` are first-class** rows with dedicated tests (the most bug-prone cells per the Phase 4 retro).
- **Pre-fill collapse.** No `Created/Submitted/Accepted` distinction — the simulator has no transport. The Phase 8 live adapter re-expands them.

### 4.3 `broker/events.py`

```python
@dataclass(frozen=True)
class BrokerEvent:                 # immutable; the unit of the append-only log
    seq: int; ts: date; kind: EventKind   # SUBMITTED|PARTIAL_FILL|FILL|REJECT|EXPIRE|CANCEL|DIVIDEND|SPLIT|SETTLE
    order_id: OrderId | None; payload: Mapping   # e.g. (price, quantity) for a fill; reason for a reject

class EventLog:
    def append(self, event: BrokerEvent) -> None: ...     # writes one JSONL line
    def __iter__(self) -> Iterator[BrokerEvent]: ...       # replay for the fold
    # DuckDB reads the JSONL directly for reporting
```

Notes:
- **State is a fold, not a mutated object.** The account is *derived* from the log by the fold (defined in `account.py`, §4.4, since it produces account state); this is what makes O5–O7 (§6.3) checkable against an independent recomputation and O10 (idempotence) meaningful.
- **Immutable records** (borrowed from backtrader's `order.clone()` snapshot discipline) — events are facts that happened, never mutable references.

### 4.4 `broker/account.py`

```python
class Account:
    settled_cash: Decimal
    settlement: SettlementModel          # owns the dated unsettled queue
    positions: dict[str, LotLedger]      # reuses backtest/trades.py lots
    def buying_power(self, as_of: date) -> Decimal: ...   # mode-dependent (D5)
    def mark_to_market(self, prices: Mapping[str, Decimal]) -> Decimal: ...  # total equity
    def total_value(self, prices) -> Decimal: ...          # settled + unsettled + Σ position value

def fold(events: Iterable[BrokerEvent], into: Account) -> Account:
    """Pure, order-independent fold of the event stream into account + order state.
    The SAME fold rehydrates state for the Phase 8 live adapter (out-of-order events)."""
```

Notes:
- **The fold lives here**, with the account it produces, so `events.py` stays dependency-free. It is order-independent and idempotent by construction — the property the live adapter relies on.
- **Positions reuse `trades.py`** — one incremental lot-ledger update per deal (the partial-reduction path built in Phase 4, `ADR 0006 §9`).
- **`total_value` is the O1 invariant target** — it must change only by mark moves, costs, and dividends.

### 4.5 `broker/settlement.py`

```python
class SettlementModel(ABC):
    @abstractmethod
    def on_trade(self, ledger: DatedLedger, trade: Trade, t: date) -> None: ...   # schedule cash movement
    @abstractmethod
    def buying_power(self, ledger: DatedLedger, t: date) -> Decimal: ...

class InstantSettlement(SettlementModel): ...     # backtester control (ADR 0005 stands)
class NettingSettlement(SettlementModel): ...     # canonical: same-bar sell proceeds fund same-bar buys; T+2 net
class SettledCashOnly(SettlementModel): ...       # pessimism: only settled cash spends; quantifies float reliance

class DatedLedger:
    settled_cash: Decimal
    pending: list[DatedCashFlow]      # (amount, settle_date); T+2 for trades, pay-date for dividends
    def mature(self, t: date) -> None: ...   # reclassify matured flows; settled+unsettled invariant (O2)
```

Notes:
- **Netting reproduces the instant book** for long-only-within-netted-means — the float line in reconciliation is expected ≈ 0; a non-zero float line is a *bug signal*, not noise.
- **Dividend cash settles at pay-date** (`FinMind CashDividendPaymentDate`; a documented assumption with a validation task), distinct from the ex-date share/basis mutation (§4.9).
- **`mature()` preserves total cash** — that reclassification invariant is oracle O2.

### 4.6 `broker/fill.py`

```python
class FillModel(ABC):
    @abstractmethod
    def fillable(self, order: Order, bar: Bar, trailing_adv: float) -> int:
        """Shares fillable on THIS bar. Pure; no engine state."""

class VolumeParticipationFiller(FillModel):
    def __init__(self, participation_rate: float = 0.05) -> None: ...
    # min(remaining, floor(participation_rate * trailing_adv_as_of_T))

class FullFill(FillModel):              # frictionless preset (= backtester behaviour)
    # returns remaining
```

Notes:
- **Trailing ADV as of T**, never T+1's realized volume (the lookahead; see §7.1). The caller supplies `trailing_adv` computed from data ≤ T.
- **Size only.** The filler decides *how much*; price is the T+1 open + worse-side slippage from `TaiwanCostModel` (the size/price split confirmed against backtrader).
- **Swappable; `FullFill` is the control** — `filler=FullFill()` is the frictionless configuration of the shared core (D1).

### 4.7 `broker/rejection.py`

```python
class RejectionReason(Enum):
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
    PRICE_LIMIT_LOCK = "price_limit_lock"
    NO_MARKET_DATA = "no_market_data"     # foundational detection
    SUSPENSION = "suspension"             # evidence-gated refinement of NO_MARKET_DATA

class PriceLimitProvider(ABC):
    @abstractmethod
    def band(self, ticker: str, t: date) -> tuple[Decimal, Decimal]: ...   # (limit_down, limit_up)
class DerivedPriceLimits(PriceLimitProvider): ...   # replay: prior close ±10%, tick-rounded, adjusted ref on ex-days

class SuspensionCalendar:
    """Per-ticker refinement of the XTAI trading calendar: 'is ticker X tradeable on D?'.
    Built from the TWTAWU historical halt records. ABSENT or unbuilt → all-tradeable
    (graceful degradation: every missing bar stays NO_MARKET_DATA)."""
    def is_halted(self, ticker: str, t: date) -> bool: ...
```

Notes:
- **`NO_MARKET_DATA` is the gate, `SUSPENSION` the upgrade.** Detection = XTAI says a bar should exist ∧ the feed lacks it. Only on positive calendar evidence is it re-labelled `SUSPENSION` (`SUSPENSION ⊆ NO_MARKET_DATA`). No calendar → everything stays `NO_MARKET_DATA`; nothing breaks.
- **Build order (§5):** `NO_MARKET_DATA` detection ships first (foundational); the TWTAWU calendar is the enrichment, validated against an independent source (the Yahoo suspended calendar) before adoption.
- **Price limits are source-agnostic** — replay derives the band; the live adapter (Phase 8) reads `limitup_price`/`limitdown_price` from the broker quote.

### 4.8 `broker/corporate_actions.py`

```python
def decompose(event: CorporateAction, position: LotLedger, t: date) -> list[DatedMutation]:
    """Split / stock dividend → shares×ratio, basis÷ratio at EX-date (deterministic).
    Cash dividend → receivable at PAY-date (into the settlement ledger).
    Combined 除權息 → both. Rights issue / capital reduction → evidence-gated, loud assert."""
```

Notes:
- **Reuses `ADR 0007`** adjustment factors; the combined cash-and-stock dividend is the TWSE Top 50 norm and is first-class (mapping it to cash-only injects ~7% error).
- **Deterministic** — specified here, not in an ADR (ADRs are for decisions with alternatives). Validated by oracles O8/O9.

### 4.9 `broker/feed.py`

```python
class BarFeed(ABC):
    @abstractmethod
    def __iter__(self) -> Iterator[Bar]: ...   # yields RAW OHLCV + any corporate-action events for the bar

class ReplayFeed(BarFeed):   # historical; the PRIMARY stability + correctness gate
    def __init__(self, start: date, end: date) -> None: ...
class FixtureFeed(BarFeed):  # hand-built adversarial sequences (§6.4)
    def __init__(self, bars: list[Bar]) -> None: ...
class LiveFeed(BarFeed):     # EOD poll (FinMind, after ~17:30), one completed day at a time — SMOKE TEST ONLY
    def __init__(self, freshness_check: bool = True) -> None: ...
```

Notes:
- **Raw, not adjusted** (D14). The strategy receives adjusted prices through the session's signal channel; the simulator receives raw prices (fills) and action events (bookkeeping) through the feed.
- **`LiveFeed` is a connectivity smoke test**, not a gate. It processes completed EOD bars after close; it is *not* a real-time intraday stream and never reaches a real exchange.

### 4.10 `broker/reconcile.py`

```python
def reconcile(sim: SimResult, reference: BacktestResult) -> ReconciliationReport:
    """ATTRIBUTION (not correctness): diff the pessimistic sim against the frictionless
    backtester and decompose the gap by friction cause. Per-line tolerances, not global."""
```

The report has three layers: (1) headline tear-sheet diff (net return, Sharpe, fees); (2) the **attribution waterfall** (§7) — each line predicted with sign and magnitude, *tripwire* lines (raw-vs-adjusted ≈ 0, slippage ≈ 0) that must be ~zero or signal a bug, and *accepted-divergence* lines (liquidity underfill, float reliance) checked for sane magnitude; (3) the order-level JSONL audit (cause-tagged underfills). See §7.

### 4.11 `broker/simulator.py` and `broker/session.py`

```python
class BrokerSimulator(BrokerBase):
    def __init__(self, *, capital, cost_model, fill_model, settlement, max_position_pct) -> None: ...
    def process_bar(self, bar: Bar) -> None:    # SIMULATOR-ONLY (not BrokerBase); the simulation clock
        # 1. mature the settlement ledger to bar.date
        # 2. apply corporate-action mutations for the bar (ex-date shares, pay-date cash)
        # 3. for each working order: reject (limit-lock / no-data / suspension / funds) OR
        #    fill min(remaining, fillable(...)) at open+slippage; emit events; flip status
        # 4. events accumulate in the internal buffer (pulled via drain_events)

class PaperSession:
    def __init__(self, broker: BrokerSimulator) -> None: ...
    def with_settlement(self, model: SettlementModel) -> "PaperSession": ...   # mode swap for the run
    def run(self, *, strategy, tickers, feed, allocator=None, schedule=None) -> SimResult:
        # per bar T from the feed:
        #   strategy.generate_signals/scores (adjusted prices)  -> allocate (cost-budgeted, D6)
        #   -> reconcile to target orders (reuse backtest/reconciler) -> broker.submit(...)
        #   ADVANCE to T+1:  broker.process_bar(T+1)  -> events = broker.drain_events()  ← the live seam
        #   -> fold events into account + EventLog -> mark-to-market at T+1 close
```

Notes:
- **The session owns the loop; the broker owns execution.** Signals enter as data; order/fill state never returns to the strategy (D16).
- **Order construction stays upstream** (allocator/reconciler), as confirmed against backtrader (`buy`/`sell` kept out of the broker).
- **The advance step is the one Phase-8 seam.** A future `LiveSession` mirrors this exact spine (`strategy → allocate → reconcile → submit → drain → fold → mark`) and replaces only the marked line: instead of `broker.process_bar(T+1)` (drive the simulation), it waits on real-time broker callback events, then drains identically. The loop is structured so that `advance-then-drain` is isolated as the swap point (see §11.2, the Phase 8 reuse contract).

---

## 5. Build sequence

One commit per step (Conventional Commits, `Co-Authored-By: Claude` trailer). **Step 0 source reading is already complete** — `journal/phase_5_references.md` (backtrader read directly; Fubon/Shioaji + TWSE/MOPS investigated). Build foundational state first, enrichments last.

### Pre-phase prerequisites

These steps fix existing modules before the `broker/` build begins. Each is a focused, self-contained change; all three must pass (tests green) before step 1.

**0a. `fix(backtest): apply floor rounding to commission and tax in TaiwanCostModel`**
`backtest/cost_model.py`; apply `math.floor()` to the per-notional commission and to the sell-side tax *before* the `max(..., NT$20 floor)` comparison. Taiwan brokers floor commission and tax to the whole NT$ before comparing to the statutory minimum (ADR 0010 Item 4). Without this, cost oracle O7 will fail (re-applied commission != accumulated commission for fractional NT$ fills). Tests: buy/sell cost assertions for fractional-NT$ notional amounts; all existing `cost_model` tests must stay green.

**0b. `feat(data): add pay_date to corporate-actions schema and FinMind extraction`**
`data/schemas.py` (add `pay_date` column, nullable `date`); `data/sources/finmind.py` (`_build_dividends` reads `CashDividendPaymentDate`). The settlement ledger (step 4) schedules dividend cash at pay-date, not ex-date; without this column the dated ledger cannot honour D5 and oracle O9 will fail. Tests: schema validation with the new column; `_build_dividends` fixture asserts `pay_date` is populated for cash dividend rows; existing cassette tests stay green (cassette re-record may be required if the response shape changes).

**0c. `refactor(backtest): extract LotLedger from build_round_trips`**
`backtest/trades.py`; extract the FIFO deque + partial-reduction logic into the new `LotLedger` class (see §4.0); `build_round_trips()` becomes a thin wrapper. `account.py` (step 4) depends on `LotLedger`; this step must land before step 4. Tests: all existing `build_round_trips` tests must stay green; add `LotLedger` unit tests covering `on_fill` (buy and sell sides), `open_shares`, `avg_cost`, `round_trips()` for each `Grouping` mode, and the `_generation` lifecycle boundary (position goes flat → counter increments).

### Foundations (event-sourced spine)

1. **`feat(broker): core types, event records, and append-only log`** — `events.py`; the `Bar` value type (raw OHLCV + corporate-action events for one ticker-day), basic types (`OrderId`, `Side`), immutable `BrokerEvent`, and `EventLog` (append-only JSONL). **No fold yet** — the fold needs the account (step 4). Tests: JSONL round-trip; event immutability.
2. **`feat(broker): order model and transition contract`** — `order.py`; `Order`, `OrderStatus`, `TimeInForce`, `LEGAL_TRANSITIONS`, `assert_legal_transition`. Tests: every legal edge; every illegal edge raises; `PartiallyFilled → Rejected` raises.
3. **`feat(broker): BrokerBase seam`** — `base.py`. ABC only; no behaviour yet.
4. **`feat(broker): account, settlement ledger, and the event fold`** — `account.py`, `settlement.py`, and the pure order-independent `fold(events) → account + order state` (lives with the account it produces, so `events.py` need not depend on it). `Instant/Netting/SettledCashOnly`, `DatedLedger.mature`. Tests: O2 (settlement neutrality), O4 (lot/position consistency), **O10 (fold idempotence on reordered commutative events)**; buying-power per mode; T+2 maturation across XTAI holidays.

### Execution mechanics

5. **`feat(broker): volume-participation fill model`** — `fill.py`; `VolumeParticipationFiller` (trailing ADV), `FullFill`. Tests: cap math; order > bar volume partials; `FullFill` == remaining.
6. **`feat(broker): rejection taxonomy and NO_MARKET_DATA detection`** — `rejection.py` (foundational part); `RejectionReason`, `DerivedPriceLimits`, the XTAI-∧-¬feed-bar detection. **`SuspensionCalendar` returns all-tradeable for now** (graceful-degradation default). Tests: limit-lock buy/sell; insufficient funds; **missing-bar ⇒ `NO_MARKET_DATA`, never silently a halt**.
7. **`feat(broker): corporate-action decomposition`** — `corporate_actions.py`; ex-date share/basis, pay-date cash, combined 除權息. Tests: O8 (split), O9 (combined cash+stock dividend).
8. **`feat(broker): bar feed abstraction (replay + fixture)`** — `feed.py`; `ReplayFeed`, `FixtureFeed` (raw OHLCV + action events). `LiveFeed` deferred to step 13.

### Orchestration

9. **`feat(broker): simulator process_bar`** — `simulator.py`; wire mature → actions → reject/fill → emit. Tests: the per-bar order of operations; events match the fold.
10. **`feat(broker): paper session run loop`** — `session.py`; feed → strategy → allocate (cost-budgeted, D6) → reconcile → submit → drain → fold → mark. Reuse `backtest/allocator.py` + `reconciler.py`. Integration: single-asset SMA replay end-to-end; `assert_no_lookahead` re-run.

### Correctness and enrichment

11. **`test(broker): independent-oracle suite O1–O10`** — `tests/broker/test_oracles.py` + invariants embedded per module. The correctness gate (§6.3).
12. **`test(broker): adversarial fixture catalogue`** — `tests/broker/fixtures/` via `FixtureFeed` (§6.4); the four mandatory-for-exit fixtures included.
13. **`feat(broker): suspension calendar enrichment + live feed`** — the TWTAWU `SuspensionCalendar` (validated against the Yahoo suspended calendar; graceful degradation if the pull is not clean) **and** `LiveFeed` (EOD poll + freshness check). Tests: confirmed-halt ⇒ `SUSPENSION`; absent calendar ⇒ `NO_MARKET_DATA`.
14. **`feat(broker): attribution reconciliation report`** — `reconcile.py`; waterfall + tripwire/accepted-divergence lines + order-level audit (§7).

### Demonstrator and close-out

15. **`feat(scripts): paper-run demonstrator`** — `scripts/phase_5_paper_run.py` (SMA single-asset + momentum cross-sectional in **both** settlement modes) + the live connectivity smoke test; `scripts/phase_5_reconcile.py` emits the report.
16. **`docs: close phase 5`** — `CLAUDE.md` (D1–D17, `broker/` modules + import boundary, Phase 5 guardrails), `PROJECT_PLAN.md` tracker (+ the phase-numbering reconciliation), `journal/phase_5_retro.md`.

**Slip-handling rule.** If the suspension enrichment (step 13's calendar) or the reconciliation report (step 14) is not complete by end of phase, ship the simulator with `NO_MARKET_DATA`-only rejection and the oracle suite as a coherent partial deliverable — it already clears the execution-realism bar — and carry the enrichment forward. **Never** ship the simulator without the O1–O10 oracle suite or the four mandatory fixtures.

---

## 6. Test strategy

Phase 4 ended at ~310 tests. Phase 5 adds per-module unit tests, the independent-oracle suite, and the adversarial fixture catalogue. Integration tests are `@pytest.mark.integration` (depend on the local Parquet store and the XTAI calendar).

### 6.1 What gets tested

| File | What it covers |
|---|---|
| `test_events.py` | Fold determinism; **O10** idempotence on reordered commutative events; JSONL round-trip. |
| `test_order.py` | Every legal transition; every illegal edge raises; `PartiallyFilled → Rejected` raises; TIF enum. |
| `test_account.py` | Buying power per settlement mode; `total_value` composition; **O1** value conservation; **O4** lot/position consistency. |
| `test_settlement.py` | T+2 maturation across XTAI holidays; dividend pay-date settlement; **O2** settlement neutrality. |
| `test_fill.py` | Trailing-ADV cap; order > bar volume → partial; fixed size across bars of differing volume; `FullFill` == remaining. |
| `test_rejection.py` | Limit-lock buy/sell; insufficient funds; **missing-bar ⇒ `NO_MARKET_DATA`** (not suspension); calendar-confirmed ⇒ `SUSPENSION`; absent calendar degrades. |
| `test_corporate_actions.py` | **O8** split; **O9** combined cash+stock dividend; ex-date vs pay-date timing. |
| `test_simulator.py` | `process_bar` order of operations; emitted events reconcile with the fold; partial-then-expire. |
| `test_session_integration.py` | Single-asset SMA + cross-sectional momentum end-to-end; `assert_no_lookahead`; both settlement modes. |
| `test_oracles.py` | **O5** cash, **O6** share, **O7** cost reconstruction against flat blotter sums (§6.3). |
| `test_reconcile.py` | Tripwire lines (~0); accepted-divergence lines (sane magnitude); per-line tolerance. |

### 6.2 What is explicitly NOT tested

- Short selling, borrow/tick rejections, pairs trading (later phases).
- Limit/stop order types, cross-bar resting orders (intraday).
- A live broker adapter, real order placement, real order-book matching (Phase 8/9).
- `LiveFeed` correctness/performance — it is a connectivity smoke test, asserted only to run and return fresh bars.
- The alpha-line / FOK-on-cross-sectional guardrails as *unit* tests where they are code-review checks (the FOK guard *does* get a dedicated loud-error test).

### 6.3 The independent-oracle suite (O1–O10) — the correctness gate

Each oracle computes the answer by a route that shares no code with the engine, so it *can* fail (unlike zero-pessimism reproduction). O1–O4 are invariants asserted every bar; O5–O7 are flat recomputations checked at the end of a fully-settled window; O8–O9 are fixture expectations; O10 is structural.

| Oracle | Asserts |
|---|---|
| O1 value conservation | `total_value` changes over a bar only by `Δ(mark) − costs + dividends`; catches a fill that moves cash but not shares. |
| O2 settlement neutrality | `settled + unsettled` unchanged across every `mature()`; catches money minted/lost in reclassification. |
| O3 order-quantity conservation | `filled + cancelled + expired == original` at terminal state; catches partial-fill leaks. |
| O4 lot/position consistency | `Σ(lot.shares) == position.shares` per ticker, every bar. |
| O5 cash oracle | `initial − Σ buys − Σ costs + Σ sells + Σ dividends` (flat blotter sum) == account cash. |
| O6 share oracle | `Σ buy fills − Σ sell fills + Σ CA share-adds` (flat tally) == lot-ledger position. |
| O7 cost oracle | cost-model re-applied per fill, summed, == accumulated costs; catches double-charged NT$20 floor / wrong-side tax. |
| O8 split oracle | `N, basis B, ratio r → rN shares, B/r basis`, cash unchanged, value continuous. |
| O9 combined dividend oracle | `N, cash c, stock s, ex E, pay P → N(1+s) shares + receivable Nc at E; cash += Nc at P`; value continuous across E. |
| O10 fold idempotence | replay (and commutative reorder) of the log → identical final state. |

### 6.4 Adversarial fixture catalogue

Hand-built `FixtureFeed` sequences (~15), mode-parameterized. **Mandatory for exit** (★): the rotation cash-bug regression, partial-then-expire, split-on-held-name, and missing-bar-isn't-suspension.

- **Fill model:** order > bar volume; fixed size across bars of differing volume.
- **Rejection:** limit-lock buy; limit-lock sell; insufficient funds; ★ missing-bar ⇒ `NO_MARKET_DATA`.
- **Cash/settlement:** ★ full-rotation cash-bug regression (the Phase 4 `InsufficientCashError`, now under netting); `SettledCashOnly` lag; dividend pay-date timing.
- **Corporate action on a held name:** ★ split; combined cash+stock dividend; ex-day adjusted-reference limit band.
- **State machine:** cancel-mid-life; ★ partial-then-expire; out-of-order fold.

Coverage criterion: every legal transition exercised at least once; every illegal transition asserted to raise.

---

## 7. The reconciliation report and attribution waterfall (DETAILED)

The most important new analysis of this phase: get the reconciliation wrong and every friction number is either falsely reassuring or falsely alarming. The report exists to answer *"given the simulator is correct, what does execution reality cost?"* — correctness itself is the oracle suite's job (§6.3), never the reconciliation's.

### 7.1 Why the fill timing is lookahead-safe (and where backtrader differs)

An order is created from T's close and fills at **T+1's open**. The volume cap must therefore use **trailing ADV as of T**, not T+1's full-day volume — at the open the day's volume does not yet exist, and a filler needing it could not run live. This is the *same* concession as sizing on T-close rather than T+1's open price: the decision is fixed from what is knowable at decision time, and the gap to realization is real cost, not error. backtrader's filler reads the execution bar's own volume — defensible for its across-the-bar fill model, lookahead for our at-open model. The variance between trailing and realized volume is accepted, not engineered away.

### 7.2 The attribution waterfall

The waterfall starts at the frictionless backtester's net return and walks to the simulator's, one friction line at a time. Each line is *predicted* with a sign and a rough magnitude before the run; a line that comes out the wrong sign or an order of magnitude off is a bug signal, not a curiosity.

- **Tripwire lines (must be ≈ 0).** Raw-vs-adjusted price handling (the simulator reads raw + action events; if corporate actions are mishandled this diverges); slippage under `FullFill` + `InstantSettlement` (the frictionless config must reproduce the backtester). A non-zero tripwire line is a correctness failure that the oracles should also have caught — it is the reconciliation's cross-check on them.
- **Accepted-divergence lines (checked for sane magnitude, not zero).** Liquidity underfill (volume cap), worse-side slippage, the float-reliance line under `SettledCashOnly` vs `Netting`. Under `NettingSettlement` for a long-only-within-means book the float line is expected ≈ 0; a non-zero float line under netting is itself a bug signal (D5).

### 7.3 Per-line tolerance, and the order-level audit

Tolerance is per line, not global — a 5 bp slippage line and a 0 bp raw-vs-adjusted tripwire have different acceptable bands. Concretely:

- **Tripwire lines carry a hard band.** Target ≈ 0. Pre-migration (the backtester is a separate implementation, §10), the residual is the two implementations' *non-friction* differences — band `|line| < ~5 bp` of initial equity — tightening to Decimal-rounding-only (effectively 0) once the backtester is migrated onto the shared core. A breach **fails** the reconciliation and points at a corporate-action or cost bug (which the oracles should also have caught — the tripwire is the reconciliation's cross-check on them).
- **Accepted-divergence lines carry no fixed pass/fail constant** — the magnitude is strategy- and period-dependent, so a spec number would be fiction. Each instead declares an **expected sign** and a **loose sanity ceiling** that flags gross errors: liquidity underfill `≥ 0` and bounded by the order-size/ADV ratio; the float-reliance line `≈ 0` under `NettingSettlement` (a non-zero float line *is* a bug signal, D5) and material only under `SettledCashOnly`. Magnitudes are reported for review and baselined from the first clean run.

Beneath the waterfall sits the order-level JSONL audit: every underfilled or rejected order, cause-tagged (`liquidity` / `limit_lock` / `no_market_data` / `suspension` / `insufficient_funds`), so any aggregate friction line can be drilled to the individual orders that produced it. This is what makes the report an *instrument* rather than a single number.

---

## 8. Exit criteria

Phase 5 is done when **all** hold:

- [ ] `BrokerSimulator` implements `BrokerBase` (`submit`/`cancel`/`query`/`drain_events`) plus its own `process_bar` (the simulation clock); the `PaperSession` loop runs a Phase 4 strategy end-to-end over `ReplayFeed`.
- [ ] The order state machine passes the full transition contract: every legal edge exercised, every illegal edge raises (incl. `PartiallyFilled → Rejected`).
- [ ] All three settlement modes work; `NettingSettlement` reproduces the instant book for the long-only demonstrator (float line ≈ 0); `SettledCashOnly` quantifies float reliance.
- [ ] The **O1–O10 oracle suite passes** — the correctness gate.
- [ ] The four ★ mandatory adversarial fixtures pass; every legal transition is covered.
- [ ] Fill model uses **trailing ADV as of T** (not T+1 volume); `assert_no_lookahead` passes for the demonstrator.
- [ ] Rejection: `NO_MARKET_DATA` detection (XTAI ∧ ¬feed-bar) works; `SUSPENSION` upgrade fires only on calendar evidence and **degrades gracefully** to `NO_MARKET_DATA` when the calendar is absent.
- [ ] Corporate actions decompose correctly (O8 split, O9 combined dividend); ex-date vs pay-date timing honoured.
- [ ] The reconciliation report runs against the frictionless backtester: tripwire lines ≈ 0, accepted-divergence lines sane, order-level audit present.
- [ ] Demonstrator: SMA single-asset + cross-sectional momentum (both settlement modes) over multi-year replay; live connectivity smoke test runs for ~14 sessions without crashing.
- [ ] `uv run pytest tests/` green; `uv run pytest -m integration` green.
- [ ] Pre-commit clean; commits follow `CLAUDE.md §9`.
- [ ] `CLAUDE.md` updated (D1–D17, `broker/` modules + import boundary, Phase 5 guardrails); `PROJECT_PLAN.md` tracker + phase-numbering reconciled; `journal/phase_5_retro.md` written.

---

## 9. Pitfalls and known gaps

**Zero-pessimism reproduction is not a correctness check.** Running the sim at `FullFill` + `InstantSettlement` and confirming it matches the backtester is tautological under one core (`ADR 0008`). It is used in reconciliation as a *tripwire* (§7.2), never as the correctness gate — that is the oracle suite. Do not reintroduce it as proof.

**Volume lookahead is the same bug as price lookahead.** Capping fills on T+1's full-day volume peeks at the future exactly as sizing on T+1's open would. Trailing ADV as of T is the only lookahead-safe cap. Audited like the Phase 3 `shift(-` grep.

**`SUSPENSION` is a positive claim; default to `NO_MARKET_DATA`.** Never label a missing bar a halt without calendar evidence — that injects phantom market events into the reconciliation. `SUSPENSION ⊆ NO_MARKET_DATA`; the floor is always derivable, the upgrade is evidence-gated.

**Combined 除權息 is the norm, not the exception.** TWSE Top 50 dividends are overwhelmingly combined cash-and-stock; modelling them as cash-only injects ~7% back-adjustment error (`ADR 0007`). O9 guards this.

**Ex-date vs pay-date are different events.** The share/basis mutation happens at ex-date; the cash arrives at pay-date (into the dated ledger). Conflating them breaks O2 and the dividend waterfall line. The ex-date→pay-date gap is an *expected* reconciliation divergence (accrued unpaid cash), not a tripwire.

**Float reliance hides under netting.** Under `NettingSettlement` a long-only book within netted means looks identical to instant settlement, so over-reliance on unsettled cash is invisible — until `SettledCashOnly` surfaces it. Both modes must be run for the cross-sectional demonstrator.

**The order state machine is the bug nest.** The Phase 4 retro flagged it; `PartiallyFilled → Expired`, `PartiallyFilled → Cancelled`, and out-of-order folds are the cells that break. They are first-class transitions with dedicated fixtures, not edge notes.

**Suspension-calendar pull may not be clean.** The TWTAWU historical pull is a bounded enrichment task; if it fights back, ship `NO_MARKET_DATA`-only (graceful degradation) rather than block the phase. Full halts on Top 50 are rare, so the cost of degrading is small.

---

## 10. Out of scope (explicit)

Not Phase 5:

- Short selling, borrow-availability / uptick (平盤下放空) rejection codes — a later shorting phase.
- Pairs trading and multi-leg atomicity (the real home of FOK) — a later phase.
- Limit / stop order types — later (Phase 5 is market orders only).
- Cross-bar resting orders (ROD distinct from IOC) — intraday.
- Real order-book matching (FIFO/pro-rata) — Phase 9 intraday, behind the same fill seam.
- A **live broker adapter** / real order placement — Phase 8 (the `BrokerBase` seam is built for it; the Phase 8 live-halt broker endpoint is a separate to-verify item, *not* `SymbolQuote.status`).
- **Backtester migration onto the shared core** — deferred, non-blocking (`ADR 0008`); the trigger is "interface proven by the Phase 5 sim." It changes no results, so it is not gated here.
- ML strategies — Phase 6 (they run through this simulator unchanged).
- Intraday bars / the across-the-bar execution-timing preset — a named post-Phase-8 phase (seams built, preset not).

If you find yourself building any of these mid-phase, stop and write a one-line ticket in `journal/phase_8_backlog.md`.

## 11. Forward-compatibility and reuse

This section makes explicit how Phase 5 fits the codebase **backward** (what it reuses from Phases 1–4, so nothing is rebuilt) and **forward** (how Phase 8 live trading reuses Phase 5, so the design reads as deliberate future-proofing). Both directions matter for anyone implementing against this spec.

### 11.1 Backward — what Phase 5 reuses from the existing codebase

Phase 5 builds on the existing layers and duplicates none of them:

- **`backtest/trades.py`** — refactored in step 0c to expose `LotLedger` (FIFO, partial reductions; `ADR 0006 §9`); `account.py` calls `on_fill` once per deal. `build_round_trips()` remains the batch public API, now a thin wrapper over `LotLedger`. Positions are *not* re-implemented.
- **`backtest` cost model (`TaiwanCostModel`)** — 0.1425%×折 commission (NT$20 floor, floored after the decimal per step 0a), 0.3% sell tax, slippage — reused verbatim for fills and for pre-trade cost-budgeting (D6).
- **`backtest/allocator.py`, `backtest/reconciler.py`** — the cross-sectional allocation and target→orders reconciliation are called by the session, not forked (allocation gains the cost-budgeted sizing of D6).
- **`backtest/engine.py`** — the existing frictionless backtester is the reconciliation *reference*, unchanged; its migration onto the shared core is deferred and non-blocking (§10).
- **`data.storage`** — `ReplayFeed` and `LiveFeed` read raw OHLCV via `read_ohlcv` and raw corporate actions via `read_corporate_actions`; `broker/` is the one layer permitted to import `data.storage` directly for this purpose (§3 import boundary). The strategy receives adjusted prices through `data.api` and the existing adjustment path (`ADR 0007`).
- **`exchange_calendars` XTAI** — gates settlement maturation and the `NO_MARKET_DATA` detection ("a bar should exist on a valid session").
- **The stateless `Strategy` / `CrossSectionalStrategy` contracts** (`ADR 0004`/`0006`) — signals/scores enter the session as data; no strategy is modified, and `assert_no_lookahead` is re-run unchanged.

The `broker/` import boundary (§3) enforces the direction: `broker/` imports *from* `backtest` / `data.api` / `universe` / `config`, never the reverse — the new layer sits cleanly atop the existing one.

### 11.2 Forward — the Phase 8 live-trading reuse contract

Phase 8 live trading is an *incremental* addition, not a rewrite. Stated explicitly so the intent is durable:

**Reused unchanged (the contract surface).** The `BrokerBase` seam (`submit`/`cancel`/`query`/`drain_events`), which the Fubon adapter implements. The order model and state machine — the live adapter tracks the three pre-fill sub-states *internally* for transport robustness (timeout on a `Submitted`-but-never-`Accepted` order, cancel semantics for un-acknowledged vs resting orders, post-disconnect reconstruction) but exposes only `Pending` upward, because the account and ledger react to *economic* events (fills), not transport events; the simulator has no transport, so `Pending` is simply the whole truth. The immutable event model and the **order-independent, idempotent fold** — built that way precisely because live broker events arrive out of order (Shioaji's deal-before-order case). The event-sourced JSONL log; the account model and lot ledger; `TaiwanCostModel` (now for pre-trade estimation; actuals arrive in broker fill reports); the `RejectionReason` enum (the adapter maps broker reject reasons onto it); the reconciliation machinery (with a live-vs-backtest reference); and the conservation oracles **O1–O4, which double as live invariant monitors / kill-switch triggers**.

**Reused with a swapped implementation (behind seams that already exist).** The `PriceLimitProvider` (derive → read the broker quote's `limitup_price`/`limitdown_price`); the suspension source (TWTAWU calendar → broker halt endpoint, same `NO_MARKET_DATA`/`SUSPENSION` semantics); the settlement model (simulated T+2 → the broker's real reported cash as primary, with the dated ledger optionally retained as a buying-power forecast cross-checked against the broker); the feed (historical/EOD → real-time).

**Simulator-only (no live counterpart).** The `FillModel` (reality fills the order — there is no `VolumeParticipationFiller` live) and `process_bar` (the simulation clock — live, reality advances asynchronously and the adapter receives fills via callbacks). This is exactly why both are `BrokerSimulator`-specific and **not** part of `BrokerBase`.

**Net-new in Phase 8.** The Fubon adapter (implements `BrokerBase`, registers the four callbacks `set_on_event`/`set_on_order`/`set_on_order_changed`/`set_on_filled`, maps them to our events); the real-time data feed; and the operational layer (monitoring, kill-switch, risk limits, scheduling, `.pfx` certificate management) — new *operational* scope, not a re-architecture of the execution core.

**The session is the one divergence point.** A `LiveSession` mirrors `PaperSession`'s `strategy → allocate → reconcile → submit → drain → fold → mark` spine and replaces only the *advance-and-collect* step: `PaperSession` calls `broker.process_bar(T+1)` to drive the simulation; `LiveSession` waits on real-time broker callback events. Both then `drain_events` identically. The Phase 5 loop is written with `advance-then-drain` isolated as the swap point, so `LiveSession` is a small subclass, not a parallel reimplementation.

---

## 12. New dependencies

| Package | Why |
|---|---|
| `duckdb` | Query the JSONL event log for reporting (already considered in the data layer; confirm it is in `pyproject.toml`). |
| `requests` (or `httpx`) | One-time TWTAWU historical suspension pull (step 13). Likely already present from the data layer; no new dep if so. |
| (otherwise none) | `pandas`, `numpy`, `exchange_calendars` (XTAI), `plotly`, and stdlib cover the rest — all present since earlier phases. |

---

## 13. Kickoff checklist

Before pasting this into Claude Code, confirm:

- [ ] Phase 4 is closed (`PROJECT_PLAN.md §6`; `journal/phase_4_retro.md` exists).
- [ ] ADRs `0008`, `0009`, `0010` are committed under `journal/decisions/`, and `journal/phase_5_references.md` (Step 0 reading) is present.
- [ ] `CLAUDE.md` carries the Phase 5 decisions (§2), the new `broker/` import boundary (§3), and the new module list (§3/§4).
- [ ] The Phase 4 public API imports cleanly (`Engine`, `TaiwanCostModel`, `EqualWeightTopN`, `reconciler`, `trades` lot ledger, `assert_no_lookahead`).
- [ ] All 50 tickers + 0050 Parquet files are present, with **raw OHLCV + corporate-action events** available (the feed reads raw, not adjusted).
- [ ] You have read this spec end-to-end and have no open questions.
