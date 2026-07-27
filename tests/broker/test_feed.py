from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from twstock.broker.events import ActionType, Bar, BarBatch, CorporateAction
from twstock.broker.feed import (
    BarFeed,
    FixtureFeed,
    LiveFeed,
    ReplayFeed,
    StaleLiveFeedError,
    _assert_fresh,
    _latest_completed_session,
)
from twstock.data.storage import write_corporate_actions, write_ohlcv

# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    d = tmp_path / "by_ticker"
    d.mkdir()
    monkeypatch.setattr("twstock.data.storage.DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def set_universe(monkeypatch):
    """Restrict ReplayFeed's universe to a small, test-controlled ticker set
    instead of the real hard-coded Top 50 (which would require fixture data
    for every ticker)."""

    def _set(tickers: list[str]) -> None:
        monkeypatch.setattr("twstock.broker.feed.get_top50_universe", lambda: tickers)

    return _set


def _make_ohlcv(ticker="2330", dates=("2026-01-05",), close=100.0) -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ticker": [ticker] * n,
            "open": [close] * n,
            "high": [close] * n,
            "low": [close] * n,
            "close": [close] * n,
            "volume": [1_000_000] * n,
        }
    )


def _make_ca(
    ticker="2330",
    ex_dates=("2026-01-05",),
    action_type="stock_split",
    cash_amount=None,
    pay_dates=None,
    ratio=0.25,
    subscription_price=None,
) -> pd.DataFrame:
    n = len(ex_dates)
    return pd.DataFrame(
        {
            "ex_date": pd.to_datetime(ex_dates),
            "ticker": [ticker] * n,
            "action_type": [action_type] * n,
            "cash_amount": [cash_amount if cash_amount is not None else float("nan")] * n,
            "pay_date": pd.to_datetime(pay_dates) if pay_dates is not None else [pd.NaT] * n,
            "ratio": [ratio if ratio is not None else float("nan")] * n,
            "subscription_price": [
                subscription_price if subscription_price is not None else float("nan")
            ]
            * n,
        }
    )


@pytest.fixture
def set_today(monkeypatch):
    """Pin LiveFeed's notion of "today" so tests don't depend on wall-clock
    date. Patches the `date` name inside feed.py's module namespace with a
    subclass whose today() is fixed -- the standard technique for a builtin
    type that can't be monkeypatched directly."""

    def _set(d: date) -> None:
        class _FixedDate(date):
            @classmethod
            def today(cls):
                return d

        monkeypatch.setattr("twstock.broker.feed.date", _FixedDate)

    return _set


def _bar(
    ticker: str = "2330",
    d: date = date(2026, 1, 1),
    close: str = "100.00",
    actions: tuple[CorporateAction, ...] = (),
) -> Bar:
    price = Decimal(close)
    return Bar(
        ticker=ticker,
        date=d,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000_000,
        corporate_actions=actions,
    )


# ── BarFeed ABC ──────────────────────────────────────────────────────────────


class TestBarFeedABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BarFeed()


# ── ReplayFeed: batching ─────────────────────────────────────────────────────


class TestReplayFeedBatching:
    def test_yields_batches_sorted_by_date_across_tickers(self, storage_dir, set_universe):
        """Universe order (2330 first) intentionally disagrees with date
        order, so a pass only proves ReplayFeed sorts by date itself rather
        than inheriting an incidental per-ticker ordering."""
        set_universe(["2330", "2317"])
        write_ohlcv("2330", _make_ohlcv(ticker="2330", dates=("2026-01-06",)))
        write_ohlcv("2317", _make_ohlcv(ticker="2317", dates=("2026-01-04", "2026-01-05")))

        batches = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))

        assert [b.date for b in batches] == [
            date(2026, 1, 4),
            date(2026, 1, 5),
            date(2026, 1, 6),
        ]

    def test_batch_bars_keyed_by_ticker(self, storage_dir, set_universe):
        set_universe(["2330", "2317"])
        write_ohlcv("2330", _make_ohlcv(ticker="2330", dates=("2026-01-05",)))
        write_ohlcv("2317", _make_ohlcv(ticker="2317", dates=("2026-01-05",)))

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))

        assert set(batch.bars.keys()) == {"2330", "2317"}

    def test_ticker_missing_on_a_date_is_absent_not_none(self, storage_dir, set_universe):
        set_universe(["2330", "2317"])
        write_ohlcv("2330", _make_ohlcv(ticker="2330", dates=("2026-01-05", "2026-01-06")))
        write_ohlcv("2317", _make_ohlcv(ticker="2317", dates=("2026-01-05",)))  # gap on 01-06

        batches = {b.date: b for b in ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31))}

        assert "2317" not in batches[date(2026, 1, 6)].bars
        assert "2317" in batches[date(2026, 1, 5)].bars

    def test_no_data_for_any_ticker_yields_no_batches(self, storage_dir, set_universe):
        set_universe(["2330"])

        assert list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31))) == []

    def test_ohlcv_outside_window_excluded(self, storage_dir, set_universe):
        set_universe(["2330"])
        write_ohlcv(
            "2330",
            _make_ohlcv(dates=("2025-12-31", "2026-01-05", "2026-02-01")),
        )

        batches = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))

        assert [b.date for b in batches] == [date(2026, 1, 5)]

    def test_batch_bars_is_immutable(self, storage_dir, set_universe):
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv())

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))

        with pytest.raises(TypeError):
            batch.bars["9999"] = _bar(ticker="9999")  # type: ignore[index]


# ── ReplayFeed: Bar conversion ───────────────────────────────────────────────


class TestReplayFeedBarConversion:
    def test_bar_fields_convert_from_raw_ohlcv(self, storage_dir, set_universe):
        set_universe(["2330"])
        write_ohlcv(
            "2330",
            pd.DataFrame(
                {
                    "date": pd.to_datetime(["2026-01-05"]),
                    "ticker": ["2330"],
                    "open": [500.5],
                    "high": [510.25],
                    "low": [495.0],
                    "close": [505.75],
                    "volume": [12_345_678],
                }
            ),
        )

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))
        bar = batch.bars["2330"]

        assert bar.ticker == "2330"
        assert bar.date == date(2026, 1, 5)
        assert bar.open == Decimal("500.5")
        assert bar.high == Decimal("510.25")
        assert bar.low == Decimal("495.0")
        assert bar.close == Decimal("505.75")
        assert bar.volume == 12_345_678
        assert isinstance(bar.volume, int)
        assert isinstance(bar.open, Decimal)

    def test_bar_without_matching_action_has_empty_tuple(self, storage_dir, set_universe):
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-01-05",)))

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))

        assert batch.bars["2330"].corporate_actions == ()


# ── ReplayFeed: corporate actions ────────────────────────────────────────────


class TestReplayFeedCorporateActions:
    def test_action_attached_to_matching_ex_date_bar(self, storage_dir, set_universe):
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-01-05",)))
        write_corporate_actions(
            "2330",
            _make_ca(ex_dates=("2026-01-05",), action_type="stock_split", ratio=0.25),
        )

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))
        [action] = batch.bars["2330"].corporate_actions

        assert action.action_type == ActionType.STOCK_SPLIT
        assert action.ex_date == date(2026, 1, 5)
        assert action.ratio == 0.25

    def test_nullable_fields_convert_nan_to_none(self, storage_dir, set_universe):
        """A stock-only split has no cash leg — cash_amount/pay_date/
        subscription_price arrive as NaN/NaT and must become None, not be
        passed through as pandas' null sentinels."""
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-01-05",)))
        write_corporate_actions(
            "2330",
            _make_ca(ex_dates=("2026-01-05",), action_type="stock_split", ratio=0.25),
        )

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))
        [action] = batch.bars["2330"].corporate_actions

        assert action.cash_amount is None
        assert action.pay_date is None
        assert action.subscription_price is None

    def test_cash_dividend_fields_convert_correctly(self, storage_dir, set_universe):
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-01-05",)))
        write_corporate_actions(
            "2330",
            _make_ca(
                ex_dates=("2026-01-05",),
                action_type="cash_dividend",
                cash_amount=2.5,
                pay_dates=("2026-02-01",),
                ratio=None,
            ),
        )

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))
        [action] = batch.bars["2330"].corporate_actions

        assert action.cash_amount == Decimal("2.5")
        assert isinstance(action.cash_amount, Decimal)
        assert action.pay_date == date(2026, 2, 1)
        assert action.ratio is None

    def test_action_outside_window_excluded(self, storage_dir, set_universe):
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-01-05",)))
        write_corporate_actions(
            "2330",
            _make_ca(ex_dates=("2025-01-01",), action_type="stock_split", ratio=0.25),
        )

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))

        assert batch.bars["2330"].corporate_actions == ()

    def test_action_scoped_to_its_own_ticker(self, storage_dir, set_universe):
        """actions_by_date must reset per ticker — a 2317 split must never
        attach to a 2330 bar sharing the same ex-date."""
        set_universe(["2330", "2317"])
        write_ohlcv("2330", _make_ohlcv(ticker="2330", dates=("2026-01-05",)))
        write_ohlcv("2317", _make_ohlcv(ticker="2317", dates=("2026-01-05",)))
        write_corporate_actions(
            "2317",
            _make_ca(
                ticker="2317", ex_dates=("2026-01-05",), action_type="stock_split", ratio=0.25
            ),
        )

        [batch] = list(ReplayFeed(start=date(2026, 1, 1), end=date(2026, 1, 31)))

        assert batch.bars["2330"].corporate_actions == ()
        assert len(batch.bars["2317"].corporate_actions) == 1


# ── FixtureFeed ──────────────────────────────────────────────────────────────


class TestFixtureFeed:
    def test_yields_given_batches_verbatim(self):
        b1 = BarBatch(date=date(2026, 1, 5), bars={"2330": _bar(d=date(2026, 1, 5))})
        b2 = BarBatch(date=date(2026, 1, 6), bars={"2330": _bar(d=date(2026, 1, 6))})

        assert list(FixtureFeed([b1, b2])) == [b1, b2]

    def test_empty_list_yields_nothing(self):
        assert list(FixtureFeed([])) == []

    def test_missing_ticker_on_a_date_is_replayed_faithfully(self):
        """Backs the 'missing-bar isn't suspension' adversarial fixture
        (§6.4): FixtureFeed must replay a batch where a ticker's key is
        simply absent, not synthesize one."""
        batch = BarBatch(date=date(2026, 1, 5), bars={"2330": _bar(d=date(2026, 1, 5))})

        [replayed] = list(FixtureFeed([batch]))

        assert "2317" not in replayed.bars


# ── _latest_completed_session ───────────────────────────────────────────────


class TestLatestCompletedSession:
    def test_returns_today_when_today_is_a_trading_session(self, set_today):
        # 2026-06-02 is a confirmed XTAI trading session (Tuesday).
        set_today(date(2026, 6, 2))
        assert _latest_completed_session() == date(2026, 6, 2)

    def test_rolls_back_over_a_non_trading_day(self, set_today):
        # 2026-06-06 is a Saturday; not an XTAI session.
        set_today(date(2026, 6, 6))
        assert _latest_completed_session() == date(2026, 6, 5)


# ── _assert_fresh ────────────────────────────────────────────────────────────


class TestAssertFresh:
    def test_raises_when_2330_has_no_stored_data(self, storage_dir):
        with pytest.raises(StaleLiveFeedError):
            _assert_fresh(date(2026, 6, 2))

    def test_raises_when_stored_range_does_not_reach_expected(self, storage_dir):
        write_ohlcv("2330", _make_ohlcv(dates=("2026-05-19",)))
        with pytest.raises(StaleLiveFeedError):
            _assert_fresh(date(2026, 6, 2))

    def test_does_not_raise_when_stored_range_reaches_expected(self, storage_dir):
        write_ohlcv("2330", _make_ohlcv(dates=("2026-06-02",)))
        _assert_fresh(date(2026, 6, 2))  # no raise

    def test_does_not_raise_when_stored_range_exceeds_expected(self, storage_dir):
        write_ohlcv("2330", _make_ohlcv(dates=("2026-06-05",)))
        _assert_fresh(date(2026, 6, 2))  # no raise


# ── LiveFeed ─────────────────────────────────────────────────────────────────


class TestLiveFeed:
    def test_is_a_replay_feed(self, storage_dir, set_universe, set_today):
        set_today(date(2026, 6, 2))
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-06-02",)))

        feed = LiveFeed(start=date(2026, 1, 1))

        assert isinstance(feed, ReplayFeed)
        assert isinstance(feed, BarFeed)

    def test_freshness_check_raises_on_stale_store(self, storage_dir, set_universe, set_today):
        set_today(date(2026, 6, 2))
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-05-19",)))  # stale

        with pytest.raises(StaleLiveFeedError):
            LiveFeed(start=date(2026, 1, 1))

    def test_freshness_check_false_bypasses_stale_store(self, storage_dir, set_universe, set_today):
        set_today(date(2026, 6, 2))
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-05-19",)))  # stale, ignored

        feed = LiveFeed(start=date(2026, 1, 1), freshness_check=False)

        assert [b.date for b in feed] == [date(2026, 5, 19)]

    def test_reads_through_the_latest_completed_session(self, storage_dir, set_universe, set_today):
        set_today(date(2026, 6, 2))
        set_universe(["2330"])
        write_ohlcv(
            "2330", _make_ohlcv(dates=("2026-05-29", "2026-06-01", "2026-06-02", "2026-06-03"))
        )

        batches = list(LiveFeed(start=date(2026, 1, 1)))

        # 2026-06-03 is beyond today's session and must be excluded, even
        # though it's already sitting in local storage.
        assert [b.date for b in batches] == [
            date(2026, 5, 29),
            date(2026, 6, 1),
            date(2026, 6, 2),
        ]

    def test_start_excludes_earlier_bars(self, storage_dir, set_universe, set_today):
        set_today(date(2026, 6, 2))
        set_universe(["2330"])
        write_ohlcv("2330", _make_ohlcv(dates=("2026-05-29", "2026-06-01", "2026-06-02")))

        batches = list(LiveFeed(start=date(2026, 6, 1)))

        assert [b.date for b in batches] == [date(2026, 6, 1), date(2026, 6, 2)]
