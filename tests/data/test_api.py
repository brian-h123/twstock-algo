import json
import pathlib
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from twstock.data import api
from twstock.data.api import get_adjusted_history, get_universe
from twstock.data.storage import get_ticker_date_range, read_ohlcv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANCHORS_PATH = pathlib.Path(__file__).parent.parent / "fixtures" / "anchor_dividends.json"
_ANCHORS = json.loads(_ANCHORS_PATH.read_text())["anchors"]


def _ohlcv(dates: list[str], closes: list[float], ticker: str = "2330") -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ticker": [ticker] * n,
            "open": [c - 1.0 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        }
    )


def _empty_actions() -> pd.DataFrame:
    return pd.DataFrame(columns=["ex_date", "ticker", "action_type", "cash_amount", "ratio"])


def _cash_div(ex_date: str, cash_amount: float, ticker: str = "2330") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime([ex_date]),
            "ticker": [ticker],
            "action_type": ["cash_dividend"],
            "cash_amount": [float(cash_amount)],
            "ratio": [float("nan")],
        }
    )


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_lru_cache():
    api._cached_adjusted_history.cache_clear()
    yield
    api._cached_adjusted_history.cache_clear()


# ---------------------------------------------------------------------------
# Unit tests — mocked storage
# ---------------------------------------------------------------------------


def test_returns_date_index():
    raw = _ohlcv(["2023-01-03", "2023-01-04", "2023-01-05"], [100.0, 101.0, 102.0])
    with (
        patch("twstock.data.api.read_ohlcv", return_value=raw),
        patch("twstock.data.api.read_corporate_actions", return_value=_empty_actions()),
    ):
        result = get_adjusted_history("2330", date(2023, 1, 3), date(2023, 1, 5))
    assert result.index.name == "date"
    assert len(result) == 3


def test_str_dates_parsed():
    raw = _ohlcv(["2023-01-03"], [100.0])
    with (
        patch("twstock.data.api.read_ohlcv", return_value=raw) as mock_read,
        patch("twstock.data.api.read_corporate_actions", return_value=_empty_actions()),
    ):
        get_adjusted_history("2330", "2023-01-03", "2023-01-03")
    mock_read.assert_called_once_with("2330", date(2023, 1, 3), date(2023, 1, 3))


def test_end_defaults_to_today():
    raw = _ohlcv(["2023-01-03"], [100.0])
    with (
        patch("twstock.data.api.read_ohlcv", return_value=raw) as mock_read,
        patch("twstock.data.api.read_corporate_actions", return_value=_empty_actions()),
    ):
        get_adjusted_history("2330", "2023-01-03")
    _ticker, _start, call_end = mock_read.call_args.args
    assert call_end == date.today()


def test_adjustment_applied():
    # T-2 close=80, T-1 close=100, T close=95, cash_div=5 on T
    # factor = (100-5)/100 = 0.95 → T-2 bar scaled by 0.95
    raw = _ohlcv(["2023-01-02", "2023-01-03", "2023-01-04"], [80.0, 100.0, 95.0])
    actions = _cash_div("2023-01-04", cash_amount=5.0)
    with (
        patch("twstock.data.api.read_ohlcv", return_value=raw),
        patch("twstock.data.api.read_corporate_actions", return_value=actions),
    ):
        result = get_adjusted_history("2330", "2023-01-02", "2023-01-04")
    expected = 80.0 * (95.0 / 100.0)
    assert result.loc[pd.Timestamp("2023-01-02"), "close"] == pytest.approx(expected)


def test_empty_data_returns_empty_dataframe():
    empty = pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])
    with (
        patch("twstock.data.api.read_ohlcv", return_value=empty),
        patch("twstock.data.api.read_corporate_actions", return_value=_empty_actions()),
    ):
        result = get_adjusted_history("9999", "2023-01-03")
    assert result.empty


def test_get_universe_returns_50_tickers():
    universe = get_universe()
    assert len(universe) == 50
    assert all(t.isdigit() and len(t) == 4 for t in universe)


# ---------------------------------------------------------------------------
# Anchor cross-validation tests (require stored data; skip if not backfilled)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def adj_2330() -> pd.DataFrame:
    if get_ticker_date_range("2330") is None:
        pytest.skip("2330 not backfilled; run scripts/backfill.py first")
    return get_adjusted_history("2330", start="2016-01-01")


@pytest.fixture(scope="module")
def raw_2330() -> pd.DataFrame:
    if get_ticker_date_range("2330") is None:
        pytest.skip("2330 not backfilled; run scripts/backfill.py first")
    return read_ohlcv("2330", start=date(2016, 1, 1)).set_index("date")


@pytest.mark.parametrize("anchor", _ANCHORS, ids=lambda a: a["ex_date"])
def test_adjustment_anchors(anchor, adj_2330):
    """Adjusted-close ratio across each ex_date must match the expected back-adjustment factor
    within 0.01% relative tolerance."""
    ex_date = pd.Timestamp(anchor["ex_date"])
    prior = adj_2330[adj_2330.index < ex_date]
    assert not prior.empty, f"No adjusted data before ex_date {anchor['ex_date']}"

    t_minus_1 = prior.index[-1]
    adj_t_minus_1 = adj_2330.loc[t_minus_1, "close"]
    adj_t = adj_2330.loc[ex_date, "close"]

    observed_ratio = adj_t_minus_1 / adj_t
    expected_ratio = anchor["expected_adj_close_ratio"]

    assert observed_ratio == pytest.approx(expected_ratio, rel=1e-4), (
        f"Anchor {anchor['ex_date']}: ratio {observed_ratio:.8f} != expected {expected_ratio:.8f}"
    )


@pytest.mark.parametrize("anchor", _ANCHORS, ids=lambda a: a["ex_date"])
def test_raw_close_fidelity(anchor, raw_2330):
    """Raw closes from storage must match TWSE anchor prices within 0.1%.
    Kept separate from test_adjustment_anchors so math failures and data failures
    are distinguishable."""
    ex_date = pd.Timestamp(anchor["ex_date"])
    prior = raw_2330[raw_2330.index < ex_date]
    assert not prior.empty, f"No raw data before ex_date {anchor['ex_date']}"

    raw_t_minus_1 = float(prior.iloc[-1]["close"])
    raw_t = float(raw_2330.loc[ex_date, "close"])

    assert raw_t_minus_1 == pytest.approx(anchor["raw_close_t_minus_1"], rel=1e-3), (
        f"T-1 raw close mismatch on {anchor['ex_date']}: "
        f"got {raw_t_minus_1}, expected {anchor['raw_close_t_minus_1']}"
    )
    assert raw_t == pytest.approx(anchor["raw_close_t"], rel=1e-3), (
        f"T raw close mismatch on {anchor['ex_date']}: "
        f"got {raw_t}, expected {anchor['raw_close_t']}"
    )
