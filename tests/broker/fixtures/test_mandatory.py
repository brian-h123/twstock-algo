"""The four mandatory-for-exit adversarial fixtures (PHASE_5.md §6.4, step 12).

Each fixture drives a full PaperSession over a hand-built FixtureFeed,
confirming the ASSEMBLED pipeline (feed -> strategy -> allocate -> reconcile
-> submit -> drain -> fold -> mark) behaves correctly under an adversarial
bar sequence. The underlying mechanics each fixture touches already have
focused unit coverage against BrokerSimulator in isolation
(test_simulator.py); this file is about composition, not the mechanic
itself:

  * rotation cash-bug regression -- Phase 4's InsufficientCashError bug,
    replayed under NettingSettlement's same-bar sell-funds-buy path, driven
    by a real allocator + reconciler rotation instead of a hand-submitted
    order pair.
  * partial-then-expire -- a volume-capped partial fill immediately expires
    (IOC-only, Phase 5 has no resting orders), and a trigger-based strategy
    like BuyAndHold never revisits the unfilled remainder.
  * split-on-held-name -- a STOCK_SPLIT arriving on a held BuyAndHold
    position doubles shares, halves cost basis, and leaves account value
    continuous across the split bar.
  * missing-bar-isn't-suspension -- a feed gap on a confirmed XTAI session
    day rejects NO_MARKET_DATA, never a phantom SUSPENSION.
"""

from datetime import date
from decimal import Decimal
from types import MappingProxyType

import pandas as pd
import pytest

from twstock.backtest.allocator import EqualWeightTopN
from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.cross_sectional import CrossSectionalStrategy
from twstock.backtest.rebalance import Daily
from twstock.backtest.strategy import BuyAndHold
from twstock.broker.events import ActionType, Bar, BarBatch, CorporateAction, EventKind
from twstock.broker.feed import FixtureFeed
from twstock.broker.fill import FullFill, VolumeParticipationFiller
from twstock.broker.rejection import RejectionReason
from twstock.broker.session import PaperSession
from twstock.broker.settlement import NettingSettlement

# ── shared fixture-catalogue helpers ─────────────────────────────────────────
# Five confirmed-session XTAI weekdays, reused from test_simulator.py /
# test_oracles.py's already-verified range.

_T1 = date(2026, 6, 1)  # Monday
_T2 = date(2026, 6, 2)  # Tuesday
_T3 = date(2026, 6, 3)  # Wednesday
_T4 = date(2026, 6, 4)  # Thursday
_T5 = date(2026, 6, 5)  # Friday


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


def _empty_batch(d: date) -> BarBatch:
    """A confirmed session day with no bars at all -- simulates a full feed gap."""
    return BarBatch(date=d, bars=MappingProxyType({}))


def _fake_bars(dates: list[date]) -> pd.DataFrame:
    """Synthetic get_adjusted_history stand-in. BuyAndHold/_ScriptedScores
    only read the index, so OHLCV values themselves are placeholders."""
    idx = pd.DatetimeIndex(dates)
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000}, index=idx
    )


class _ScriptedScores(CrossSectionalStrategy):
    """Replays a pre-built scores DataFrame verbatim, ignoring the panel --
    lets a fixture dictate the exact rotation it wants to exercise without
    depending on any real indicator."""

    def __init__(self, scores: pd.DataFrame) -> None:
        self._scores = scores

    def generate_scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        return self._scores

    @property
    def name(self) -> str:
        return "ScriptedScores"


# ── ★ rotation cash-bug regression ───────────────────────────────────────────


def test_rotation_does_not_starve_the_buy_under_netting(monkeypatch) -> None:
    """Phase 4's InsufficientCashError bug: a same-bar rotation (sell 1101,
    buy 2330) must not starve the buy for cash. Under NettingSettlement the
    sell's proceeds must fund the buy within the SAME process_bar call --
    the same mechanic test_simulator.py's
    test_same_bar_sell_funds_same_bar_buy_under_netting checks in
    isolation, replayed here through the real allocator + reconciler +
    session pipeline instead of a hand-submitted order pair."""
    dates = [_T1, _T2, _T3, _T4]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )

    # T1: only 1101 eligible (buy). T2: only 2330 eligible -> full rotation.
    scores = pd.DataFrame(
        {"1101": [1.0, float("nan")], "2330": [float("nan"), 1.0]},
        index=pd.DatetimeIndex([_T1, _T2]),
    )
    feed = FixtureFeed(batches=[_batch(d, _bar("1101", d), _bar("2330", d)) for d in dates])

    session = PaperSession(
        strategy=_ScriptedScores(scores),
        tickers=["1101", "2330"],
        feed=feed,
        capital=Decimal("1000000"),
        cost_model=TaiwanCostModel(),
        fill_model=FullFill(),
        settlement=NettingSettlement(),
        allocator=EqualWeightTopN(n=1),
        schedule=Daily(),
        max_position_pct=1.0,
    )
    session.run()

    events = list(session.event_log)
    reject_reasons = [e.payload["reason"] for e in events if e.kind == EventKind.REJECT]
    assert RejectionReason.INSUFFICIENT_BUYING_POWER.value not in reject_reasons

    buy_2330 = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)
        and e.payload.get("ticker") == "2330"
        and e.payload.get("side") == "buy"
    ]
    sell_1101_same_bar = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)
        and e.payload.get("ticker") == "1101"
        and e.payload.get("side") == "sell"
        and e.ts == _T3
    ]

    assert len(buy_2330) == 1
    assert buy_2330[0].ts == _T3  # decided at T2, resolved at T3
    assert int(buy_2330[0].payload["shares"]) > 0
    assert len(sell_1101_same_bar) == 1


# ── ★ partial-then-expire ────────────────────────────────────────────────────


def test_partial_fill_is_never_silently_retried(monkeypatch) -> None:
    """A volume-capped partial fill immediately expires (IOC, Phase 5 has
    no resting orders) -- and BuyAndHold never proposes the trade a second
    time, since its signal is a one-shot trigger at bar 0. If the
    single-asset decide loop ever grew a retry path, this is the
    regression that would catch a position quietly growing bar after bar
    instead of settling at one partial tranche."""
    dates = [_T1, _T2, _T3, _T4, _T5]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    feed = FixtureFeed(batches=[_batch(d, _bar("2330", d, volume=1_000_000)) for d in dates])

    session = PaperSession(
        strategy=BuyAndHold(),
        tickers=["2330"],
        feed=feed,
        capital=Decimal("200000000"),  # far more than 5% of daily volume can absorb
        cost_model=TaiwanCostModel(),
        fill_model=VolumeParticipationFiller(participation_rate=0.05),
        settlement=NettingSettlement(),
        max_position_pct=1.0,
        liquidate_at_end=True,
    )
    session.run()

    events = list(session.event_log)
    partial_fills = [e for e in events if e.kind == EventKind.PARTIAL_FILL]
    expires = [e for e in events if e.kind == EventKind.EXPIRE]
    all_buy_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "buy"
    ]
    sell_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "sell"
    ]

    assert len(partial_fills) == 1
    assert int(partial_fills[0].payload["shares"]) == 50_000  # 5% of 1,000,000
    assert len(expires) == 1
    assert expires[0].order_id == partial_fills[0].order_id
    assert len(all_buy_fills) == 1  # never retried on a later bar

    # the forced-liquidation sell at the end proves the position never grew
    # past the one partial tranche
    assert len(sell_fills) == 1
    assert int(sell_fills[0].payload["shares"]) == 50_000


# ── ★ split-on-held-name ─────────────────────────────────────────────────────


def test_split_on_held_name_doubles_shares_and_preserves_value(monkeypatch) -> None:
    """A STOCK_SPLIT arriving on an already-open BuyAndHold position must
    double shares and halve cost basis (LotLedger.on_split), and -- since
    the post-split bar's price is set to half the pre-split price, mirroring
    a real split -- total account value must stay continuous across the
    split bar rather than jumping."""
    dates = [_T1, _T2, _T3, _T4, _T5]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    split_action = CorporateAction(
        action_type=ActionType.STOCK_SPLIT,
        ex_date=_T4,
        cash_amount=None,
        ratio=0.5,  # 1-for-2 split: share_multiplier = 1/ratio = 2.0
        pay_date=None,
        subscription_price=None,
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1, price="100")),
            _batch(_T2, _bar("2330", _T2, price="100")),
            _batch(_T3, _bar("2330", _T3, price="100")),
            _batch(_T4, _bar("2330", _T4, price="50", actions=(split_action,))),
            _batch(_T5, _bar("2330", _T5, price="50")),
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
    assert len(split_events) == 1
    assert split_events[0].ts == _T4
    assert float(split_events[0].payload["ratio"]) == pytest.approx(2.0)

    equity_T3 = float(result.equity_curve[_T3])
    equity_T4 = float(result.equity_curve[_T4])
    # value continuous across the split bar (well within ordinary cost/
    # slippage noise) -- a value halving or doubling here would mean the
    # split was double-applied or not applied to the mark.
    assert equity_T4 == pytest.approx(equity_T3, rel=0.01)


# ── ★ missing-bar-isn't-suspension ───────────────────────────────────────────


def test_missing_bar_on_session_day_rejects_no_market_data_not_suspension(monkeypatch) -> None:
    """A feed gap on a confirmed XTAI session day must reject the working
    order NO_MARKET_DATA, never silently upgrade to SUSPENSION -- the
    default SuspensionCalendar (graceful degradation, D12) always reports
    not-halted, so nothing here can manufacture a phantom halt. Exercised
    end-to-end: the order is submitted by BuyAndHold's decide loop and
    rejected inside the full session run, not hand-submitted to the
    simulator directly."""
    dates = [_T1, _T2, _T3, _T4, _T5]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1)),
            _empty_batch(_T2),  # feed gap: no bar at all for 2330 on a session day
            _batch(_T3, _bar("2330", _T3)),
            _batch(_T4, _bar("2330", _T4)),
            _batch(_T5, _bar("2330", _T5)),
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

    assert len(rejects) == 1
    assert rejects[0].ts == _T2
    assert rejects[0].payload["reason"] == RejectionReason.NO_MARKET_DATA.value
    assert rejects[0].payload["reason"] != RejectionReason.SUSPENSION.value

    # the buy never filled -- position stays flat for the entire run
    fills = [e for e in events if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)]
    assert fills == []
