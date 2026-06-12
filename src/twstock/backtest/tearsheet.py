import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from twstock.backtest.result import BacktestResult

_ANN_FACTOR = 252  # TWSE daily sessions; committed policy — see phase_3_references.md borrow #14


def _sharpe(returns: pd.Series) -> float:
    # Sharpe = mean(r) / std(r, ddof=1) * sqrt(252), rf=0 — borrow #10
    std = returns.std(ddof=1)
    if std == 0:
        return float("nan")
    return (returns.mean() / std) * (_ANN_FACTOR**0.5)


def _sortino(returns: pd.Series) -> float:
    # Sortino = mean(r) / downside_dev * sqrt(252), target=0;
    # downside_dev averages over ALL n periods (not downside-only) — borrow #10
    adj_returns = returns.copy()
    adj_returns[adj_returns > 0] = 0
    downside_dev = np.sqrt(np.nanmean(adj_returns**2))
    if downside_dev == 0:
        return float("nan")
    return (returns.mean() / downside_dev) * (_ANN_FACTOR**0.5)


def _drawdown_series(equity: pd.Series) -> pd.Series:
    # DD = (peak - equity) / peak; reported positive (0.30 = 30% drawdown) — borrow #10
    peak = equity.cummax()
    return (peak - equity) / peak  # always ≥ 0; 0 = at all-time high, positive = underwater


def _max_drawdown(equity: pd.Series) -> float:
    return float(_drawdown_series(equity=equity).max())


def _cagr(equity: pd.Series, years: float) -> float:
    # CAGR = (final/initial)^(1/years) - 1; actual elapsed years, not _ANN_FACTOR — borrow #10
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def _max_drawdown_duration(equity: pd.Series) -> int:
    # longest consecutive run of trading days spent below the running peak (underwater duration)
    peak = equity.cummax()
    underwater = equity < peak
    group = (
        ~underwater
    ).cumsum()  # increments at each surface; consecutive underwater bars share a group label
    run_lengths = underwater.groupby(group).sum()
    return int(run_lengths.max()) if underwater.any() else 0


def _round_trips(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["ticker", "entry_price", "exit_price", "shares", "ret"])

    rows = []
    for ticker, grp in trades.groupby("ticker"):
        buys = grp[grp["side"] == "buy"].sort_values("timestamp").reset_index(drop=True)
        sells = grp[grp["side"] == "sell"].sort_values("timestamp").reset_index(drop=True)
        n = min(len(buys), len(sells))
        if n == 0:
            continue
        trip = pd.DataFrame(
            {
                "ticker": ticker,
                "entry_price": buys["fill_price"].to_numpy()[:n],
                "exit_price": sells["fill_price"].to_numpy()[:n],
                "shares": buys["shares"].to_numpy()[:n],
            }
        )
        trip["ret"] = trip["exit_price"] / trip["entry_price"] - 1
        rows.append(trip)

    return (
        pd.concat(rows, ignore_index=True)
        if rows
        else pd.DataFrame(columns=["ticker", "entry_price", "exit_price", "shares", "ret"])
    )


def tear_sheet(result: BacktestResult, benchmark: BacktestResult | None = None) -> dict:
    """Return a dict of metrics. Keys:
    total_return, cagr, sharpe, sortino, max_drawdown,
    max_drawdown_duration_days, hit_rate, avg_win, avg_loss,
    win_loss_ratio, turnover, total_fees, n_trades.
    If benchmark provided: alpha, beta, tracking_error, info_ratio."""

    returns = result.equity_curve.pct_change().dropna()
    equity = result.equity_curve
    trades = result.trades

    years = (equity.index[-1] - equity.index[0]).days / 365.25
    trips = _round_trips(trades=trades)

    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = _cagr(equity=equity, years=years)
    sharpe = _sharpe(returns=returns)
    sortino = _sortino(returns=returns)
    max_drawdown = _max_drawdown(equity=equity)
    max_drawdown_duration_days = _max_drawdown_duration(equity=equity)

    turnover = (trades[trades["side"] == "buy"]["notional"].sum()) / equity.mean() / years

    wins = trips[trips["ret"] > 0]["ret"]
    losses = trips[trips["ret"] <= 0]["ret"]

    hit_rate = len(wins) / len(trips) if len(trips) > 0 else float("nan")
    avg_win = wins.mean()
    avg_loss = losses.mean()
    win_loss_ratio = (
        wins.mean() / abs(losses.mean()) if (len(wins) > 0 and len(losses) > 0) else float("nan")
    )

    total_fees = result.total_commission + result.total_tax + result.total_slippage_cost
    n_trades = len(trips)

    sheet = {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "max_drawdown_duration_days": max_drawdown_duration_days,
        "hit_rate": hit_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": win_loss_ratio,
        "turnover": turnover,
        "total_fees": total_fees,
        "n_trades": n_trades,
    }

    if benchmark is not None:
        strat_r, bench_r = returns.align(benchmark.equity_curve.pct_change().dropna(), join="inner")
        excess = strat_r - bench_r
        sheet["beta"] = strat_r.cov(bench_r) / bench_r.var(ddof=1)
        sheet["alpha"] = (strat_r - sheet["beta"] * bench_r).mean() * _ANN_FACTOR
        sheet["tracking_error"] = excess.std(ddof=1) * (_ANN_FACTOR**0.5)
        sheet["info_ratio"] = excess.mean() / excess.std(ddof=1) * (_ANN_FACTOR**0.5)

    sheet["note"] = "Universe is a 2026-05-08 snapshot; survivorship bias not corrected."
    return sheet


def equity_curve_figure(
    result: BacktestResult, benchmark: BacktestResult | None = None
) -> go.Figure:
    """Plotly figure: equity curve, drawdown subplot below."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,  # zoom/pan both subplots together
        row_heights=[0.7, 0.3],  # equity curve gets more vertical space
        vertical_spacing=0.05,
    )

    norm = result.equity_curve / result.equity_curve.iloc[0]
    fig.add_trace(go.Scatter(x=norm.index, y=norm, name=result.strategy_name), row=1, col=1)

    if benchmark is not None:
        b = benchmark.equity_curve / benchmark.equity_curve.iloc[0]
        fig.add_trace(
            go.Scatter(x=b.index, y=b, name=benchmark.strategy_name, line=dict(dash="dash")),
            row=1,
            col=1,
        )

    dd = _drawdown_series(result.equity_curve)
    fig.add_trace(
        go.Scatter(
            x=dd.index, y=-dd, fill="tozeroy", name="Drawdown"
        ),  # _drawdown_series returns positive; use -dd to fill downward
        row=2,
        col=1,
    )

    fig.update_yaxes(tickformat=".0%", row=2, col=1)  # drawdown as %
    fig.update_layout(title=result.strategy_name, showlegend=True)
    return fig


def trades_table(result: BacktestResult) -> pd.DataFrame:
    """Pretty-printed trades table for journal writeups."""

    df = result.trades.copy()
    if df.empty:
        return pd.DataFrame(
            columns=["date", "ticker", "side", "shares", "price", "notional", "fees", "net"]
        )
    df["date"] = df["timestamp"].dt.date
    df["net"] = np.where(
        df["side"] == "buy", -(df["notional"] + df["total_cost"]), df["notional"] - df["total_cost"]
    )
    return (
        df[["date", "ticker", "side", "shares", "fill_price", "notional", "total_cost", "net"]]
        .rename(columns={"fill_price": "price", "total_cost": "fees"})
        .round({"price": 2, "notional": 0, "fees": 0, "net": 0})
        .reset_index(drop=True)
    )
