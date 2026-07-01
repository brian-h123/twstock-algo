from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import exchange_calendars as xcals
import pandas as pd

from twstock.broker.events import Side

_XTAI = xcals.get_calendar("XTAI")


@dataclass(frozen=True)
class DatedCashFlow:
    """An immutable cash flow with a scheduled settlement date.

    Attributes:
        amount: Cash amount in NT$; positive for inflows, negative for outflows.
        settle_date: The date on which this flow matures into settled cash.
    """

    amount: Decimal
    settle_date: date


@dataclass(frozen=True)
class Fill:
    """Snapshot of a single executed fill, passed to settlement models.

    Attributes:
        side: BUY or SELL.
        shares: Number of shares filled.
        fill_price: Per-share gross fill price in NT$.
        total_cost: Total brokerage costs (commission + tax) for the fill.
    """

    side: Side
    shares: int
    fill_price: Decimal
    total_cost: Decimal


class DatedLedger:
    """Dual-bucket cash register: settled cash and pending-settlement flows.

    ``settled_cash`` is immediately spendable. ``pending`` holds
    DatedCashFlows that migrate to ``settled_cash`` on their
    ``settle_date`` via ``mature()``. Total cash (settled + pending) is
    invariant across ``mature()`` calls (invariant O2).
    """

    def __init__(self) -> None:
        self.settled_cash: Decimal = Decimal(0)
        self.pending: list[DatedCashFlow] = []

    def mature(self, t: date) -> None:
        """Reclassify flows whose settle_date <= t into settled_cash.

        Total cash (settled + pending) is unchanged by this call (O2).

        Args:
            t: The current date; flows on or before this date are matured.
        """
        matured = [cf for cf in self.pending if cf.settle_date <= t]
        self.pending = [cf for cf in self.pending if cf.settle_date > t]
        self.settled_cash += sum((cf.amount for cf in matured), Decimal(0))


class SettlementModel(ABC):
    """Abstract settlement policy: controls when fills affect buying power.

    Concrete implementations differ in how and when they commit the cash
    delta from a fill to the DatedLedger.
    """

    @abstractmethod
    def on_trade(self, ledger: DatedLedger, fill: Fill, t: date) -> None:
        """Record a fill against the ledger under this settlement policy.

        Args:
            ledger: The account's cash ledger to update.
            fill: The executed fill details.
            t: Trade date (bar date, not wall-clock time).
        """
        ...

    @abstractmethod
    def buying_power(self, ledger: DatedLedger, t: date) -> Decimal:
        """Return spendable cash as of date t under this settlement policy.

        Args:
            ledger: The account's cash ledger.
            t: The date for which buying power is computed.

        Returns:
            Maximum cash available to spend on new buys.
        """
        ...


def _cash_delta(fill: Fill) -> Decimal:
    if fill.side == Side.BUY:
        delta = -(fill.shares * fill.fill_price + fill.total_cost)
    elif fill.side == Side.SELL:
        delta = +(fill.shares * fill.fill_price - fill.total_cost)
    return delta


def _xtai_t_plus(t: date, n: int) -> date:
    sessions = _XTAI.sessions_window(pd.Timestamp(t), n + 1)
    return sessions[n].date()


class InstantSettlement(SettlementModel):
    """Settlement model used by the backtester.

    Cash deltas are applied to ``settled_cash`` synchronously with each
    fill; ``buying_power`` equals ``ledger.settled_cash`` at all times.
    Suitable for backtests that treat all cash as settled within the bar.
    """

    def on_trade(self, ledger: DatedLedger, fill: Fill, t: date) -> None:
        cash_delta = _cash_delta(fill=fill)
        ledger.settled_cash += cash_delta

    def buying_power(self, ledger: DatedLedger, t: date) -> Decimal:
        return ledger.settled_cash


class NettingSettlement(SettlementModel):
    """TWSE-flavored settlement: net intrabar cash deferred to T+2 XTAI.

    Within a bar, buys and sells are netted in ``_bar_net``. When a new
    bar date is observed, the accumulated net is flushed to
    ``ledger.pending`` with a settlement date of T+2 on the XTAI
    calendar. ``buying_power`` includes the current bar's running net so
    a same-bar sell can immediately fund a same-bar buy.
    """

    def __init__(self) -> None:
        self._bar_date: date | None = None
        self._bar_net: Decimal = Decimal(0)

    def on_trade(self, ledger: DatedLedger, fill: Fill, t: date) -> None:
        if self._bar_date is not None and self._bar_date != t:
            ledger.pending.append(
                DatedCashFlow(amount=self._bar_net, settle_date=_xtai_t_plus(t=self._bar_date, n=2))
            )
            self._bar_net = Decimal(0)

        self._bar_date = t
        self._bar_net += _cash_delta(fill=fill)

    def buying_power(self, ledger: DatedLedger, t: date) -> Decimal:
        return max(Decimal(0), ledger.settled_cash + self._bar_net)


class SettledCashOnly(SettlementModel):
    """Conservative settlement: every fill deferred to T+2 XTAI.

    Each trade's cash delta goes to ``ledger.pending`` at T+2;
    ``buying_power`` equals only ``settled_cash``. No same-bar credit —
    a sell on date T cannot fund a buy on the same date T.
    """

    def on_trade(self, ledger: DatedLedger, fill: Fill, t: date) -> None:
        cash_delta = _cash_delta(fill=fill)
        ledger.pending.append(DatedCashFlow(amount=cash_delta, settle_date=_xtai_t_plus(t=t, n=2)))

    def buying_power(self, ledger: DatedLedger, t: date) -> Decimal:
        return ledger.settled_cash
