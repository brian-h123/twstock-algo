"""Rejection adversarial fixtures (PHASE_5.md §6.4, step 12 -- rejection group).

Price-limit-lock and insufficient-funds mechanics already have isolated
BrokerSimulator coverage with hand-submitted orders
(test_simulator.py::TestPriceLimitLock, ::TestInsufficientBuyingPower).
These fixtures instead exercise the surrounding composition through
PaperSession, where each rejection has a consequence the raw simulator
test can't see: a lock at the final forced-liquidation bar can leave the
account permanently holding a position when the run ends, and the
single-asset decide loop sizes an order off bar T's CLOSE while the fill
resolves at T+1's OPEN -- a real, currently un-cushioned gap between those
two prices can turn a "correctly sized" order into an unaffordable one by
the time it actually executes.
"""

from datetime import date
from decimal import Decimal
from types import MappingProxyType

import pandas as pd

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.strategy import BuyAndHold
from twstock.broker.events import Bar, BarBatch, EventKind
from twstock.broker.feed import FixtureFeed
from twstock.broker.fill import FullFill
from twstock.broker.rejection import PriceLimitProvider, RejectionReason
from twstock.broker.session import PaperSession
from twstock.broker.settlement import NettingSettlement

# Confirmed-session XTAI weekdays, reused from test_simulator.py / test_oracles.py.
_T1 = date(2026, 6, 1)  # Monday
_T2 = date(2026, 6, 2)  # Tuesday
_T3 = date(2026, 6, 3)  # Wednesday
_T4 = date(2026, 6, 4)  # Thursday


def _bar(ticker: str, d: date, price: str = "100", volume: int = 10_000_000) -> Bar:
    p = Decimal(price)
    return Bar(
        ticker=ticker, date=d, open=p, high=p, low=p, close=p, volume=volume, corporate_actions=()
    )


def _batch(d: date, *bars: Bar) -> BarBatch:
    return BarBatch(date=d, bars=MappingProxyType({b.ticker: b for b in bars}))


def _fake_bars(dates: list[date]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(dates)
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000}, index=idx
    )


class _NoLimits(PriceLimitProvider):
    """Always-open price-limit provider, used to isolate the cash-
    affordability path from the (unrelated) price-limit-lock mechanic."""

    def band(self, ticker: str, t: date) -> None:
        return None


# ── limit-lock buy ────────────────────────────────────────────────────────────


def test_buy_locked_at_ceiling_never_fills(monkeypatch) -> None:
    """DerivedPriceLimits needs one observed bar before a band exists; the
    warm-up call (bar 0, before any order is submitted) supplies that, so
    the very first resolution bar (T2) already has a real band derived
    from T1's close=100 -> (90.0, 110.0). Opening T2 AT the ceiling must
    lock the buy end-to-end: no fill, no crash, position stays flat."""
    dates = [_T1, _T2, _T3]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1, price="100")),
            _batch(_T2, _bar("2330", _T2, price="110.0")),  # at the ceiling -> locked
            _batch(_T3, _bar("2330", _T3, price="110.0")),
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
    session.run()

    events = list(session.event_log)
    rejects = [e for e in events if e.kind == EventKind.REJECT]
    fills = [e for e in events if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)]

    assert len(rejects) == 1
    assert rejects[0].ts == _T2
    assert rejects[0].payload["reason"] == RejectionReason.PRICE_LIMIT_LOCK.value
    assert fills == []
    assert session.account.positions.get("2330") is None or (
        session.account.positions["2330"].open_shares == 0
    )


# ── limit-lock sell, at the mandatory final liquidation ─────────────────────


def test_sell_locked_at_floor_leaves_position_stuck_after_forced_liquidation(monkeypatch) -> None:
    """The forced-liquidation sell on the FINAL bar is not exempt from the
    price-limit check -- if the market is locked limit-down exactly on
    that bar, the session must still complete without crashing, and the
    position is left OPEN at the end of the run rather than being
    magically force-closed. This is a real, surprising consequence of
    D9 (submit-and-partial-fill, never pre-screen/clamp): "forced
    liquidation" is a best-effort submission, not a guarantee."""
    dates = [_T1, _T2, _T3, _T4]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1, price="100")),  # decide: buy
            _batch(_T2, _bar("2330", _T2, price="100")),  # buy fills, unlocked
            _batch(_T3, _bar("2330", _T3, price="100")),  # holds; seeds T4's reference
            _batch(_T4, _bar("2330", _T4, price="90.0")),  # at the floor -> forced sell locked
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
    buy_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "buy"
    ]
    sell_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "sell"
    ]
    rejects = [e for e in events if e.kind == EventKind.REJECT]

    assert len(buy_fills) == 1
    assert sell_fills == []  # the forced-liquidation sell never filled
    assert len(rejects) == 1
    assert rejects[0].ts == _T4
    assert rejects[0].payload["reason"] == RejectionReason.PRICE_LIMIT_LOCK.value
    assert session.account.positions["2330"].open_shares == int(buy_fills[0].payload["shares"])


# ── insufficient funds from a decide-close vs. fill-open price gap ─────────


def test_insufficient_funds_from_decide_to_fill_price_gap(monkeypatch) -> None:
    """The single-asset decide loop sizes shares off bar T's CLOSE
    (spendable // close); the order then resolves at T+1's OPEN. Unlike
    the cross-sectional path (budget_weights, D6), there is no cushion for
    that gap here. A large enough close-to-open jump makes a "correctly"
    sized order unaffordable by the time it actually executes -- not a
    contrived tiny-capital case, but a real gap in the current sizing.

    Price limits are disabled (_NoLimits) specifically to isolate this
    path: under the real ±10% DerivedPriceLimits band, any gap large
    enough to zero out affordability is also large enough to trip
    PRICE_LIMIT_LOCK first (checked before cash affordability in
    _resolve_order), so the two rejection causes can't both be observed
    from the same price move -- see test_buy_locked_at_ceiling_never_fills
    for that path instead."""
    dates = [_T1, _T2, _T3]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1, price="100")),  # decide sizes 10,000 shares @ 100
            _batch(_T2, _bar("2330", _T2, price="1000000")),  # open gaps up 10,000x
            _batch(_T3, _bar("2330", _T3, price="1000000")),
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
        price_limit_provider=_NoLimits(),
    )
    session.run()

    events = list(session.event_log)
    rejects = [e for e in events if e.kind == EventKind.REJECT]
    fills = [e for e in events if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)]

    assert len(rejects) == 1
    assert rejects[0].ts == _T2
    assert rejects[0].payload["reason"] == RejectionReason.INSUFFICIENT_BUYING_POWER.value
    assert fills == []
