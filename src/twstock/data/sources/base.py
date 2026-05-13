from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class SourceError(Exception):
    pass


class RateLimitError(SourceError):
    pass


class SourceUnavailableError(SourceError):
    pass


class NoDataError(SourceError):
    pass


class DataSource(ABC):
    @abstractmethod
    def get_daily_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Return daily OHLCV for `ticker` between `start` and `end` inclusive.

        Raises:
            RateLimitError: provider throttled the request.
            SourceUnavailableError: provider returned 5xx or is unreachable.
            NoDataError: valid request but no data exists for this ticker/range.
        """

    @abstractmethod
    def get_corporate_actions(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Return splits, dividends, and ticker changes.

        Raises:
            RateLimitError: provider throttled the request.
            SourceUnavailableError: provider returned 5xx or is unreachable.
            NoDataError: valid request but no data exists for this ticker/range.
        """

    @abstractmethod
    def list_universe(self) -> list[str]:
        """Return source's view of the trading universe (sanity check vs
        hard-coded Top 50)."""
