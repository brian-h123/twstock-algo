"""Behavioral tests for twstock.backtest.engine.Engine.

These tests patch get_adjusted_history so we control bar data precisely,
then verify the engine's execution logic rather than its data plumbing.
All real Engine, Portfolio, TaiwanCostModel, and Strategy code runs unchanged.

Coverage:
  - T+1 execution timing (the fundamental invariant)
  - Signal state-transition semantics (NaN / 0 / 1)
  - Buy sizing via equity × max_position_pct
  - Force-close at last-bar open
  - Pending order dropped on final day
  - Cost model always applied (commission, tax, slippage)
  - ETF sell-tax rate selection
  - Short-history ticker skipped with warning
  - BacktestResult column contracts and MultiIndex shape
  - Multi-ticker independent positions
"""

import math
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from tests.backtest.conftest import make_synthetic_bars
from twstock.backtest.audit import assert_no_lookahead
from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.engine import Engine
from twstock.backtest.strategy import BuyAndHold, Strategy
from twstock.strategies.sma_crossover import SmaCrossover

# Patch target: engine imports get_adjusted_history directly into its namespace.
_PATCH = "twstock.backtest.engine.get_adjusted_history"


# ─── helpers ──────────────────────────────────────────────────────────────────


def _flat_bars(n: int, price: float = 100.0) -> pd.DataFrame:
    """OHLCV bars where open == high == low == close == price for all rows.

    Flat prices let us isolate execution logic without price-movement noise.
    """
    idx = pd.date_range("2020-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 10_000,
        },
        index=idx,
    )


class _BuyBar0SellBar3(Strategy):
    """Deterministic: 1 on bar 0, 0 on bar 3, NaN everywhere else.

    Requires at least 6 bars to produce a sell *before* the last day
    (bar-3 signal → sell executes at bar 4's open; bar 5 is the last day).
    """

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        sig = pd.Series(np.nan, index=bars.index, dtype=float)
        sig.iloc[0] = 1
        sig.iloc[3] = 0
        return sig


# ─── T+1 execution timing ─────────────────────────────────────────────────────


def test_buy_fills_at_t_plus_1_open(engine: Engine) -> None:
    """Signal=1 on bar 0 must produce a trade timestamped at bar 1.

    Bar 1's open is set to 150 (unique) so the fill price is unambiguous:
    if the engine bought at bar 0's open it would show 100, not 150.
    """
    bars = _flat_bars(5, price=100.0)
    bars.loc[bars.index[1], "open"] = 150.0

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    buys = result.trades[result.trades["side"] == "buy"]
    assert len(buys) == 1

    buy = buys.iloc[0]
    assert buy["timestamp"] == bars.index[1], "buy must fill at bar 1, not bar 0"

    # fill_price = open + slippage (buy side is worse = higher)
    slippage_pct = TaiwanCostModel().slippage_bps_per_side / 10_000
    expected_fill = 150.0 * (1 + slippage_pct)
    assert abs(buy["fill_price"] - expected_fill) < 1e-9


def test_sell_fills_at_t_plus_1_open(engine: Engine) -> None:
    """Signal=0 on bar 3 must produce a sell timestamped at bar 4.

    Bar 4's open is set to 200 (unique) to make the fill unambiguous.
    """
    bars = _flat_bars(6, price=100.0)
    bars.loc[bars.index[4], "open"] = 200.0

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=_BuyBar0SellBar3(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    sells = result.trades[result.trades["side"] == "sell"]
    assert len(sells) == 1

    sell = sells.iloc[0]
    assert sell["timestamp"] == bars.index[4], "sell must fill at bar 4, not bar 3"

    slippage_pct = TaiwanCostModel().slippage_bps_per_side / 10_000
    expected_fill = 200.0 * (1 - slippage_pct)  # sell side is worse = lower
    assert abs(sell["fill_price"] - expected_fill) < 1e-9


# ─── Signal state-transition semantics ────────────────────────────────────────


def test_nan_signal_holds_position_open(engine: Engine) -> None:
    """NaN signals after entry must keep the position open.

    BuyAndHold emits [1, NaN, NaN, NaN, NaN]. If NaN were treated as a
    close signal the engine would sell at bar 2 open; instead it should
    hold until the force-close on the last bar.
    """
    bars = _flat_bars(5)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    # Exactly one buy (bar 1) and one sell (force-close at bar 4).
    assert len(result.trades) == 2
    assert result.trades.iloc[0]["side"] == "buy"
    assert result.trades.iloc[1]["side"] == "sell"


def test_signal_zero_closes_position_at_next_bar(engine: Engine) -> None:
    """Signal=0 schedules a sell that fills at the *next* bar's open.

    _BuyBar0SellBar3: signal[0]=1 → buy at bar 1; signal[3]=0 → sell at bar 4.
    The sell must not appear at bar 3's open.
    """
    bars = _flat_bars(6)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=_BuyBar0SellBar3(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert len(result.trades) == 2
    assert result.trades.iloc[0]["timestamp"] == bars.index[1]  # buy at bar 1
    assert result.trades.iloc[1]["timestamp"] == bars.index[4]  # sell at bar 4


def test_repeated_entry_signal_produces_only_one_buy(engine: Engine) -> None:
    """Consecutive 1 signals (current == prev) must not re-enter.

    The engine diffs successive signals; once long, a second 1 is a no-op.
    """
    bars = _flat_bars(5)

    class AlwaysBuy(Strategy):
        def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
            return pd.Series(1.0, index=bars.index)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=AlwaysBuy(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    buys = result.trades[result.trades["side"] == "buy"]
    assert len(buys) == 1, "bars 1..3 repeat signal=1; only bar-1 should buy"


def test_signal_zero_without_prior_long_produces_no_order(engine: Engine) -> None:
    """Signal=0 when prev=None (never entered) must not generate a sell."""
    bars = _flat_bars(5)

    class FlatFromStart(Strategy):
        def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
            return pd.Series(0.0, index=bars.index)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=FlatFromStart(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert result.trades.empty
    assert result.positions_daily.empty
    assert result.final_equity == pytest.approx(1_000_000)


# ─── Buy sizing ────────────────────────────────────────────────────────────────


def test_buy_sizing_floor_of_equity_times_max_pct(engine: Engine) -> None:
    """Shares bought = floor(equity × max_pct / open_price).

    The engine sizes by the reference (open) price; slippage is applied
    post-sizing inside portfolio.buy() and does not influence share count.

    At execution (bar 1):
      equity     = 1,000,000  (MTM at bar 0 close; no positions held)
      target     = 1,000,000 × 0.10 = 100,000
      open_price = 100.0
      shares     = floor(100,000 / 100.0) = 1,000
    """
    open_price = 100.0
    bars = _flat_bars(5, price=open_price)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    buy = result.trades[result.trades["side"] == "buy"].iloc[0]

    expected_shares = math.floor(100_000 / open_price)
    assert buy["shares"] == expected_shares


# ─── Force-close on last day ───────────────────────────────────────────────────


def test_force_close_uses_last_bar_open_price(engine: Engine, cost_model: TaiwanCostModel) -> None:
    """Open positions must be force-closed at the final bar's open, not close."""
    bars = _flat_bars(5, price=100.0)
    bars.loc[bars.index[-1], "open"] = 300.0  # last day open is unmistakable

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    sell = result.trades[result.trades["side"] == "sell"].iloc[0]
    assert sell["timestamp"] == bars.index[-1]

    expected_fill = 300.0 * (1 - cost_model.slippage_bps_per_side / 10_000)
    assert abs(sell["fill_price"] - expected_fill) < 1e-9


def test_pending_buy_on_last_day_is_dropped(engine: Engine) -> None:
    """A buy scheduled for the last bar must be dropped, not executed.

    The engine calls pending_orders.clear() before processing the last bar,
    so a buy pending from T-1 never reaches portfolio.buy().  No buy should
    appear in the trade ledger and no position should be force-closed.
    """
    bars = _flat_bars(4)

    class EnterPenultimate(Strategy):
        """Signal=1 on the second-to-last bar — buy would fill at the last bar."""

        def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
            sig = pd.Series(np.nan, index=bars.index, dtype=float)
            sig.iloc[-2] = 1
            return sig

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=EnterPenultimate(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert result.trades.empty, "pending buy on last day must be dropped, not executed"


# ─── Cost model always applied ────────────────────────────────────────────────


def test_flat_price_round_trip_erodes_capital(engine: Engine) -> None:
    """Flat prices guarantee zero gain; any friction means final equity < start."""
    bars = _flat_bars(5)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert result.total_commission > 0
    assert result.total_tax > 0
    assert result.final_equity < 1_000_000


def test_etf_ticker_uses_0_1_pct_sell_tax(cost_model: TaiwanCostModel) -> None:
    """0050 (ETF) sells must apply the 0.1% ETF rate, not the 0.3% stock rate."""
    engine = Engine(capital=1_000_000, cost_model=cost_model, max_position_pct=0.10)
    bars = _flat_bars(5, price=100.0)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["0050"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    sell = result.trades[result.trades["side"] == "sell"].iloc[0]
    expected_tax = sell["notional"] * cost_model.etf_sell_tax
    assert abs(sell["tax"] - expected_tax) < 0.01


def test_stock_ticker_uses_0_3_pct_sell_tax(cost_model: TaiwanCostModel) -> None:
    """Non-ETF sells must apply the statutory 0.3% securities transaction tax."""
    engine = Engine(capital=1_000_000, cost_model=cost_model, max_position_pct=0.10)
    bars = _flat_bars(5, price=100.0)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],  # stock, not in etf_tickers
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    sell = result.trades[result.trades["side"] == "sell"].iloc[0]
    expected_tax = sell["notional"] * cost_model.stock_sell_tax
    assert abs(sell["tax"] - expected_tax) < 0.01


# ─── Short-history ticker handling ────────────────────────────────────────────


def test_short_history_ticker_warns_and_is_excluded(engine: Engine) -> None:
    """A ticker with <2 bars emits a UserWarning and is skipped without a crash."""
    bars = _flat_bars(1)  # 1 row — below the 2-row minimum

    with patch(_PATCH, return_value=bars):
        with pytest.warns(UserWarning):
            result = engine.run(
                strategy=BuyAndHold(),
                tickers=["2330"],
                start=date(2020, 1, 1),
                end=date(2020, 12, 31),
            )

    assert result.tickers == ()  # skipped ticker is absent
    assert result.trades.empty
    assert result.final_equity == pytest.approx(1_000_000)


# ─── BacktestResult structure ─────────────────────────────────────────────────


def test_trades_dataframe_column_contract(engine: Engine) -> None:
    """trades must carry exactly the documented column set (Phase 3 D10 contract)."""
    bars = _flat_bars(5)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    expected = {
        "timestamp",
        "ticker",
        "side",
        "shares",
        "fill_price",
        "commission",
        "tax",
        "notional",
        "total_cost",
    }
    assert set(result.trades.columns) == expected


def test_empty_trades_still_has_documented_columns(engine: Engine) -> None:
    """A no-trade run must still return a trades DataFrame with the right columns.

    Downstream reporters should not need to guard against missing columns.
    """
    bars = _flat_bars(5)

    class NeverSignals(Strategy):
        def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
            return pd.Series(np.nan, index=bars.index, dtype=float)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=NeverSignals(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert result.trades.empty
    expected = {
        "timestamp",
        "ticker",
        "side",
        "shares",
        "fill_price",
        "commission",
        "tax",
        "notional",
        "total_cost",
    }
    assert set(result.trades.columns) == expected


def test_positions_daily_multiindex_and_columns(engine: Engine) -> None:
    """positions_daily must have a (date, ticker) MultiIndex with documented columns."""
    bars = _flat_bars(5)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert result.positions_daily.index.names == ["date", "ticker"]
    assert set(result.positions_daily.columns) == {"shares", "avg_cost", "market_value"}


def test_positions_daily_rows_span_days_position_is_held(engine: Engine) -> None:
    """positions_daily has one row per day the position is open (not before buy or after sell).

    _BuyBar0SellBar3 on 6 bars:
      buy executes at bar 1 → position first appears in positions_daily at bar 1's close
      sell executes at bar 4 → position gone at bar 4's close
      → rows for bars 1, 2, 3  (3 rows)
    """
    bars = _flat_bars(6)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=_BuyBar0SellBar3(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    dates_held = result.positions_daily.index.get_level_values("date").unique()
    assert len(dates_held) == 3  # bar 1, bar 2, bar 3


def test_equity_curve_one_entry_per_trading_bar(engine: Engine) -> None:
    """equity_curve must be indexed by the same dates as the bars, one per bar."""
    n = 20
    bars = _flat_bars(n)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert len(result.equity_curve) == n
    assert result.equity_curve.index.equals(bars.index)


def test_result_metadata_matches_run_arguments(engine: Engine) -> None:
    """strategy_name, tickers, start, end, and initial_capital must reflect run() args."""
    bars = _flat_bars(5)
    start_d = date(2020, 1, 1)
    end_d = date(2020, 12, 31)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["2330"],
            start=start_d,
            end=end_d,
        )

    assert result.strategy_name == "BuyAndHold"
    assert result.tickers == ("2330",)
    assert result.start == start_d
    assert result.end == end_d
    assert result.initial_capital == 1_000_000


# ─── Multi-ticker ─────────────────────────────────────────────────────────────


def test_two_ticker_run_produces_independent_round_trips(
    cost_model: TaiwanCostModel,
) -> None:
    """Each ticker independently produces a buy and a force-close sell.

    The two tickers have different prices (100 vs 200) so sizing differs;
    the important check is that both appear in the trade ledger.
    """
    engine = Engine(capital=1_000_000, cost_model=cost_model, max_position_pct=0.10)
    bars_a = _flat_bars(5, price=100.0)
    bars_b = _flat_bars(5, price=200.0)

    def _mock(ticker: str, start, end) -> pd.DataFrame:
        return bars_a if ticker == "AAAA" else bars_b

    with patch(_PATCH, side_effect=_mock):
        result = engine.run(
            strategy=BuyAndHold(),
            tickers=["AAAA", "BBBB"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert result.tickers == ("AAAA", "BBBB")
    assert len(result.trades) == 4  # buy + sell per ticker

    buy_tickers = set(result.trades[result.trades["side"] == "buy"]["ticker"])
    sell_tickers = set(result.trades[result.trades["side"] == "sell"]["ticker"])
    assert buy_tickers == {"AAAA", "BBBB"}
    assert sell_tickers == {"AAAA", "BBBB"}


# ─── SmaCrossover integration ─────────────────────────────────────────────────


def test_sma_crossover_net_return_below_gross(engine: Engine) -> None:
    """Net equity after costs must be less than gross equity (pre-cost).

    Uses a monotonically rising price series (vol=0) so SMA(20) > SMA(60)
    is guaranteed after the slow-MA warmup, producing at least one round
    trip. Gross equity is reconstructed by adding all cost components back.
    """
    bars = make_synthetic_bars(252, trend=0.3, vol=0.0)

    with patch(_PATCH, return_value=bars):
        result = engine.run(
            strategy=SmaCrossover(fast=20, slow=60),
            tickers=["2330"],
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )

    assert len(result.trades) >= 2, "expected at least one buy and one force-close sell"
    assert result.total_commission > 0
    assert result.total_tax > 0

    gross_equity = (
        result.final_equity
        + result.total_commission
        + result.total_tax
        + result.total_slippage_cost
    )
    assert result.final_equity < gross_equity


def test_sma_crossover_passes_lookahead_audit() -> None:
    """SmaCrossover must pass assert_no_lookahead on a 252-bar sample.

    Perturbs the last 50 bars and verifies the first 202 signals are
    unchanged — confirming no signal depends on future data.
    """
    bars = make_synthetic_bars(252, trend=0.3, vol=0.0)
    assert_no_lookahead(SmaCrossover(fast=20, slow=60), bars)
