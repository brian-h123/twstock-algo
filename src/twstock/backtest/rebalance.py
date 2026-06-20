from abc import ABC, abstractmethod
from dataclasses import dataclass

import exchange_calendars as xcals
import pandas as pd

_XTAI = xcals.get_calendar("XTAI")


@dataclass(frozen=True)
class RebalanceContext:
    """Snapshot of portfolio state passed to a RebalanceSchedule on each bar.

    Calendar-based schedules read only `date`. The richer fields exist so
    future threshold or event-driven schedules (e.g. drift > X%, signal
    changed) can slot into the same interface without reshaping it.

    Attributes:
        date: The current bar's date.
        current_weights: Ticker-indexed weights derived from current positions.
        target_weights: Ticker-indexed target weights from the allocator.
        signal_changed: Whether the strategy's scores changed since the last bar.
    """

    date: pd.Timestamp
    current_weights: pd.Series
    target_weights: pd.Series
    signal_changed: bool


class RebalanceSchedule(ABC):
    """Abstract base class for rebalance cadence rules.

    A schedule answers one question per bar: should the portfolio rebalance
    now? Implementations may read any field of `ctx`, but must not mutate it
    or carry cross-bar state.
    """

    @abstractmethod
    def should_rebalance(self, ctx: RebalanceContext) -> bool:
        """Return True if the portfolio should rebalance on `ctx.date`."""
        ...


class Monthly(RebalanceSchedule):
    """Rebalances on the first XTAI trading day of each calendar month.

    January 1 and Lunar New Year close the exchange, so the first trading
    day of the month is resolved against the XTAI session list rather than
    assumed to be the 1st of the month.
    """

    def should_rebalance(self, ctx: RebalanceContext) -> bool:
        month_start = ctx.date.replace(day=1)
        month_end = month_start + pd.offsets.MonthEnd(1)
        sessions = _XTAI.sessions_in_range(month_start, month_end)
        return ctx.date == sessions[0]


class Weekly(RebalanceSchedule):
    """Rebalances on the first XTAI trading day of each ISO week (Mon–Sun)."""

    def should_rebalance(self, ctx: RebalanceContext) -> bool:
        week_start = ctx.date - pd.Timedelta(days=ctx.date.weekday())
        week_end = week_start + pd.Timedelta(days=6)
        sessions = _XTAI.sessions_in_range(week_start, week_end)
        return ctx.date == sessions[0]


class Quarterly(RebalanceSchedule):
    """Rebalances on the first XTAI trading day of each calendar quarter."""

    def should_rebalance(self, ctx: RebalanceContext) -> bool:
        quarter_start = ctx.date.to_period("Q").start_time.date()
        quarter_end = ctx.date.to_period("Q").end_time.date()
        sessions = _XTAI.sessions_in_range(quarter_start, quarter_end)
        return ctx.date == sessions[0]


class Daily(RebalanceSchedule):
    """Rebalances on every bar.

    The engine only iterates over bars that exist in the data (trading days),
    so a calendar check here would be redundant.
    """

    def should_rebalance(self, ctx: RebalanceContext) -> bool:
        return True
