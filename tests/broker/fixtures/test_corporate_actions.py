"""Corporate-actions-on-a-held-name adversarial fixtures (PHASE_5.md §6.4,
step 12 -- corporate actions group).

decompose()'s combined-dividend decomposition and DerivedPriceLimits'
ex-day reference math already have isolated unit coverage
(test_corporate_actions.py, and rejection.py's own _ex_day_reference
tests). These fixtures exercise the composition: a combined 除權息 event
must produce BOTH mutations from ONE action and leave account value
continuous, and the ex-day-adjusted reference must be the one an actual
order gets checked against inside a full PaperSession run -- not just
computed correctly by _ex_day_reference() in isolation.
"""

from datetime import date
from decimal import Decimal
from types import MappingProxyType

import pandas as pd
import pytest

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.strategy import BuyAndHold
from twstock.broker.events import ActionType, Bar, BarBatch, CorporateAction, EventKind
from twstock.broker.feed import FixtureFeed
from twstock.broker.fill import FullFill
from twstock.broker.rejection import RejectionReason
from twstock.broker.session import PaperSession
from twstock.broker.settlement import NettingSettlement

# Confirmed-session XTAI weekdays, reused from test_cash_settlement.py
# (2026-06-06/07 is a weekend; 2026-06-08 is a confirmed following session).
_T1 = date(2026, 6, 1)  # Monday
_T2 = date(2026, 6, 2)  # Tuesday
_T3 = date(2026, 6, 3)  # Wednesday
_T4 = date(2026, 6, 4)  # Thursday
_T5 = date(2026, 6, 5)  # Friday
_T6 = date(2026, 6, 8)  # Monday


def _bar(
    ticker: str,
    d: date,
    price: str = "100",
    volume: int = 10_000_000,
    actions: tuple[CorporateAction, ...] = (),
) -> Bar:
    p = Decimal(price)
    return Bar(
        ticker=ticker,
        date=d,
        open=p,
        high=p,
        low=p,
        close=p,
        volume=volume,
        corporate_actions=actions,
    )


def _batch(d: date, *bars: Bar) -> BarBatch:
    return BarBatch(date=d, bars=MappingProxyType({b.ticker: b for b in bars}))


def _fake_bars(dates: list[date]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(dates)
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000}, index=idx
    )


# ── combined cash + stock dividend (除權息) ──────────────────────────────────


def test_combined_dividend_produces_both_mutations_with_continuous_value(monkeypatch) -> None:
    """A single CASH_AND_STOCK_DIVIDEND action must decompose into BOTH a
    ShareMutation (SPLIT event) and a CashMutation (DIVIDEND event) on the
    same bar -- the TWSE Top 50 norm, per ADR 0007 -- not one or the
    other. The ex-day close is set to the same economically-neutral
    reference DerivedPriceLimits uses ((prior_close - cash) / multiplier
    = (100-1)/1.1 = 90 exactly), so total account value must be
    continuous across the ex-date: the extra 10% shares at the lower
    price plus the new cash receivable should reproduce the pre-dividend
    value almost exactly."""
    dates = [_T1, _T2, _T3, _T4, _T5, _T6]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    dividend = CorporateAction(
        action_type=ActionType.CASH_AND_STOCK_DIVIDEND,
        ex_date=_T3,
        cash_amount=Decimal("1"),
        ratio=0.1,  # 10% stock dividend: share_multiplier = 1 + ratio = 1.1
        pay_date=_T5,
        subscription_price=None,
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1, price="100")),
            _batch(_T2, _bar("2330", _T2, price="100")),
            _batch(_T3, _bar("2330", _T3, price="90", actions=(dividend,))),  # ex-date
            _batch(_T4, _bar("2330", _T4, price="90")),
            _batch(_T5, _bar("2330", _T5, price="90")),  # pay-date
            _batch(_T6, _bar("2330", _T6, price="90")),
        ]
    )
    session = PaperSession(
        strategy=BuyAndHold(),
        tickers=["2330"],
        feed=feed,
        capital=Decimal("1000000"),
        cost_model=TaiwanCostModel(),
        fill_model=FullFill(),
        settlement=NettingSettlement(),
        max_position_pct=1.0,
    )
    result = session.run()

    events = list(session.event_log)
    split_events = [e for e in events if e.kind == EventKind.SPLIT]
    dividend_events = [e for e in events if e.kind == EventKind.DIVIDEND]

    assert len(split_events) == 1
    assert split_events[0].ts == _T3
    assert float(split_events[0].payload["ratio"]) == pytest.approx(1.1)

    assert len(dividend_events) == 1
    assert dividend_events[0].ts == _T3
    assert dividend_events[0].payload["pay_date"] == _T5.isoformat()

    equity_T2 = float(result.equity_curve[_T2])
    equity_T3 = float(result.equity_curve[_T3])
    assert equity_T3 == pytest.approx(equity_T2, rel=0.01)


# ── ex-day adjusted-reference limit band ─────────────────────────────────────


def test_ex_day_price_limit_band_uses_adjusted_reference_not_raw_close(monkeypatch) -> None:
    """A 1-for-2 split (ratio=0.5, multiplier=2.0) halves the fair price.
    DerivedPriceLimits must center that day's band on the ADJUSTED
    reference (100 / 2.0 = 50 -> band (45, 55)), not the raw prior close
    (100 -> band (90, 110)). The forced-liquidation sell resolves exactly
    on this ex-date bar, at an open of 50 -- economically neutral, but
    50 <= 90 would falsely lock as PRICE_LIMIT_LOCK under the unadjusted
    band. The correct, adjusted band must let the sell fill normally."""
    dates = [_T1, _T2, _T3, _T4]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    split_action = CorporateAction(
        action_type=ActionType.STOCK_SPLIT,
        ex_date=_T4,
        cash_amount=None,
        ratio=0.5,  # 1-for-2 split: share_multiplier = 1/0.5 = 2.0
        pay_date=None,
        subscription_price=None,
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1, price="100")),  # decide: buy
            _batch(_T2, _bar("2330", _T2, price="100")),  # buy fills, unlocked
            _batch(_T3, _bar("2330", _T3, price="100")),  # holds; seeds T4's reference
            _batch(_T4, _bar("2330", _T4, price="50", actions=(split_action,))),  # ex-date
        ]
    )
    session = PaperSession(
        strategy=BuyAndHold(),
        tickers=["2330"],
        feed=feed,
        capital=Decimal("1000000"),
        cost_model=TaiwanCostModel(),
        fill_model=FullFill(),
        settlement=NettingSettlement(),
        max_position_pct=1.0,
    )
    session.run()  # must not raise

    events = list(session.event_log)
    split_events = [e for e in events if e.kind == EventKind.SPLIT]
    sell_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "sell"
    ]
    price_locked_rejects = [
        e
        for e in events
        if e.kind == EventKind.REJECT
        and e.payload["reason"] == RejectionReason.PRICE_LIMIT_LOCK.value
    ]

    assert len(split_events) == 1
    assert split_events[0].ts == _T4
    assert len(sell_fills) == 1
    assert sell_fills[0].ts == _T4
    assert price_locked_rejects == []
