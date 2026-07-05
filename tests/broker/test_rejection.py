from datetime import date
from decimal import Decimal

import pytest

from twstock.broker.events import ActionType, Bar, CorporateAction
from twstock.broker.rejection import (
    DerivedPriceLimits,
    PriceLimitProvider,
    RejectionReason,
    SuspensionCalendar,
    classify_missing_bar,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _bar(
    ticker: str = "2330",
    d: date = date(2026, 1, 1),
    close: str = "100.00",
    actions: tuple[CorporateAction, ...] = (),
) -> Bar:
    price = Decimal(close)
    return Bar(
        ticker=ticker,
        date=d,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000_000,
        corporate_actions=actions,
    )


def _stock_split(ex_date: date, ratio: float | None) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.STOCK_SPLIT,
        ex_date=ex_date,
        cash_amount=None,
        ratio=ratio,
        pay_date=None,
        subscription_price=None,
    )


def _cash_dividend(ex_date: date, cash_amount: str | None) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.CASH_DIVIDEND,
        ex_date=ex_date,
        cash_amount=Decimal(cash_amount) if cash_amount is not None else None,
        ratio=None,
        pay_date=None,
        subscription_price=None,
    )


def _stock_dividend(ex_date: date, ratio: float | None) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.STOCK_DIVIDEND,
        ex_date=ex_date,
        cash_amount=None,
        ratio=ratio,
        pay_date=None,
        subscription_price=None,
    )


def _cash_and_stock_dividend(
    ex_date: date, cash_amount: str | None, ratio: float | None
) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.CASH_AND_STOCK_DIVIDEND,
        ex_date=ex_date,
        cash_amount=Decimal(cash_amount) if cash_amount is not None else None,
        ratio=ratio,
        pay_date=None,
        subscription_price=None,
    )


def _rights_issue(ex_date: date) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.RIGHTS_ISSUE,
        ex_date=ex_date,
        cash_amount=None,
        ratio=1.0,
        pay_date=None,
        subscription_price=Decimal("10"),
    )


def _capital_reduction(ex_date: date, ratio: float = 0.5) -> CorporateAction:
    return CorporateAction(
        action_type=ActionType.CAPITAL_REDUCTION,
        ex_date=ex_date,
        cash_amount=None,
        ratio=ratio,
        pay_date=None,
        subscription_price=None,
    )


def _band_from_reference(reference: str) -> tuple[Decimal, Decimal] | None:
    """Drive DerivedPriceLimits through two plain bars so `reference`'s
    day-1 close becomes the cached reference price for day 2, with no
    corporate action involved. Used to test the tick-rounding policy in
    band() in isolation from the ex-day adjustment formulas."""
    dpl = DerivedPriceLimits()
    dpl.observe(_bar(d=date(2026, 1, 1), close=reference))
    dpl.observe(_bar(d=date(2026, 1, 2), close=reference))
    return dpl.band("2330", date(2026, 1, 2))


# ── PriceLimitProvider ABC ───────────────────────────────────────────────────


class TestPriceLimitProviderIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            PriceLimitProvider()


# ── observe()/band() mechanics ───────────────────────────────────────────────


class TestObserveAndBandMechanics:
    def test_first_bar_for_ticker_has_no_band(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        assert dpl.band("2330", date(2026, 1, 1)) is None

    def test_band_returns_none_for_ticker_never_observed(self):
        dpl = DerivedPriceLimits()
        assert dpl.band("9999", date(2026, 1, 1)) is None

    def test_second_bar_produces_band_from_first_bars_close(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        dpl.observe(_bar(d=date(2026, 1, 2), close="105.00"))
        # reference = day-1's close (100.00), unaffected by day-2's own close
        assert dpl.band("2330", date(2026, 1, 2)) == (Decimal("90.0"), Decimal("110.0"))

    def test_band_raises_when_queried_for_stale_date(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        dpl.observe(_bar(d=date(2026, 1, 2), close="105.00"))
        with pytest.raises(ValueError):
            dpl.band("2330", date(2026, 1, 1))

    def test_prior_close_rolls_forward_across_a_missing_bar(self):
        """A ticker suspended for a day (no Bar observed) must have its next
        real bar's reference computed off the last real close, not treat the
        gap as anything other than absence."""
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        # no observe() call for 2026-01-02 -- simulates a suspended/missing day
        dpl.observe(_bar(d=date(2026, 1, 3), close="130.00"))
        # reference must be 100.00 (day-1's close), not 130.00 (day-3's own close)
        assert dpl.band("2330", date(2026, 1, 3)) == (Decimal("90.0"), Decimal("110.0"))

    def test_band_is_independent_per_ticker(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(ticker="2330", d=date(2026, 1, 1), close="100.00"))
        dpl.observe(_bar(ticker="2317", d=date(2026, 1, 1), close="200.00"))
        dpl.observe(_bar(ticker="2330", d=date(2026, 1, 2), close="100.00"))
        dpl.observe(_bar(ticker="2317", d=date(2026, 1, 2), close="200.00"))
        assert dpl.band("2330", date(2026, 1, 2)) == (Decimal("90.0"), Decimal("110.0"))
        assert dpl.band("2317", date(2026, 1, 2)) == (Decimal("180.0"), Decimal("220.0"))


# ── tick-rounded band ─────────────────────────────────────────────────────────


class TestTickRoundedBand:
    """Verified against real TWSE data: the 2330 dividend reference/limit
    triples (FinMind TaiwanStockDividendResult) and the 2327 stock-split
    reference/limit triple (FinMind TaiwanStockSplitPrice, ADR 0007) --
    0050 is deliberately excluded as a fixture since it ticks on a
    different (ETF-specific) schedule than ordinary equities. The sub-$100
    cases are hand/script-computed against the tick table, which is itself
    validated against 100,000+ real closes across the Top 50 (see the
    conversation's empirical check); they are not independently sourced
    market events.
    """

    @pytest.mark.parametrize(
        "reference, expected_down, expected_up",
        [
            ("8.005", "7.21", "8.80"),  # sub-$10 tier, tick 0.01
            ("30.03", "27.05", "33.00"),  # $10-50 tier, tick 0.05
            ("70.07", "63.1", "77.0"),  # $50-100 tier, tick 0.1
            ("136.50", "123.0", "150.0"),  # real: 2327 4:1 split reference
            ("508.25", "457.5", "559"),  # real: 2330 cash dividend, crosses 500 boundary
            ("965.49", "869", "1060"),  # real: 2330 cash dividend, crosses 1000 boundary
            ("1838.99", "1660", "2020"),  # real: 2330 cash dividend, both bounds >= 1000
        ],
    )
    def test_tick_rounded_band_matches_verified_values(self, reference, expected_down, expected_up):
        band = _band_from_reference(reference)
        assert band == (Decimal(expected_down), Decimal(expected_up))


# ── corporate-action reference-price adjustment ──────────────────────────────


class TestCorporateActionAdjustment:
    """_ex_day_reference is exercised only through observe()/band(), matching
    this codebase's convention of testing private helpers via their public
    entry point (see adjustments.py's _compute_factor, tested only through
    adjust())."""

    def test_stock_split_scales_reference_by_ratio(self):
        """Real event: 2327 (Yageo) 4:1 split, 2025-08-25 (ADR 0007)."""
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(ticker="2327", d=date(2025, 8, 22), close="546.00"))
        dpl.observe(
            _bar(
                ticker="2327",
                d=date(2025, 8, 25),
                close="136.50",
                actions=(_stock_split(date(2025, 8, 25), 0.25),),
            )
        )
        assert dpl.band("2327", date(2025, 8, 25)) == (Decimal("123.0"), Decimal("150.0"))

    def test_cash_dividend_subtracts_amount_from_reference(self):
        """Real event: 2330 (TSMC) cash dividend, ex-date 2023-03-16."""
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(ticker="2330", d=date(2023, 3, 15), close="511.00"))
        dpl.observe(
            _bar(
                ticker="2330",
                d=date(2023, 3, 16),
                close="505.00",
                actions=(_cash_dividend(date(2023, 3, 16), "2.75"),),
            )
        )
        assert dpl.band("2330", date(2023, 3, 16)) == (Decimal("457.5"), Decimal("559"))

    def test_stock_dividend_divides_reference_by_one_plus_ratio(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="110"))
        dpl.observe(
            _bar(
                d=date(2026, 1, 2),
                close="100",
                actions=(_stock_dividend(date(2026, 1, 2), 0.10),),
            )
        )
        # reference = 110 / 1.10 = 100
        assert dpl.band("2330", date(2026, 1, 2)) == (Decimal("90.0"), Decimal("110.0"))

    def test_combined_cash_and_stock_dividend_applies_single_formula(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="220"))
        dpl.observe(
            _bar(
                d=date(2026, 1, 2),
                close="207.62",
                actions=(_cash_and_stock_dividend(date(2026, 1, 2), "2", 0.05),),
            )
        )
        # reference = (220 - 2) / 1.05 = 207.619047...
        assert dpl.band("2330", date(2026, 1, 2)) == (Decimal("187.0"), Decimal("228.0"))

    def test_no_corporate_action_leaves_reference_unchanged(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        dpl.observe(_bar(d=date(2026, 1, 2), close="100.00", actions=()))
        assert dpl.band("2330", date(2026, 1, 2)) == (Decimal("90.0"), Decimal("110.0"))


# ── corporate-action validation ───────────────────────────────────────────────


class TestCorporateActionValidation:
    def test_stock_split_with_none_ratio_raises(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(_bar(d=date(2026, 1, 2), actions=(_stock_split(date(2026, 1, 2), None),)))

    def test_stock_split_with_zero_ratio_raises(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(_bar(d=date(2026, 1, 2), actions=(_stock_split(date(2026, 1, 2), 0),)))

    def test_stock_split_with_negative_ratio_raises(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(_bar(d=date(2026, 1, 2), actions=(_stock_split(date(2026, 1, 2), -0.5),)))

    def test_cash_dividend_with_none_cash_amount_raises(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(_bar(d=date(2026, 1, 2), actions=(_cash_dividend(date(2026, 1, 2), None),)))

    def test_stock_dividend_with_zero_ratio_raises(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(_bar(d=date(2026, 1, 2), actions=(_stock_dividend(date(2026, 1, 2), 0),)))

    def test_combined_dividend_missing_cash_amount_raises(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(
                _bar(
                    d=date(2026, 1, 2),
                    actions=(_cash_and_stock_dividend(date(2026, 1, 2), None, 0.05),),
                )
            )

    def test_combined_dividend_invalid_ratio_raises(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(
                _bar(
                    d=date(2026, 1, 2),
                    actions=(_cash_and_stock_dividend(date(2026, 1, 2), "2", 0),),
                )
            )

    def test_rights_issue_raises_as_unmodeled(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(_bar(d=date(2026, 1, 2), actions=(_rights_issue(date(2026, 1, 2)),)))

    def test_capital_reduction_raises_as_unmodeled(self):
        dpl = DerivedPriceLimits()
        dpl.observe(_bar(d=date(2026, 1, 1), close="100.00"))
        with pytest.raises(ValueError):
            dpl.observe(_bar(d=date(2026, 1, 2), actions=(_capital_reduction(date(2026, 1, 2)),)))


# ── SuspensionCalendar ─────────────────────────────────────────────────────────


class TestSuspensionCalendar:
    """Step 6 ships the graceful-degradation stub only; the real TWTAWU-backed
    calendar is step 13."""

    def test_stub_always_reports_not_halted(self):
        cal = SuspensionCalendar()
        assert cal.is_halted("2330", date(2026, 6, 1)) is False
        assert cal.is_halted("2330", date(2020, 1, 1)) is False
        assert cal.is_halted("9999", date(2026, 6, 1)) is False


# ── classify_missing_bar ───────────────────────────────────────────────────────


class TestClassifyMissingBar:
    def test_bar_present_returns_none_regardless_of_calendar(self):
        result = classify_missing_bar(
            "2330", date(2026, 6, 2), has_bar=True, suspension_calendar=SuspensionCalendar()
        )
        assert result is None

    def test_non_trading_day_returns_none(self):
        # 2026-06-06 is a Saturday; not an XTAI session, so no bar is expected.
        result = classify_missing_bar(
            "2330", date(2026, 6, 6), has_bar=False, suspension_calendar=SuspensionCalendar()
        )
        assert result is None

    def test_missing_bar_on_trading_day_defaults_to_no_market_data(self):
        # 2026-06-02 is a confirmed XTAI trading session (Tuesday).
        result = classify_missing_bar(
            "2330", date(2026, 6, 2), has_bar=False, suspension_calendar=SuspensionCalendar()
        )
        assert result == RejectionReason.NO_MARKET_DATA

    def test_confirmed_halt_upgrades_to_suspension(self):
        class _AlwaysHalted(SuspensionCalendar):
            def is_halted(self, ticker: str, t: date) -> bool:
                return True

        result = classify_missing_bar(
            "2330", date(2026, 6, 2), has_bar=False, suspension_calendar=_AlwaysHalted()
        )
        assert result == RejectionReason.SUSPENSION

    def test_never_silently_a_halt_without_calendar_evidence(self):
        """D12: absent confirmed evidence, always the weaker NO_MARKET_DATA
        claim -- never SUSPENSION, no matter how many consecutive missing
        bars are observed."""
        for _ in range(30):
            result = classify_missing_bar(
                "2330", date(2026, 6, 2), has_bar=False, suspension_calendar=SuspensionCalendar()
            )
            assert result == RejectionReason.NO_MARKET_DATA
