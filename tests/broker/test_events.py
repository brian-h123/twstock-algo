import json
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from twstock.broker.events import (
    ActionType,
    Bar,
    BrokerEvent,
    CorporateAction,
    EventKind,
    EventLog,
    OrderId,
    Side,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_event(
    *,
    ts: date = date(2026, 6, 30),
    kind: EventKind = EventKind.SUBMITTED,
    order_id: OrderId | None = OrderId("o1"),
    payload: dict | None = None,
) -> BrokerEvent:
    return BrokerEvent(ts=ts, kind=kind, order_id=order_id, payload=payload or {})


# ── immutability ──────────────────────────────────────────────────────────────


class TestImmutability:
    def test_broker_event_is_frozen(self):
        event = _make_event()
        with pytest.raises(FrozenInstanceError):
            event.ts = date(2026, 7, 1)  # type: ignore[misc]

    def test_corporate_action_is_frozen(self):
        ca = CorporateAction(
            action_type=ActionType.CASH_DIVIDEND,
            ex_date=date(2026, 6, 1),
            cash_amount=Decimal("5.00"),
            ratio=None,
            pay_date=date(2026, 6, 15),
            subscription_price=None,
        )
        with pytest.raises(FrozenInstanceError):
            ca.cash_amount = Decimal("10.00")  # type: ignore[misc]

    def test_bar_is_frozen(self):
        bar = Bar(
            ticker="2330",
            date=date(2026, 6, 30),
            open=Decimal("550.00"),
            high=Decimal("555.00"),
            low=Decimal("548.00"),
            close=Decimal("552.00"),
            volume=10_000_000,
            corporate_actions=(),
        )
        with pytest.raises(FrozenInstanceError):
            bar.close = Decimal("553.00")  # type: ignore[misc]


# ── JSONL round-trip ──────────────────────────────────────────────────────────


class TestEventLogRoundTrip:
    def test_single_event_round_trips(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        event = _make_event(payload={"ticker": "2330", "quantity": 1000})
        log.append(event)
        assert list(log) == [event]

    def test_multiple_events_returned_in_append_order(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        events = [
            _make_event(kind=EventKind.SUBMITTED, payload={"ticker": "2330"}),
            _make_event(kind=EventKind.FILL, payload={"ticker": "2330", "shares": 1000}),
            BrokerEvent(ts=date(2026, 7, 1), kind=EventKind.SETTLE, order_id=None, payload={}),
        ]
        for e in events:
            log.append(e)
        assert list(log) == events

    def test_none_order_id_round_trips(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        event = BrokerEvent(
            ts=date(2026, 6, 30),
            kind=EventKind.DIVIDEND,
            order_id=None,
            payload={"ticker": "2330"},
        )
        log.append(event)
        [recovered] = list(log)
        assert recovered.order_id is None
        assert recovered == event

    def test_all_event_kind_values_survive_round_trip(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        for kind in EventKind:
            log.append(_make_event(kind=kind))
        recovered_kinds = [e.kind for e in log]
        assert recovered_kinds == list(EventKind)

    def test_ts_reconstructed_as_date(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        log.append(_make_event(ts=date(2025, 1, 15)))
        [recovered] = list(log)
        assert recovered.ts == date(2025, 1, 15)
        assert isinstance(recovered.ts, date)

    def test_kind_reconstructed_as_event_kind_enum(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        log.append(_make_event(kind=EventKind.FILL))
        [recovered] = list(log)
        assert recovered.kind is EventKind.FILL


# ── seq field ─────────────────────────────────────────────────────────────────


class TestSeq:
    def test_seq_written_to_jsonl_not_on_event(self, tmp_path):
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        log.append(_make_event())
        data = json.loads(path.read_text().strip())
        assert "seq" in data
        assert not hasattr(BrokerEvent, "seq")

    def test_seq_starts_at_zero_and_increments(self, tmp_path):
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        for kind in [EventKind.SUBMITTED, EventKind.PARTIAL_FILL, EventKind.FILL]:
            log.append(_make_event(kind=kind))
        seqs = [json.loads(line)["seq"] for line in path.read_text().splitlines()]
        assert seqs == [0, 1, 2]

    def test_seq_continuous_across_instances(self, tmp_path):
        """Reopening an existing log file continues seq from where it left off."""
        path = tmp_path / "events.jsonl"
        EventLog(path).append(_make_event(kind=EventKind.SUBMITTED))
        EventLog(path).append(_make_event(kind=EventKind.FILL))
        log3 = EventLog(path)
        log3.append(
            BrokerEvent(ts=date(2026, 7, 1), kind=EventKind.SETTLE, order_id=None, payload={})
        )
        seqs = [json.loads(line)["seq"] for line in path.read_text().splitlines()]
        assert seqs == [0, 1, 2]

    def test_seq_stripped_from_yielded_event(self, tmp_path):
        """__iter__ strips seq before constructing BrokerEvent — no spurious fields."""
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        log.append(_make_event())
        [recovered] = list(log)
        # BrokerEvent has no seq field; accessing it raises AttributeError
        with pytest.raises(AttributeError):
            _ = recovered.seq  # type: ignore[attr-defined]


# ── payload serialization ─────────────────────────────────────────────────────


class TestPayloadSerialization:
    def test_decimal_payload_stored_as_exact_string(self, tmp_path):
        """Decimal → str preserves exact representation (no float rounding)."""
        log = EventLog(tmp_path / "events.jsonl")
        log.append(_make_event(payload={"price": Decimal("550.0000"), "shares": 1000}))
        [recovered] = list(log)
        assert recovered.payload["price"] == "550.0000"
        assert recovered.payload["shares"] == 1000

    def test_string_payload_values_survive_unchanged(self, tmp_path):
        log = EventLog(tmp_path / "events.jsonl")
        event = _make_event(payload={"ticker": "2330", "reason": "insufficient_buying_power"})
        log.append(event)
        assert list(log) == [event]

    def test_one_jsonl_line_per_event(self, tmp_path):
        path = tmp_path / "events.jsonl"
        log = EventLog(path)
        for i in range(5):
            log.append(_make_event(order_id=OrderId(f"o{i}")))
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 5
        for line in lines:
            json.loads(line)  # every line is valid JSON


# ── re-exports ────────────────────────────────────────────────────────────────


def test_side_re_exported_from_events_module():
    """Downstream broker modules import Side from events without knowing its origin."""
    assert Side.BUY.value == "buy"
    assert Side.SELL.value == "sell"
