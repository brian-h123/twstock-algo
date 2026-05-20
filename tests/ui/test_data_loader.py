from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from twstock.ui.data_loader import (
    get_available_range,
    load_adjusted,
    load_corporate_actions,
    load_raw,
)

_TICKER = "2330"
_START = date(2024, 1, 2)
_END = date(2024, 12, 31)


@pytest.fixture(autouse=True)
def clear_loader_caches():
    load_adjusted.clear()
    load_raw.clear()
    load_corporate_actions.clear()
    get_available_range.clear()
    yield
    load_adjusted.clear()
    load_raw.clear()
    load_corporate_actions.clear()
    get_available_range.clear()


def test_load_adjusted_returns_dataframe():
    fake = pd.DataFrame({"open": [100.0], "close": [101.0]}, index=[_START])
    with patch("twstock.ui.data_loader.get_adjusted_history", return_value=fake) as mock_fn:
        result = load_adjusted(_TICKER, _START, _END)
        mock_fn.assert_called_once_with(_TICKER, _START, _END)
    assert list(result.columns) == ["open", "close"]


def test_load_raw_returns_dataframe():
    fake = pd.DataFrame(
        {
            "date": [_START],
            "ticker": [_TICKER],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000],
            "value": [100000.0],
            "fetched_at": [_START],
        }
    )
    with patch("twstock.ui.data_loader.read_ohlcv", return_value=fake) as mock_fn:
        result = load_raw(_TICKER, _START, _END)
        mock_fn.assert_called_once_with(_TICKER, _START, _END)
    assert {"date", "open", "high", "low", "close", "volume"}.issubset(result.columns)


def test_load_corporate_actions_returns_dataframe():
    fake = pd.DataFrame(
        {
            "ex_date": [_START],
            "ticker": [_TICKER],
            "action_type": ["cash_dividend"],
            "cash_amount": [2.5],
            "ratio": [0.0],
        }
    )
    with patch("twstock.ui.data_loader.read_corporate_actions", return_value=fake) as mock_fn:
        result = load_corporate_actions(_TICKER)
        mock_fn.assert_called_once_with(_TICKER)
    assert {"ex_date", "action_type"}.issubset(result.columns)


def test_get_available_range_returns_date_tuple():
    with patch(
        "twstock.ui.data_loader.get_ticker_date_range", return_value=(_START, _END)
    ) as mock_fn:
        result = get_available_range(_TICKER)
        mock_fn.assert_called_once_with(_TICKER)
    assert result == (_START, _END)


def test_get_available_range_none_falls_back_to_today():
    today = date.today()
    with patch("twstock.ui.data_loader.get_ticker_date_range", return_value=None):
        result = get_available_range(_TICKER)
    assert result == (today, today)


def test_load_adjusted_cache_hit():
    fake = pd.DataFrame({"close": [101.0]}, index=[_START])
    with patch("twstock.ui.data_loader.get_adjusted_history", return_value=fake) as mock_fn:
        load_adjusted(_TICKER, _START, _END)
        load_adjusted(_TICKER, _START, _END)
    mock_fn.assert_called_once()


def test_load_raw_cache_hit():
    fake = pd.DataFrame({"close": [100.0]})
    with patch("twstock.ui.data_loader.read_ohlcv", return_value=fake) as mock_fn:
        load_raw(_TICKER, _START, _END)
        load_raw(_TICKER, _START, _END)
    mock_fn.assert_called_once()
