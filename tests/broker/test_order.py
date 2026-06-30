from dataclasses import FrozenInstanceError

import pytest

from twstock.broker.events import OrderId, Side
from twstock.broker.order import (
    LEGAL_TRANSITIONS,
    IllegalTransitionError,
    Order,
    OrderStatus,
    TimeInForce,
    assert_legal_transition,
)

# Derive illegal transitions from the full Cartesian product minus the legal set.
# This is computed once at module load so parametrize can consume it.
_ALL_TRANSITIONS = {(s, d) for s in OrderStatus for d in OrderStatus}
_ILLEGAL_TRANSITIONS = sorted(
    _ALL_TRANSITIONS - LEGAL_TRANSITIONS,
    key=lambda t: (t[0].value, t[1].value),
)


# ── enum completeness ─────────────────────────────────────────────────────────


class TestEnumCompleteness:
    def test_order_status_has_all_six_states(self):
        values = {s.value for s in OrderStatus}
        assert values == {
            "pending",
            "partially_filled",
            "filled",
            "rejected",
            "expired",
            "cancelled",
        }

    def test_time_in_force_has_all_three_values(self):
        values = {t.value for t in TimeInForce}
        assert values == {"ioc", "rod", "fok"}


# ── Order construction ────────────────────────────────────────────────────────


class TestOrderConstruction:
    def _make_order(self, **kwargs) -> Order:
        defaults = dict(
            order_id=OrderId("o1"),
            ticker="2330",
            side=Side.BUY,
            quantity=1000,
        )
        return Order(**{**defaults, **kwargs})

    def test_default_tif_is_ioc(self):
        order = self._make_order()
        assert order.tif is TimeInForce.IOC

    def test_explicit_tif_is_stored(self):
        order = self._make_order(tif=TimeInForce.ROD)
        assert order.tif is TimeInForce.ROD

    def test_order_is_frozen(self):
        order = self._make_order()
        with pytest.raises(FrozenInstanceError):
            order.quantity = 500  # type: ignore[misc]

    def test_fields_stored_correctly(self):
        order = self._make_order(quantity=500, side=Side.SELL)
        assert order.order_id == OrderId("o1")
        assert order.ticker == "2330"
        assert order.side is Side.SELL
        assert order.quantity == 500


# ── legal transitions ─────────────────────────────────────────────────────────


_LEGAL_CASES = sorted(LEGAL_TRANSITIONS, key=lambda t: (t[0].value, t[1].value))


class TestLegalTransitions:
    @pytest.mark.parametrize("src,dst", _LEGAL_CASES)
    def test_legal_edge_does_not_raise(self, src: OrderStatus, dst: OrderStatus):
        assert_legal_transition(src, dst)  # must not raise

    def test_legal_transitions_count_is_nine(self):
        assert len(LEGAL_TRANSITIONS) == 9


# ── illegal transitions ───────────────────────────────────────────────────────


class TestIllegalTransitions:
    @pytest.mark.parametrize("src,dst", _ILLEGAL_TRANSITIONS)
    def test_illegal_edge_raises(self, src: OrderStatus, dst: OrderStatus):
        with pytest.raises(IllegalTransitionError):
            assert_legal_transition(src, dst)

    def test_partially_filled_to_rejected_raises(self):
        """Spec explicitly forbids this edge: rejection is pre-fill only."""
        with pytest.raises(IllegalTransitionError):
            assert_legal_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.REJECTED)

    @pytest.mark.parametrize(
        "terminal",
        [
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.CANCELLED,
        ],
    )
    def test_terminal_state_has_no_outgoing_edges(self, terminal: OrderStatus):
        for dst in OrderStatus:
            with pytest.raises(IllegalTransitionError):
                assert_legal_transition(terminal, dst)

    def test_illegal_transitions_count_is_twenty_seven(self):
        assert len(_ILLEGAL_TRANSITIONS) == 27


# ── IllegalTransitionError attributes ────────────────────────────────────────


class TestIllegalTransitionError:
    def test_src_attribute_is_set(self):
        with pytest.raises(IllegalTransitionError) as exc_info:
            assert_legal_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.REJECTED)
        assert exc_info.value.src is OrderStatus.PARTIALLY_FILLED

    def test_dst_attribute_is_set(self):
        with pytest.raises(IllegalTransitionError) as exc_info:
            assert_legal_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.REJECTED)
        assert exc_info.value.dst is OrderStatus.REJECTED

    def test_is_subclass_of_value_error(self):
        with pytest.raises(ValueError):
            assert_legal_transition(OrderStatus.FILLED, OrderStatus.PENDING)

    def test_message_contains_src_and_dst_values(self):
        with pytest.raises(IllegalTransitionError) as exc_info:
            assert_legal_transition(OrderStatus.FILLED, OrderStatus.PENDING)
        msg = str(exc_info.value)
        assert "filled" in msg
        assert "pending" in msg
