import argparse
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import backfill  # noqa: E402

from twstock.data.sources.base import NoDataError, RateLimitError, SourceUnavailableError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START = date(2023, 1, 1)
END = date(2023, 12, 31)


def _make_ohlcv(ticker: str = "2330", dates: tuple = ("2023-01-03",)) -> pd.DataFrame:
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


def _make_ca(ticker: str = "2330") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-01"]),
            "ticker": [ticker],
            "action_type": ["cash_dividend"],
            "cash_amount": [3.0],
            "ratio": [float("nan")],
        }
    )


def make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        tickers="2330",
        start=START,
        end=END,
        throttle_ms=0,
        force=False,
        source="finmind",
        skip_actions=True,
        log_dir=None,
        verbose=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# resolve_fetch_window
# ---------------------------------------------------------------------------


def test_resolve_no_stored_data_returns_full_window(monkeypatch):
    monkeypatch.setattr(backfill, "get_ticker_date_range", lambda t: None)
    assert backfill.resolve_fetch_window("2330", START, END, force=False) == (START, END)


def test_resolve_fully_covered_returns_none(monkeypatch):
    monkeypatch.setattr(
        backfill, "get_ticker_date_range", lambda t: (date(2022, 1, 1), date(2024, 1, 1))
    )
    assert backfill.resolve_fetch_window("2330", START, END, force=False) is None


def test_resolve_fully_covered_force_returns_expanded_window(monkeypatch):
    stored = (date(2022, 1, 1), date(2024, 1, 1))
    monkeypatch.setattr(backfill, "get_ticker_date_range", lambda t: stored)
    result = backfill.resolve_fetch_window("2330", START, END, force=True)
    assert result == (date(2022, 1, 1), date(2024, 1, 1))


def test_resolve_partial_end_gap_returns_expanded_window(monkeypatch):
    monkeypatch.setattr(
        backfill, "get_ticker_date_range", lambda t: (date(2022, 6, 1), date(2023, 6, 1))
    )
    result = backfill.resolve_fetch_window("2330", START, END, force=False)
    assert result == (date(2022, 6, 1), END)


def test_resolve_partial_start_gap_returns_expanded_window(monkeypatch):
    monkeypatch.setattr(
        backfill, "get_ticker_date_range", lambda t: (date(2023, 6, 1), date(2024, 1, 1))
    )
    result = backfill.resolve_fetch_window("2330", START, END, force=False)
    assert result == (START, date(2024, 1, 1))


# ---------------------------------------------------------------------------
# fetch_with_backoff
# ---------------------------------------------------------------------------


def test_fetch_with_backoff_success_first_try(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    fn = MagicMock(return_value="result")
    assert backfill.fetch_with_backoff(fn, "arg") == "result"
    fn.assert_called_once_with("arg")


def test_fetch_with_backoff_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    fn = MagicMock(side_effect=[RateLimitError("throttled"), "result"])
    assert backfill.fetch_with_backoff(fn) == "result"
    assert fn.call_count == 2


def test_fetch_with_backoff_retries_on_source_unavailable(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    fn = MagicMock(side_effect=[SourceUnavailableError("down"), "result"])
    assert backfill.fetch_with_backoff(fn) == "result"
    assert fn.call_count == 2


def test_fetch_with_backoff_reraises_after_all_retries(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    fn = MagicMock(side_effect=RateLimitError("throttled"))
    with pytest.raises(RateLimitError):
        backfill.fetch_with_backoff(fn)
    # 3 retries in the loop + 1 final attempt
    assert fn.call_count == len(backfill.BACKOFF_SCHEDULE) + 1


# ---------------------------------------------------------------------------
# fetch_ticker
# ---------------------------------------------------------------------------


def test_fetch_ticker_success_returns_ok_dict(monkeypatch):
    monkeypatch.setattr(backfill, "write_ohlcv", MagicMock())
    monkeypatch.setattr(backfill, "write_corporate_actions", MagicMock())
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)

    source = MagicMock()
    source.get_daily_ohlcv.return_value = _make_ohlcv(dates=("2023-01-03", "2023-01-04"))
    source.get_corporate_actions.return_value = _make_ca()

    result = backfill.fetch_ticker(source, "2330", START, END, throttle_s=0.0, skip_actions=False)

    assert result["status"] == "ok"
    assert result["ticker"] == "2330"
    assert result["rows_written"] == 2
    assert result["actions_written"] == 1


def test_fetch_ticker_no_ohlcv_data_returns_ok_with_zero_rows(monkeypatch):
    monkeypatch.setattr(backfill, "write_ohlcv", MagicMock())
    monkeypatch.setattr(backfill, "write_corporate_actions", MagicMock())
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)

    source = MagicMock()
    source.get_daily_ohlcv.side_effect = NoDataError("no data")
    source.get_corporate_actions.return_value = _make_ca()

    result = backfill.fetch_ticker(source, "2330", START, END, throttle_s=0.0, skip_actions=False)

    assert result["status"] == "ok"
    assert result["rows_written"] == 0


def test_fetch_ticker_no_ca_data_returns_ok_with_zero_actions(monkeypatch):
    monkeypatch.setattr(backfill, "write_ohlcv", MagicMock())
    monkeypatch.setattr(backfill, "write_corporate_actions", MagicMock())
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)

    source = MagicMock()
    source.get_daily_ohlcv.return_value = _make_ohlcv()
    source.get_corporate_actions.side_effect = NoDataError("no data")

    result = backfill.fetch_ticker(source, "2330", START, END, throttle_s=0.0, skip_actions=False)

    assert result["status"] == "ok"
    assert result["actions_written"] == 0


def test_fetch_ticker_skip_actions_does_not_call_ca(monkeypatch):
    mock_write_ca = MagicMock()
    monkeypatch.setattr(backfill, "write_ohlcv", MagicMock())
    monkeypatch.setattr(backfill, "write_corporate_actions", mock_write_ca)
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)

    source = MagicMock()
    source.get_daily_ohlcv.return_value = _make_ohlcv()

    backfill.fetch_ticker(source, "2330", START, END, throttle_s=0.0, skip_actions=True)

    source.get_corporate_actions.assert_not_called()
    mock_write_ca.assert_not_called()


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_source(monkeypatch):
    source = MagicMock()
    source.get_daily_ohlcv.return_value = _make_ohlcv()
    source.get_corporate_actions.side_effect = NoDataError("no data")
    monkeypatch.setattr(backfill, "FinMindSource", lambda: source)
    monkeypatch.setattr(backfill, "write_ohlcv", MagicMock())
    monkeypatch.setattr(backfill, "write_corporate_actions", MagicMock())
    monkeypatch.setattr(backfill, "get_ticker_date_range", lambda t: None)
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    return source


def test_main_fresh_ticker_fetches_and_writes(mock_source, tmp_path):
    args = make_args(log_dir=tmp_path / "logs")
    with pytest.raises(SystemExit) as exc_info:
        backfill.main(args)
    assert exc_info.value.code == 0
    mock_source.get_daily_ohlcv.assert_called_once()


def test_main_covered_ticker_is_skipped(mock_source, monkeypatch, tmp_path):
    monkeypatch.setattr(
        backfill, "get_ticker_date_range", lambda t: (date(2022, 1, 1), date(2024, 1, 1))
    )
    args = make_args(log_dir=tmp_path / "logs")
    with pytest.raises(SystemExit) as exc_info:
        backfill.main(args)
    assert exc_info.value.code == 0
    mock_source.get_daily_ohlcv.assert_not_called()


def test_main_force_fetches_even_when_covered(mock_source, monkeypatch, tmp_path):
    monkeypatch.setattr(
        backfill, "get_ticker_date_range", lambda t: (date(2022, 1, 1), date(2024, 1, 1))
    )
    args = make_args(log_dir=tmp_path / "logs", force=True)
    with pytest.raises(SystemExit) as exc_info:
        backfill.main(args)
    assert exc_info.value.code == 0
    mock_source.get_daily_ohlcv.assert_called_once()


def test_main_one_ticker_fails_run_continues(mock_source, tmp_path):
    mock_source.get_daily_ohlcv.side_effect = [_make_ohlcv("2330"), Exception("network error")]
    args = make_args(tickers="2330,2317", log_dir=tmp_path / "logs")
    with pytest.raises(SystemExit) as exc_info:
        backfill.main(args)
    assert exc_info.value.code == 1
    assert mock_source.get_daily_ohlcv.call_count == 2


def test_main_rate_limited_ticker_exits_with_code_1(mock_source, tmp_path):
    mock_source.get_daily_ohlcv.side_effect = RateLimitError("throttled")
    args = make_args(log_dir=tmp_path / "logs")
    with pytest.raises(SystemExit) as exc_info:
        backfill.main(args)
    assert exc_info.value.code == 1


def test_main_log_file_is_written(mock_source, tmp_path):
    log_dir = tmp_path / "logs"
    args = make_args(log_dir=log_dir)
    with pytest.raises(SystemExit):
        backfill.main(args)

    log_files = list(log_dir.glob("backfill_*.jsonl"))
    assert len(log_files) == 1

    lines = log_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2  # at least one ticker record + one summary

    summary = json.loads(lines[-1])
    assert summary["summary"] is True
    assert "ok" in summary
    assert "failed" in summary
