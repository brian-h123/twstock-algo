"""Unit tests for twstock.broker.reconcile.

Coverage so far (module is mid-build -- see PHASE_5.md step 14, ADR 0011):
  - _build_order_audit: schema, ticker population for both REJECT and
    PARTIAL_FILL (ADR 0011), null notional_gap for REJECT, empty-input schema
  - _headline_diff, _liquidity_underfill_line, _float_reliance_line,
    _slippage_line, _raw_vs_adjusted_line: each in isolation, on hand-built
    SimResult/BacktestResult fixtures
  - generate_reconcile_report: end-to-end wiring on a minimal real EventLog
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from twstock.backtest.result import BacktestResult
from twstock.broker.events import BrokerEvent, EventKind, EventLog, OrderId
from twstock.broker.reconcile import (
    WaterfallLine,
    _build_order_audit,
    _float_reliance_line,
    _headline_diff,
    _liquidity_underfill_line,
    _raw_vs_adjusted_line,
    _slippage_line,
    generate_reconcile_report,
)
from twstock.broker.rejection import RejectionReason
from twstock.broker.result import SimResult

_DATE = date(2026, 6, 1)

_AUDIT_COLUMNS = ["order_id", "date", "ticker", "cause", "requested", "filled", "notional_gap"]

_TRADE_COLUMNS = [
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


# ─── shared fixtures ─────────────────────────────────────────────────────────


def _equity_curve(values: list[float], start: date = date(2026, 1, 1)) -> pd.Series:
    idx = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(values, index=idx)


def _trade_row(
    ts: date, ticker: str, side: str, shares: int, fill_price: float, commission=0.0, tax=0.0
) -> dict:
    notional = shares * fill_price
    return {
        "timestamp": pd.Timestamp(ts),
        "ticker": ticker,
        "side": side,
        "shares": shares,
        "fill_price": fill_price,
        "commission": commission,
        "tax": tax,
        "notional": notional,
        "total_cost": commission + tax,
    }


def _trades_df(*rows: dict) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_TRADE_COLUMNS)
    return pd.DataFrame(list(rows))


_EMPTY_POSITIONS = pd.DataFrame(
    columns=["shares", "avg_cost", "market_value"],
    index=pd.MultiIndex.from_tuples([], names=["date", "ticker"]),
)


def _sim_result(**overrides) -> SimResult:
    base = dict(
        strategy_name="test",
        tickers=("2330",),
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        initial_capital=Decimal("1000000"),
        final_equity=Decimal("1010000"),
        equity_curve=_equity_curve([1_000_000, 1_005_000, 1_002_000, 1_008_000, 1_010_000]),
        trades=_trades_df(
            _trade_row(date(2026, 1, 1), "2330", "buy", 1000, 100.0, commission=20),
            _trade_row(date(2026, 1, 6), "2330", "sell", 1000, 101.0, commission=20, tax=30),
        ),
        positions_daily=_EMPTY_POSITIONS,
        total_commission=Decimal("40"),
        total_tax=Decimal("30"),
        total_slippage_cost=Decimal("50"),
        settled_cash_curve=_equity_curve([1_000_000] * 5),
        unsettled_cash_curve=_equity_curve([0] * 5),
        event_log_path=Path("unused.jsonl"),
    )
    base.update(overrides)
    return SimResult(**base)


def _backtest_result(**overrides) -> BacktestResult:
    base = dict(
        strategy_name="test",
        tickers=("2330",),
        start=date(2026, 1, 1),
        end=date(2026, 1, 7),
        initial_capital=1_000_000.0,
        final_equity=1_010_000.0,
        equity_curve=_equity_curve([1_000_000, 1_005_000, 1_002_000, 1_008_000, 1_010_000]),
        trades=_trades_df(
            _trade_row(date(2026, 1, 1), "2330", "buy", 1000, 100.0, commission=20),
            _trade_row(date(2026, 1, 6), "2330", "sell", 1000, 101.0, commission=20, tax=30),
        ),
        positions_daily=_EMPTY_POSITIONS,
        total_commission=40.0,
        total_tax=30.0,
        total_slippage_cost=50.0,
    )
    base.update(overrides)
    return BacktestResult(**base)


def _submitted(order_id: str, quantity: int, ts: date = _DATE) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.SUBMITTED,
        order_id=OrderId(order_id),
        payload={"quantity": str(quantity)},
    )


def _reject(order_id: str, ticker: str, reason: RejectionReason, ts: date = _DATE) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.REJECT,
        order_id=OrderId(order_id),
        payload={"ticker": ticker, "reason": reason.value},
    )


def _partial_fill(
    order_id: str, ticker: str, side: str, shares: int, fill_price: Decimal, ts: date = _DATE
) -> BrokerEvent:
    return BrokerEvent(
        ts=ts,
        kind=EventKind.PARTIAL_FILL,
        order_id=OrderId(order_id),
        payload={
            "ticker": ticker,
            "side": side,
            "shares": str(shares),
            "fill_price": str(fill_price),
            "total_cost": "60",
        },
    )


def _fill(
    order_id: str, ticker: str, side: str, shares: int, fill_price: Decimal, ts: date = _DATE
):
    return BrokerEvent(
        ts=ts,
        kind=EventKind.FILL,
        order_id=OrderId(order_id),
        payload={
            "ticker": ticker,
            "side": side,
            "shares": str(shares),
            "fill_price": str(fill_price),
            "total_cost": "20",
        },
    )


# ─── _build_order_audit ─────────────────────────────────────────────────────


def test_reject_row_has_ticker_and_null_notional_gap() -> None:
    events = [_submitted("O1", 1000), _reject("O1", "2330", RejectionReason.PRICE_LIMIT_LOCK)]

    audit = _build_order_audit(events)

    assert list(audit.columns) == _AUDIT_COLUMNS
    assert len(audit) == 1
    row = audit.iloc[0]
    assert row["ticker"] == "2330"
    assert row["cause"] == RejectionReason.PRICE_LIMIT_LOCK.value
    assert row["requested"] == 1000
    assert row["filled"] == 0
    assert pd.isna(row["notional_gap"])


def test_partial_fill_row_has_ticker_and_computed_notional_gap() -> None:
    events = [_submitted("O2", 1000), _partial_fill("O2", "2330", "buy", 600, Decimal("100.05"))]

    audit = _build_order_audit(events)

    assert len(audit) == 1
    row = audit.iloc[0]
    assert row["ticker"] == "2330"
    assert row["cause"] == "liquidity"
    assert row["requested"] == 1000
    assert row["filled"] == 600
    assert row["notional_gap"] == (1000 - 600) * 100.05


def test_fully_filled_order_produces_no_audit_row() -> None:
    """A FILL (not PARTIAL_FILL) means nothing was left unfilled -- it must
    not appear in the audit at all."""
    events = [_submitted("O3", 500), _fill("O3", "2330", "buy", 500, Decimal("100"))]

    audit = _build_order_audit(events)

    assert audit.empty


def test_multiple_orders_produce_one_row_each() -> None:
    events = [
        _submitted("O1", 1000),
        _reject("O1", "2330", RejectionReason.NO_MARKET_DATA),
        _submitted("O2", 2000),
        _partial_fill("O2", "2317", "sell", 1500, Decimal("99.95")),
        _submitted("O3", 300),
        _fill("O3", "2454", "buy", 300, Decimal("500")),
    ]

    audit = _build_order_audit(events)

    assert len(audit) == 2
    assert set(audit["order_id"]) == {OrderId("O1"), OrderId("O2")}
    assert set(audit["ticker"]) == {"2330", "2317"}


def test_no_underfills_or_rejects_produces_empty_audit_with_full_schema() -> None:
    """A clean run (every order fully filled) must still yield an audit frame
    with the expected columns -- not a columnless empty DataFrame, which
    would raise a KeyError the moment a waterfall-line helper does
    order_audit["cause"] on it."""
    events = [_submitted("O1", 500), _fill("O1", "2330", "buy", 500, Decimal("100"))]

    audit = _build_order_audit(events)

    assert audit.empty
    assert list(audit.columns) == _AUDIT_COLUMNS


# ─── _headline_diff ─────────────────────────────────────────────────────────


def test_headline_diff_has_sim_reference_diff_columns() -> None:
    sim = _sim_result()
    reference = _backtest_result()

    headline = _headline_diff(sim, reference)

    assert list(headline.columns) == ["sim", "reference", "diff"]
    assert "total_return" in headline.index
    assert headline.loc["total_return", "diff"] == pytest.approx(
        headline.loc["total_return", "sim"] - headline.loc["total_return", "reference"]
    )


def test_headline_diff_is_zero_for_identical_sim_and_reference() -> None:
    """Same equity curve, same trades -- every metric's diff must land on 0."""
    sim = _sim_result()
    reference = _backtest_result()

    headline = _headline_diff(sim, reference)

    assert headline["diff"].fillna(0).abs().max() == pytest.approx(0, abs=1e-9)


# ─── _liquidity_underfill_line ──────────────────────────────────────────────


def test_liquidity_underfill_line_sums_only_liquidity_rows() -> None:
    sim = _sim_result(initial_capital=Decimal("1000000"))
    order_audit = pd.DataFrame(
        [
            {
                "order_id": OrderId("O1"),
                "date": _DATE,
                "ticker": "2330",
                "cause": "liquidity",
                "requested": 1000,
                "filled": 600,
                "notional_gap": 40_000.0,
            },
            {
                "order_id": OrderId("O2"),
                "date": _DATE,
                "ticker": "2317",
                "cause": RejectionReason.PRICE_LIMIT_LOCK.value,
                "requested": 500,
                "filled": 0,
                "notional_gap": None,
            },
        ]
    )

    line = _liquidity_underfill_line(sim, order_audit)

    assert line.value == Decimal("40000") / Decimal("1000000")
    assert line.category == "accepted_divergence"
    assert line.ok is True


def test_liquidity_underfill_line_zero_when_no_liquidity_rows() -> None:
    sim = _sim_result(initial_capital=Decimal("1000000"))
    order_audit = pd.DataFrame(columns=_AUDIT_COLUMNS)

    line = _liquidity_underfill_line(sim, order_audit)

    assert line.value == Decimal(0)
    assert line.ok is True


# ─── _float_reliance_line ───────────────────────────────────────────────────


def test_float_reliance_line_zero_when_unsettled_curve_is_all_zero() -> None:
    sim = _sim_result(
        initial_capital=Decimal("1000000"), unsettled_cash_curve=_equity_curve([0, 0, 0, 0, 0])
    )

    line = _float_reliance_line(sim)

    assert line.value == Decimal(0)
    assert line.ok is True


def test_float_reliance_line_flags_large_unsettled_average() -> None:
    """Average unsettled cash of 50,000 against 1,000,000 capital is 5% --
    well past the 1% tolerance -- ok must be False."""
    sim = _sim_result(
        initial_capital=Decimal("1000000"),
        unsettled_cash_curve=_equity_curve([50_000] * 5),
    )

    line = _float_reliance_line(sim)

    assert line.value == Decimal("0.05")
    assert line.ok is False


# ─── _slippage_line ─────────────────────────────────────────────────────────


def test_slippage_line_zero_when_rates_match() -> None:
    sim = _sim_result(
        total_slippage_cost=Decimal("50"),
        trades=_trades_df(_trade_row(date(2026, 1, 1), "2330", "buy", 1000, 100.0)),
    )
    reference = _backtest_result(
        total_slippage_cost=50.0,
        trades=_trades_df(_trade_row(date(2026, 1, 1), "2330", "buy", 1000, 100.0)),
    )

    line = _slippage_line(sim, reference)

    assert line.value == Decimal(0)
    assert line.category == "tripwire"
    assert line.ok is True


def test_slippage_line_flags_mismatched_effective_rates() -> None:
    """Same notional traded, but sim's slippage cost is double reference's
    rate -- the effective-rate gap must exceed the tight tripwire band."""
    sim = _sim_result(
        total_slippage_cost=Decimal("100"),
        trades=_trades_df(_trade_row(date(2026, 1, 1), "2330", "buy", 1000, 100.0)),
    )
    reference = _backtest_result(
        total_slippage_cost=50.0,
        trades=_trades_df(_trade_row(date(2026, 1, 1), "2330", "buy", 1000, 100.0)),
    )

    line = _slippage_line(sim, reference)

    assert line.ok is False


# ─── _raw_vs_adjusted_line ──────────────────────────────────────────────────


def test_raw_vs_adjusted_line_is_the_residual_after_measured_lines() -> None:
    sim = _sim_result(initial_capital=Decimal("1000000"), final_equity=Decimal("1100000"))
    reference = _backtest_result(initial_capital=1_000_000.0, final_equity=1_050_000.0)
    liquidity = WaterfallLine(
        name="liquidity_underfill",
        category="accepted_divergence",
        value=Decimal("0.02"),
        expected_sign="nonnegative",
        tolerance=Decimal("0.05"),
        ok=True,
    )
    float_line = WaterfallLine(
        name="float_reliance",
        category="accepted_divergence",
        value=Decimal("0.01"),
        expected_sign="zero",
        tolerance=Decimal("0.01"),
        ok=True,
    )

    line = _raw_vs_adjusted_line(sim, reference, liquidity, float_line)

    # total_gap = 0.10 - 0.05 = 0.05; residual = 0.05 - 0.02 - 0.01 = 0.02
    assert float(line.value) == pytest.approx(0.02, abs=1e-9)
    assert line.category == "tripwire"
    assert line.ok is False  # 0.02 exceeds the 5bp (0.0005) band


def test_raw_vs_adjusted_line_ok_when_residual_within_band() -> None:
    sim = _sim_result(initial_capital=Decimal("1000000"), final_equity=Decimal("1080000"))
    reference = _backtest_result(initial_capital=1_000_000.0, final_equity=1_050_000.0)
    liquidity = WaterfallLine(
        name="liquidity_underfill",
        category="accepted_divergence",
        value=Decimal("0.02"),
        expected_sign="nonnegative",
        tolerance=Decimal("0.05"),
        ok=True,
    )
    float_line = WaterfallLine(
        name="float_reliance",
        category="accepted_divergence",
        value=Decimal("0.01"),
        expected_sign="zero",
        tolerance=Decimal("0.01"),
        ok=True,
    )

    line = _raw_vs_adjusted_line(sim, reference, liquidity, float_line)

    # total_gap = 0.08 - 0.05 = 0.03; residual = 0.03 - 0.02 - 0.01 = 0.00
    assert float(line.value) == pytest.approx(0.0, abs=1e-9)
    assert line.ok is True


# ─── generate_reconcile_report ──────────────────────────────────────────────


def test_generate_reconcile_report_passes_for_a_clean_matching_run(tmp_path) -> None:
    """No underfills/rejects (empty event log) and identical sim/reference
    fixtures -- every tripwire line should read as ok."""
    log_path = tmp_path / "session.jsonl"
    log_path.touch()

    sim = _sim_result(event_log_path=log_path)
    reference = _backtest_result()

    report = generate_reconcile_report(sim, reference)

    assert report.sim_strategy == "test"
    assert report.reference_strategy == "test"
    assert report.order_audit.empty
    assert list(report.order_audit.columns) == _AUDIT_COLUMNS
    assert report.passed is True


def test_generate_reconcile_report_fails_when_slippage_rate_diverges(tmp_path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.touch()

    sim = _sim_result(event_log_path=log_path, total_slippage_cost=Decimal("500"))
    reference = _backtest_result()

    report = generate_reconcile_report(sim, reference)

    slippage_line = next(line for line in report.waterfall if line.name == "slippage_diff")
    assert slippage_line.ok is False
    assert report.passed is False


def test_generate_reconcile_report_order_audit_reflects_real_event_log(tmp_path) -> None:
    """Uses a real EventLog with an actual REJECT event, confirming
    generate_reconcile_report reads sim.event_log_path end-to-end rather
    than being handed events directly."""
    log_path = tmp_path / "session.jsonl"
    log = EventLog(path=log_path)
    log.append(
        BrokerEvent(
            ts=_DATE, kind=EventKind.SUBMITTED, order_id=OrderId("O1"), payload={"quantity": "500"}
        )
    )
    log.append(
        BrokerEvent(
            ts=_DATE,
            kind=EventKind.REJECT,
            order_id=OrderId("O1"),
            payload={"ticker": "2330", "reason": RejectionReason.PRICE_LIMIT_LOCK.value},
        )
    )

    sim = _sim_result(event_log_path=log_path)
    reference = _backtest_result()

    report = generate_reconcile_report(sim, reference)

    assert len(report.order_audit) == 1
    assert report.order_audit.iloc[0]["ticker"] == "2330"
    assert report.order_audit.iloc[0]["cause"] == RejectionReason.PRICE_LIMIT_LOCK.value
