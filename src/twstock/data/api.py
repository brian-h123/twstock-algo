import functools
from datetime import date

import pandas as pd

from twstock.data.adjustments import adjust
from twstock.data.storage import read_corporate_actions, read_ohlcv
from twstock.universe import get_top50_universe


@functools.lru_cache(maxsize=128)
def _cached_adjusted_history(ticker: str, start: date, end: date) -> pd.DataFrame:
    raw_ohlcv = read_ohlcv(ticker, start, end)
    # Bounded to [start, end]: an action recorded after `end` must not back-adjust
    # bars inside the window -- adjust()'s "every row strictly before ex_date" rule
    # would otherwise apply a not-yet-known future action to the entire window.
    corporate_actions = read_corporate_actions(ticker, start, end)

    adjusted = adjust(raw_ohlcv, corporate_actions)
    return adjusted.set_index("date")


def get_adjusted_history(
    ticker: str, start: date | str, end: date | str | None = None
) -> pd.DataFrame:
    """Return back-adjusted OHLCV for a ticker over [start, end].

    Args:
        ticker: Four-digit TWSE ticker string.
        start: Start date, inclusive. Accepts date or ISO string.
        end: End date, inclusive. Defaults to today. Accepts date or ISO string.

    Returns:
        DataFrame with date index and adjusted open/high/low/close columns.
        Empty DataFrame if no data is stored for the ticker.
    """

    if end is None:
        end = date.today()
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(end, str):
        end = date.fromisoformat(end)
    return _cached_adjusted_history(ticker, start, end)


def get_universe() -> list[str]:
    """Return the active trading universe (TWSE Top 50).

    Returns:
        List of 50 four-digit ticker strings.
    """

    return get_top50_universe()
