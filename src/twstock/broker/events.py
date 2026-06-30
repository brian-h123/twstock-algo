import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, NewType

from twstock.backtest.trades import Side as Side  # re-exported for broker modules


class _EventEncoder(json.JSONEncoder):
    def default(self, o: object) -> object:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


class ActionType(Enum):
    """Corporate action category that determines how ex-date arithmetic is applied."""

    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    CASH_AND_STOCK_DIVIDEND = "cash_and_stock_dividend"
    RIGHTS_ISSUE = "rights_issue"
    CAPITAL_REDUCTION = "capital_reduction"
    STOCK_SPLIT = "stock_split"


@dataclass(frozen=True)
class CorporateAction:
    """A single corporate action event attached to a market bar.

    Attributes:
        action_type: Category of the action.
        ex_date: Ex-dividend or ex-rights date.
        cash_amount: Per-share cash amount; None for non-cash actions.
        ratio: Share ratio for splits, stock dividends, or capital reductions.
        pay_date: Payment date; set for cash dividends only.
        subscription_price: Rights-issue subscription price per share.
    """

    action_type: ActionType
    ex_date: date
    cash_amount: Decimal | None
    ratio: float | None
    pay_date: date | None
    subscription_price: Decimal | None


@dataclass(frozen=True)
class Bar:
    """A single daily market bar delivered to the simulator run loop.

    Values are raw (unadjusted). Corporate actions whose ex-date falls on
    this date are attached in ``corporate_actions`` for the account fold to handle.

    Attributes:
        ticker: Four-digit TWSE ticker symbol.
        date: Trading date.
        open: Opening price.
        high: Intraday high.
        low: Intraday low.
        close: Closing price.
        volume: Share volume.
        corporate_actions: Corporate actions effective on this bar.
    """

    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    corporate_actions: tuple[CorporateAction, ...]


OrderId = NewType("OrderId", str)


class EventKind(Enum):
    """Lifecycle event types emitted by the broker or settlement engine."""

    SUBMITTED = "submitted"
    PARTIAL_FILL = "partial_fill"
    FILL = "fill"
    REJECT = "reject"
    EXPIRE = "expire"
    CANCEL = "cancel"
    DIVIDEND = "dividend"
    SPLIT = "split"
    SETTLE = "settle"


@dataclass(frozen=True)
class BrokerEvent:
    """An immutable record of a single lifecycle event from the broker.

    Events are appended to ``EventLog``; the full account state is
    reconstructed by folding the log in append order.

    Attributes:
        ts: Date on which the event occurred.
        kind: Event category.
        order_id: Associated order, or None for account-level events
            such as dividends and settlements.
        payload: Event-specific fields; schema varies by ``kind``.
    """

    ts: date
    kind: EventKind
    order_id: OrderId | None
    payload: Mapping[str, Any]


def _event_to_dict(event: BrokerEvent) -> dict:
    return {
        "ts": event.ts.isoformat(),
        "kind": event.kind.value,
        "order_id": event.order_id,
        "payload": {
            k: str(v)
            if isinstance(v, Decimal)
            else v.isoformat()
            if isinstance(v, date)
            else v.value
            if isinstance(v, Enum)
            else v
            for k, v in event.payload.items()
        },
    }


def _dict_to_event(raw: dict) -> BrokerEvent:
    return BrokerEvent(
        ts=date.fromisoformat(raw["ts"]),
        kind=EventKind(raw["kind"]),
        order_id=raw["order_id"],
        payload=raw["payload"],
    )


class EventLog:
    """Append-only JSONL event log for a single paper-trading session.

    Each line in the backing file is one JSON object with a monotonically
    increasing ``seq`` field prepended by ``append``. DuckDB can query the
    file directly; ``__iter__`` replays it as ``BrokerEvent`` objects.

    Args:
        path: Destination file. Created on first append if it does not exist;
            opened in append mode so an existing file is extended correctly.
            ``_seq`` is initialised from the existing line count so sequence
            numbers remain continuous across instances.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        if path.exists():
            with open(path, encoding="utf-8") as f:
                self._seq: int = sum(1 for _ in f)
        else:
            self._seq = 0

    def append(self, event: BrokerEvent) -> None:
        """Append one event to the log.

        Writes a single JSON line: ``{"seq": N, "ts": ..., "kind": ..., ...}``.
        ``seq`` is managed internally and is never exposed on ``BrokerEvent`` itself.

        Args:
            event: Event to persist.
        """
        record = {"seq": self._seq, **_event_to_dict(event)}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, cls=_EventEncoder) + "\n")
        self._seq += 1

    def __iter__(self) -> Iterator[BrokerEvent]:
        """Yield every event in append order.

        Reads the backing JSONL file from the beginning, strips the
        storage-only ``seq`` field, and reconstructs each ``BrokerEvent``.

        Yields:
            Events in the order they were appended.
        """
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                data = json.loads(line)
                data.pop("seq")
                yield _dict_to_event(data)
