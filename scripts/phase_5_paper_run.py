"""Phase 5 demonstrator: paper-trading simulator replay + connectivity smoke test.

Runs a Phase 4 strategy through the paper-trading simulator under the
canonical pessimistic execution preset (VolumeParticipationFiller +
NettingSettlement), reconciles it against the frictionless backtester, runs
the cross-sectional momentum strategy in both settlement modes to quantify
float reliance (ADR 0009 D5), and finishes with a short live-data
connectivity smoke test. Mirrors PHASE_5.md Section 1.

Usage:
    uv run python scripts/phase_5_paper_run.py
    uv run python scripts/phase_5_paper_run.py --start 2024-01-01 --end 2024-12-31
    uv run python scripts/phase_5_paper_run.py --skip-momentum --skip-live
    uv run python scripts/phase_5_paper_run.py --skip-single-asset --skip-momentum  # live smoke
"""

import argparse
from datetime import date
from decimal import Decimal

import exchange_calendars as xcals
import pandas as pd

from twstock.backtest import Engine, TaiwanCostModel
from twstock.backtest.allocator import EqualWeightTopN
from twstock.backtest.rebalance import Monthly
from twstock.backtest.result import BacktestResult
from twstock.broker import (
    LiveFeed,
    NettingSettlement,
    PaperSession,
    ReplayFeed,
    SettledCashOnly,
    SimResult,
    StaleLiveFeedError,
    VolumeParticipationFiller,
    generate_reconcile_report,
)
from twstock.config import BACKTEST_CAPITAL_NTD, MAX_POSITION_PCT
from twstock.strategies.cross_sectional_momentum import CrossSectionalMomentum
from twstock.strategies.sma_crossover import SmaCrossover
from twstock.universe import get_top50_universe

DEFAULT_START = date(2021, 1, 1)
DEFAULT_END = date(2025, 12, 31)
SINGLE_ASSET_TICKER = "2330"
MOMENTUM_LOOKBACK = 252
MOMENTUM_SKIP = 21
MOMENTUM_TOP_N = 10
PARTICIPATION_RATE = 0.05
LIVE_SMOKE_SESSIONS = 14

_XTAI = xcals.get_calendar("XTAI")


def _n_sessions_before(end: date, n: int) -> date:
    """The XTAI trading session `n` sessions strictly before `end`.

    `end` need not itself be a session (e.g. `date.today()` on a weekend) --
    resolved to the nearest prior session first, since `sessions_window`
    requires its anchor to already be one.
    """
    anchor = _XTAI.date_to_session(pd.Timestamp(end), direction="previous")
    sessions = _XTAI.sessions_window(anchor, -n - 1)
    return sessions[0].date()


def run_single_asset_replay(start: date, end: date) -> tuple[SimResult, BacktestResult]:
    """SMA(20,60) on the reference ticker under the pessimistic preset.

    Returns the pessimistic sim result alongside the frictionless Engine
    reference over the same window, for reconciliation.
    """
    print(f"\n=== Single-asset SMA replay: {SINGLE_ASSET_TICKER}, {start} -> {end} ===")

    sim_session = PaperSession(
        strategy=SmaCrossover(fast=20, slow=60),
        tickers=[SINGLE_ASSET_TICKER],
        feed=ReplayFeed(start=start, end=end),
        capital=Decimal(str(BACKTEST_CAPITAL_NTD)),
        cost_model=TaiwanCostModel(),
        fill_model=VolumeParticipationFiller(participation_rate=PARTICIPATION_RATE),
        settlement=NettingSettlement(),
        max_position_pct=MAX_POSITION_PCT,
    )
    sim_result = sim_session.run()
    print(f"  sim (pessimistic)   final equity: {sim_result.final_equity:,.0f}")

    reference = Engine(
        capital=BACKTEST_CAPITAL_NTD,
        cost_model=TaiwanCostModel(),
        max_position_pct=MAX_POSITION_PCT,
    ).run(
        strategy=SmaCrossover(fast=20, slow=60),
        tickers=[SINGLE_ASSET_TICKER],
        start=start,
        end=end,
    )
    print(f"  reference (frictionless) final equity: {reference.final_equity:,.0f}")

    return sim_result, reference


def print_reconciliation(sim_result: SimResult, reference: BacktestResult, label: str) -> None:
    """Run generate_reconcile_report and print the headline diff + waterfall.

    `label` distinguishes which leg this is in the printed output -- the
    script calls this more than once (single-asset, cross-sectional), and
    the section headers would otherwise be indistinguishable.
    """
    report = generate_reconcile_report(sim=sim_result, reference=reference)

    print(f"\n=== Reconciliation ({label}): headline diff (sim vs frictionless reference) ===")
    print(report.headline.to_string())

    print(f"\n=== Reconciliation ({label}): attribution waterfall ===")
    for line in report.waterfall:
        status = "OK" if line.ok else "FAIL"
        print(
            f"  [{status:4s}] {line.name:<18s} {line.category:<20s} "
            f"value={line.value: .6f}  expected_sign={line.expected_sign}  "
            f"tolerance={line.tolerance}"
        )
    print(f"\n  reconciliation passed (tripwires only): {report.passed}")

    if not report.order_audit.empty:
        # REJECT rows always have filled == 0; PARTIAL_FILL rows never do
        # (ADR 0011 D2 follow-up: "liquidity" or "insufficient_buying_power").
        underfilled = report.order_audit[report.order_audit["filled"] > 0]
        rejected = report.order_audit[report.order_audit["filled"] == 0]
        n_liquidity = (underfilled["cause"] == "liquidity").sum()
        n_cash = (underfilled["cause"] == "insufficient_buying_power").sum()
        print(
            f"  order-level audit: {len(report.order_audit)} underfilled/rejected orders "
            f"({n_liquidity} liquidity-capped, {n_cash} cash-capped, {len(rejected)} rejected)"
        )
    else:
        print("  order-level audit: no underfilled or rejected orders")


def run_cross_sectional_momentum_both_modes(start: date, end: date) -> None:
    """CrossSectionalMomentum over the Top 50, netting vs settled-cash-only,
    netting reconciled against the frictionless Engine reference.

    Both settlement modes run under identical friction otherwise; comparing
    their unsettled-cash curves quantifies float reliance (ADR 0009 D5) --
    the float line is expected near zero under NettingSettlement and
    material under SettledCashOnly. NettingSettlement is the canonical
    Phase 5 preset (CLAUDE.md), so it's the leg reconciled against Engine.
    """
    print(f"\n=== Cross-sectional momentum: Top 50, {start} -> {end} ===")
    universe = get_top50_universe()

    def _build(settlement: NettingSettlement | SettledCashOnly) -> PaperSession:
        return PaperSession(
            strategy=CrossSectionalMomentum(lookback=MOMENTUM_LOOKBACK, skip=MOMENTUM_SKIP),
            tickers=universe,
            feed=ReplayFeed(start=start, end=end),
            capital=Decimal(str(BACKTEST_CAPITAL_NTD)),
            cost_model=TaiwanCostModel(),
            fill_model=VolumeParticipationFiller(participation_rate=PARTICIPATION_RATE),
            settlement=settlement,
            allocator=EqualWeightTopN(n=MOMENTUM_TOP_N),
            schedule=Monthly(),
            max_position_pct=MAX_POSITION_PCT,
        )

    netting_result = _build(NettingSettlement()).run()
    settled_result = _build(SettledCashOnly()).run()

    print(f"  netting          final equity: {netting_result.final_equity:,.0f}")
    print(f"  settled-cash-only final equity: {settled_result.final_equity:,.0f}")
    print(
        f"  netting avg unsettled cash:          "
        f"{netting_result.unsettled_cash_curve.mean():,.0f} (expect ~0)"
    )
    print(
        f"  settled-cash-only avg unsettled cash: "
        f"{settled_result.unsettled_cash_curve.mean():,.0f} (quantifies float reliance)"
    )

    reference = Engine(
        capital=BACKTEST_CAPITAL_NTD,
        cost_model=TaiwanCostModel(),
        max_position_pct=MAX_POSITION_PCT,
    ).run(
        strategy=CrossSectionalMomentum(lookback=MOMENTUM_LOOKBACK, skip=MOMENTUM_SKIP),
        tickers=universe,
        start=start,
        end=end,
        allocator=EqualWeightTopN(n=MOMENTUM_TOP_N),
        schedule=Monthly(),
    )
    print(f"  reference (frictionless) final equity: {reference.final_equity:,.0f}")
    print_reconciliation(netting_result, reference, label="cross-sectional momentum, netting")


def run_live_smoke_test(sessions: int) -> None:
    """~`sessions`-session live connectivity smoke test.

    Not a correctness/perf gate (PHASE_5.md D14/§6.2) -- only asserts the
    full session loop runs against fresh local data without crashing. A
    stale local store (daily backfill hasn't run yet) is reported, not
    raised, so it doesn't block the rest of the demonstrator.
    """
    start = _n_sessions_before(date.today(), sessions)
    print(f"\n=== Live connectivity smoke test: {SINGLE_ASSET_TICKER}, {start} onward ===")

    try:
        session = PaperSession(
            strategy=SmaCrossover(fast=20, slow=60),
            tickers=[SINGLE_ASSET_TICKER],
            feed=LiveFeed(start=start),
            capital=Decimal(str(BACKTEST_CAPITAL_NTD)),
            cost_model=TaiwanCostModel(),
            fill_model=VolumeParticipationFiller(participation_rate=PARTICIPATION_RATE),
            settlement=NettingSettlement(),
            max_position_pct=MAX_POSITION_PCT,
        )
        result = session.run()
    except StaleLiveFeedError as exc:
        print(f"  skipped -- local store is stale: {exc}")
        return

    print(f"  completed: {len(result.equity_curve)} bars processed without crashing")
    print(f"  final equity: {result.final_equity:,.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 5 paper-trading simulator demonstrator (PHASE_5.md Section 1)."
    )
    parser.add_argument("--start", default=str(DEFAULT_START), help="Replay start date YYYY-MM-DD")
    parser.add_argument("--end", default=str(DEFAULT_END), help="Replay end date YYYY-MM-DD")
    parser.add_argument(
        "--skip-single-asset",
        action="store_true",
        help="Skip the single-asset SMA replay section",
    )
    parser.add_argument(
        "--skip-momentum",
        action="store_true",
        help="Skip the cross-sectional momentum section (slow: 50 tickers x both modes)",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip the live connectivity smoke test",
    )
    parser.add_argument(
        "--live-sessions",
        type=int,
        default=LIVE_SMOKE_SESSIONS,
        help="Number of trailing XTAI sessions for the live smoke test",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if not args.skip_single_asset:
        sim_result, reference = run_single_asset_replay(start, end)
        print_reconciliation(sim_result, reference, label="single-asset SMA")
    else:
        print("\n=== Single-asset SMA replay: skipped (--skip-single-asset) ===")

    if not args.skip_momentum:
        run_cross_sectional_momentum_both_modes(start, end)
    else:
        print("\n=== Cross-sectional momentum: skipped (--skip-momentum) ===")

    if not args.skip_live:
        run_live_smoke_test(args.live_sessions)
    else:
        print("\n=== Live connectivity smoke test: skipped (--skip-live) ===")


if __name__ == "__main__":
    main()
