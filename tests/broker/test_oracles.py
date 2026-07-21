"""Independent-oracle suite (O1-O10) -- the Phase 5 correctness gate (ADR 0008 D2).

Each oracle computes its expected answer by a route that shares no code with
the engine, so it can actually fail -- unlike reproducing the engine against
itself, which is a tautology under one shared execution core (D2 explicitly
rejects that as a check). Concretely: O5-O7 recompute cash/shares/costs from
a flat tally over the recorded event log rather than trusting the engine's
own incrementally-maintained totals; O1/O2/O4/O8/O9 restate the invariant in
terms the engine's own bookkeeping does not directly expose (mark-at-fill-
price neutrality, cash-flow-only-moves-on-trades, a fresh lot-deque sum,
value continuity across a corporate action); O3 tallies fills straight from
event payloads instead of trusting OrderState's incrementally-accumulated
filled_quantity; O10 replays the log into an independently constructed
Account.

O1, O2, O4, O8, O9, and O10 already have focused unit-level coverage
embedded in their owning module's test file (test_account.py,
test_settlement.py, test_corporate_actions.py, test_events.py) per the
spec's "invariants embedded per module" note. This file re-verifies those
same invariants end-to-end through the full BrokerSimulator (exercising the
cost model, fill model, and corporate-action decomposition together, not
just a hand-built event list), and is the sole home for O3, O5, O6, and O7.
Overlap with the per-module tests is intentional, not redundant: the whole
point of an independent oracle is that it does not share a code path with
what it's checking, so the same invariant is worth restating through a
different entry point.
"""

from datetime import date
from decimal import Decimal
from types import MappingProxyType

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.broker.account import Account, fold
from twstock.broker.events import (
    ActionType,
    Bar,
    BarBatch,
    CorporateAction,
    EventKind,
    OrderId,
    Side,
)
from twstock.broker.fill import FillModel, FullFill, VolumeParticipationFiller
from twstock.broker.order import Order, OrderStatus, TimeInForce
from twstock.broker.settlement import InstantSettlement, NettingSettlement
from twstock.broker.simulator import BrokerSimulator

# ── helpers ──────────────────────────────────────────────────────────────────

_T1 = date(2026, 6, 1)  # Monday, confirmed XTAI session
_T2 = date(2026, 6, 2)  # Tuesday
_T3 = date(2026, 6, 3)  # Wednesday
_T4 = date(2026, 6, 4)  # Thursday
_T5 = date(2026, 6, 5)  # Friday


def _bar(
    ticker: str = "2330",
    d: date = _T2,
    price: str = "100",
    volume: int = 1_000_000,
    actions: tuple[CorporateAction, ...] = (),
) -> Bar:
    p = Decimal(price)
    return Bar(
        ticker=ticker,
        date=d,
        open=p,
        high=p,
        low=p,
        close=p,
        volume=volume,
        corporate_actions=actions,
    )


def _batch(d: date, *bars: Bar) -> BarBatch:
    return BarBatch(date=d, bars=MappingProxyType({b.ticker: b for b in bars}))


def _order(
    order_id: str = "o1",
    ticker: str = "2330",
    decided_at: date = _T1,
    side: Side = Side.BUY,
    quantity: int = 1000,
    tif: TimeInForce = TimeInForce.IOC,
) -> Order:
    return Order(
        order_id=OrderId(order_id),
        ticker=ticker,
        decided_at=decided_at,
        side=side,
        quantity=quantity,
        tif=tif,
    )


def _broker(
    capital: str = "1000000",
    fill_model: FillModel | None = None,
    settlement=None,
) -> BrokerSimulator:
    return BrokerSimulator(
        capital=Decimal(capital),
        cost_model=TaiwanCostModel(),
        fill_model=fill_model if fill_model is not None else FullFill(),
        settlement=settlement if settlement is not None else InstantSettlement(),
    )


def _cash_dividend(ex_date: date, pay_date: date, cash_amount: str) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.CASH_DIVIDEND,
        ex_date=ex_date,
        cash_amount=Decimal(cash_amount),
        ratio=None,
        pay_date=pay_date,
        subscription_price=None,
    )


def _stock_split(ex_date: date, ratio: float) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.STOCK_SPLIT,
        ex_date=ex_date,
        cash_amount=None,
        ratio=ratio,
        pay_date=None,
        subscription_price=None,
    )


def _fills(events, order_id: OrderId | None = None, ticker: str | None = None) -> list:
    return [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)
        and (order_id is None or e.order_id == order_id)
        and (ticker is None or e.payload.get("ticker") == ticker)
    ]


# ── O1: value conservation, end-to-end through the full simulator ───────────


class TestO1ValueConservationEndToEnd:
    """O1: total_value changes over a bar only by Δ(mark) − costs + dividends."""

    def test_buy_fill_marked_at_fill_price_leaves_value_reduced_by_commission_only(self):
        b = _broker(capital="1000000")
        cost_model = TaiwanCostModel()
        tv_before = b._account.total_value({})

        b.submit(_order(quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))

        fill_price, commission = cost_model.buy_costs("2330", 1000, 100.0)
        tv_after = b._account.total_value({"2330": Decimal(str(fill_price))})
        assert tv_after == tv_before - Decimal(str(commission))

    def test_sell_fill_marked_at_fill_price_leaves_value_reduced_by_commission_and_tax_only(self):
        b = _broker(capital="2000000")
        cost_model = TaiwanCostModel()
        b.submit(_order(order_id="b1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))

        # Mark the pre-sale position at the sell's OWN fill price (which
        # differs from the bar's stated 100 by slippage) -- otherwise the
        # slippage-driven mark gap pollutes the cost-only comparison.
        sell_fill_price, commission, tax = cost_model.sell_costs("2330", 1000, 100.0)
        tv_before = b._account.total_value({"2330": Decimal(str(sell_fill_price))})

        b.submit(_order(order_id="s1", side=Side.SELL, quantity=1000, decided_at=_T2))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="100")))

        tv_after = b._account.total_value({})
        assert tv_after == tv_before - Decimal(str(commission)) - Decimal(str(tax))

    def test_no_trade_bar_moves_value_by_exactly_shares_times_price_delta(self):
        b = _broker(capital="1000000")
        b.submit(_order(quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        tv_at_t2 = b._account.total_value({"2330": Decimal("100")})

        b.process_bar(_batch(_T3, _bar(d=_T3, price="105")))  # no orders this bar
        tv_at_t3 = b._account.total_value({"2330": Decimal("105")})

        assert tv_at_t3 - tv_at_t2 == 1000 * (Decimal("105") - Decimal("100"))

    def test_dividend_declaration_increases_value_by_cash_amount_times_shares(self):
        b = _broker(capital="1000000")
        b.submit(_order(quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        tv_before = b._account.total_value({"2330": Decimal("100")})

        b.process_bar(
            _batch(_T3, _bar(d=_T3, price="100", actions=(_cash_dividend(_T3, _T4, "2"),)))
        )
        tv_after = b._account.total_value({"2330": Decimal("100")})

        assert tv_after == tv_before + Decimal("2") * 1000


# ── O2: settlement neutrality, end-to-end through the full simulator ────────


class TestO2SettlementNeutralityEndToEnd:
    """O2: total cash (settled + pending) moves only on trades, never on
    maturation or the per-bar netting flush -- checked across a run that
    includes several no-trade bars, where NettingSettlement's on_bar_start
    fires every bar regardless of whether anything happened."""

    def _total_cash(self, b: BrokerSimulator) -> Decimal:
        # bar_net is the current, still-open bar's not-yet-flushed net --
        # legitimately outside `pending` until on_bar_start/close moves it
        # there (that's the whole point of O2: the flush must not change
        # this three-way total, only reclassify between its buckets).
        ledger = b._account._ledger
        pending_sum = sum((cf.amount for cf in ledger.pending), Decimal(0))
        return ledger.settled_cash + pending_sum + ledger.bar_net

    def test_total_cash_unchanged_by_maturation_across_several_no_trade_bars(self):
        b = _broker(capital="1000000", settlement=NettingSettlement())
        cost_model = TaiwanCostModel()
        before = self._total_cash(b)

        b.submit(_order(quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))

        fill_price, commission = cost_model.buy_costs("2330", 1000, 100.0)
        expected_delta = -(1000 * Decimal(str(fill_price)) + Decimal(str(commission)))
        after_trade = self._total_cash(b)
        assert after_trade == before + expected_delta

        for d in [_T3, _T4, _T5]:
            b.process_bar(_batch(d, _bar(d=d, price="100")))
            assert self._total_cash(b) == after_trade


# ── O3: order-quantity conservation ──────────────────────────────────────────


class TestO3OrderQuantityConservation:
    """O3: filled + cancelled/expired/rejected-remainder == original at
    terminal state. filled is independently tallied straight from FILL /
    PARTIAL_FILL event payloads, not read from OrderState.filled_quantity."""

    def _independent_filled(self, events, order_id: OrderId) -> int:
        return sum(int(e.payload["shares"]) for e in _fills(events, order_id=order_id))

    def test_full_fill_conserves_quantity(self):
        b = _broker(fill_model=FullFill())
        b.submit(_order(order_id="o1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2)))
        events = b.drain_events()

        filled = self._independent_filled(events, OrderId("o1"))
        state = b.query(OrderId("o1"))
        assert filled == 1000
        assert state.status == OrderStatus.FILLED
        assert filled == state.filled_quantity
        assert filled + state.remaining_quantity == 1000

    def test_partial_then_auto_expire_conserves_quantity(self):
        b = _broker(
            capital="10000000", fill_model=VolumeParticipationFiller(participation_rate=0.05)
        )
        b.process_bar(_batch(_T1, _bar(d=_T1, volume=100_000)))  # warm up prior_volume
        b.submit(_order(order_id="o2", quantity=100_000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, volume=100_000)))
        events = b.drain_events()

        filled = self._independent_filled(events, OrderId("o2"))
        state = b.query(OrderId("o2"))
        assert 0 < filled < 100_000
        assert filled == 5_000  # 5% of 100,000
        assert state.status == OrderStatus.EXPIRED
        assert filled == state.filled_quantity
        assert filled + state.remaining_quantity == 100_000

    def test_rejection_conserves_quantity_as_entirely_unfilled(self):
        b = _broker(capital="10")  # far too little to buy anything
        b.submit(_order(order_id="o3", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        events = b.drain_events()

        filled = self._independent_filled(events, OrderId("o3"))
        state = b.query(OrderId("o3"))
        assert filled == 0
        assert state.status == OrderStatus.REJECTED
        assert state.filled_quantity == 0
        assert state.remaining_quantity == 1000

    def test_cancel_before_any_bar_conserves_quantity_as_entirely_unfilled(self):
        b = _broker()
        b.submit(_order(order_id="o4", quantity=1000, decided_at=_T1))
        b.drain_events()
        b.cancel(OrderId("o4"))
        events = b.drain_events()

        filled = self._independent_filled(events, OrderId("o4"))
        state = b.query(OrderId("o4"))
        assert filled == 0
        assert state.status == OrderStatus.CANCELLED
        assert filled + state.remaining_quantity == 1000


# ── O4: lot/position consistency, end-to-end through the full simulator ────


class TestO4LotPositionConsistencyEndToEnd:
    """O4: a fresh sum over the open-lot deque equals the incrementally-
    maintained open_shares counter, across buys, sells, and a split, driven
    through real submit/process_bar calls rather than direct fold()."""

    def _lot_sum(self, ledger) -> int:
        return sum(lot.shares for lot in ledger._lot_deque)

    def test_two_buys_then_a_partial_sell_then_a_split(self):
        b = _broker(capital="5000000", fill_model=FullFill())
        b.submit(_order(order_id="b1", quantity=700, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        b.submit(_order(order_id="b2", quantity=300, decided_at=_T2))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="105")))

        ll = b._account.positions["2330"]
        assert self._lot_sum(ll) == ll.open_shares == 1000

        b.submit(_order(order_id="s1", side=Side.SELL, quantity=400, decided_at=_T3))
        b.process_bar(
            _batch(_T4, _bar(d=_T4, price="103", actions=(_stock_split(_T4, 0.5),)))
        )  # split (x2) applies to the pre-sell 1000 shares, then the 400-share sell resolves

        ll = b._account.positions["2330"]
        assert self._lot_sum(ll) == ll.open_shares == 1600


# ── O5: cash oracle ──────────────────────────────────────────────────────────


class TestO5CashOracle:
    """O5: initial − Σbuys − Σcosts + Σsells + Σdividends (flat blotter sum,
    recomputed fresh via TaiwanCostModel from known reference prices/shares)
    == account settled cash."""

    def test_matches_across_two_buys_and_a_sell(self):
        capital = Decimal("1000000")
        b = _broker(capital=str(capital), fill_model=FullFill())
        cost_model = TaiwanCostModel()

        b.submit(_order(order_id="b1", quantity=700, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        b.submit(_order(order_id="b2", quantity=300, decided_at=_T2))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="102")))
        b.submit(_order(order_id="s1", side=Side.SELL, quantity=500, decided_at=_T3))
        b.process_bar(_batch(_T4, _bar(d=_T4, price="105")))

        fp1, c1 = cost_model.buy_costs("2330", 700, 100.0)
        fp2, c2 = cost_model.buy_costs("2330", 300, 102.0)
        fp3, c3, tax3 = cost_model.sell_costs("2330", 500, 105.0)
        expected = (
            capital
            - (700 * Decimal(str(fp1)) + Decimal(str(c1)))
            - (300 * Decimal(str(fp2)) + Decimal(str(c2)))
            + (500 * Decimal(str(fp3)) - Decimal(str(c3)) - Decimal(str(tax3)))
        )
        assert b._account._ledger.settled_cash == expected

    def test_matches_with_a_matured_dividend_included(self):
        capital = Decimal("1000000")
        b = _broker(capital=str(capital), fill_model=FullFill())
        cost_model = TaiwanCostModel()

        b.submit(_order(order_id="b1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        fill_price, commission = cost_model.buy_costs("2330", 1000, 100.0)

        b.process_bar(
            _batch(_T3, _bar(d=_T3, price="98", actions=(_cash_dividend(_T3, _T4, "2"),)))
        )
        b.process_bar(_batch(_T4, _bar(d=_T4, price="97")))  # matures the dividend

        buy_outlay = 1000 * Decimal(str(fill_price)) + Decimal(str(commission))
        expected = capital - buy_outlay + Decimal("2") * 1000
        assert b._account._ledger.settled_cash == expected
        assert b._account._ledger.pending == []


# ── O6: share oracle ─────────────────────────────────────────────────────────


class TestO6ShareOracle:
    """O6: Σbuy fills − Σsell fills (+ CA share-adds), tallied independently
    from event payloads, == the lot-ledger's open_shares per ticker."""

    def _independent_shares(self, events, ticker: str) -> int:
        total = 0
        for e in _fills(events, ticker=ticker):
            shares = int(e.payload["shares"])
            total += shares if e.payload["side"] == "buy" else -shares
        return total

    def test_matches_across_multiple_fills_two_tickers(self):
        b = _broker(capital="5000000", fill_model=FullFill())
        b.submit(_order(order_id="b1", ticker="2330", quantity=700, decided_at=_T1))
        b.submit(_order(order_id="b2", ticker="2317", quantity=200, decided_at=_T1))
        bar_2330_t2 = _bar(ticker="2330", d=_T2, price="100")
        bar_2317_t2 = _bar(ticker="2317", d=_T2, price="50")
        b.process_bar(_batch(_T2, bar_2330_t2, bar_2317_t2))
        b.submit(_order(order_id="b3", ticker="2330", quantity=300, decided_at=_T2))
        b.submit(_order(order_id="s1", ticker="2317", side=Side.SELL, quantity=50, decided_at=_T2))
        bar_2330_t3 = _bar(ticker="2330", d=_T3, price="105")
        bar_2317_t3 = _bar(ticker="2317", d=_T3, price="52")
        b.process_bar(_batch(_T3, bar_2330_t3, bar_2317_t3))

        events = b.drain_events()
        assert self._independent_shares(events, "2330") == 1000
        assert b._account.positions["2330"].open_shares == 1000
        assert self._independent_shares(events, "2317") == 150
        assert b._account.positions["2317"].open_shares == 150

    def test_matches_including_a_split_share_add(self):
        b = _broker(capital="5000000", fill_model=FullFill())
        b.submit(_order(order_id="b1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="50", actions=(_stock_split(_T3, 0.5),))))

        events = b.drain_events()
        fill_shares = self._independent_shares(events, "2330")
        [split_event] = [e for e in events if e.kind == EventKind.SPLIT]
        ratio = float(split_event.payload["ratio"])

        expected = round(fill_shares * ratio)
        assert expected == b._account.positions["2330"].open_shares == 2000


# ── O7: cost oracle ──────────────────────────────────────────────────────────


class TestO7CostOracle:
    """O7: cost model re-applied per fill from the known reference price and
    filled shares, summed, == accumulated recorded costs. Catches a
    double-charged or wrongly-floored commission/tax."""

    def test_buy_and_sell_fill_costs_match_a_fresh_cost_model_application(self):
        b = _broker(capital="5000000", fill_model=FullFill())
        cost_model = TaiwanCostModel()

        b.submit(_order(order_id="b1", quantity=777, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="123.45")))
        b.submit(_order(order_id="s1", side=Side.SELL, quantity=777, decided_at=_T2))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="130.7")))
        events = b.drain_events()

        buy_fill = next(e for e in _fills(events) if e.payload["side"] == "buy")
        sell_fill = next(e for e in _fills(events) if e.payload["side"] == "sell")

        exp_fp, exp_c = cost_model.buy_costs("2330", 777, 123.45)
        assert buy_fill.payload["fill_price"] == str(Decimal(str(exp_fp)))
        assert buy_fill.payload["total_cost"] == str(Decimal(str(exp_c)))

        exp_fp2, exp_c2, exp_tax2 = cost_model.sell_costs("2330", 777, 130.7)
        assert sell_fill.payload["fill_price"] == str(Decimal(str(exp_fp2)))
        assert sell_fill.payload["total_cost"] == str(Decimal(str(exp_c2)) + Decimal(str(exp_tax2)))

    def test_accumulated_cost_matches_sum_of_freshly_reapplied_costs_across_several_fills(self):
        """Regression guard for the floor-rounding-per-fill fix (step 0a):
        several small, independently-submitted orders should each floor
        their own commission to the NT$20 minimum, and the total must equal
        the sum of separately re-derived per-fill costs -- not a single
        floor applied once to the combined notional."""
        b = _broker(capital="500000", fill_model=FullFill())
        cost_model = TaiwanCostModel()
        quantities = [10, 20, 15]
        for i, qty in enumerate(quantities):
            b.submit(_order(order_id=f"b{i}", quantity=qty, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="50")))
        events = b.drain_events()

        fills = [e for e in _fills(events) if e.payload["side"] == "buy"]
        assert len(fills) == 3
        total_recorded_cost = sum(Decimal(e.payload["total_cost"]) for e in fills)

        expected_total = Decimal(0)
        for qty in quantities:
            _, c = cost_model.buy_costs("2330", qty, 50.0)
            expected_total += Decimal(str(c))

        assert total_recorded_cost == expected_total
        # all three floor to the NT$20 minimum, independently
        assert all(Decimal(e.payload["total_cost"]) == Decimal("20") for e in fills)


# ── O8: split oracle, end-to-end value continuity ───────────────────────────


class TestO8SplitOracleEndToEnd:
    """O8: N shares at basis B, ratio r → rN shares at basis B/r; cash is
    unaffected and total value is continuous across the split when marked
    at a proportionally-adjusted price."""

    def test_split_leaves_cash_unchanged_and_value_continuous(self):
        b = _broker(capital="5000000", fill_model=FullFill())
        b.submit(_order(quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))

        cash_before = b._account._ledger.settled_cash
        pre_price = Decimal("240")
        value_before = b._account.total_value({"2330": pre_price})

        schema_ratio = 0.5  # share_multiplier -> 1/0.5 = 2.0 (a 2-for-1 split)
        split_bar = _bar(d=_T3, price="120", actions=(_stock_split(_T3, schema_ratio),))
        b.process_bar(_batch(_T3, split_bar))

        assert b._account._ledger.settled_cash == cash_before  # zero cash effect

        multiplier = 2.0
        post_price = pre_price / Decimal(str(multiplier))
        value_after = b._account.total_value({"2330": post_price})
        assert value_after == value_before
        assert b._account.positions["2330"].open_shares == 2000


# ── O9: combined dividend oracle, end-to-end value continuity ───────────────


class TestO9CombinedDividendOracleEndToEnd:
    """O9: N shares, cash c, stock ratio s → N(1+s) shares + a receivable Nc
    at the ex-date; total value is continuous across the ex-date when marked
    at the dividend-adjusted price (P−c)/(1+s)."""

    def test_combined_dividend_is_value_continuous_across_ex_date(self):
        b = _broker(capital="5000000", fill_model=FullFill())
        b.submit(_order(quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))

        cash_before = b._account._ledger.settled_cash
        pre_price = Decimal("100")
        value_before = b._account.total_value({"2330": pre_price})

        cash_amount = Decimal("2")
        stock_ratio = 0.4  # multiplier = 1 + 0.4 = 1.4, chosen so the post-price divides exactly
        action = CorporateAction(
            action_type=ActionType.CASH_AND_STOCK_DIVIDEND,
            ex_date=_T3,
            cash_amount=cash_amount,
            ratio=stock_ratio,
            pay_date=_T4,
            subscription_price=None,
        )
        b.process_bar(_batch(_T3, _bar(d=_T3, price="90", actions=(action,))))

        # not settled yet -- it's a pending receivable, not settled cash
        assert b._account._ledger.settled_cash == cash_before
        [flow] = b._account._ledger.pending
        assert flow.amount == 1000 * cash_amount  # 2000

        multiplier = 1.0 + stock_ratio  # (100-2)/1.4 = 70 exactly
        post_price = (pre_price - cash_amount) / Decimal(str(multiplier))
        value_after = b._account.total_value({"2330": post_price})
        assert value_after == value_before
        assert b._account.positions["2330"].open_shares == 1400


# ── O10: fold idempotence, end-to-end through the full simulator ────────────


class TestO10FoldIdempotenceEndToEnd:
    """O10: the same scripted scenario run twice produces identical final
    state, and replaying the drained event log into an independently
    constructed Account reproduces the state the simulator built live."""

    def _run_scenario(self) -> BrokerSimulator:
        b = _broker(capital="1000000", fill_model=FullFill(), settlement=NettingSettlement())
        b.submit(_order(order_id="b1", quantity=700, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        b.submit(_order(order_id="s1", side=Side.SELL, quantity=300, decided_at=_T2))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="105")))
        return b

    def test_two_independent_runs_of_the_same_scenario_agree(self):
        b1 = self._run_scenario()
        b2 = self._run_scenario()

        assert b1._account._ledger.settled_cash == b2._account._ledger.settled_cash
        assert (
            b1._account.positions["2330"].open_shares == b2._account.positions["2330"].open_shares
        )
        assert b1.query(OrderId("b1")).status == b2.query(OrderId("b1")).status
        assert b1.query(OrderId("s1")).status == b2.query(OrderId("s1")).status

    def test_replaying_the_drained_log_into_a_fresh_account_reproduces_live_state(self):
        b = _broker(capital="1000000", fill_model=FullFill())
        b.submit(_order(order_id="b1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        b.submit(_order(order_id="s1", side=Side.SELL, quantity=400, decided_at=_T2))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="105")))
        live = b._account

        events = b.drain_events()
        fresh = Account(initial_capital=Decimal("1000000"), settlement=InstantSettlement())
        fold(events, into=fresh)

        assert fresh._ledger.settled_cash == live._ledger.settled_cash
        assert fresh.positions["2330"].open_shares == live.positions["2330"].open_shares
        assert fresh._orders[OrderId("b1")].status == live._orders[OrderId("b1")].status
        assert fresh._orders[OrderId("s1")].status == live._orders[OrderId("s1")].status
