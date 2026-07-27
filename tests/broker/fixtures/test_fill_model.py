"""Fill-model adversarial fixtures (PHASE_5.md §6.4, step 12 -- fill-model group).

Both mechanics here (participation-rate floor math; trailing-vs-current-bar
volume) already have isolated unit coverage: floor math against
VolumeParticipationFiller directly (test_fill.py), and prior-volume tracking
against BrokerSimulator with hand-submitted orders
(test_simulator.py::TestPriorVolumeFallback). What's untested there is
whether the full PaperSession composition preserves those properties once
share counts and dates flow through cost-budgeted sizing, Decimal/string
event-payload round-tripping, and the session's own prior-volume warm-up --
that's what these two fixtures check.
"""

from datetime import date
from decimal import Decimal
from types import MappingProxyType

import pandas as pd

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.strategy import BuyAndHold
from twstock.broker.events import Bar, BarBatch, EventKind
from twstock.broker.feed import FixtureFeed
from twstock.broker.fill import VolumeParticipationFiller
from twstock.broker.session import PaperSession
from twstock.broker.settlement import NettingSettlement

# Confirmed-session XTAI weekdays, reused from test_simulator.py / test_oracles.py.
_T1 = date(2026, 6, 1)  # Monday
_T2 = date(2026, 6, 2)  # Tuesday
_T3 = date(2026, 6, 3)  # Wednesday


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


# ── trailing ADV, not the fill bar's own volume ──────────────────────────────


def test_fill_capped_by_trailing_volume_not_the_fill_bars_own_volume(monkeypatch) -> None:
    """D10: the fill cap must use trailing ADV AS OF the decision bar T, never
    the (not-yet-knowable-at-the-open) volume of the fill bar T+1 itself.

    T1 (decision bar) carries a thin volume; T2 (fill bar) carries a huge
    one. BuyAndHold wants a FIXED 15,000 shares -- comfortably under what
    T2's own volume would allow (2,500,000) but above what T1's trailing
    volume allows (10,000). If the simulator ever capped using the fill
    bar's own volume instead of the trailing one, this order would fill in
    full; under the correct trailing-ADV rule it must partially fill at
    exactly 10,000 and expire the remainder."""
    dates = [_T1, _T2, _T3]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1, volume=200_000)),  # decision bar: thin
            _batch(_T2, _bar("2330", _T2, volume=50_000_000)),  # fill bar: huge
            _batch(_T3, _bar("2330", _T3, volume=50_000_000)),
        ]
    )
    session = PaperSession(
        strategy=BuyAndHold(),
        tickers=["2330"],
        feed=feed,
        capital=Decimal("1500000"),  # 1,500,000 / 100 = 15,000 desired shares
        cost_model=TaiwanCostModel(),
        fill_model=VolumeParticipationFiller(participation_rate=0.05),
        settlement=NettingSettlement(),
        max_position_pct=1.0,
    )
    session.run()

    events = list(session.event_log)
    partial_fills = [e for e in events if e.kind == EventKind.PARTIAL_FILL]
    expires = [e for e in events if e.kind == EventKind.EXPIRE]

    assert len(partial_fills) == 1
    # 5% of T1's 200,000 (trailing) = 10,000 -- NOT 5% of T2's 50,000,000 (2,500,000)
    assert int(partial_fills[0].payload["shares"]) == 10_000
    assert len(expires) == 1


# ── non-round participation cap survives the full pipeline ──────────────────


def test_non_round_participation_cap_survives_the_full_pipeline(monkeypatch) -> None:
    """floor(participation_rate * trailing_adv) must land on the exact
    integer end-to-end -- through cost-budgeted order sizing, Order
    construction, and the event payload's Decimal/str round-trip -- not
    just in the isolated VolumeParticipationFiller.fillable() unit test.
    0.03 * 333,333 = 9,999.99, which must floor to 9,999, never round up
    to 10,000 nor drift from float error."""
    dates = [_T1, _T2, _T3]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    feed = FixtureFeed(batches=[_batch(d, _bar("2330", d, volume=333_333)) for d in dates])
    session = PaperSession(
        strategy=BuyAndHold(),
        tickers=["2330"],
        feed=feed,
        capital=Decimal("200000000"),  # desired shares far exceeds any cap here
        cost_model=TaiwanCostModel(),
        fill_model=VolumeParticipationFiller(participation_rate=0.03),
        settlement=NettingSettlement(),
        max_position_pct=1.0,
    )
    session.run()

    events = list(session.event_log)
    partial_fills = [e for e in events if e.kind == EventKind.PARTIAL_FILL]

    assert len(partial_fills) == 1
    assert int(partial_fills[0].payload["shares"]) == 9_999
