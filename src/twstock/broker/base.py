from abc import ABC, abstractmethod

from twstock.broker.events import BrokerEvent, OrderId
from twstock.broker.order import Order, OrderState


class BrokerBase(ABC):
    @abstractmethod
    def submit(self, order: Order) -> OrderId:
        # fire-and-forget; returns an ack id
        ...

    @abstractmethod
    def cancel(self, order_id: OrderId) -> None:
        # request cancel; effect arrives as an event
        ...

    @abstractmethod
    def query(self, order_id: OrderId) -> OrderState:
        # current mirror of one order
        ...

    @abstractmethod
    def drain_events(self) -> list[BrokerEvent]:
        # pull all events since last drain; clears buffer
        ...
