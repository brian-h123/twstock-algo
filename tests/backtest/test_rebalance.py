import exchange_calendars as xcals
import pandas as pd

from twstock.backtest.rebalance import (
    Daily,
    Monthly,
    Quarterly,
    RebalanceContext,
    RebalanceSchedule,
    Weekly,
)

_XTAI = xcals.get_calendar("XTAI")


def _ctx(date: str | pd.Timestamp) -> RebalanceContext:
    return RebalanceContext(
        date=pd.Timestamp(date),
        current_weights=pd.Series(dtype=float),
        target_weights=pd.Series(dtype=float),
        signal_changed=False,
    )


def _month_sessions(year: int, month: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(year, month, 1)
    end = start + pd.offsets.MonthEnd(1)
    return _XTAI.sessions_in_range(start, end)


def _week_sessions(anchor: pd.Timestamp) -> pd.DatetimeIndex:
    week_start = anchor - pd.Timedelta(days=anchor.weekday())
    week_end = week_start + pd.Timedelta(days=6)
    return _XTAI.sessions_in_range(week_start, week_end)


def _quarter_sessions(anchor: pd.Timestamp) -> pd.DatetimeIndex:
    quarter_start = anchor.to_period("Q").start_time.date()
    quarter_end = anchor.to_period("Q").end_time.date()
    return _XTAI.sessions_in_range(quarter_start, quarter_end)


class TestRebalanceScheduleInterface:
    def test_monthly_is_subclass(self):
        assert issubclass(Monthly, RebalanceSchedule)

    def test_weekly_is_subclass(self):
        assert issubclass(Weekly, RebalanceSchedule)

    def test_quarterly_is_subclass(self):
        assert issubclass(Quarterly, RebalanceSchedule)

    def test_daily_is_subclass(self):
        assert issubclass(Daily, RebalanceSchedule)


class TestMonthly:
    def test_returns_true_on_first_session_normal_month(self):
        first = _month_sessions(2023, 3)[0]
        assert Monthly().should_rebalance(_ctx(first)) is True

    def test_returns_false_on_second_session(self):
        sessions = _month_sessions(2023, 3)
        assert Monthly().should_rebalance(_ctx(sessions[1])) is False

    def test_returns_false_on_last_session(self):
        sessions = _month_sessions(2023, 3)
        assert Monthly().should_rebalance(_ctx(sessions[-1])) is False

    def test_jan_2024_first_session_is_not_jan_1(self):
        # Jan 1, 2024 = Monday (New Year's Day) → exchange closed
        first = _month_sessions(2024, 1)[0]
        assert first != pd.Timestamp("2024-01-01")
        assert Monthly().should_rebalance(_ctx(first)) is True

    def test_jan_2024_jan_1_returns_false(self):
        # Jan 1, 2024 is a public holiday — not the rebalance day
        assert Monthly().should_rebalance(_ctx("2024-01-01")) is False

    def test_feb_2022_lunar_new_year_pushes_first_session(self):
        # LNY 2022 = Feb 1 (Tue); TWSE closed Feb 1–4 → first session is Feb 7
        first = _month_sessions(2022, 2)[0]
        assert first != pd.Timestamp("2022-02-01")
        assert Monthly().should_rebalance(_ctx(first)) is True

    def test_feb_2022_feb_1_returns_false(self):
        # Feb 1, 2022 is Lunar New Year's Eve — exchange closed
        assert Monthly().should_rebalance(_ctx("2022-02-01")) is False

    def test_exactly_one_rebalance_per_month_in_year(self):
        sched = Monthly()
        for month in range(1, 13):
            sessions = _month_sessions(2023, month)
            hits = [d for d in sessions if sched.should_rebalance(_ctx(d))]
            assert len(hits) == 1, f"Expected 1 rebalance in 2023-{month:02d}, got {len(hits)}"


class TestWeekly:
    def test_returns_true_on_first_session_of_week(self):
        anchor = pd.Timestamp("2023-03-07")
        first = _week_sessions(anchor)[0]
        assert Weekly().should_rebalance(_ctx(first)) is True

    def test_returns_false_on_second_session_of_week(self):
        anchor = pd.Timestamp("2023-03-07")
        sessions = _week_sessions(anchor)
        assert Weekly().should_rebalance(_ctx(sessions[1])) is False

    def test_returns_false_on_last_session_of_week(self):
        anchor = pd.Timestamp("2023-03-07")
        sessions = _week_sessions(anchor)
        assert Weekly().should_rebalance(_ctx(sessions[-1])) is False

    def test_holiday_monday_pushes_first_session(self):
        # Week of 2024-01-01: Monday = New Year's Day → exchange closed
        # First session of the week should be Tuesday Jan 2
        anchor = pd.Timestamp("2024-01-01")
        sessions = _week_sessions(anchor)
        first = sessions[0]
        assert first != pd.Timestamp("2024-01-01")
        assert Weekly().should_rebalance(_ctx(first)) is True
        assert Weekly().should_rebalance(_ctx("2024-01-01")) is False


class TestQuarterly:
    def test_returns_true_on_first_session_of_quarter(self):
        # Q1 2023 starts Jan 1 (Sunday + New Year's Day) → first session is Jan 3
        anchor = pd.Timestamp("2023-02-15")
        first = _quarter_sessions(anchor)[0]
        assert Quarterly().should_rebalance(_ctx(first)) is True

    def test_returns_false_on_second_session_of_quarter(self):
        anchor = pd.Timestamp("2023-02-15")
        sessions = _quarter_sessions(anchor)
        assert Quarterly().should_rebalance(_ctx(sessions[1])) is False

    def test_returns_false_mid_quarter(self):
        assert Quarterly().should_rebalance(_ctx("2023-04-15")) is False

    def test_exactly_four_rebalances_in_year(self):
        sessions_2023 = _XTAI.sessions_in_range("2023-01-01", "2023-12-31")
        sched = Quarterly()
        hits = [d for d in sessions_2023 if sched.should_rebalance(_ctx(d))]
        assert len(hits) == 4


class TestDaily:
    def test_always_returns_true(self):
        for date in ["2023-01-03", "2023-06-15", "2023-12-29"]:
            assert Daily().should_rebalance(_ctx(date)) is True

    def test_returns_bool(self):
        result = Daily().should_rebalance(_ctx("2023-03-01"))
        assert result is True
