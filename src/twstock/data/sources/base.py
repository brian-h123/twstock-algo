from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSource(ABC):
    @abstractmethod
    def get_daily_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Return daily OHLCV for `ticker` between `start` and `end` inclusive."""

    @abstractmethod
    def get_corporate_actions(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Return splits, dividends, and ticker changes."""

    @abstractmethod
    def list_universe(self, snapshot_date: date) -> list[str]:
        """Return tickers in the configured universe as of `snapshot_date`"""
