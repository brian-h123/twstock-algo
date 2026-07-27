from datetime import date
from decimal import Decimal

import pytest

from twstock.backtest.trades import LotLedger
from twstock.broker.account import Account, fold
from twstock.broker.events import BrokerEvent, EventKind, OrderId, Side
from twstock.broker.order import OrderStatus
from twstock.broker.settlement import (
    DatedCashFlow,
    InstantSettlement,
    NettingSettlement,
    SettledCashOnly,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_T1 = date(2026, 7, 1)
_T2 = date(2026, 7, 2)


def _account(capital: str = "1000000", settlement=None) -> Account:
    if settlement is None:
        settlement = InstantSettlement()
    return Account(initial_capital=Decimal(capital), settlement=settlement)


def _submitted(order_id: str, quantity: int, ts: date = _T1) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.SUBMITTED,
        order_id=OrderId(order_id),
        payload={"quantity": str(quantity)},
    )


def _fill_event(
    order_id: str,
    ticker: str,
    side: Side,
    shares: int,
    fill_price: str,
    total_cost: str = "0",
    ts: date = _T1,
) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.FILL,
        order_id=OrderId(order_id),
        payload={
            "ticker": ticker,
            "side": side.value,
            "shares": str(shares),
            "fill_price": fill_price,
            "total_cost": total_cost,
        },
    )


def _partial_fill_event(
    order_id: str,
    ticker: str,
    side: Side,
    shares: int,
    fill_price: str,
    total_cost: str = "0",
    ts: date = _T1,
) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.PARTIAL_FILL,
        order_id=OrderId(order_id),
        payload={
            "ticker": ticker,
            "side": side.value,
            "shares": str(shares),
            "fill_price": fill_price,
            "total_cost": total_cost,
        },
    )


def _terminal(order_id: str, kind: EventKind, ts: date = _T1) -> BrokerEvent:
    return BrokerEvent(ts=ts, kind=kind, order_id=OrderId(order_id), payload={})


def _dividend(amount: str, pay_date: date, ts: date = _T1) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.DIVIDEND,
        order_id=None,
        payload={"amount": amount, "pay_date": pay_date.isoformat()},
    )


def _split(ticker: str, ratio: str, ts: date = _T1) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.SPLIT,
        order_id=None,
        payload={"ticker": ticker, "ratio": ratio},
    )


def _settle(ts: date = _T2) -> BrokerEvent:
    return BrokerEvent(ts=ts, kind=EventKind.SETTLE, order_id=None, payload={})


# ── Account init ──────────────────────────────────────────────────────────────


class TestAccountInit:
    def test_initial_capital_in_settled_cash(self):
        acc = _account("500000")
        assert acc._ledger.settled_cash == Decimal("500000")

    def test_initial_positions_empty(self):
        assert _account().positions == {}

    def test_initial_orders_empty(self):
        assert _account()._orders == {}


# ── buying_power ──────────────────────────────────────────────────────────────


class TestBuyingPower:
    def test_instant_settlement_equals_settled_cash(self):
        acc = _account("800000", InstantSettlement())
        assert acc.buying_power(as_of=_T1) == Decimal("800000")

    def test_settled_cash_only_equals_settled_cash(self):
        acc = _account("800000", SettledCashOnly())
        assert acc.buying_power(as_of=_T1) == Decimal("800000")

    def test_netting_before_any_trade_equals_settled_cash(self):
        acc = _account("800000", NettingSettlement())
        assert acc.buying_power(as_of=_T1) == Decimal("800000")


# ── mark_to_market ────────────────────────────────────────────────────────────


class TestMarkToMarket:
    def test_empty_positions_mark_is_zero(self):
        assert _account().mark_to_market({"2330": Decimal("500")}) == Decimal("0")

    def test_single_position_shares_times_price(self):
        acc = _account()
        fold([_submitted("o1", 1000), _fill_event("o1", "2330", Side.BUY, 1000, "500")], acc)
        assert acc.mark_to_market({"2330": Decimal("550")}) == Decimal("550000")

    def test_flat_position_excluded(self):
        acc = _account("2000000")
        fold(
            [
                _submitted("o1", 1000),
                _fill_event("o1", "2330", Side.BUY, 1000, "500"),
                _submitted("o2", 1000),
                _fill_event("o2", "2330", Side.SELL, 1000, "500"),
            ],
            acc,
        )
        assert acc.mark_to_market({"2330": Decimal("600")}) == Decimal("0")

    def test_prices_for_unheld_ticker_ignored(self):
        acc = _account()
        fold([_submitted("o1", 1000), _fill_event("o1", "2330", Side.BUY, 1000, "500")], acc)
        mark = acc.mark_to_market({"2330": Decimal("500"), "2317": Decimal("100")})
        assert mark == Decimal("500000")


# ── total_value ───────────────────────────────────────────────────────────────


class TestTotalValue:
    def test_initial_total_value_equals_capital(self):
        assert _account("1000000").total_value({}) == Decimal("1000000")

    def test_sums_settled_pending_and_mark(self):
        acc = _account("500000")
        acc._ledger.pending.append(DatedCashFlow(Decimal("100000"), date(2026, 7, 5)))
        fold([_submitted("o1", 100), _fill_event("o1", "2330", Side.BUY, 100, "500")], acc)
        # settled = 500,000 − 50,000 = 450,000; pending = 100,000; mark = 100 × 550 = 55,000
        assert acc.total_value({"2330": Decimal("550")}) == Decimal("605000")


# ── fold: SUBMITTED ───────────────────────────────────────────────────────────


class TestFoldSubmitted:
    def test_creates_pending_order_state(self):
        acc = _account()
        fold([_submitted("o1", 1000)], acc)
        state = acc._orders[OrderId("o1")]
        assert state.status == OrderStatus.PENDING
        assert state.filled_quantity == 0
        assert state.remaining_quantity == 1000
        assert state.avg_fill_price is None


# ── fold: FILL ────────────────────────────────────────────────────────────────


class TestFoldFill:
    def test_creates_position_and_marks_order_filled(self):
        acc = _account()
        fold([_submitted("o1", 1000), _fill_event("o1", "2330", Side.BUY, 1000, "500")], acc)
        assert acc.positions["2330"].open_shares == 1000
        state = acc._orders[OrderId("o1")]
        assert state.status == OrderStatus.FILLED
        assert state.filled_quantity == 1000
        assert state.remaining_quantity == 0

    def test_sets_avg_fill_price(self):
        acc = _account()
        fold([_submitted("o1", 500), _fill_event("o1", "2330", Side.BUY, 500, "550")], acc)
        assert acc._orders[OrderId("o1")].avg_fill_price == pytest.approx(550.0)

    def test_instant_settlement_moves_cash(self):
        acc = _account("1000000", InstantSettlement())
        fold(
            [_submitted("o1", 1000), _fill_event("o1", "2330", Side.BUY, 1000, "500", "4275")], acc
        )
        expected = Decimal("1000000") - (1000 * Decimal("500") + Decimal("4275"))
        assert acc._ledger.settled_cash == expected

    def test_second_fill_same_ticker_adds_to_position(self):
        acc = _account("2000000")
        fold(
            [
                _submitted("o1", 1000),
                _fill_event("o1", "2330", Side.BUY, 1000, "500"),
                _submitted("o2", 500),
                _fill_event("o2", "2330", Side.BUY, 500, "510"),
            ],
            acc,
        )
        assert acc.positions["2330"].open_shares == 1500


# ── fold: PARTIAL_FILL ────────────────────────────────────────────────────────


class TestFoldPartialFill:
    def test_sets_partially_filled_status_and_quantities(self):
        acc = _account()
        fold([_submitted("o1", 1000), _partial_fill_event("o1", "2330", Side.BUY, 400, "500")], acc)
        state = acc._orders[OrderId("o1")]
        assert state.status == OrderStatus.PARTIALLY_FILLED
        assert state.filled_quantity == 400
        assert state.remaining_quantity == 600

    def test_partial_then_fill_computes_weighted_avg_price(self):
        acc = _account("2000000")
        fold(
            [
                _submitted("o1", 1000),
                _partial_fill_event("o1", "2330", Side.BUY, 400, "500"),
                _fill_event("o1", "2330", Side.BUY, 600, "510"),
            ],
            acc,
        )
        state = acc._orders[OrderId("o1")]
        assert state.status == OrderStatus.FILLED
        assert state.filled_quantity == 1000
        expected_avg = (400 * 500 + 600 * 510) / 1000
        assert state.avg_fill_price == pytest.approx(expected_avg)


# ── fold: REJECT / EXPIRE / CANCEL ───────────────────────────────────────────


class TestFoldTerminalEvents:
    @pytest.mark.parametrize(
        "kind, expected_status",
        [
            (EventKind.REJECT, OrderStatus.REJECTED),
            (EventKind.EXPIRE, OrderStatus.EXPIRED),
            (EventKind.CANCEL, OrderStatus.CANCELLED),
        ],
    )
    def test_terminal_event_sets_correct_status(self, kind, expected_status):
        acc = _account()
        fold([_submitted("o1", 1000), _terminal("o1", kind)], acc)
        assert acc._orders[OrderId("o1")].status == expected_status

    def test_reject_preserves_quantities(self):
        acc = _account()
        fold([_submitted("o1", 1000), _terminal("o1", EventKind.REJECT)], acc)
        state = acc._orders[OrderId("o1")]
        assert state.filled_quantity == 0
        assert state.remaining_quantity == 1000

    def test_cancel_after_partial_fill_preserves_filled_quantity(self):
        acc = _account("2000000")
        fold(
            [
                _submitted("o1", 1000),
                _partial_fill_event("o1", "2330", Side.BUY, 400, "500"),
                _terminal("o1", EventKind.CANCEL),
            ],
            acc,
        )
        state = acc._orders[OrderId("o1")]
        assert state.status == OrderStatus.CANCELLED
        assert state.filled_quantity == 400
        assert state.remaining_quantity == 600


# ── fold: DIVIDEND ────────────────────────────────────────────────────────────


class TestFoldDividend:
    def test_adds_dated_cash_flow_to_pending(self):
        acc = _account()
        pay = date(2026, 7, 20)
        fold([_dividend("5000.00", pay)], acc)
        assert len(acc._ledger.pending) == 1
        assert acc._ledger.pending[0].amount == Decimal("5000.00")
        assert acc._ledger.pending[0].settle_date == pay

    def test_does_not_touch_settled_cash(self):
        acc = _account("1000000")
        fold([_dividend("5000.00", date(2026, 7, 20))], acc)
        assert acc._ledger.settled_cash == Decimal("1000000")


# ── fold: SPLIT ───────────────────────────────────────────────────────────────


class TestFoldSplit:
    def test_doubles_shares_and_halves_basis(self):
        acc = _account("1000000")
        fold([_submitted("o1", 1000), _fill_event("o1", "2330", Side.BUY, 1000, "500")], acc)
        before_avg = acc.positions["2330"].avg_cost
        fold([_split("2330", "2.0")], acc)
        assert acc.positions["2330"].open_shares == 2000
        assert acc.positions["2330"].avg_cost == pytest.approx(before_avg / 2)

    def test_unheld_ticker_is_noop(self):
        acc = _account()
        fold([_split("2330", "2.0")], acc)
        assert "2330" not in acc.positions

    def test_does_not_affect_other_positions(self):
        acc = _account("2000000")
        fold(
            [
                _submitted("o1", 1000),
                _fill_event("o1", "2330", Side.BUY, 1000, "500"),
                _submitted("o2", 500),
                _fill_event("o2", "2317", Side.BUY, 500, "100"),
            ],
            acc,
        )
        fold([_split("2330", "2.0")], acc)
        assert acc.positions["2317"].open_shares == 500


# ── fold: SETTLE ──────────────────────────────────────────────────────────────


class TestFoldSettle:
    def test_matures_due_pending_flows_to_settled_cash(self):
        acc = _account("500000")
        acc._ledger.pending.append(DatedCashFlow(Decimal("200000"), _T1))
        fold([_settle(_T1)], acc)
        assert acc._ledger.settled_cash == Decimal("700000")
        assert acc._ledger.pending == []

    def test_leaves_future_flows_pending(self):
        acc = _account("500000")
        acc._ledger.pending.append(DatedCashFlow(Decimal("200000"), date(2026, 7, 10)))
        fold([_settle(_T1)], acc)
        assert acc._ledger.settled_cash == Decimal("500000")
        assert len(acc._ledger.pending) == 1


# ── O1: value conservation ────────────────────────────────────────────────────


class TestO1ValueConservation:
    """O1: total_value changes only by Δ(mark) − costs + dividends."""

    def test_buy_at_mark_price_reduces_total_value_by_cost(self):
        acc = _account("1000000", InstantSettlement())
        mark_price = Decimal("500")
        cost = Decimal("4275")
        tv_before = acc.total_value({})
        fold(
            [
                _submitted("o1", 1000),
                _fill_event("o1", "2330", Side.BUY, 1000, str(mark_price), str(cost)),
            ],
            acc,
        )
        tv_after = acc.total_value({"2330": mark_price})
        assert tv_after == tv_before - cost

    def test_netting_same_day_trade_reflected_immediately(self):
        """Regression: NettingSettlement holds a same-day trade's cash
        delta in ledger.bar_net until the next day's SETTLE flushes it.
        Before the fix, total_value ignored bar_net entirely, so a
        same-day fill was invisible to equity until a day later -- the
        buy's debit wouldn't show, while the new position's mark value
        would, overstating equity by the full notional until the flush."""
        acc = _account("1000000", NettingSettlement())
        mark_price = Decimal("500")
        cost = Decimal("4275")
        tv_before = acc.total_value({})
        fold(
            [
                _submitted("o1", 1000),
                _fill_event("o1", "2330", Side.BUY, 1000, str(mark_price), str(cost)),
            ],
            acc,
        )
        tv_same_day = acc.total_value({"2330": mark_price})
        assert tv_same_day == tv_before - cost

    def test_sell_at_mark_price_reduces_total_value_by_sell_cost(self):
        acc = _account("2000000", InstantSettlement())
        price = Decimal("500")
        buy_cost = Decimal("4275")
        fold(
            [
                _submitted("o1", 1000),
                _fill_event("o1", "2330", Side.BUY, 1000, str(price), str(buy_cost)),
            ],
            acc,
        )
        tv_after_buy = acc.total_value({"2330": price})

        sell_cost = Decimal("1500")
        fold(
            [
                _submitted("o2", 1000),
                _fill_event("o2", "2330", Side.SELL, 1000, str(price), str(sell_cost)),
            ],
            acc,
        )
        tv_after_sell = acc.total_value({})
        assert tv_after_sell == tv_after_buy - sell_cost

    def test_dividend_increases_total_value(self):
        acc = _account("1000000")
        tv_before = acc.total_value({})
        dividend_amount = Decimal("5000")
        fold([_dividend(str(dividend_amount), date(2026, 7, 20))], acc)
        assert acc.total_value({}) == tv_before + dividend_amount


# ── O4: lot/position consistency ──────────────────────────────────────────────


class TestO4LotConsistency:
    """O4: Σ(lot.shares) == position.open_shares per ticker after every fill."""

    def _lot_sum(self, ledger: LotLedger) -> int:
        return sum(lot.shares for lot in ledger._lot_deque)

    def test_single_buy(self):
        acc = _account()
        fold([_submitted("o1", 1000), _fill_event("o1", "2330", Side.BUY, 1000, "500")], acc)
        ll = acc.positions["2330"]
        assert self._lot_sum(ll) == ll.open_shares == 1000

    def test_after_partial_sell(self):
        acc = _account("2000000")
        fold(
            [
                _submitted("o1", 1000),
                _fill_event("o1", "2330", Side.BUY, 1000, "500"),
                _submitted("o2", 400),
                _fill_event("o2", "2330", Side.SELL, 400, "520"),
            ],
            acc,
        )
        ll = acc.positions["2330"]
        assert self._lot_sum(ll) == ll.open_shares == 600

    def test_after_split(self):
        acc = _account("1000000")
        fold([_submitted("o1", 1000), _fill_event("o1", "2330", Side.BUY, 1000, "500")], acc)
        fold([_split("2330", "2.0")], acc)
        ll = acc.positions["2330"]
        assert self._lot_sum(ll) == ll.open_shares == 2000

    def test_two_buy_lots(self):
        acc = _account("2000000")
        fold(
            [
                _submitted("o1", 700),
                _fill_event("o1", "2330", Side.BUY, 700, "500"),
                _submitted("o2", 300),
                _fill_event("o2", "2330", Side.BUY, 300, "510"),
            ],
            acc,
        )
        ll = acc.positions["2330"]
        assert self._lot_sum(ll) == ll.open_shares == 1000
