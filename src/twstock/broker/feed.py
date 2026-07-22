from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import NamedTuple, cast

import exchange_calendars as xcals
import pandas as pd

from twstock.broker.events import ActionType, Bar, BarBatch, CorporateAction
from twstock.data.api import get_top50_universe
from twstock.data.storage import get_ticker_date_range, read_corporate_actions, read_ohlcv

_XTAI = xcals.get_calendar("XTAI")


class _CARow(NamedTuple):
    ex_date: pd.Timestamp
    ticker: str
    action_type: str
    cash_amount: float
    pay_date: pd.Timestamp
    ratio: float
    subscription_price: float


class _OHLCVRow(NamedTuple):
    date: pd.Timestamp
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarFeed(ABC):
    @abstractmethod
    def __iter__(self) -> Iterator[BarBatch]: ...


class ReplayFeed(BarFeed):
    def __init__(self, start: date, end: date) -> None:
        tickers = get_top50_universe()
        by_date: dict[date, dict[str, Bar]] = defaultdict(dict)
        for ticker in tickers:
            ohlcv = read_ohlcv(ticker, start, end)
            ca = read_corporate_actions(ticker)
            ca = ca[(ca["ex_date"] >= pd.Timestamp(start)) & (ca["ex_date"] <= pd.Timestamp(end))]
            actions_by_date: dict[date, list[CorporateAction]] = defaultdict(list)
            for raw_action in cast(Iterator[_CARow], ca.itertuples(index=False)):
                action = CorporateAction(
                    action_type=ActionType(raw_action.action_type),
                    ex_date=raw_action.ex_date.date(),
                    cash_amount=(
                        None
                        if pd.isna(raw_action.cash_amount)
                        else Decimal(str(raw_action.cash_amount))
                    ),
                    ratio=None if pd.isna(raw_action.ratio) else float(raw_action.ratio),
                    pay_date=None if pd.isna(raw_action.pay_date) else raw_action.pay_date.date(),
                    subscription_price=(
                        None
                        if pd.isna(raw_action.subscription_price)
                        else Decimal(str(raw_action.subscription_price))
                    ),
                )
                actions_by_date[action.ex_date].append(action)
            for raw_bar in cast(Iterator[_OHLCVRow], ohlcv.itertuples(index=False)):
                bar_date = raw_bar.date.date()
                bar = Bar(
                    ticker=raw_bar.ticker,
                    date=bar_date,
                    open=Decimal(str(raw_bar.open)),
                    high=Decimal(str(raw_bar.high)),
                    low=Decimal(str(raw_bar.low)),
                    close=Decimal(str(raw_bar.close)),
                    volume=int(raw_bar.volume),
                    corporate_actions=tuple(actions_by_date.get(bar_date, [])),
                )
                by_date[bar_date][ticker] = bar
        self._batches = [
            BarBatch(date=d, bars=MappingProxyType(by_date[d])) for d in sorted(by_date)
        ]

    def __iter__(self) -> Iterator[BarBatch]:
        yield from self._batches


class FixtureFeed(BarFeed):
    def __init__(self, batches: list[BarBatch]) -> None:
        self._batches = batches

    def __iter__(self) -> Iterator[BarBatch]:
        yield from self._batches


class StaleLiveFeedError(RuntimeError):
    """Local storage's latest date doesn't reach the expected latest
    completed XTAI session -- the daily backfill/fetch_suspensions cron
    job likely hasn't run yet (or failed) for today."""


def _latest_completed_session() -> date:
    """Most recent XTAI session on or before today.

    LiveFeed is an EOD-only poll (D14): it assumes it's invoked after the
    day's data is published (~17:30+), not that it's called at an
    arbitrary time. No intraday/wall-clock cutoff logic here -- that's
    the cron schedule's job, not this function's.
    """
    return _XTAI.date_to_session(pd.Timestamp(date.today()), direction="previous").date()


def _assert_fresh(expected_session: date) -> None:
    """Raise if local storage hasn't been topped up to expected_session.

    Checked against a single reference ticker (2330 -- already the
    project's canonical single-asset reference, e.g. the demonstrator's
    SmaCrossover run) rather than the whole universe: cheap, and a
    genuine single-ticker gap (e.g. a real halt) shouldn't false-fail
    the whole feed the way requiring all 50 tickers current would.
    """
    stored_range = get_ticker_date_range("2330")
    if stored_range is None or stored_range[1] < expected_session:
        raise StaleLiveFeedError(
            f"local storage's latest date for 2330 is "
            f"{stored_range[1] if stored_range else 'missing'}, expected {expected_session} "
            f"-- has today's backfill.py / fetch_suspensions.py run?"
        )


class LiveFeed(ReplayFeed):
    """Connectivity smoke test: paper-trade against the current market,
    not a historical window. Still a full BrokerSimulator/PaperSession
    run -- no real trades. A thin ReplayFeed subclass because the read
    logic is identical; only "what end date, and is it actually fresh"
    is live-specific.

    start stays a real, caller-supplied parameter (not hardcoded) so this
    doubles as the expanding-replay mode for forward paper trading today
    (pass the same fixed start every day) and composes cleanly with
    resume later (pass last_resumed_date + 1) without LiveFeed itself
    changing. Resume (rehydrating Account from an existing EventLog) is
    tracked separately -- see [journal entry / commit] -- not implemented
    here.
    """

    def __init__(self, start: date, freshness_check: bool = True) -> None:
        expected_session = _latest_completed_session()
        if freshness_check:
            _assert_fresh(expected_session)
        super().__init__(start=start, end=expected_session)
