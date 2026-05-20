from datetime import date

import pandas as pd
import streamlit as st

from twstock.data.api import get_adjusted_history
from twstock.data.storage import get_ticker_date_range, read_corporate_actions, read_ohlcv


@st.cache_data(show_spinner=False)
def load_adjusted(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Wraps twstock.data.api.get_adjusted_history."""
    return get_adjusted_history(ticker, start, end)


@st.cache_data(show_spinner=False)
def load_raw(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Wraps twstock.data.storage.read_ohlcv."""
    return read_ohlcv(ticker, start, end)


@st.cache_data(show_spinner=False)
def load_corporate_actions(ticker: str) -> pd.DataFrame:
    """Wraps twstock.data.storage.read_corporate_actions."""
    return read_corporate_actions(ticker)


@st.cache_data(show_spinner=False)
def get_available_range(ticker: str) -> tuple[date, date]:
    """Wraps twstock.data.storage.get_ticker_date_range. Used for
    date-picker clamping on short-history tickers (3711, 6669, 6919, 7769)."""
    result = get_ticker_date_range(ticker)
    return result if result is not None else (date.today(), date.today())
