"""Walk-forward stability report for SmaCrossover(20, 60) on 2330.

Usage:
    uv run python scripts/phase_3_stability_report.py

Runs one anchored 3y/1y walk-forward split over 2017-01-01 to 2025-12-31,
prints per-fold tear sheets, and writes a markdown summary to
journal/phase_3_stability_report.md.
"""

from datetime import date
from pathlib import Path

from twstock.backtest import Engine, TaiwanCostModel, WalkForwardSplit
from twstock.backtest.tearsheet import tear_sheet
from twstock.strategies.sma_crossover import SmaCrossover

TICKER = "2330"
OVERALL_START = date(2017, 1, 1)
OVERALL_END = date(2025, 12, 31)
REPORT_PATH = Path(__file__).parent.parent / "journal" / "phase_3_stability_report.md"

_METRIC_LABELS = {
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
}

_PCT_METRICS = {
    "total_return",
    "cagr",
    "max_drawdown",
    "hit_rate",
    "avg_win",
    "avg_loss",
    "turnover",
}


def _fmt(key: str, val: float) -> str:
    if isinstance(val, int):
        return str(val)
    if key == "total_fees":
        return f"{val:,.0f}"
    if key in _PCT_METRICS:
        return f"{val:.2%}"
    return f"{val:.3f}"


def _print_fold(
    fold_num: int,
    train_start: date,
    train_end: date,
    test_start: date,
    test_end: date,
    metrics: dict,
) -> None:
    print(f"\n{'=' * 60}")
    print(
        f"Fold {fold_num}  |  train {train_start} → {train_end}  |  test {test_start} → {test_end}"
    )
    print(f"{'=' * 60}")
    for key, label in _METRIC_LABELS.items():
        val = metrics.get(key, float("nan"))
        print(f"  {label:<25} {_fmt(key, val)}")


def _markdown_table(folds: list[dict]) -> str:
    headers = [
        "Fold",
        "Test Period",
        "Total Return",
        "CAGR",
        "Sharpe",
        "Max DD",
        "# Trades",
        "Total Fees (NT$)",
    ]
    rows = []
    for f in folds:
        m = f["metrics"]
        rows.append(
            [
                str(f["fold"]),
                f"{f['test_start']} → {f['test_end']}",
                _fmt("total_return", m["total_return"]),
                _fmt("cagr", m["cagr"]),
                _fmt("sharpe", m["sharpe"]),
                _fmt("max_drawdown", m["max_drawdown"]),
                str(m["n_trades"]),
                _fmt("total_fees", m["total_fees"]),
            ]
        )

    header_row = "| " + " | ".join(headers) + " |"
    sep_row = "| " + " | ".join("---" for _ in headers) + " |"
    data_rows = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return f"{header_row}\n{sep_row}\n{data_rows}"


def main() -> None:
    engine = Engine(
        capital=1_000_000,
        cost_model=TaiwanCostModel(),
        max_position_pct=0.10,
        execution="next_open",
    )
    strategy = SmaCrossover(fast=20, slow=60)
    splitter = WalkForwardSplit(train_years=3, test_years=1, step_years=1, anchored=True)

    print("SmaCrossover(20, 60) — walk-forward stability report")
    print(f"Ticker: {TICKER}  |  Overall range: {OVERALL_START} → {OVERALL_END}")
    print("Split: anchored 3y train / 1y test, step 1y")

    fold_records = []
    for fold_num, (train_start, train_end, test_start, test_end) in enumerate(
        splitter.split(OVERALL_START, OVERALL_END), start=1
    ):
        result = engine.run(
            strategy=strategy,
            tickers=[TICKER],
            start=test_start,
            end=test_end,
        )
        metrics = tear_sheet(result)
        _print_fold(fold_num, train_start, train_end, test_start, test_end, metrics)
        fold_records.append(
            {
                "fold": fold_num,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "metrics": metrics,
            }
        )

    _write_report(fold_records)
    print(f"\nReport written to {REPORT_PATH}")


def _write_report(folds: list[dict]) -> None:
    lines = [
        "# Phase 3 — SMA Crossover Walk-Forward Stability Report",
        "",
        "**Strategy:** SmaCrossover(fast=20, slow=60)  ",
        f"**Ticker:** {TICKER} (TSMC)  ",
        f"**Overall range:** {OVERALL_START} → {OVERALL_END}  ",
        "**Split:** anchored 3-year train / 1-year test, step 1 year  ",
        "**Engine:** NT$1,000,000 capital, 10% max position, T+1 open execution  ",
        "",
        "> Anchored walk-forward: the training window is pinned at the overall start date and",
        "> grows by one year each fold. Only the out-of-sample test periods are reported below.",
        "> Parameters (fast=20, slow=60) are **not** re-optimised per fold — this report tests",
        "> temporal stability of a fixed parameterisation, not in-sample tuning.",
        "",
        "## Fold Summary",
        "",
        _markdown_table(folds),
        "",
        "## Per-Fold Detail",
        "",
    ]

    for f in folds:
        m = f["metrics"]
        lines += [
            f"### Fold {f['fold']}",
            "",
            f"- **Train:** {f['train_start']} → {f['train_end']}",
            f"- **Test:** {f['test_start']} → {f['test_end']}",
            "",
            "| Metric | Value |",
            "| --- | --- |",
        ]
        for key, label in _METRIC_LABELS.items():
            val = m.get(key, float("nan"))
            lines.append(f"| {label} | {_fmt(key, val)} |")
        lines.append("")

    lines += [
        "---",
        "",
        "> **Survivorship bias note:** Universe is a 2026-05-08 snapshot;",
        "> survivorship bias not corrected.",
        "",
        "*Generated by `scripts/phase_3_stability_report.py`*",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
