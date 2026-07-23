"""Unit + integration tests for twstock.broker.session.PaperSession.

Unit tests use BuyAndHold + a hand-built FixtureFeed, with get_adjusted_history
monkeypatched to a synthetic panel — no local Parquet store or network needed.

Coverage:
  - CrossSectionalStrategy without allocator/schedule raises; single-asset
    Strategy given either warns (constructor validation)
  - with_settlement() returns an independent sibling: different settlement,
    same everything else, distinct broker/account/session_id
  - run() raises on a second call
  - The warm-up call primes trailing volume from bar 0 before the first real
    fill is resolved (VolumeParticipationFiller caps the very first trade)
  - Forced liquidation closes every open position by the final bar
  - Integration: single-asset SMA replay over real 2330 data end-to-end,
    plus assert_no_lookahead re-run on the same strategy
"""

from datetime import date
from decimal import Decimal
from types import MappingProxyType
from unittest.mock import MagicMock

import pandas as pd
import pytest

from twstock.backtest.allocator import Allocator
from twstock.backtest.audit import assert_no_lookahead
from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.cross_sectional import CrossSectionalStrategy
from twstock.backtest.rebalance import RebalanceSchedule
from twstock.backtest.strategy import BuyAndHold
from twstock.broker.events import Bar, BarBatch, EventKind
from twstock.broker.feed import FixtureFeed, ReplayFeed
from twstock.broker.fill import FullFill, VolumeParticipationFiller
from twstock.broker.session import PaperSession
from twstock.broker.settlement import InstantSettlement, NettingSettlement
from twstock.data.api import get_adjusted_history
from twstock.strategies.sma_crossover import SmaCrossover

# ── helpers ──────────────────────────────────────────────────────────────────

_TICKER = "2330"
_DATES = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5)]


def _bar(d: date, volume: int = 1_000_000) -> Bar:
    p = Decimal("100")
    return Bar(
        ticker=_TICKER, date=d, open=p, high=p, low=p, close=p, volume=volume, corporate_actions=()
    )


def _batch(d: date, volume: int = 1_000_000) -> BarBatch:
    return BarBatch(date=d, bars=MappingProxyType({_TICKER: _bar(d, volume)}))


def _fake_bars() -> pd.DataFrame:
    """Synthetic adjusted-history stand-in — BuyAndHold only reads the index."""
    idx = pd.DatetimeIndex(_DATES)
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1_000_000}, index=idx
    )


def _default_kwargs(feed) -> dict:
    return dict(
        strategy=BuyAndHold(),
        tickers=[_TICKER],
        feed=feed,
        capital=Decimal("200000000"),
        cost_model=TaiwanCostModel(),
        fill_model=VolumeParticipationFiller(participation_rate=0.05),
        settlement=InstantSettlement(),
        max_position_pct=1.0,
    )


# ─── Construction validation ──────────────────────────────────────────────────


def test_cross_sectional_requires_allocator_and_schedule() -> None:
    kwargs = _default_kwargs(FixtureFeed(batches=[]))
    kwargs["strategy"] = MagicMock(spec=CrossSectionalStrategy)

    with pytest.raises(ValueError, match="requires both allocator and schedule"):
        PaperSession(**kwargs)


def test_single_asset_warns_if_allocator_or_schedule_given() -> None:
    kwargs = _default_kwargs(FixtureFeed(batches=[]))
    kwargs["allocator"] = MagicMock(spec=Allocator)
    kwargs["schedule"] = MagicMock(spec=RebalanceSchedule)

    with pytest.warns(UserWarning, match="ignored for single-asset"):
        PaperSession(**kwargs)


# ─── with_settlement() ─────────────────────────────────────────────────────────


def test_with_settlement_returns_independent_sibling() -> None:
    original = PaperSession(**_default_kwargs(FixtureFeed(batches=[])))
    sibling = original.with_settlement(NettingSettlement())

    assert sibling is not original
    assert sibling.broker is not original.broker
    assert sibling.account is not original.account
    assert isinstance(sibling.settlement, NettingSettlement)
    assert isinstance(original.settlement, InstantSettlement)  # original untouched
    assert sibling.strategy is original.strategy
    assert sibling.tickers == original.tickers
    assert sibling.capital == original.capital
    assert sibling.session_id != original.session_id


# ─── run() lifecycle ────────────────────────────────────────────────────────────


def test_run_raises_on_second_call(monkeypatch) -> None:
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history", lambda ticker, start, end: _fake_bars()
    )
    feed = FixtureFeed(batches=[_batch(d) for d in _DATES])
    session = PaperSession(**_default_kwargs(feed))

    session.run()

    with pytest.raises(RuntimeError, match="only be called once"):
        session.run()


# ─── Warm-up primes trailing volume before the first real fill ────────────────


def test_warm_up_caps_first_fill_by_prior_bar_volume(monkeypatch) -> None:
    """BuyAndHold wants far more shares than 5% of bar 0's volume allows.
    If the warm-up call didn't prime _prior_volume from bar 0, the first
    real fill (decided at bar 0, resolved at bar 1) would execute uncapped."""
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history", lambda ticker, start, end: _fake_bars()
    )
    feed = FixtureFeed(batches=[_batch(d, volume=1_000_000) for d in _DATES])
    session = PaperSession(**_default_kwargs(feed))

    session.run()

    events = list(session.event_log)
    buy_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "buy"
    ]
    assert len(buy_fills) == 1
    # 5% of 1,000,000 = 50,000 — far below the ~2,000,000 shares capital/price would allow
    assert int(buy_fills[0].payload["shares"]) == 50_000


# ─── Forced liquidation ─────────────────────────────────────────────────────────


def test_forced_liquidation_closes_position_by_final_bar(monkeypatch) -> None:
    """BuyAndHold never emits its own exit signal — the position must still
    be fully closed by the last bar via the forced-liquidation step."""
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history", lambda ticker, start, end: _fake_bars()
    )
    feed = FixtureFeed(batches=[_batch(d) for d in _DATES])
    session = PaperSession(**_default_kwargs(feed))

    session.run()

    assert session.account.positions[_TICKER].open_shares == 0

    events = list(session.event_log)
    sell_fills = [
        e
        for e in events
        if e.kind in (EventKind.FILL, EventKind.PARTIAL_FILL) and e.payload.get("side") == "sell"
    ]
    assert len(sell_fills) == 1


# ─── NettingSettlement close() at the final bar ─────────────────────────────


def test_netting_settlement_final_equity_reflects_the_closing_trade(monkeypatch) -> None:
    """Regression: the forced-liquidation sell is the LAST trade of the run,
    so no later bar's on_bar_start can ever flush its net -- close() must
    fire before the final equity_curve point is read. Before the fix, this
    trade's cash simply never reached the ledger and final_equity silently
    stayed at initial capital (a phantom ~equal-to-notional overstatement)."""
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history", lambda ticker, start, end: _fake_bars()
    )
    feed = FixtureFeed(batches=[_batch(d) for d in _DATES])
    kwargs = _default_kwargs(feed)
    kwargs["settlement"] = NettingSettlement()
    session = PaperSession(**kwargs)

    result = session.run()

    # A full buy-then-sell round trip on ~all capital costs a few tenths of a
    # percent (commission + tax + slippage) -- final equity must be close to,
    # but strictly less than, initial capital. Before the fix it stayed
    # exactly at initial capital (the closing sell's cash was never folded).
    assert result.final_equity < result.initial_capital
    assert result.final_equity > result.initial_capital * Decimal("0.99")
    assert session.account._ledger.bar_net == Decimal(0)
    assert session.account._ledger.bar_date is None


# ─── SimResult.trades and cost totals ──────────────────────────────────────


def test_trades_has_one_row_per_fill_with_expected_schema(monkeypatch) -> None:
    """Default fixture config produces exactly one volume-capped buy
    (test_warm_up_caps_first_fill_by_prior_bar_volume) and one closing sell
    (test_forced_liquidation_closes_position_by_final_bar) -- trades must
    carry one row per fill, in the BacktestResult trade-schema column order."""
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history", lambda ticker, start, end: _fake_bars()
    )
    feed = FixtureFeed(batches=[_batch(d) for d in _DATES])
    session = PaperSession(**_default_kwargs(feed))

    result = session.run()

    assert list(result.trades.columns) == [
        "timestamp",
        "ticker",
        "side",
        "shares",
        "fill_price",
        "commission",
        "tax",
        "notional",
        "total_cost",
    ]
    assert len(result.trades) == 2
    assert set(result.trades["side"]) == {"buy", "sell"}


def test_total_commission_and_tax_match_independent_recompute(monkeypatch) -> None:
    """Recomputes commission/tax from a fresh TaiwanCostModel call at the
    known fixture reference price (every bar's open == 100), independent of
    reading SimResult.trades' own commission/tax columns back -- catches a
    wrong reference price (e.g. an adjusted or stale close) feeding _trade_row,
    not just an arithmetic slip in the summation."""
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history", lambda ticker, start, end: _fake_bars()
    )
    feed = FixtureFeed(batches=[_batch(d) for d in _DATES])
    session = PaperSession(**_default_kwargs(feed))

    result = session.run()

    cost_model = TaiwanCostModel()
    buy_shares = int(result.trades.loc[result.trades["side"] == "buy", "shares"].iloc[0])
    sell_shares = int(result.trades.loc[result.trades["side"] == "sell", "shares"].iloc[0])
    _, exp_buy_commission = cost_model.buy_costs(_TICKER, buy_shares, 100.0)
    _, exp_sell_commission, exp_sell_tax = cost_model.sell_costs(_TICKER, sell_shares, 100.0)

    assert result.total_commission == Decimal(str(exp_buy_commission + exp_sell_commission))
    assert result.total_tax == Decimal(str(exp_sell_tax))


def test_total_slippage_cost_uses_backtester_formula(monkeypatch) -> None:
    """total_slippage_cost must equal notional x slippage_pct -- the same
    formula backtest/engine.py uses for BacktestResult -- so a future
    reconciliation report's slippage tripwire compares like with like instead
    of two differently-defined "slippage cost" numbers."""
    monkeypatch.setattr(
        "twstock.broker.session.get_adjusted_history", lambda ticker, start, end: _fake_bars()
    )
    feed = FixtureFeed(batches=[_batch(d) for d in _DATES])
    session = PaperSession(**_default_kwargs(feed))

    result = session.run()

    slippage_pct = Decimal(str(TaiwanCostModel().slippage_bps_per_side / 10_000))
    expected = sum(
        (
            Decimal(str(row.fill_price)) * row.shares * slippage_pct
            for row in result.trades.itertuples()
        ),
        Decimal(0),
    )
    assert result.total_slippage_cost == expected


def test_trades_empty_for_a_run_with_no_decision_bars() -> None:
    """A single-bar feed never reaches a decide() call -- no bar pair exists
    to decide on and later resolve -- so zero orders are submitted. trades
    must stay an empty frame with the full column set (not just missing
    columns), and every cost total must be exactly zero, not NaN."""
    feed = FixtureFeed(batches=[_batch(_DATES[0])])
    session = PaperSession(**_default_kwargs(feed))

    result = session.run()

    assert result.trades.empty
    assert list(result.trades.columns) == [
        "timestamp",
        "ticker",
        "side",
        "shares",
        "fill_price",
        "commission",
        "tax",
        "notional",
        "total_cost",
    ]
    assert result.total_commission == Decimal(0)
    assert result.total_tax == Decimal(0)
    assert result.total_slippage_cost == Decimal(0)


# ─── Integration: single-asset SMA replay, real data ───────────────────────────


@pytest.mark.integration
def test_sma_replay_end_to_end(monkeypatch) -> None:
    monkeypatch.setattr("twstock.broker.feed.get_top50_universe", lambda: [_TICKER])
    session = PaperSession(
        strategy=SmaCrossover(fast=20, slow=60),
        tickers=[_TICKER],
        feed=ReplayFeed(start=date(2024, 1, 1), end=date(2024, 12, 31)),
        capital=Decimal("1000000"),
        cost_model=TaiwanCostModel(),
        fill_model=FullFill(),
        settlement=NettingSettlement(),
        max_position_pct=0.10,
    )

    result = session.run()

    assert not result.equity_curve.empty
    assert not result.positions_daily.empty
    assert list(result.positions_daily.index.names) == ["date", "ticker"]


@pytest.mark.integration
def test_no_lookahead_sma() -> None:
    bars = get_adjusted_history(_TICKER, start=date(2020, 1, 1), end=date(2025, 12, 31))
    sample = bars.iloc[-252:].copy()
    assert_no_lookahead(SmaCrossover(fast=20, slow=60), sample)
