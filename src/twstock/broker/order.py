from dataclasses import dataclass
from enum import Enum

from twstock.broker.events import OrderId, Side


class TimeInForce(Enum):
    IOC = "ioc"
    ROD = "rod"
    FOK = "fok"


class OrderStatus(Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Order:
    order_id: OrderId
    ticker: str
    side: Side
    quantity: int
    tif: TimeInForce = TimeInForce.IOC


LEGAL_TRANSITIONS: frozenset[tuple[OrderStatus, OrderStatus]] = frozenset(
    {
        (OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.PENDING, OrderStatus.FILLED),
        (OrderStatus.PENDING, OrderStatus.REJECTED),
        (OrderStatus.PENDING, OrderStatus.CANCELLED),
        (OrderStatus.PENDING, OrderStatus.EXPIRED),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELLED),
        (OrderStatus.PARTIALLY_FILLED, OrderStatus.EXPIRED),
    }
)


class IllegalTransitionError(ValueError):
    def __init__(self, src: OrderStatus, dst: OrderStatus) -> None:
        self.src = src
        self.dst = dst
        super().__init__(f"illegal transition: {src.value} -> {dst.value}")


def assert_legal_transition(src: OrderStatus, dst: OrderStatus) -> None:
    """Raise IllegalTransitionError if (src, dst) not in LEGAL_TRANSITIONS."""
    if (src, dst) not in LEGAL_TRANSITIONS:
        raise IllegalTransitionError(src, dst)
