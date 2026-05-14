import pandas as pd
import pytest

from twstock.data.storage import (
    get_ticker_date_range,
    list_stored_tickers,
    query,
    read_corporate_actions,
    read_ohlcv,
    write_corporate_actions,
    write_ohlcv,
)


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    d = tmp_path / "by_ticker"
    d.mkdir()
    monkeypatch.setattr("twstock.data.storage.DATA_DIR", tmp_path)
    return tmp_path


def _make_ohlcv(ticker="2330", dates=("2023-01-03",)) -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ticker": [ticker] * n,
            "open": [500.0] * n,
            "high": [510.0] * n,
            "low": [495.0] * n,
            "close": [505.0] * n,
            "volume": [10_000_000] * n,
        }
    )


# ---------------------------------------------------------------------------
# write_ohlcv
# ---------------------------------------------------------------------------


def test_write_ohlcv_round_trip(storage_dir):
    df = _make_ohlcv()
    write_ohlcv("2330", df)
    result = read_ohlcv("2330")
    assert len(result) == 1
    assert result["close"].iloc[0] == 505.0


def test_write_ohlcv_idempotent(storage_dir):
    df = _make_ohlcv()
    write_ohlcv("2330", df)
    write_ohlcv("2330", df)
    result = read_ohlcv("2330")
    assert len(result) == 1


def test_write_ohlcv_new_value_wins_on_same_date(storage_dir):
    df_old = _make_ohlcv(dates=("2023-01-03",))
    write_ohlcv("2330", df_old)

    df_new = _make_ohlcv(dates=("2023-01-03",))
    df_new["close"] = 507.0
    write_ohlcv("2330", df_new)

    result = read_ohlcv("2330")
    assert len(result) == 1
    assert result["close"].iloc[0] == 507.0


def test_write_ohlcv_appends_new_dates(storage_dir):
    df_first = _make_ohlcv(dates=("2023-01-03", "2023-01-04", "2023-01-05"))
    write_ohlcv("2330", df_first)

    df_second = _make_ohlcv(dates=("2023-01-06", "2023-01-09"))
    write_ohlcv("2330", df_second)

    result = read_ohlcv("2330")
    assert len(result) == 5


# ---------------------------------------------------------------------------
# read_ohlcv
# ---------------------------------------------------------------------------


def test_read_ohlcv_missing_ticker_returns_empty_df(storage_dir):
    result = read_ohlcv("9999")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0
    assert "date" in result.columns
    assert "close" in result.columns


def test_read_ohlcv_no_filters_returns_all_rows(storage_dir):
    df = _make_ohlcv(dates=("2023-01-03", "2023-01-04", "2023-01-05"))
    write_ohlcv("2330", df)
    result = read_ohlcv("2330")
    assert len(result) == 3


def test_read_ohlcv_start_filter_excludes_earlier_rows(storage_dir):
    df = _make_ohlcv(dates=("2023-01-03", "2023-01-04", "2023-01-05"))
    write_ohlcv("2330", df)
    from datetime import date

    result = read_ohlcv("2330", start=date(2023, 1, 4))
    assert len(result) == 2
    assert result["date"].min() == pd.Timestamp("2023-01-04")


def test_read_ohlcv_end_filter_excludes_later_rows(storage_dir):
    df = _make_ohlcv(dates=("2023-01-03", "2023-01-04", "2023-01-05"))
    write_ohlcv("2330", df)
    from datetime import date

    result = read_ohlcv("2330", end=date(2023, 1, 4))
    assert len(result) == 2
    assert result["date"].max() == pd.Timestamp("2023-01-04")


def test_read_ohlcv_both_filters_returns_window(storage_dir):
    df = _make_ohlcv(dates=("2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"))
    write_ohlcv("2330", df)
    from datetime import date

    result = read_ohlcv("2330", start=date(2023, 1, 4), end=date(2023, 1, 5))
    assert len(result) == 2
    assert result["date"].min() == pd.Timestamp("2023-01-04")
    assert result["date"].max() == pd.Timestamp("2023-01-05")


# ---------------------------------------------------------------------------
# list_stored_tickers
# ---------------------------------------------------------------------------


def test_list_stored_tickers_empty_store(storage_dir):
    result = list_stored_tickers()
    assert result == []


def test_list_stored_tickers_returns_written_tickers(storage_dir):
    write_ohlcv("2330", _make_ohlcv(ticker="2330"))
    write_ohlcv("2317", _make_ohlcv(ticker="2317"))
    result = list_stored_tickers()
    assert result == ["2317", "2330"]


def test_list_stored_tickers_is_sorted(storage_dir):
    write_ohlcv("2454", _make_ohlcv(ticker="2454"))
    write_ohlcv("0050", _make_ohlcv(ticker="0050"))
    write_ohlcv("2330", _make_ohlcv(ticker="2330"))
    result = list_stored_tickers()
    assert result == sorted(result)


# ---------------------------------------------------------------------------
# get_ticker_date_range
# ---------------------------------------------------------------------------


def test_get_ticker_date_range_missing_ticker_returns_none(storage_dir):
    result = get_ticker_date_range("9999")
    assert result is None


def test_get_ticker_date_range_returns_correct_range(storage_dir):
    from datetime import date

    df = _make_ohlcv(dates=("2023-01-03", "2023-01-04", "2023-01-05"))
    write_ohlcv("2330", df)
    result = get_ticker_date_range("2330")
    assert result == (date(2023, 1, 3), date(2023, 1, 5))


def test_get_ticker_date_range_single_row(storage_dir):
    from datetime import date

    df = _make_ohlcv(dates=("2023-06-15",))
    write_ohlcv("2330", df)
    result = get_ticker_date_range("2330")
    assert result == (date(2023, 6, 15), date(2023, 6, 15))


# ---------------------------------------------------------------------------
# helpers — corporate actions
# ---------------------------------------------------------------------------


def _make_ca(ticker="2330", ex_dates=("2023-07-01",), action_type="cash_dividend") -> pd.DataFrame:
    n = len(ex_dates)
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime(ex_dates),
            "ticker": [ticker] * n,
            "action_type": [action_type] * n,
            "cash_amount": [3.0] * n,
            "ratio": [float("nan")] * n,
        }
    )


# ---------------------------------------------------------------------------
# write_corporate_actions / read_corporate_actions
# ---------------------------------------------------------------------------


def test_write_ca_round_trip(storage_dir):
    write_corporate_actions("2330", _make_ca())
    result = read_corporate_actions("2330")
    assert len(result) == 1
    assert result["cash_amount"].iloc[0] == 3.0


def test_write_ca_idempotent(storage_dir):
    write_corporate_actions("2330", _make_ca())
    write_corporate_actions("2330", _make_ca())
    result = read_corporate_actions("2330")
    assert len(result) == 1


def test_write_ca_new_value_wins_on_same_key(storage_dir):
    write_corporate_actions("2330", _make_ca())

    df_updated = _make_ca()
    df_updated["cash_amount"] = 5.0
    write_corporate_actions("2330", df_updated)

    result = read_corporate_actions("2330")
    assert len(result) == 1
    assert result["cash_amount"].iloc[0] == 5.0


def test_write_ca_appends_new_events(storage_dir):
    write_corporate_actions("2330", _make_ca(ex_dates=("2023-07-01",)))
    write_corporate_actions("2330", _make_ca(ex_dates=("2024-07-01",)))
    result = read_corporate_actions("2330")
    assert len(result) == 2


def test_read_ca_missing_ticker_returns_empty_df(storage_dir):
    result = read_corporate_actions("9999")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0
    for col in ["ex_date", "ticker", "action_type", "cash_amount", "ratio"]:
        assert col in result.columns


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def test_query_ohlcv_view(storage_dir):
    write_ohlcv("2330", _make_ohlcv())
    result = query("SELECT * FROM ohlcv WHERE ticker = '2330'")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 1
    assert result["close"].iloc[0] == 505.0


def test_query_ca_view(storage_dir):
    write_corporate_actions("2330", _make_ca())
    result = query("SELECT * FROM corporate_actions WHERE ticker = '2330'")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 1
    assert result["action_type"].iloc[0] == "cash_dividend"
