from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import NamedTuple, cast

import pandas as pd

from twstock.broker.events import ActionType, Bar, BarBatch, CorporateAction
from twstock.data.api import get_top50_universe
from twstock.data.storage import read_corporate_actions, read_ohlcv


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
