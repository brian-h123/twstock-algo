import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from twstock.backtest.result import BacktestResult
from twstock.backtest.tearsheet import (
    _cagr,
    _drawdown_series,
    _max_drawdown,
    _max_drawdown_duration,
    _sharpe,
    _sortino,
    equity_curve_figure,
    tear_sheet,
    trades_table,
)

# ── fixture helpers ───────────────────────────────────────────────────────────

_TRADES_COLS = [
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


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=_TRADES_COLS)


def _empty_positions() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["shares", "avg_cost", "market_value"],
        index=pd.MultiIndex.from_tuples([], names=["date", "ticker"]),
    )


def _make_result(
    equity: list[float],
    trades: pd.DataFrame | None = None,
    commission: float = 0.0,
    tax: float = 0.0,
    slippage: float = 0.0,
    name: str = "Test",
) -> BacktestResult:
    idx = pd.date_range("2020-01-02", periods=len(equity), freq="B")
    if trades is None:
        trades = _empty_trades()
    return BacktestResult(
        strategy_name=name,
        tickers=("2330",),
        start=idx[0].date(),
        end=idx[-1].date(),
        initial_capital=float(equity[0]),
        final_equity=float(equity[-1]),
        equity_curve=pd.Series(equity, index=idx, dtype=float),
        trades=trades,
        positions_daily=_empty_positions(),
        total_commission=commission,
        total_tax=tax,
        total_slippage_cost=slippage,
    )


def _make_trades(rows: list[tuple]) -> pd.DataFrame:
    """Build a trades DataFrame.

    Each row: (date_str, ticker, side, fill_price, shares, commission, tax)
    """
    records = []
    for date_str, ticker, side, price, shares, commission, tax in rows:
        records.append(
            {
                "timestamp": pd.Timestamp(date_str),
                "ticker": ticker,
                "side": side,
                "shares": int(shares),
                "fill_price": float(price),
                "commission": float(commission),
                "tax": float(tax),
                "notional": float(price * shares),
                "total_cost": float(commission + tax),
            }
        )
    return pd.DataFrame(records)


# ── _sharpe ───────────────────────────────────────────────────────────────────


class TestSharpe:
    def test_known_value(self):
        r = pd.Series([0.01, -0.01, 0.02, -0.02, 0.01])
        expected = (r.mean() / r.std(ddof=1)) * 252**0.5
        assert _sharpe(r) == pytest.approx(expected)

    def test_zero_std_returns_nan(self):
        assert math.isnan(_sharpe(pd.Series([0.005, 0.005, 0.005])))


# ── _sortino ──────────────────────────────────────────────────────────────────


class TestSortino:
    def test_known_value(self):
        r = pd.Series([0.01, -0.01, 0.02, -0.02, 0.01])
        adj = r.copy()
        adj[adj > 0] = 0
        downside_dev = float(np.sqrt(np.nanmean(adj**2)))
        expected = (r.mean() / downside_dev) * 252**0.5
        assert _sortino(r) == pytest.approx(expected)

    def test_no_downside_returns_nan(self):
        assert math.isnan(_sortino(pd.Series([0.01, 0.02, 0.03])))


# ── _drawdown_series ──────────────────────────────────────────────────────────


class TestDrawdownSeries:
    def test_monotone_increasing_is_all_zeros(self):
        equity = pd.Series([100.0, 110.0, 120.0])
        assert float(_drawdown_series(equity).max()) == pytest.approx(0.0)

    def test_known_drawdown_value(self):
        # peak = 110; equity drops to 95 → dd = (110 - 95) / 110
        equity = pd.Series([100.0, 110.0, 95.0])
        assert _drawdown_series(equity).iloc[2] == pytest.approx(15.0 / 110.0)


# ── _max_drawdown ─────────────────────────────────────────────────────────────


class TestMaxDrawdown:
    def test_known_value(self):
        # Peak = 110, trough = 95 → (110 - 95) / 110 = 15/110
        equity = pd.Series([100.0, 110.0, 105.0, 95.0, 100.0, 108.0])
        assert _max_drawdown(equity) == pytest.approx(15.0 / 110.0)

    def test_monotone_increasing_is_zero(self):
        equity = pd.Series([100.0, 110.0, 120.0, 130.0])
        assert _max_drawdown(equity) == 0.0

    def test_delegates_to_drawdown_series(self):
        equity = pd.Series([100.0, 90.0, 110.0, 85.0, 120.0])
        assert _max_drawdown(equity) == pytest.approx(float(_drawdown_series(equity).max()))


# ── _max_drawdown_duration ────────────────────────────────────────────────────


class TestMaxDrawdownDuration:
    def test_known_duration(self):
        # underwater: F F T T T F T F  →  longest run = 3 (bars 2–4)
        equity = pd.Series([100.0, 110.0, 105.0, 95.0, 100.0, 110.0, 108.0, 120.0])
        assert _max_drawdown_duration(equity) == 3

    def test_no_drawdown_is_zero(self):
        equity = pd.Series([100.0, 110.0, 120.0])
        assert _max_drawdown_duration(equity) == 0

    def test_two_separate_runs_returns_longer(self):
        # underwater: F F T T F T F  →  runs of 2 and 1  →  max = 2
        equity = pd.Series([100.0, 110.0, 105.0, 100.0, 110.0, 108.0, 120.0])
        assert _max_drawdown_duration(equity) == 2


# ── _cagr ─────────────────────────────────────────────────────────────────────


class TestCagr:
    def test_doubles_in_one_year(self):
        equity = pd.Series([1_000_000.0, 2_000_000.0])
        assert _cagr(equity, years=1.0) == pytest.approx(1.0)

    def test_ten_percent_over_one_year(self):
        equity = pd.Series([1_000_000.0, 1_100_000.0])
        assert _cagr(equity, years=1.0) == pytest.approx(0.10)

    def test_two_year_annualization(self):
        # 1_000_000 → 1_440_000 over 2 years  →  CAGR = sqrt(1.44) - 1 = 0.20
        equity = pd.Series([1_000_000.0, 1_440_000.0])
        assert _cagr(equity, years=2.0) == pytest.approx(0.20, rel=1e-4)


# ── tear_sheet ────────────────────────────────────────────────────────────────

_BASE_KEYS = frozenset(
    {
        "total_return",
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "max_drawdown_duration_days",
        "hit_rate",
        "avg_win",
        "avg_loss",
        "win_loss_ratio",
        "turnover",
        "total_fees",
        "n_trades",
        "note",
    }
)
_BENCH_KEYS = frozenset({"alpha", "beta", "tracking_error", "info_ratio"})


class TestTearSheet:
    def test_all_base_keys_present(self):
        result = _make_result([1_000_000 + i * 500 for i in range(50)])
        assert _BASE_KEYS.issubset(tear_sheet(result).keys())

    def test_benchmark_keys_absent_without_benchmark(self):
        result = _make_result([1_000_000 + i * 500 for i in range(10)])
        assert _BENCH_KEYS.isdisjoint(tear_sheet(result).keys())

    def test_benchmark_keys_present_with_benchmark(self):
        result = _make_result([1_000_000 + i * 500 for i in range(10)])
        bench = _make_result([1_000_000 + i * 200 for i in range(10)], name="BuyAndHold")
        assert _BENCH_KEYS.issubset(tear_sheet(result, benchmark=bench).keys())

    def test_total_return_known(self):
        # 1_000_000 → 1_100_000 = +10 %
        result = _make_result([1_000_000.0, 1_100_000.0])
        assert tear_sheet(result)["total_return"] == pytest.approx(0.10)

    def test_total_fees_sums_all_cost_components(self):
        result = _make_result(
            [1_000_000 + i * 100 for i in range(5)],
            commission=500.0,
            tax=300.0,
            slippage=100.0,
        )
        assert tear_sheet(result)["total_fees"] == pytest.approx(900.0)

    def test_empty_trades_produces_nan_trade_metrics(self):
        result = _make_result([1_000_000 + i * 1_000 for i in range(50)])
        sheet = tear_sheet(result)
        assert math.isnan(sheet["hit_rate"])
        assert math.isnan(sheet["avg_win"])
        assert math.isnan(sheet["avg_loss"])
        assert math.isnan(sheet["win_loss_ratio"])
        assert sheet["n_trades"] == 0
        assert sheet["turnover"] == pytest.approx(0.0)

    def test_hit_rate_two_wins_one_loss(self):
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 0.0, 0.0),
                ("2020-01-06", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-07", "2330", "sell", 112.0, 1000, 0.0, 0.0),
                ("2020-01-08", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-09", "2330", "sell", 90.0, 1000, 0.0, 0.0),
            ]
        )
        result = _make_result([1_000_000 + i * 500 for i in range(10)], trades=trades)
        sheet = tear_sheet(result)
        assert sheet["hit_rate"] == pytest.approx(2 / 3)
        assert sheet["n_trades"] == 3

    def test_metrics_match_helpers_on_50_rows(self):
        # Build a deterministic 50-row equity curve and verify tear_sheet
        # delegates correctly to each helper.
        rng = np.random.default_rng(42)
        daily_r = rng.normal(0.0003, 0.012, 49)  # 49 returns → 50 rows
        equity_vals = [1_000_000.0]
        for r in daily_r:
            equity_vals.append(equity_vals[-1] * (1 + r))

        result = _make_result(equity_vals)
        equity = result.equity_curve
        returns = equity.pct_change().dropna()
        sheet = tear_sheet(result)

        assert sheet["sharpe"] == pytest.approx(_sharpe(returns))
        assert sheet["sortino"] == pytest.approx(_sortino(returns))
        assert sheet["max_drawdown"] == pytest.approx(_max_drawdown(equity))
        assert sheet["max_drawdown_duration_days"] == _max_drawdown_duration(equity)


# ── trades_table ──────────────────────────────────────────────────────────────

_TABLE_COLS = ["date", "ticker", "side", "shares", "price", "notional", "fees", "net"]


class TestTradesTable:
    def test_empty_result_has_correct_columns(self):
        result = _make_result([1_000_000.0, 1_010_000.0])
        tbl = trades_table(result)
        assert tbl.empty
        assert list(tbl.columns) == _TABLE_COLS

    def test_raw_column_names_not_in_output(self):
        trades = _make_trades([("2020-01-02", "2330", "buy", 100.0, 1000, 85.5, 0.0)])
        result = _make_result([1_000_000.0, 1_010_000.0], trades=trades)
        tbl = trades_table(result)
        assert "fill_price" not in tbl.columns
        assert "total_cost" not in tbl.columns

    def test_buy_net_is_negative_cash_out(self):
        # 1000 shares @ 100.0, commission=85.5, tax=0
        # net = -(notional + fees) = -(100_000 + 85.5)
        trades = _make_trades([("2020-01-02", "2330", "buy", 100.0, 1000, 85.5, 0.0)])
        result = _make_result([1_000_000.0, 1_010_000.0], trades=trades)
        tbl = trades_table(result)
        assert tbl.iloc[0]["net"] == pytest.approx(-(100_000 + 85.5), abs=1.0)

    def test_sell_net_is_positive_cash_in(self):
        # 1000 shares @ 110.0, commission=94.1, tax=330.0
        # net = notional - fees = 110_000 - (94.1 + 330.0)
        trades = _make_trades([("2020-01-03", "2330", "sell", 110.0, 1000, 94.1, 330.0)])
        result = _make_result([1_000_000.0, 1_010_000.0], trades=trades)
        tbl = trades_table(result)
        assert tbl.iloc[0]["net"] == pytest.approx(110_000 - 424.1, abs=1.0)


# ── equity_curve_figure ───────────────────────────────────────────────────────


class TestEquityCurveFigure:
    def test_returns_plotly_figure(self):
        result = _make_result([1_000_000 + i * 500 for i in range(10)])
        assert isinstance(equity_curve_figure(result), go.Figure)

    def test_two_traces_without_benchmark(self):
        # strategy equity curve + drawdown
        result = _make_result([1_000_000 + i * 500 for i in range(10)])
        fig = equity_curve_figure(result)
        assert len(fig.data) == 2  # type: ignore[reportArgumentType]

    def test_three_traces_with_benchmark(self):
        # strategy + benchmark equity curves + drawdown
        result = _make_result([1_000_000 + i * 500 for i in range(10)])
        bench = _make_result([1_000_000 + i * 200 for i in range(10)], name="BuyAndHold")
        fig = equity_curve_figure(result, benchmark=bench)
        assert len(fig.data) == 3  # type: ignore[reportArgumentType]
