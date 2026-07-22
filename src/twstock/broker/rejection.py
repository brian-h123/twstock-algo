from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import Enum
from typing import Literal

import exchange_calendars as xcals
import pandas as pd

from twstock.broker.corporate_actions import share_multiplier
from twstock.broker.events import ActionType, Bar, CorporateAction
from twstock.data.storage import read_suspensions
from twstock.universe import get_top50_universe

_XTAI = xcals.get_calendar("XTAI")


class RejectionReason(Enum):
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
    PRICE_LIMIT_LOCK = "price_limit_lock"
    NO_MARKET_DATA = "no_market_data"
    SUSPENSION = "suspension"


class PriceLimitProvider(ABC):
    def observe(self, bar: Bar) -> None:
        """Optional hook for providers that need to accumulate history to
        derive a band (e.g. replay). Providers that read limits directly
        (e.g. live) can leave this a no-op."""
        pass

    @abstractmethod
    def band(self, ticker: str, t: date) -> tuple[Decimal, Decimal] | None:
        """Return (limit_down, limit_up) for ticker on date t, or None if
        no band is determinable (e.g., no prior reference available yet).
        Source-agnostic: replay derives it, a future live provider reads it
        from the broker's quote. Callers must handle None."""
        ...


def _tick_size(price: Decimal) -> Decimal:
    if price < 10:
        return Decimal("0.01")
    if price < 50:
        return Decimal("0.05")
    if price < 100:
        return Decimal("0.1")
    if price < 500:
        return Decimal("0.5")
    if price < 1000:
        return Decimal("1")
    return Decimal("5")


def _round_to_tick(price: Decimal, *, direction: Literal["up", "down"]) -> Decimal:
    tick = _tick_size(price)
    if direction == "up":
        return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    elif direction == "down":
        return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    raise ValueError(f"unknown direction: {direction}")


def _ex_day_reference(prior_close: Decimal, actions: tuple[CorporateAction, ...]) -> Decimal:
    reference_price = prior_close
    for action in actions:
        if action.action_type in (ActionType.STOCK_SPLIT, ActionType.STOCK_DIVIDEND):
            reference_price = reference_price / Decimal(str(share_multiplier(action)))
        elif action.action_type == ActionType.CASH_DIVIDEND:
            if action.cash_amount is None:
                raise ValueError(f"CASH_DIVIDEND action missing cash_amount: {action}")
            reference_price -= action.cash_amount
        elif action.action_type == ActionType.CASH_AND_STOCK_DIVIDEND:
            if action.cash_amount is None:
                raise ValueError(f"CASH_AND_STOCK_DIVIDEND action missing cash_amount: {action}")
            reference_price = (reference_price - action.cash_amount) / Decimal(
                str(share_multiplier(action))
            )
        elif action.action_type in (ActionType.RIGHTS_ISSUE, ActionType.CAPITAL_REDUCTION):
            raise ValueError(
                f"unmodeled action type for price-limit reference: {action.action_type}"
            )
        else:
            raise ValueError(f"unhandled action_type: {action.action_type}")
    return reference_price


class DerivedPriceLimits(PriceLimitProvider):
    def __init__(self, limit_pct: Decimal = Decimal("0.10")) -> None:
        self.limit_pct = limit_pct
        self._prior_close: dict[str, Decimal] = {}
        self._reference: dict[str, tuple[date, Decimal]] = {}

    def observe(self, bar: Bar) -> None:
        prior_close = self._prior_close.get(bar.ticker)
        if prior_close is not None:
            if bar.corporate_actions:
                reference_price = _ex_day_reference(prior_close, bar.corporate_actions)
            else:
                reference_price = prior_close
            self._reference[bar.ticker] = (bar.date, reference_price)
        self._prior_close[bar.ticker] = bar.close

    def band(self, ticker: str, t: date) -> tuple[Decimal, Decimal] | None:
        cached = self._reference.get(ticker)
        if cached is None:
            return None
        cached_date, reference = cached
        if cached_date != t:
            raise ValueError(
                f"band() queried for {t} but last observed {cached_date} for {ticker!r}"
            )
        limit_down = _round_to_tick(reference * (1 - self.limit_pct), direction="up")
        limit_up = _round_to_tick(reference * (1 + self.limit_pct), direction="down")
        return (limit_down, limit_up)


class SuspensionCalendar:
    """Per-ticker refinement of the XTAI trading calendar: 'is ticker X tradeable on D?'.
    Built from the TWTAWU historical halt records. ABSENT or unbuilt → all-tradeable
    (graceful degradation: every missing bar stays NO_MARKET_DATA)."""

    def __init__(self, halts: dict[str, list[tuple[date, date]]] | None = None) -> None:
        self._halts = halts or {}

    @classmethod
    def from_storage(cls, tickers: Iterable[str] | None = None) -> "SuspensionCalendar":
        tickers = tickers if tickers is not None else get_top50_universe()
        halts: dict[str, list[tuple[date, date]]] = {}
        for ticker in tickers:
            df = read_suspensions(ticker)
            if df.empty:
                continue
            halts[ticker] = list(zip(df["halt_date"].dt.date, df["resume_date"].dt.date))
        return cls(halts)

    def is_halted(self, ticker: str, t: date) -> bool:
        return any(halt <= t < resume for halt, resume in self._halts.get(ticker, ()))


def classify_missing_bar(
    ticker: str, t: date, *, has_bar: bool, suspension_calendar: SuspensionCalendar
) -> RejectionReason | None:
    if has_bar:
        return None
    if not _XTAI.is_session(pd.Timestamp(t)):
        return None
    if suspension_calendar.is_halted(ticker, t):
        return RejectionReason.SUSPENSION
    return RejectionReason.NO_MARKET_DATA
