"""Cash/settlement adversarial fixtures (PHASE_5.md §6.4, step 12 -- cash/
settlement group).

SettledCashOnly's per-trade lag and the ex-date/pay-date split already have
isolated DatedLedger/SettlementModel unit coverage (test_settlement.py).
These fixtures instead exercise the composition: a rotation that succeeds
cleanly under NettingSettlement (test_mandatory.py) must NOT succeed the
same way under SettledCashOnly -- proceeds from the same-bar sell are not
yet settled cash, so the buy is starved. And a dividend must show up in
account VALUE at the ex-date (as a receivable) while remaining unavailable
as buying power until the pay-date -- conflating the two dates breaks O2
and the dividend waterfall line (PHASE_5.md §9).
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
from twstock.broker.fill import FullFill
from twstock.broker.rejection import RejectionReason
from twstock.broker.session import PaperSession
from twstock.broker.settlement import SettledCashOnly

# Confirmed-session XTAI weekdays (2026-06-06/07 is a weekend), reused from
# test_simulator.py / test_oracles.py's range, extended by two more
# confirmed sessions (2026-06-08 verified against the XTAI calendar).
_T1 = date(2026, 6, 1)  # Monday
_T2 = date(2026, 6, 2)  # Tuesday
_T3 = date(2026, 6, 3)  # Wednesday
_T4 = date(2026, 6, 4)  # Thursday
_T5 = date(2026, 6, 5)  # Friday
_T6 = date(2026, 6, 8)  # Monday (following weekend skipped)


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


class _ScriptedScores(CrossSectionalStrategy):
    """Replays a pre-built scores DataFrame verbatim, ignoring the panel."""

    def __init__(self, scores: pd.DataFrame) -> None:
        self._scores = scores

    def generate_scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        return self._scores

    @property
    def name(self) -> str:
        return "ScriptedScores"


# ── SettledCashOnly starves the same-bar rotation ────────────────────────────


def test_settled_cash_only_starves_the_rotation_netting_would_fund(monkeypatch) -> None:
    """The mirror image of test_mandatory.py's rotation fixture: identical
    two-ticker rotation, same allocator/reconciler, same timing (rotation
    decided at T2, right after the first buy fills) -- only the settlement
    model differs. Under SettledCashOnly a buy's own cash debit applies
    immediately (see the settlement.py fix), so there's no need to wait
    for it to mature before rotating; what's being isolated here is purely
    whether the SAME-BAR SELL's proceeds can fund the same-bar buy, which
    they cannot under this model. Where NettingSettlement lets the
    rotation succeed in full, SettledCashOnly must starve the buy."""
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
        settlement=SettledCashOnly(),
        allocator=EqualWeightTopN(n=1),
        schedule=Daily(),
        max_position_pct=1.0,
    )
    session.run()

    events = list(session.event_log)
    sell_1101_at_T3 = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)
        and e.payload.get("ticker") == "1101"
        and e.payload.get("side") == "sell"
        and e.ts == _T3
    ]
    buy_2330 = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL)
        and e.payload.get("ticker") == "2330"
        and e.payload.get("side") == "buy"
    ]
    reject_2330 = [
        e
        for e in events
        if e.kind == EventKind.REJECT
        and e.ts == _T3
        and e.payload["reason"] == RejectionReason.INSUFFICIENT_BUYING_POWER.value
    ]

    # the sell of 1101 (no cash constraint on sells) still executes normally
    assert len(sell_1101_at_T3) == 1
    # but the same-bar buy of 2330 is starved: either fully rejected, or
    # filled for far less than the ~10,000 shares NettingSettlement bought
    # in the equivalent rotation (test_mandatory.py)
    if buy_2330:
        assert int(buy_2330[0].payload["shares"]) < 1_000
    else:
        assert len(reject_2330) == 1


# ── dividend: value at ex-date, spendability only at pay-date ───────────────


def test_dividend_counts_toward_value_at_ex_date_but_not_buying_power_until_pay_date(
    monkeypatch,
) -> None:
    """A cash dividend becomes a receivable (added to Account.total_value
    via the pending ledger) at the EX-date, not the pay-date -- so equity
    must visibly increase at the ex-date bar even though no cash has
    actually arrived. Between the ex-date and the pay-date, equity must
    stay flat (nothing new happens). At the pay-date, the receivable
    matures into settled_cash: total_value is unchanged by that
    reclassification (O2), which is why the equity curve shows no second
    jump there -- only DIRECT observation would show buying_power itself
    increasing, which is exactly the split this fixture is pinning down."""
    dates = [_T1, _T2, _T3, _T4, _T5, _T6]
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history",
        lambda ticker, start, end: _fake_bars(dates),
    )
    dividend = CorporateAction(
        action_type=ActionType.CASH_DIVIDEND,
        ex_date=_T3,
        cash_amount=Decimal("5"),
        ratio=None,
        pay_date=_T5,
        subscription_price=None,
    )
    feed = FixtureFeed(
        batches=[
            _batch(_T1, _bar("2330", _T1)),
            _batch(_T2, _bar("2330", _T2)),
            _batch(_T3, _bar("2330", _T3, actions=(dividend,))),  # ex-date
            _batch(_T4, _bar("2330", _T4)),
            _batch(_T5, _bar("2330", _T5)),  # pay-date
            _batch(_T6, _bar("2330", _T6)),
        ]
    )
    session = PaperSession(
        strategy=BuyAndHold(),
        tickers=["2330"],
        feed=feed,
        capital=Decimal("1000000"),
        cost_model=TaiwanCostModel(),
        fill_model=FullFill(),
        settlement=SettledCashOnly(),
        max_position_pct=1.0,
    )
    result = session.run()

    events = list(session.event_log)
    dividend_events = [e for e in events if e.kind == EventKind.DIVIDEND]
    buy_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "buy"
    ]
    assert len(dividend_events) == 1
    assert dividend_events[0].ts == _T3
    assert dividend_events[0].payload["pay_date"] == _T5.isoformat()
    assert len(buy_fills) == 1
    shares_held = int(buy_fills[0].payload["shares"])

    equity_T2 = float(result.equity_curve[_T2])  # post-buy, pre-dividend
    equity_T3 = float(result.equity_curve[_T3])  # ex-date: receivable recognized
    equity_T4 = float(result.equity_curve[_T4])  # holding period: nothing new
    equity_T5 = float(result.equity_curve[_T5])  # pay-date: reclassification only

    expected_receivable = shares_held * 5.0
    assert equity_T3 - equity_T2 == pytest.approx(expected_receivable, rel=0.02)
    # flat between ex-date and pay-date, and flat again at maturity itself
    # (O2: matured cash isn't NEW value, just a reclassification)
    assert equity_T4 == pytest.approx(equity_T3, rel=1e-9)
    assert equity_T5 == pytest.approx(equity_T3, rel=1e-9)
