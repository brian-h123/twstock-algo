from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pandas as pd

from twstock.backtest.result import BacktestResult
from twstock.backtest.tearsheet import tear_sheet
from twstock.broker.events import BrokerEvent, EventKind, EventLog
from twstock.broker.result import SimResult


@dataclass(frozen=True)
class WaterfallLine:
    name: str
    category: Literal["tripwire", "accepted_divergence"]
    value: Decimal
    expected_sign: Literal["zero", "nonnegative", "nonpositive"]
    tolerance: Decimal
    ok: bool


@dataclass(frozen=True)
class ReconciliationReport:
    sim_strategy: str
    reference_strategy: str
    headline: pd.DataFrame
    waterfall: list[WaterfallLine]
    order_audit: pd.DataFrame
    passed: bool


def _as_backtest_result(sim: SimResult) -> BacktestResult:
    return BacktestResult(
        strategy_name=sim.strategy_name,
        tickers=sim.tickers,
        start=sim.start,
        end=sim.end,
        initial_capital=float(sim.initial_capital),
        final_equity=float(sim.final_equity),
        equity_curve=sim.equity_curve.astype(float),
        trades=sim.trades,
        positions_daily=sim.positions_daily,
        total_commission=float(sim.total_commission),
        total_tax=float(sim.total_tax),
        total_slippage_cost=float(sim.total_slippage_cost),
    )


def _headline_diff(sim: SimResult, reference: BacktestResult) -> pd.DataFrame:
    sim_sheet, ref_sheet = tear_sheet(_as_backtest_result(sim)), tear_sheet(reference)
    rows = {k: (sim_sheet[k], ref_sheet[k]) for k in ref_sheet if k != "note"}
    df = pd.DataFrame(rows, index=["sim", "reference"]).T
    df["diff"] = df["sim"] - df["reference"]
    return df


def _build_order_audit(events: list[BrokerEvent]) -> pd.DataFrame:
    """One row per order that didn't fully fill cleanly: REJECT (exact
    RejectionReason) or PARTIAL_FILL (defaulted to "liquidity" per ADR 0011 D2)."""
    submitted = {
        e.order_id: int(e.payload["quantity"]) for e in events if e.kind == EventKind.SUBMITTED
    }
    rows = []
    for e in events:
        if e.kind == EventKind.REJECT:
            rows.append(
                {
                    "order_id": e.order_id,
                    "date": e.ts,
                    "ticker": e.payload["ticker"],
                    "cause": e.payload["reason"],
                    "requested": submitted[e.order_id],
                    "filled": 0,
                    "notional_gap": None,
                }
            )
        elif e.kind == EventKind.PARTIAL_FILL:
            filled = int(e.payload["shares"])
            gap = submitted[e.order_id] - filled
            rows.append(
                {
                    "order_id": e.order_id,
                    "date": e.ts,
                    "ticker": e.payload["ticker"],
                    "cause": "liquidity",
                    "requested": submitted[e.order_id],
                    "filled": filled,
                    "notional_gap": gap * float(e.payload["fill_price"]),
                }
            )
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(
        columns=["order_id", "date", "ticker", "cause", "requested", "filled", "notional_gap"]
    )


def _liquidity_underfill_line(sim: SimResult, order_audit: pd.DataFrame) -> WaterfallLine:
    """Total missed notional from liquidity-capped orders, as a fraction of
    initial capital. This is a FRICTION MAGNITUDE, not an estimated return
    contribution -- ADR 0011 D1 folds it into the residual as-is, deliberately
    not attempting a counterfactual "what would those shares have earned"."""
    gap = Decimal(str(order_audit.loc[order_audit["cause"] == "liquidity", "notional_gap"].sum()))
    value = gap / sim.initial_capital
    return WaterfallLine(
        name="liquidity_underfill",
        category="accepted_divergence",
        value=value,
        expected_sign="nonnegative",
        tolerance=Decimal("0.05"),
        ok=(value >= 0),
    )


def _float_reliance_line(sim: SimResult) -> WaterfallLine:
    """Time-weighted-average unsettled cash as a fraction of initial capital.
    Expected ~0 under NettingSettlement for a long-only-within-means book
    (ADR 0009 D5) -- a nonzero value there is itself a bug signal, not noise."""
    avg_unsettled = Decimal(str(sim.unsettled_cash_curve.mean()))
    value = avg_unsettled / sim.initial_capital
    return WaterfallLine(
        name="float_reliance",
        category="accepted_divergence",
        value=value,
        expected_sign="zero",
        tolerance=Decimal("0.01"),
        ok=(value <= Decimal("0.01")),
    )


def _slippage_line(sim: SimResult, reference: BacktestResult) -> WaterfallLine:
    sim_rate = sim.total_slippage_cost / Decimal(str(sim.trades["notional"].sum()))
    ref_rate = Decimal(str(reference.total_slippage_cost / reference.trades["notional"].sum()))
    value = sim_rate - ref_rate
    return WaterfallLine(
        name="slippage_diff",
        category="tripwire",
        value=value,
        expected_sign="zero",
        tolerance=Decimal("0.00001"),
        ok=(abs(value) <= Decimal("0.00001")),
    )


def _raw_vs_adjusted_line(
    sim: SimResult, reference: BacktestResult, liquidity: WaterfallLine, float_line: WaterfallLine
) -> WaterfallLine:
    sim_return = sim.final_equity / sim.initial_capital - 1
    ref_return = Decimal(str(reference.final_equity / reference.initial_capital - 1))
    total_gap = sim_return - ref_return
    value = total_gap - liquidity.value - float_line.value
    tolerance = Decimal("0.0005")
    return WaterfallLine(
        name="raw_vs_adjusted",
        category="tripwire",
        value=value,
        expected_sign="zero",
        tolerance=tolerance,
        ok=(abs(value) <= tolerance),
    )


def generate_reconcile_report(sim: SimResult, reference: BacktestResult) -> ReconciliationReport:
    events = list(EventLog(sim.event_log_path))

    headline = _headline_diff(sim, reference)
    order_audit = _build_order_audit(events)

    liquidity_line = _liquidity_underfill_line(sim, order_audit)
    float_line = _float_reliance_line(sim)
    slippage_line = _slippage_line(sim, reference)
    raw_vs_adjusted_line = _raw_vs_adjusted_line(sim, reference, liquidity_line, float_line)

    waterfall = [slippage_line, raw_vs_adjusted_line, liquidity_line, float_line]

    return ReconciliationReport(
        sim_strategy=sim.strategy_name,
        reference_strategy=reference.strategy_name,
        headline=headline,
        waterfall=waterfall,
        order_audit=order_audit,
        passed=all(line.ok for line in waterfall if line.category == "tripwire"),
    )
