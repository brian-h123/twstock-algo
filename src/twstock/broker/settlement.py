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

    ``bar_net`` and ``bar_date`` are scratch space for settlement models
    that net same-bar trades before scheduling them (``NettingSettlement``).
    They live here — one per account's own ledger — rather than on the
    settlement model instance, because a single ``SettlementModel`` instance
    may be shared across more than one ``Account`` (e.g. ``BrokerSimulator``'s
    internal account and ``PaperSession``'s own account both fold the same
    event stream against one settlement policy). Keeping the accumulator on
    the ledger keeps each account's bookkeeping independent even when the
    policy object is shared. Settlement models that don't net (``Instant``,
    ``SettledCashOnly``) simply never touch these two fields.
    """

    def __init__(self) -> None:
        self.settled_cash: Decimal = Decimal(0)
        self.pending: list[DatedCashFlow] = []
        self.bar_net: Decimal = Decimal(0)
        self.bar_date: date | None = None

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

    def on_bar_start(self, ledger: DatedLedger, t: date) -> None:
        """Advance the settlement policy's per-day state to trading day t.

        Wired to the SETTLE event (which fires before that bar's own trades
        are folded) — so a settlement model can push any now-fully-concluded
        PRIOR day's accrual into ``ledger.pending`` without waiting for a
        later trade to trigger the flush. Default is a no-op; only models
        that net same-day trades (``NettingSettlement``) need to override
        this.

        ``t`` must be the trading DAY, never a finer intraday bar timestamp
        — implementations compare ``t`` against the ledger's currently-open
        day and only act when it actually changes. That comparison is what
        makes repeated same-day calls safe no-ops: SETTLE fires once per
        ``process_bar`` call, which today (daily bars) means once per
        trading day, but a future intraday feed could call this several
        times within one day without harm, *as long as every one of those
        calls passes the same calendar date*. Passing a per-bar timestamp
        instead would break same-day netting by making every intraday bar
        look like a new day.

        Args:
            ledger: The account's cash ledger.
            t: The trading day about to be processed (not a bar timestamp).
        """
        return None

    def close(self, ledger: DatedLedger) -> None:
        """Flush any remaining per-day accrual once no more days will come.

        Called exactly once, after the last bar of a run has been processed,
        to account for the case ``on_bar_start`` cannot reach: the final
        trading day's own trades have no later day to flush them. Default
        is a no-op.

        Args:
            ledger: The account's cash ledger.
        """
        return None


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
    """TWSE-flavored settlement: net intraday cash deferred to T+2 XTAI.

    Within a trading day, buys and sells are netted in ``ledger.bar_net``
    (state lives on the ledger, not this policy object, so one
    NettingSettlement instance can be safely shared across more than one
    Account/ledger — see DatedLedger's docstring). The accumulated net is
    flushed to ``ledger.pending`` with a settlement date of T+2 on the XTAI
    calendar, either when a new trading day is observed (``on_bar_start``,
    the common case — safe to call more than once per day, see its
    docstring) or when the run ends (``close``, for the final day's own net,
    which has no later day to trigger the flush). ``buying_power`` includes
    the current day's running net so a same-day sell can immediately fund a
    same-day buy.

    Despite the ``bar_``-prefixed field names (kept for continuity with the
    ledger's ``bar_net``/``bar_date`` attributes), every ``t`` this class
    receives is a trading DAY, never a bar timestamp — see
    ``on_bar_start``'s docstring for why that distinction matters once bars
    stop being one-per-day.
    """

    def _flush(self, ledger: DatedLedger) -> None:
        assert ledger.bar_date is not None
        ledger.pending.append(
            DatedCashFlow(amount=ledger.bar_net, settle_date=_xtai_t_plus(t=ledger.bar_date, n=2))
        )
        ledger.bar_net = Decimal(0)
        ledger.bar_date = None

    def on_trade(self, ledger: DatedLedger, fill: Fill, t: date) -> None:
        if ledger.bar_date is not None and ledger.bar_date != t:
            self._flush(ledger)

        ledger.bar_date = t
        ledger.bar_net += _cash_delta(fill=fill)

    def buying_power(self, ledger: DatedLedger, t: date) -> Decimal:
        return max(Decimal(0), ledger.settled_cash + ledger.bar_net)

    def on_bar_start(self, ledger: DatedLedger, t: date) -> None:
        if ledger.bar_date is not None and ledger.bar_date != t:
            self._flush(ledger)

    def close(self, ledger: DatedLedger) -> None:
        if ledger.bar_date is not None:
            self._flush(ledger)


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
