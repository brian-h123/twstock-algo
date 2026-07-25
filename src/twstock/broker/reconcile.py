from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pandas as pd

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.result import BacktestResult
from twstock.backtest.tearsheet import tear_sheet
from twstock.broker.events import BrokerEvent, EventKind, EventLog
from twstock.broker.rejection import RejectionReason
from twstock.broker.result import SimResult

# ADR 0011 D2 follow-up: a PARTIAL_FILL's cause is either the volume/
# participation cap ("liquidity") or the cash ceiling (reuses
# RejectionReason.INSUFFICIENT_BUYING_POWER's string -- same underlying
# constraint as the zero-fill REJECT case, just short of a full reject).
# Both are accepted-divergence friction from the sim's account-state model,
# which the frictionless Engine reference has no equivalent of.
_UNDERFILL_CAUSES = frozenset({"liquidity", RejectionReason.INSUFFICIENT_BUYING_POWER.value})


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
    RejectionReason) or PARTIAL_FILL (exact binding ceiling -- "liquidity" or
    "insufficient_buying_power", ADR 0011 D2 follow-up)."""
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
                    "cause": e.payload["cause"],
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
    """Total missed notional from partial fills, as a fraction of initial
    capital -- both volume-capped ("liquidity") and cash-capped
    ("insufficient_buying_power") underfills, per ADR 0011 D2's follow-up
    distinguishing the two causes in the audit while still treating both as
    the same class of accepted, sim-only friction. This is a FRICTION
    MAGNITUDE, not an estimated return contribution -- ADR 0011 D1 folds it
    into the residual as-is, deliberately not attempting a counterfactual
    "what would those shares have earned"."""
    gap = Decimal(
        str(order_audit.loc[order_audit["cause"].isin(_UNDERFILL_CAUSES), "notional_gap"].sum())
    )
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


def _unliquidated_residual_cost_line(sim: SimResult, cost_model: TaiwanCostModel) -> WaterfallLine:
    """Hypothetical cost of liquidating whatever is still open at sim.end.

    liquidate_at_end defaults to False, and even True cannot guarantee a
    close -- a last-day halt/suspension still rejects the sell, and a thin
    volume cap can leave a partial remainder. The frictionless Engine
    reference has no such failure mode: its own final bar unconditionally
    sells every open position (engine.py's is_last branch), so
    reference.final_equity is always net of a real liquidation's costs.
    Comparing sim.final_equity (gross mark-to-market on anything still
    open) against that directly is gross-vs-net, not sim-vs-reference
    friction. This line prices what a real exit would have cost -- via
    cost_model.sell_costs at the last known price -- without the sim ever
    submitting a sell order that could itself fail."""
    if sim.positions_daily.empty:
        return WaterfallLine(
            name="unliquidated_residual_cost",
            category="accepted_divergence",
            value=Decimal(0),
            expected_sign="nonnegative",
            tolerance=Decimal("0.01"),
            ok=True,
        )
    last_date = sim.positions_daily.index.get_level_values("date").max()
    open_rows = sim.positions_daily.xs(last_date, level="date")

    total_cost = Decimal(0)
    for ticker, row in open_rows.iterrows():
        shares = int(row["shares"])
        if shares == 0:
            continue
        market_value = Decimal(str(row["market_value"]))
        reference_price = float(market_value) / shares
        fill_price, commission, tax = cost_model.sell_costs(ticker, shares, reference_price)
        net_proceeds = (
            Decimal(str(fill_price)) * shares - Decimal(str(commission)) - Decimal(str(tax))
        )
        total_cost += market_value - net_proceeds

    value = total_cost / sim.initial_capital
    return WaterfallLine(
        name="unliquidated_residual_cost",
        category="accepted_divergence",
        value=value,
        expected_sign="nonnegative",
        tolerance=Decimal("0.01"),
        ok=(value >= 0),
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
    sim: SimResult,
    reference: BacktestResult,
    liquidity: WaterfallLine,
    float_line: WaterfallLine,
    unliquidated: WaterfallLine,
) -> WaterfallLine:
    sim_return = sim.final_equity / sim.initial_capital - 1
    ref_return = Decimal(str(reference.final_equity / reference.initial_capital - 1))
    total_gap = sim_return - ref_return
    value = total_gap - liquidity.value - float_line.value - unliquidated.value
    tolerance = Decimal("0.0005")
    return WaterfallLine(
        name="raw_vs_adjusted",
        category="tripwire",
        value=value,
        expected_sign="zero",
        tolerance=tolerance,
        ok=(abs(value) <= tolerance),
    )


def generate_reconcile_report(
    sim: SimResult, reference: BacktestResult, cost_model: TaiwanCostModel = TaiwanCostModel()
) -> ReconciliationReport:
    events = list(EventLog(sim.event_log_path))

    headline = _headline_diff(sim, reference)
    order_audit = _build_order_audit(events)

    liquidity_line = _liquidity_underfill_line(sim, order_audit)
    float_line = _float_reliance_line(sim)
    slippage_line = _slippage_line(sim, reference)
    unliquidated_line = _unliquidated_residual_cost_line(sim, cost_model)
    raw_vs_adjusted_line = _raw_vs_adjusted_line(
        sim, reference, liquidity_line, float_line, unliquidated_line
    )

    waterfall = [slippage_line, raw_vs_adjusted_line, liquidity_line, float_line, unliquidated_line]

    return ReconciliationReport(
        sim_strategy=sim.strategy_name,
        reference_strategy=reference.strategy_name,
        headline=headline,
        waterfall=waterfall,
        order_audit=order_audit,
        passed=all(line.ok for line in waterfall if line.category == "tripwire"),
    )
