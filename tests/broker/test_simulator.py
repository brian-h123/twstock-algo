from datetime import date
from decimal import Decimal
from types import MappingProxyType

import pytest

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.broker.corporate_actions import CashMutation, ShareMutation
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
from twstock.broker.rejection import (
    DerivedPriceLimits,
    RejectionReason,
    SuspensionCalendar,
)
from twstock.broker.settlement import InstantSettlement, NettingSettlement
from twstock.broker.simulator import BrokerSimulator, _max_affordable_shares, _mutation_to_event

# ── helpers ──────────────────────────────────────────────────────────────────

_T1 = date(2026, 6, 1)  # Monday, confirmed XTAI session
_T2 = date(2026, 6, 2)  # Tuesday, confirmed XTAI session
_T3 = date(2026, 6, 3)  # Wednesday, confirmed XTAI session
_WEEKEND = date(2026, 6, 6)  # Saturday, confirmed non-session


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
    suspension_calendar: SuspensionCalendar | None = None,
    price_limit_provider=None,
) -> BrokerSimulator:
    return BrokerSimulator(
        capital=Decimal(capital),
        cost_model=TaiwanCostModel(),
        fill_model=fill_model if fill_model is not None else FullFill(),
        settlement=settlement if settlement is not None else InstantSettlement(),
        suspension_calendar=suspension_calendar,
        price_limit_provider=price_limit_provider,
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


def _seed_buy_fill(
    broker: BrokerSimulator,
    ticker: str = "2330",
    shares: int = 1000,
    d: date = _T1,
    fill_d: date = _T2,
    price: str = "100",
) -> OrderId:
    """Submit a buy at d, process at fill_d, and assert it fully filled.
    Returns the order_id. Uses the full public submit/process_bar path so
    the resulting position and cash state are exactly what a real run would
    produce -- not a shortcut that could drift from real behavior."""
    order_id = broker.submit(
        _order(
            order_id=f"seed-{ticker}-{d}",
            ticker=ticker,
            decided_at=d,
            side=Side.BUY,
            quantity=shares,
        )
    )
    broker.process_bar(_batch(fill_d, _bar(ticker=ticker, d=fill_d, price=price)))
    state = broker.query(order_id)
    assert state.status == OrderStatus.FILLED
    return order_id


# ── constructor ────────────────────────────────────────────────────────────


class TestConstructor:
    def test_default_suspension_calendar_and_price_limit_provider_are_independent_per_instance(
        self,
    ):
        """Regression: mutable-default-argument bug. Two instances built without
        explicit providers must not share state."""
        b1 = _broker()
        b2 = _broker()
        b1.process_bar(_batch(_T1, _bar(price="500")))
        # b2 must not have observed anything from b1's process_bar call
        assert b2.price_limit_provider.band("2330", _T1) is None
        assert b1.price_limit_provider is not b2.price_limit_provider
        assert b1.suspension_calendar is not b2.suspension_calendar

    def test_explicit_providers_are_used_as_given(self):
        my_calendar = SuspensionCalendar()
        my_limits = DerivedPriceLimits()
        b = _broker(suspension_calendar=my_calendar, price_limit_provider=my_limits)
        assert b.suspension_calendar is my_calendar
        assert b.price_limit_provider is my_limits

    def test_buying_power_equals_initial_capital(self):
        b = _broker(capital="500000")
        assert b._account.buying_power(_T1) == Decimal("500000")


# ── submit ─────────────────────────────────────────────────────────────────


class TestSubmit:
    def test_registers_working_order_and_returns_order_id(self):
        b = _broker()
        oid = b.submit(_order(order_id="o1"))
        assert oid == OrderId("o1")
        assert OrderId("o1") in b._working_orders

    def test_submitted_event_ts_is_order_decided_at(self):
        b = _broker()
        b.submit(_order(order_id="o1", decided_at=_T1))
        events = b.drain_events()
        assert len(events) == 1
        assert events[0].kind == EventKind.SUBMITTED
        assert events[0].ts == _T1

    def test_query_after_submit_is_pending(self):
        b = _broker()
        b.submit(_order(order_id="o1", quantity=1000))
        state = b.query(OrderId("o1"))
        assert state.status == OrderStatus.PENDING
        assert state.remaining_quantity == 1000

    def test_duplicate_order_id_raises_even_after_resolution(self):
        """Regression: fold's SUBMITTED branch has no legality check, so a
        naive re-submit would silently reset a resolved order. The guard
        must check account history (_account._orders), not just the
        currently-working set, since a resolved order has already left
        _working_orders."""
        b = _broker()
        b.submit(_order(order_id="o1", decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2)))
        assert b.query(OrderId("o1")).status == OrderStatus.FILLED
        assert OrderId("o1") not in b._working_orders  # already resolved and removed

        with pytest.raises(ValueError, match="already submitted"):
            b.submit(_order(order_id="o1", decided_at=_T2))

    def test_duplicate_order_id_raises_while_still_pending(self):
        b = _broker()
        b.submit(_order(order_id="o1"))
        with pytest.raises(ValueError, match="already submitted"):
            b.submit(_order(order_id="o1"))


# ── cancel ─────────────────────────────────────────────────────────────────


class TestCancel:
    def test_cancel_working_order_emits_cancel_and_removes_from_working_orders(self):
        b = _broker()
        b.submit(_order(order_id="o1", decided_at=_T1))
        b.drain_events()
        b.cancel(OrderId("o1"))
        assert OrderId("o1") not in b._working_orders
        assert b.query(OrderId("o1")).status == OrderStatus.CANCELLED
        events = b.drain_events()
        assert len(events) == 1
        assert events[0].kind == EventKind.CANCEL
        assert events[0].ts == _T1  # same as decided_at: no time passed since submit

    def test_cancel_unknown_order_id_raises_no_such_order(self):
        b = _broker()
        with pytest.raises(ValueError, match="no such order"):
            b.cancel(OrderId("ghost"))

    def test_cancel_already_resolved_order_raises_with_status(self):
        b = _broker()
        b.submit(_order(order_id="o1", decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2)))
        with pytest.raises(ValueError, match="already filled"):
            b.cancel(OrderId("o1"))


# ── query ──────────────────────────────────────────────────────────────────


class TestQuery:
    def test_unknown_order_id_raises(self):
        b = _broker()
        with pytest.raises(ValueError, match="no order found"):
            b.query(OrderId("ghost"))

    def test_reflects_fill_state(self):
        b = _broker()
        b.submit(_order(order_id="o1", quantity=500, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        state = b.query(OrderId("o1"))
        assert state.status == OrderStatus.FILLED
        assert state.filled_quantity == 500
        assert state.remaining_quantity == 0


# ── drain_events ───────────────────────────────────────────────────────────


class TestDrainEvents:
    def test_second_call_returns_empty(self):
        b = _broker()
        b.submit(_order(order_id="o1"))
        b.drain_events()
        assert b.drain_events() == []

    def test_accumulates_across_submit_and_process_bar_until_drained(self):
        b = _broker()
        b.submit(_order(order_id="o1", decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2)))
        events = b.drain_events()
        kinds = [e.kind for e in events]
        assert EventKind.SUBMITTED in kinds
        assert EventKind.SETTLE in kinds
        assert EventKind.FILL in kinds


# ── _reject ────────────────────────────────────────────────────────────────


class TestReject:
    def test_reject_preserves_quantities_and_sets_reason_payload(self):
        b = _broker()
        b.submit(_order(order_id="o1", ticker="9999", quantity=1000, decided_at=_T1))
        # 9999 never appears in any bar batch -> NO_MARKET_DATA
        b.process_bar(_batch(_T2, _bar(ticker="2330", d=_T2)))
        state = b.query(OrderId("o1"))
        assert state.status == OrderStatus.REJECTED
        assert state.filled_quantity == 0
        assert state.remaining_quantity == 1000
        events = [e for e in b.drain_events() if e.kind == EventKind.REJECT]
        assert events[0].payload["reason"] == RejectionReason.NO_MARKET_DATA.value


# ── _fill ──────────────────────────────────────────────────────────────────


class TestFill:
    def test_full_fill_emits_fill_only(self):
        b = _broker()
        b.submit(_order(order_id="o1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        events = [e for e in b.drain_events() if e.order_id == OrderId("o1")]
        kinds = [e.kind for e in events]
        assert EventKind.FILL in kinds
        assert EventKind.EXPIRE not in kinds

    def test_buy_costs_are_commission_only(self):
        b = _broker()
        b.submit(_order(order_id="o1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        fill = next(e for e in b.drain_events() if e.kind == EventKind.FILL)
        # fill_price = 100 * 1.0005 = 100.05; commission = floor(100050 * 0.000855) = 85
        assert fill.payload["fill_price"] == "100.05"
        assert fill.payload["total_cost"] == "85"

    def test_sell_costs_include_commission_and_tax(self):
        b = _broker()
        _seed_buy_fill(b, d=_T1, fill_d=_T2, shares=1000, price="100")
        b.submit(_order(order_id="s1", side=Side.SELL, quantity=1000, decided_at=_T2))
        b.process_bar(_batch(_T3, _bar(d=_T3, price="100")))
        fill = next(
            e for e in b.drain_events() if e.kind == EventKind.FILL and e.order_id == OrderId("s1")
        )
        # fill_price = 100 * 0.9995 = 99.95
        # commission = floor(99950*0.000855)=85; tax=floor(99950*0.003)=299
        assert fill.payload["fill_price"] == "99.95"
        assert fill.payload["total_cost"] == "384"

    def test_partial_fill_immediately_followed_by_expire(self):
        """The IOC/ROD same-day-collapse invariant: an under-filled order's
        remainder always expires in the same process_bar call, never rests."""
        b = _broker(
            capital="10000000", fill_model=VolumeParticipationFiller(participation_rate=0.05)
        )
        # seed prior_volume via a no-order bar first (nothing submitted yet)
        b.process_bar(_batch(_T1, _bar(d=_T1, volume=100_000)))
        b.submit(_order(order_id="o2", quantity=100_000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, volume=100_000)))
        events = [e for e in b.drain_events() if e.order_id == OrderId("o2")]
        kinds = [e.kind for e in events]
        assert kinds == [EventKind.SUBMITTED, EventKind.PARTIAL_FILL, EventKind.EXPIRE]
        assert b.query(OrderId("o2")).status == OrderStatus.EXPIRED
        assert OrderId("o2") not in b._working_orders
        partial = next(e for e in events if e.kind == EventKind.PARTIAL_FILL)
        assert partial.payload["cause"] == "liquidity"


# ── _resolve_order / process_bar: rejection paths ───────────────────────────


class TestMissingBarRejection:
    def test_missing_bar_on_session_day_rejects_no_market_data(self):
        b = _broker()
        b.submit(_order(order_id="o1", ticker="9999", decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(ticker="2330", d=_T2)))
        events = [e for e in b.drain_events() if e.kind == EventKind.REJECT]
        assert events[0].payload["reason"] == RejectionReason.NO_MARKET_DATA.value

    def test_missing_bar_with_confirmed_halt_rejects_suspension(self):
        class _AlwaysHalted(SuspensionCalendar):
            def is_halted(self, ticker: str, t: date) -> bool:
                return True

        b = _broker(suspension_calendar=_AlwaysHalted())
        b.submit(_order(order_id="o1", ticker="9999", decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(ticker="2330", d=_T2)))
        events = [e for e in b.drain_events() if e.kind == EventKind.REJECT]
        assert events[0].payload["reason"] == RejectionReason.SUSPENSION.value


class TestPriceLimitLock:
    def _seed_reference(self, b: BrokerSimulator, ticker: str = "2330") -> None:
        """Two plain bars so DerivedPriceLimits has a real band by T3."""
        b.process_bar(_batch(_T1, _bar(ticker=ticker, d=_T1, price="100")))
        b.process_bar(_batch(_T2, _bar(ticker=ticker, d=_T2, price="100")))

    def test_buy_locked_at_ceiling_rejects_exactly_once(self):
        """Regression: a missing `return` after this rejection previously let
        execution fall through into a second _reject/_fill call on the same
        (already-terminal) order, raising IllegalTransitionError. Must
        produce exactly one REJECT and nothing else."""
        b = _broker()
        self._seed_reference(b)
        b.submit(_order(order_id="o1", side=Side.BUY, decided_at=_T2))
        # band from ref=100 -> limit_up = 110.0; open at or above locks buys
        b.process_bar(_batch(_T3, _bar(d=_T3, price="110.0")))

        state = b.query(OrderId("o1"))
        assert state.status == OrderStatus.REJECTED
        events = [e for e in b.drain_events() if e.order_id == OrderId("o1")]
        reject_events = [e for e in events if e.kind == EventKind.REJECT]
        assert len(reject_events) == 1
        assert reject_events[0].payload["reason"] == RejectionReason.PRICE_LIMIT_LOCK.value
        assert OrderId("o1") not in b._working_orders

    def test_sell_locked_at_floor_rejects(self):
        b = _broker()
        self._seed_reference(b)
        b.submit(_order(order_id="o1", side=Side.SELL, decided_at=_T2))
        # band from ref=100 -> limit_down = 90.0; open at or below locks sells
        b.process_bar(_batch(_T3, _bar(d=_T3, price="90.0")))
        state = b.query(OrderId("o1"))
        assert state.status == OrderStatus.REJECTED
        events = [e for e in b.drain_events() if e.kind == EventKind.REJECT]
        assert events[0].payload["reason"] == RejectionReason.PRICE_LIMIT_LOCK.value

    def test_no_band_yet_does_not_lock(self):
        """First-ever observation for a ticker: band() is None, so no lock
        can apply regardless of price."""
        b = _broker()
        b.submit(_order(order_id="o1", side=Side.BUY, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        assert b.query(OrderId("o1")).status == OrderStatus.FILLED


class TestInsufficientBuyingPower:
    def test_zero_affordable_shares_rejects(self):
        b = _broker(capital="10")  # far too little to buy anything
        b.submit(_order(order_id="o1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        state = b.query(OrderId("o1"))
        assert state.status == OrderStatus.REJECTED
        events = [e for e in b.drain_events() if e.kind == EventKind.REJECT]
        assert events[0].payload["reason"] == RejectionReason.INSUFFICIENT_BUYING_POWER.value

    def test_partial_cash_affordability_partially_fills(self):
        b = _broker(capital="50100")  # enough for ~500 shares at ~100.05+commission
        b.submit(_order(order_id="o1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        state = b.query(OrderId("o1"))
        assert state.status == OrderStatus.EXPIRED
        assert 0 < state.filled_quantity < 1000

    def test_partial_cash_affordability_labels_cause_insufficient_buying_power(self):
        """ADR 0011 D2 follow-up: a cash-ceiling underfill must not be
        mislabeled "liquidity" -- the volume/participation cap never bound
        here (capital, not volume, is what's scarce)."""
        b = _broker(capital="50100")
        b.submit(_order(order_id="o1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        partial = next(
            e
            for e in b.drain_events()
            if e.kind == EventKind.PARTIAL_FILL and e.order_id == OrderId("o1")
        )
        assert partial.payload["cause"] == RejectionReason.INSUFFICIENT_BUYING_POWER.value

    def test_volume_cap_below_cash_ceiling_labels_cause_liquidity(self):
        """When plenty of cash is available but the participation cap binds
        first, the cause must read "liquidity", not the cash-ceiling label --
        confirms the binding_ceiling comparison picks the correct side."""
        b = _broker(
            capital="100000000", fill_model=VolumeParticipationFiller(participation_rate=0.05)
        )
        b.process_bar(_batch(_T1, _bar(d=_T1, volume=100_000)))
        b.submit(_order(order_id="o1", quantity=100_000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, volume=100_000, price="100")))
        partial = next(
            e
            for e in b.drain_events()
            if e.kind == EventKind.PARTIAL_FILL and e.order_id == OrderId("o1")
        )
        assert partial.payload["cause"] == "liquidity"

    def test_sell_is_never_cash_constrained(self):
        b = _broker(capital="1000000")
        _seed_buy_fill(b, d=_T1, fill_d=_T2, shares=1000, price="100")
        # drain the account's cash down to near zero via a second, larger buy... instead,
        # just confirm sells fill fully even though buying_power alone would be tiny.
        low_cash_b = _broker(capital="0")
        # 0 buying power still can't seed a position without cash, so seed via a broker
        # with real capital, then swap in a near-zero-cash account state directly:
        low_cash_b._account._ledger.settled_cash = Decimal("0")
        oid = low_cash_b.submit(
            _order(order_id="s1", side=Side.SELL, quantity=1000, decided_at=_T1)
        )
        # manually give the account a position to sell, mirroring a pre-existing holding
        from twstock.backtest.trades import LotLedger

        ll = LotLedger()
        ll.on_fill(Side.BUY, 1000, 100.0, 0.0)
        low_cash_b._account.positions["2330"] = ll
        low_cash_b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        assert low_cash_b.query(oid).status == OrderStatus.FILLED


class TestPriorVolumeFallback:
    def test_never_observed_ticker_is_not_volume_capped(self):
        """A ticker's very first appearance in the feed has no _prior_volume
        entry; the order must fill in full rather than being capped to zero.
        Quantity kept small so buying power is never the binding constraint --
        this isolates the volume-fallback behavior specifically."""
        b = _broker(fill_model=VolumeParticipationFiller(participation_rate=0.05))
        b.submit(_order(order_id="o1", quantity=1000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, volume=1)))  # thin bar's OWN volume irrelevant
        assert b.query(OrderId("o1")).status == OrderStatus.FILLED

    def test_recorded_prior_volume_caps_the_next_fill(self):
        b = _broker(fill_model=VolumeParticipationFiller(participation_rate=0.05))
        # first bar: no order, just to populate _prior_volume
        b.process_bar(_batch(_T1, _bar(d=_T1, volume=100_000)))
        assert b._prior_volume["2330"] == 100_000

        b.submit(_order(order_id="o1", quantity=100_000, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, volume=100_000)))
        state = b.query(OrderId("o1"))
        # capped at 5% of 100_000 = 5_000
        assert state.filled_quantity == 5_000
        assert state.status == OrderStatus.EXPIRED

    def test_zero_fill_sell_is_unreachable_by_design(self):
        """Documents the acknowledged taxonomy gap: a sell that fills zero
        shares purely from a liquidity floor has no RejectionReason, so the
        code asserts rather than silently mislabeling the cause."""
        b = _broker(fill_model=VolumeParticipationFiller(participation_rate=0.05))
        b.process_bar(_batch(_T1, _bar(d=_T1, volume=1)))  # trailing_adv=1 -> cap=0

        from twstock.backtest.trades import LotLedger

        ll = LotLedger()
        ll.on_fill(Side.BUY, 1000, 100.0, 0.0)
        b._account.positions["2330"] = ll
        b.submit(_order(order_id="s1", side=Side.SELL, quantity=1000, decided_at=_T1))
        with pytest.raises(AssertionError, match="unreachable"):
            b.process_bar(_batch(_T2, _bar(d=_T2, volume=1)))


# ── process_bar: invalid session date ───────────────────────────────────────


class TestInvalidSessionDate:
    def test_non_session_date_warns_and_leaves_working_orders_untouched(self):
        b = _broker()
        b.submit(_order(order_id="o1", decided_at=_T1))
        b.drain_events()
        with pytest.warns(UserWarning, match="non-session date"):
            b.process_bar(_batch(_WEEKEND, _bar(d=_WEEKEND)))
        assert OrderId("o1") in b._working_orders
        assert b.drain_events() == []

    def test_order_resolves_normally_on_the_next_real_session(self):
        b = _broker()
        b.submit(_order(order_id="o1", decided_at=_T1))
        with pytest.warns(UserWarning):
            b.process_bar(_batch(_WEEKEND, _bar(d=_WEEKEND)))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        assert b.query(OrderId("o1")).status == OrderStatus.FILLED


# ── process_bar: settlement ordering ────────────────────────────────────────


class TestSettleOrdering:
    def test_dividend_maturing_this_bar_funds_a_buy_in_the_same_call(self):
        b = _broker(capital="0", settlement=InstantSettlement())
        # directly seed a pending dividend maturing exactly on _T2
        from twstock.broker.settlement import DatedCashFlow

        b._account._ledger.pending.append(DatedCashFlow(amount=Decimal("50100"), settle_date=_T2))
        b.submit(_order(order_id="o1", quantity=500, decided_at=_T1))
        b.process_bar(_batch(_T2, _bar(d=_T2, price="100")))
        assert b.query(OrderId("o1")).status == OrderStatus.FILLED


# ── process_bar: corporate actions ──────────────────────────────────────────


class TestCorporateActionsDuringProcessBar:
    def test_split_on_held_position_doubles_shares_and_emits_split_event(self):
        b = _broker()
        _seed_buy_fill(b, d=_T1, fill_d=_T2, shares=1000, price="100")
        before_avg = b._account.positions["2330"].avg_cost

        b.process_bar(_batch(_T3, _bar(d=_T3, price="50", actions=(_stock_split(_T3, 0.5),))))
        assert b._account.positions["2330"].open_shares == 2000
        assert b._account.positions["2330"].avg_cost == pytest.approx(before_avg / 2)
        split_events = [e for e in b.drain_events() if e.kind == EventKind.SPLIT]
        assert len(split_events) == 1
        assert split_events[0].payload == {"ticker": "2330", "ratio": "2.0"}

    def test_action_on_unheld_ticker_is_a_no_op(self):
        b = _broker()
        b.process_bar(_batch(_T1, _bar(d=_T1, price="50", actions=(_stock_split(_T1, 0.5),))))
        assert "2330" not in b._account.positions
        assert [e for e in b.drain_events() if e.kind == EventKind.SPLIT] == []

    def test_cash_dividend_schedules_pay_date_cash_flow(self):
        b = _broker()
        _seed_buy_fill(b, d=_T1, fill_d=_T2, shares=1000, price="100")
        pay_date = date(2026, 6, 20)
        b.process_bar(
            _batch(_T3, _bar(d=_T3, price="98", actions=(_cash_dividend(_T3, pay_date, "2"),)))
        )
        div_events = [e for e in b.drain_events() if e.kind == EventKind.DIVIDEND]
        assert len(div_events) == 1
        assert div_events[0].payload["amount"] == "2000"
        assert div_events[0].payload["pay_date"] == pay_date.isoformat()
        assert div_events[0].ts == _T3  # declared_date (ex-date), not pay_date


# ── process_bar: sells before buys (rotation funding) ───────────────────────


def _seed_position_directly(
    broker: BrokerSimulator, ticker: str = "2330", shares: int = 1000, price: float = 100.0
) -> None:
    """Give the account a held position with no cash outlay -- used for
    scenarios that start with capital already fully deployed (a realistic
    'fully invested, no spare cash' starting point), where routing the seed
    through a real buy order isn't possible because there's no cash to buy
    with in the first place."""
    from twstock.backtest.trades import LotLedger

    ll = LotLedger()
    ll.on_fill(Side.BUY, shares, price, 0.0)
    broker._account.positions[ticker] = ll


class TestRotationFunding:
    def test_same_bar_sell_funds_same_bar_buy_under_netting(self):
        """The mandatory ★ full-rotation cash-bug regression (spec §6.4):
        under NettingSettlement, a same-bar sell's proceeds must be visible
        to a same-bar buy for a different ticker. Starts fully invested with
        zero spare cash -- the realistic scenario this fixture guards against."""
        b = _broker(capital="0", settlement=NettingSettlement())
        _seed_position_directly(b, ticker="2330", shares=1000, price=100.0)

        b.submit(
            _order(order_id="sell", ticker="2330", side=Side.SELL, quantity=1000, decided_at=_T2)
        )
        b.submit(_order(order_id="buy", ticker="2317", side=Side.BUY, quantity=500, decided_at=_T2))
        b.process_bar(
            _batch(
                _T3,
                _bar(ticker="2330", d=_T3, price="100"),
                _bar(ticker="2317", d=_T3, price="100"),
            )
        )

        assert b.query(OrderId("sell")).status == OrderStatus.FILLED
        assert b.query(OrderId("buy")).status == OrderStatus.FILLED

    def test_sells_are_resolved_before_buys_in_event_order(self):
        b = _broker(capital="0", settlement=NettingSettlement())
        _seed_position_directly(b, ticker="2330", shares=1000, price=100.0)
        b.submit(
            _order(order_id="sell", ticker="2330", side=Side.SELL, quantity=1000, decided_at=_T2)
        )
        b.submit(_order(order_id="buy", ticker="2317", side=Side.BUY, quantity=500, decided_at=_T2))
        b.process_bar(
            _batch(
                _T3,
                _bar(ticker="2330", d=_T3, price="100"),
                _bar(ticker="2317", d=_T3, price="100"),
            )
        )
        events = b.drain_events()
        sell_fill_idx = next(
            i
            for i, e in enumerate(events)
            if e.order_id == OrderId("sell") and e.kind == EventKind.FILL
        )
        buy_fill_idx = next(
            i
            for i, e in enumerate(events)
            if e.order_id == OrderId("buy") and e.kind == EventKind.FILL
        )
        assert sell_fill_idx < buy_fill_idx


# ── _max_affordable_shares ──────────────────────────────────────────────────


class TestMaxAffordableShares:
    def test_zero_ceiling_returns_zero(self):
        result = _max_affordable_shares(
            buying_power=Decimal("999999999"),
            ticker="2330",
            reference_price=Decimal("100"),
            cost_model=TaiwanCostModel(),
            ceiling=0,
        )
        assert result == 0

    def test_exact_boundary_affordability(self):
        # cost(500) = 500*100.05 + floor(50025*0.000855)=50025+42=50067
        # cost(501) = 501*100.05 + floor(50125.05*0.000855)=50125.05+42=50167.05
        result = _max_affordable_shares(
            buying_power=Decimal("50067"),
            ticker="2330",
            reference_price=Decimal("100"),
            cost_model=TaiwanCostModel(),
            ceiling=1000,
        )
        assert result == 500

    def test_never_exceeds_ceiling_even_with_unlimited_cash(self):
        result = _max_affordable_shares(
            buying_power=Decimal("99999999999"),
            ticker="2330",
            reference_price=Decimal("100"),
            cost_model=TaiwanCostModel(),
            ceiling=42,
        )
        assert result == 42


# ── _mutation_to_event ───────────────────────────────────────────────────────


class TestMutationToEvent:
    def test_share_mutation_becomes_split_event(self):
        mutation = ShareMutation(ratio=2.0, effective_date=_T2)
        event = _mutation_to_event("2330", mutation)
        assert event.kind == EventKind.SPLIT
        assert event.ts == _T2
        assert event.order_id is None
        assert event.payload == {"ticker": "2330", "ratio": "2.0"}

    def test_cash_mutation_becomes_dividend_event_with_declared_date_ts(self):
        mutation = CashMutation(amount=Decimal("2000"), declared_date=_T2, settle_date=_T3)
        event = _mutation_to_event("2330", mutation)
        assert event.kind == EventKind.DIVIDEND
        assert event.ts == _T2  # declared_date, not settle_date
        assert event.order_id is None
        assert event.payload == {"amount": "2000", "pay_date": _T3.isoformat()}
