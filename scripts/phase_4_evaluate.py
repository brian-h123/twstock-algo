"""Single-asset evaluation harness for Phase 4 strategies.

Runs a named strategy across all 50 TWSE universe tickers, collects
tear-sheet metrics per ticker, and prints a distribution summary
(median/IQR net Sharpe, % beating own B&H, % beating 0050).

Data comes from the local Parquet store via get_adjusted_history.
No network calls are made.

Usage:
    uv run python scripts/phase_4_evaluate.py --strategy rsi_reversion
    uv run python scripts/phase_4_evaluate.py --strategy ema_crossover \
        --start 2020-01-01 --end 2025-12-31

Available strategies: sma_crossover, ema_crossover, rsi_reversion,
                      bollinger_breakout, macd, donchian_breakout
"""

import argparse
import math
import warnings
from datetime import date
from typing import Any

import numpy as np

from twstock.backtest import BuyAndHold, Engine, TaiwanCostModel
from twstock.backtest.tearsheet import tear_sheet
from twstock.universe import get_top50_universe

DEFAULT_START = date(2020, 1, 1)
DEFAULT_END = date.today()
BENCHMARK_TICKER = "0050"
EXAMPLE_TICKER = "2330"  # shown in full detail per D3

_PCT_METRICS = {
    "total_return",
    "cagr",
    "max_drawdown",
    "hit_rate",
    "avg_win",
    "avg_loss",
    "turnover",
}

_DETAIL_LABELS: dict[str, str] = {
    "total_return": "Total Return",
    "cagr": "CAGR",
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "max_drawdown": "Max DD",
    "max_drawdown_duration_days": "Max DD Days",
    "hit_rate": "Hit Rate",
    "avg_win": "Avg Win",
    "avg_loss": "Avg Loss",
    "win_loss_ratio": "W/L Ratio",
    "turnover": "Turnover",
    "total_fees": "Total Fees (NT$)",
    "n_trades": "# Trades",
    "alpha": "Alpha (ann., vs 0050)",
    "beta": "Beta (vs 0050)",
    "tracking_error": "Tracking Error",
    "info_ratio": "Information Ratio",
}


def _strategy_registry() -> dict[str, type]:
    from twstock.strategies.bollinger_breakout import BollingerBreakout
    from twstock.strategies.donchian_breakout import DonchianBreakout
    from twstock.strategies.ema_crossover import EmaCrossover
    from twstock.strategies.macd import Macd
    from twstock.strategies.rsi_reversion import RsiReversion
    from twstock.strategies.sma_crossover import SmaCrossover

    return {
        "sma_crossover": SmaCrossover,
        "ema_crossover": EmaCrossover,
        "rsi_reversion": RsiReversion,
        "bollinger_breakout": BollingerBreakout,
        "macd": Macd,
        "donchian_breakout": DonchianBreakout,
    }


def _fmt(key: str, val: Any) -> str:
    if isinstance(val, int):
        return str(val)
    try:
        if math.isnan(val):
            return "n/a"
    except TypeError:
        return str(val)
    if key == "total_fees":
        return f"{val:,.0f}"
    if key in _PCT_METRICS:
        return f"{val:.2%}"
    return f"{val:.3f}"


def _run_ticker(
    engine: Engine,
    strategy: Any,
    ticker: str,
    start: date,
    end: date,
    benchmark_result: Any = None,
) -> dict | None:
    """Run engine for one ticker; return metrics dict or None on failure."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = engine.run(strategy=strategy, tickers=[ticker], start=start, end=end)

    if result.equity_curve.empty or len(result.equity_curve) < 2:
        return None

    return tear_sheet(result, benchmark=benchmark_result)


def _print_distribution(
    strategy_name: str,
    records: list[dict],
    benchmark_metrics: dict,
    start: date,
    end: date,
) -> None:
    valid_sharpes = [r["net_sharpe"] for r in records if not math.isnan(r["net_sharpe"])]
    n_total = len(records)
    n_valid = len(valid_sharpes)
    n_beats_bh = sum(1 for r in records if r["beats_own_bh"])
    n_beats_0050 = sum(1 for r in records if r["beats_0050"])

    if valid_sharpes:
        q1, median, q3 = np.percentile(valid_sharpes, [25, 50, 75])
    else:
        q1 = median = q3 = float("nan")

    print(f"\n{'=' * 70}")
    print(f"  {strategy_name}")
    print(f"  Period: {start} -> {end}  |  {n_total} tickers")
    print(f"{'=' * 70}")

    print("\n  0050 Buy-and-Hold (benchmark)")
    print(f"    Net Sharpe   {_fmt('sharpe', benchmark_metrics['sharpe'])}")
    print(f"    Net CAGR     {_fmt('cagr', benchmark_metrics['cagr'])}")
    print(f"    Max DD       {_fmt('max_drawdown', benchmark_metrics['max_drawdown'])}")

    print(f"\n  Distribution -- net Sharpe  ({n_valid}/{n_total} tickers with valid metrics)")
    print(f"    Median           {_fmt('sharpe', median)}")
    print(f"    Q1 - Q3          {_fmt('sharpe', q1)} - {_fmt('sharpe', q3)}")
    print(f"    Beats own B&H    {n_beats_bh}/{n_total}  ({n_beats_bh / n_total:.1%})")
    print(f"    Beats 0050 B&H   {n_beats_0050}/{n_total}  ({n_beats_0050 / n_total:.1%})")


def _print_table(records: list[dict]) -> None:
    col_w = {
        "ticker": 8,
        "sharpe": 7,
        "cagr": 8,
        "dd": 7,
        "trades": 7,
        "fees": 12,
        "bh": 9,
        "0050": 6,
    }
    header = (
        f"  {'Ticker':<{col_w['ticker']}} "
        f"{'Sharpe':>{col_w['sharpe']}} "
        f"{'CAGR':>{col_w['cagr']}} "
        f"{'Max DD':>{col_w['dd']}} "
        f"{'Trades':>{col_w['trades']}} "
        f"{'Fees (NT$)':>{col_w['fees']}} "
        f"{'Own B&H':>{col_w['bh']}} "
        f"{'0050':>{col_w['0050']}}"
    )
    sep = "  " + "  ".join("-" * w for w in col_w.values())

    print("\n  Per-ticker results")
    print(f"\n{header}")
    print(sep)

    for r in records:
        print(
            f"  {r['ticker']:<{col_w['ticker']}} "
            f"{_fmt('sharpe', r['net_sharpe']):>{col_w['sharpe']}} "
            f"{_fmt('cagr', r['net_cagr']):>{col_w['cagr']}} "
            f"{_fmt('max_drawdown', r['max_drawdown']):>{col_w['dd']}} "
            f"{r['n_trades']:>{col_w['trades']}} "
            f"{_fmt('total_fees', r['total_fees']):>{col_w['fees']}} "
            f"{'yes' if r['beats_own_bh'] else 'no':>{col_w['bh']}} "
            f"{'yes' if r['beats_0050'] else 'no':>{col_w['0050']}}"
        )


def _print_detail(ticker: str, metrics: dict) -> None:
    print(f"\n  {ticker} -- detailed tear sheet")
    print(f"  {'-' * 45}")
    for key, label in _DETAIL_LABELS.items():
        val = metrics.get(key)
        if val is None:
            continue
        print(f"    {label:<28} {_fmt(key, val)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a single-asset strategy across all 50 TWSE tickers."
    )
    parser.add_argument(
        "--strategy",
        required=True,
        metavar="NAME",
        help="Strategy name, e.g. rsi_reversion",
    )
    parser.add_argument("--start", default=str(DEFAULT_START), help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=str(DEFAULT_END), help="End date YYYY-MM-DD")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    registry = _strategy_registry()
    if args.strategy not in registry:
        available = ", ".join(sorted(registry))
        print(f"Unknown strategy '{args.strategy}'. Available: {available}")
        raise SystemExit(1)

    strategy = registry[args.strategy]()

    engine = Engine(
        capital=1_000_000,
        cost_model=TaiwanCostModel(),
        max_position_pct=0.10,
        execution="next_open",
    )

    print(f"Strategy : {strategy.name}")
    print(f"Period   : {start} -> {end}")
    print("Capital  : NT$1,000,000  |  max 10% per position  |  T+1 open execution")
    print("Costs    : TaiwanCostModel (always on)")
    print()

    # Run 0050 benchmark once; reused for alpha/beta in every per-ticker tear sheet
    print("Running 0050 Buy-and-Hold benchmark...")
    benchmark_result = engine.run(
        strategy=BuyAndHold(),
        tickers=[BENCHMARK_TICKER],
        start=start,
        end=end,
    )
    benchmark_metrics = tear_sheet(benchmark_result)
    benchmark_sharpe = benchmark_metrics["sharpe"]
    sharpe_str = _fmt("sharpe", benchmark_sharpe)
    cagr_str = _fmt("cagr", benchmark_metrics["cagr"])
    print(f"  0050 Sharpe: {sharpe_str}  CAGR: {cagr_str}")
    print()

    tickers = get_top50_universe()
    records: list[dict] = []
    example_metrics: dict | None = None
    skipped: list[str] = []

    print(f"Running across {len(tickers)} tickers...")
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i:2d}/{len(tickers)}] {ticker}...", end="", flush=True)

        # Strategy run -- pass benchmark_result so tear_sheet computes alpha/beta
        strat_metrics = _run_ticker(engine, strategy, ticker, start, end, benchmark_result)
        if strat_metrics is None:
            print(" skipped (insufficient data)")
            skipped.append(ticker)
            continue

        # Own buy-and-hold for this ticker -- no benchmark comparison needed
        bh_metrics = _run_ticker(engine, BuyAndHold(), ticker, start, end)
        own_bh_sharpe = bh_metrics["sharpe"] if bh_metrics else float("nan")

        net_sharpe = strat_metrics["sharpe"]

        def _nan_safe_gt(a: float, b: float) -> bool:
            return (not math.isnan(a)) and (not math.isnan(b)) and a > b

        beats_own_bh = _nan_safe_gt(net_sharpe, own_bh_sharpe)
        beats_0050 = _nan_safe_gt(net_sharpe, benchmark_sharpe)

        records.append(
            {
                "ticker": ticker,
                "net_sharpe": net_sharpe,
                "net_cagr": strat_metrics["cagr"],
                "max_drawdown": strat_metrics["max_drawdown"],
                "n_trades": strat_metrics["n_trades"],
                "total_fees": strat_metrics["total_fees"],
                "beats_own_bh": beats_own_bh,
                "beats_0050": beats_0050,
            }
        )

        tag = "  [beats B&H]" if beats_own_bh else ""
        s_str = _fmt("sharpe", net_sharpe)
        c_str = _fmt("cagr", strat_metrics["cagr"])
        print(f" Sharpe={s_str}  CAGR={c_str}{tag}")

        if ticker == EXAMPLE_TICKER:
            example_metrics = strat_metrics

    _print_distribution(strategy.name, records, benchmark_metrics, start, end)
    _print_table(records)

    if example_metrics is not None:
        _print_detail(EXAMPLE_TICKER, example_metrics)

    if skipped:
        print(f"\n  Skipped ({len(skipped)}): {', '.join(skipped)}")

    print(
        "\n  Note: Universe is a 2026-05-08 snapshot; survivorship bias not corrected."
        "\n        Parameters are canonical (not optimised for selection). See PHASE_4.md D2."
        "\n        'Beats' = net Sharpe of strategy > net Sharpe of comparator."
    )


if __name__ == "__main__":
    main()
