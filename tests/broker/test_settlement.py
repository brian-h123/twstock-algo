from datetime import date, timedelta
from decimal import Decimal

from twstock.broker.events import Side
from twstock.broker.settlement import (
    DatedCashFlow,
    DatedLedger,
    Fill,
    InstantSettlement,
    NettingSettlement,
    SettledCashOnly,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _buy(shares: int, price: str, cost: str = "0") -> Fill:
    return Fill(Side.BUY, shares, Decimal(price), Decimal(cost))


def _sell(shares: int, price: str, cost: str = "0") -> Fill:
    return Fill(Side.SELL, shares, Decimal(price), Decimal(cost))


def _ledger(settled: str = "0") -> DatedLedger:
    ledger = DatedLedger()
    ledger.settled_cash = Decimal(settled)
    return ledger


def _total(ledger: DatedLedger) -> Decimal:
    return ledger.settled_cash + sum((cf.amount for cf in ledger.pending), Decimal(0))


# ── DatedLedger ───────────────────────────────────────────────────────────────


class TestDatedLedger:
    def test_mature_moves_due_flows_to_settled_cash(self):
        ledger = _ledger("1000")
        ledger.pending.append(DatedCashFlow(Decimal("200"), date(2026, 7, 1)))
        ledger.mature(date(2026, 7, 1))
        assert ledger.settled_cash == Decimal("1200")
        assert ledger.pending == []

    def test_mature_leaves_future_flows_pending(self):
        ledger = _ledger("1000")
        ledger.pending.append(DatedCashFlow(Decimal("200"), date(2026, 7, 5)))
        ledger.mature(date(2026, 7, 1))
        assert ledger.settled_cash == Decimal("1000")
        assert len(ledger.pending) == 1

    def test_mature_splits_pending_at_cutoff(self):
        ledger = _ledger("1000")
        ledger.pending.append(DatedCashFlow(Decimal("100"), date(2026, 7, 1)))
        ledger.pending.append(DatedCashFlow(Decimal("200"), date(2026, 7, 5)))
        ledger.mature(date(2026, 7, 1))
        assert ledger.settled_cash == Decimal("1100")
        assert len(ledger.pending) == 1
        assert ledger.pending[0].amount == Decimal("200")

    def test_mature_on_empty_pending_is_noop(self):
        ledger = _ledger("500")
        ledger.mature(date(2026, 7, 1))
        assert ledger.settled_cash == Decimal("500")


# ── O2 settlement neutrality ──────────────────────────────────────────────────


class TestO2SettlementNeutrality:
    """O2: settled + unsettled is unchanged across every mature() call."""

    def test_mature_preserves_total_cash(self):
        ledger = _ledger("1000")
        ledger.pending.append(DatedCashFlow(Decimal("500"), date(2026, 7, 1)))
        before = _total(ledger)
        ledger.mature(date(2026, 7, 1))
        assert _total(ledger) == before

    def test_partial_mature_preserves_total_cash(self):
        ledger = _ledger("1000")
        ledger.pending.append(DatedCashFlow(Decimal("300"), date(2026, 7, 1)))
        ledger.pending.append(DatedCashFlow(Decimal("200"), date(2026, 7, 3)))
        before = _total(ledger)
        ledger.mature(date(2026, 7, 1))
        assert _total(ledger) == before

    def test_multiple_matures_preserve_total_cash(self):
        ledger = _ledger("1000")
        for i in range(5):
            ledger.pending.append(DatedCashFlow(Decimal("100"), date(2026, 7, i + 1)))
        initial = _total(ledger)
        for d in [date(2026, 7, 1), date(2026, 7, 3), date(2026, 7, 5)]:
            ledger.mature(d)
            assert _total(ledger) == initial

    def test_negative_amount_preserves_total_cash(self):
        ledger = _ledger("2000")
        ledger.pending.append(DatedCashFlow(Decimal("-500"), date(2026, 7, 1)))
        before = _total(ledger)
        ledger.mature(date(2026, 7, 1))
        assert _total(ledger) == before


# ── T+2 XTAI calendar ─────────────────────────────────────────────────────────


class TestT2CalendarMath:
    """T+2 uses XTAI trading sessions, not timedelta(2) calendar days."""

    def test_friday_settles_on_tuesday_not_sunday(self):
        # 2025-07-11 is a Friday; timedelta(2) gives 2025-07-13 (Sunday, non-trading).
        # XTAI T+2 must give 2025-07-15 (Tuesday).
        friday = date(2025, 7, 11)
        ledger = _ledger("1000000")
        model = SettledCashOnly()
        model.on_trade(ledger, _buy(1000, "500"), friday)
        assert len(ledger.pending) == 1
        settle_date = ledger.pending[0].settle_date
        assert settle_date.weekday() < 5, f"{settle_date} is a weekend"
        assert settle_date > friday + timedelta(2), "settle_date must be after calendar T+2"
        assert settle_date == date(2025, 7, 15)

    def test_netting_flush_uses_xtai_calendar(self):
        # Same Friday → Tuesday assertion via NettingSettlement flush.
        friday = date(2025, 7, 11)
        monday = date(2025, 7, 14)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _buy(1000, "500"), friday)
        # Trigger flush by advancing to the next bar.
        model.on_trade(ledger, _buy(100, "500"), monday)
        assert len(ledger.pending) >= 1
        assert ledger.pending[0].settle_date == date(2025, 7, 15)


# ── InstantSettlement ─────────────────────────────────────────────────────────


class TestInstantSettlement:
    def test_buy_deducts_from_settled_cash_immediately(self):
        ledger = _ledger("1000000")
        model = InstantSettlement()
        model.on_trade(ledger, _buy(1000, "500", "4275"), date(2026, 7, 1))
        expected = Decimal("1000000") - (1000 * Decimal("500") + Decimal("4275"))
        assert ledger.settled_cash == expected
        assert ledger.pending == []

    def test_sell_credits_settled_cash_immediately(self):
        ledger = _ledger("0")
        model = InstantSettlement()
        model.on_trade(ledger, _sell(1000, "500", "1500"), date(2026, 7, 1))
        expected = 1000 * Decimal("500") - Decimal("1500")
        assert ledger.settled_cash == expected
        assert ledger.pending == []

    def test_buying_power_equals_settled_cash_ignores_pending(self):
        ledger = _ledger("500000")
        ledger.pending.append(DatedCashFlow(Decimal("100000"), date(2026, 7, 5)))
        model = InstantSettlement()
        assert model.buying_power(ledger, date(2026, 7, 1)) == Decimal("500000")


# ── NettingSettlement ─────────────────────────────────────────────────────────


class TestNettingSettlement:
    def test_same_bar_trades_are_netted_into_one_pending_flow(self):
        t1 = date(2026, 7, 1)
        t2 = date(2026, 7, 2)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _buy(1000, "500"), t1)
        model.on_trade(ledger, _sell(500, "480"), t1)
        model.on_trade(ledger, _buy(100, "500"), t2)  # triggers t1 flush
        # t1 net = -(1000×500) + (500×480) = −260 000
        assert len(ledger.pending) == 1
        expected_net = -(1000 * Decimal("500")) + (500 * Decimal("480"))
        assert ledger.pending[0].amount == expected_net

    def test_buying_power_includes_current_bar_net(self):
        t = date(2026, 7, 1)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _sell(1000, "500"), t)
        expected = Decimal("1000000") + 1000 * Decimal("500")
        assert model.buying_power(ledger, t) == expected

    def test_buying_power_floored_at_zero(self):
        t = date(2026, 7, 1)
        ledger = _ledger("0")
        model = NettingSettlement()
        model.on_trade(ledger, _buy(1000, "500"), t)
        assert model.buying_power(ledger, t) == Decimal("0")

    def test_new_bar_flushes_previous_bar_net(self):
        t1 = date(2026, 7, 1)
        t2 = date(2026, 7, 2)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _buy(1000, "500"), t1)
        assert ledger.pending == []  # not flushed yet
        model.on_trade(ledger, _buy(100, "500"), t2)
        assert len(ledger.pending) == 1  # t1 net now flushed


# ── on_bar_start ──────────────────────────────────────────────────────────────


class TestOnBarStart:
    """on_bar_start pushes a fully-concluded prior bar's net into pending
    without waiting for a later trade — the fix for the case where a bar's
    trade is never followed by another trade."""

    def test_no_op_when_nothing_accrued(self):
        ledger = _ledger("1000000")
        NettingSettlement().on_bar_start(ledger, date(2026, 7, 1))
        assert ledger.pending == []
        assert ledger.bar_net == Decimal(0)

    def test_flushes_prior_bar_with_no_further_trade(self):
        """The case on_trade's flush-on-next-trade alone cannot reach: a
        bar's net with no later trade to trigger it."""
        t1, t2 = date(2026, 7, 1), date(2026, 7, 2)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _buy(1000, "500"), t1)
        assert ledger.pending == []

        model.on_bar_start(ledger, t2)  # rolling into t2, no trade on t2 at all
        assert len(ledger.pending) == 1
        assert ledger.pending[0].amount == -(1000 * Decimal("500"))
        assert ledger.bar_net == Decimal(0)
        assert ledger.bar_date is None

    def test_same_date_does_not_flush(self):
        """on_bar_start called again for the SAME bar (e.g. a second event
        on the same date) must not prematurely flush a still-open bar."""
        t1 = date(2026, 7, 1)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _buy(1000, "500"), t1)
        model.on_bar_start(ledger, t1)
        assert ledger.pending == []
        assert ledger.bar_date == t1

    def test_multi_bar_gap_flushes_on_the_very_next_bar(self):
        """A trade followed by several quiet bars (no trades at all) must
        still flush on the first of those bars, not remain stuck."""
        t1, t2, t3 = date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _buy(1000, "500"), t1)
        model.on_bar_start(ledger, t2)
        assert len(ledger.pending) == 1
        model.on_bar_start(ledger, t3)  # already flushed; must not double-flush
        assert len(ledger.pending) == 1


# ── close ─────────────────────────────────────────────────────────────────────


class TestClose:
    """close() covers the one gap on_bar_start cannot: the final bar of a
    run, which has no later bar to trigger a flush."""

    def test_no_op_when_flat(self):
        ledger = _ledger("1000000")
        NettingSettlement().close(ledger)
        assert ledger.pending == []

    def test_flushes_open_bar_net(self):
        t1 = date(2026, 7, 1)
        ledger = _ledger("1000000")
        model = NettingSettlement()
        model.on_trade(ledger, _sell(1000, "500"), t1)
        model.close(ledger)
        assert len(ledger.pending) == 1
        assert ledger.pending[0].amount == 1000 * Decimal("500")
        assert ledger.pending[0].settle_date == date(2026, 7, 3)
        assert ledger.bar_date is None


# ── cross-ledger independence when one NettingSettlement is shared ───────────


class TestSharedSettlementInstanceAcrossLedgers:
    """Regression: BrokerSimulator and PaperSession each keep their own
    Account/DatedLedger but are constructed with the SAME SettlementModel
    instance. Before the bar_net/bar_date state moved onto DatedLedger, this
    caused every trade to be netted twice (once per ledger) into shared
    instance attributes on NettingSettlement itself."""

    def test_one_instance_two_ledgers_do_not_double_count(self):
        t1 = date(2026, 7, 1)
        shared_model = NettingSettlement()
        ledger_a = _ledger("1000000")
        ledger_b = _ledger("1000000")

        shared_model.on_trade(ledger_a, _buy(1000, "500"), t1)
        shared_model.on_trade(ledger_b, _buy(1000, "500"), t1)

        assert ledger_a.bar_net == -(1000 * Decimal("500"))
        assert ledger_b.bar_net == -(1000 * Decimal("500"))

    def test_flushing_one_ledger_does_not_affect_the_other(self):
        t1, t2 = date(2026, 7, 1), date(2026, 7, 2)
        shared_model = NettingSettlement()
        ledger_a = _ledger("1000000")
        ledger_b = _ledger("1000000")
        shared_model.on_trade(ledger_a, _buy(1000, "500"), t1)
        shared_model.on_trade(ledger_b, _buy(1000, "500"), t1)

        shared_model.on_bar_start(ledger_a, t2)  # flush only ledger_a

        assert len(ledger_a.pending) == 1
        assert ledger_a.bar_date is None
        assert len(ledger_b.pending) == 0  # untouched
        assert ledger_b.bar_date == t1


# ── SettledCashOnly ───────────────────────────────────────────────────────────


class TestSettledCashOnly:
    def test_buy_goes_to_pending_not_settled_cash(self):
        ledger = _ledger("1000000")
        model = SettledCashOnly()
        model.on_trade(ledger, _buy(1000, "500", "4275"), date(2026, 7, 1))
        assert ledger.settled_cash == Decimal("1000000")
        assert len(ledger.pending) == 1

    def test_sell_goes_to_pending_not_settled_cash(self):
        ledger = _ledger("0")
        model = SettledCashOnly()
        model.on_trade(ledger, _sell(1000, "500", "1500"), date(2026, 7, 1))
        assert ledger.settled_cash == Decimal("0")
        assert len(ledger.pending) == 1
        assert ledger.pending[0].amount == Decimal("498500")

    def test_buying_power_equals_settled_cash_ignores_pending(self):
        ledger = _ledger("500000")
        ledger.pending.append(DatedCashFlow(Decimal("200000"), date(2026, 7, 5)))
        model = SettledCashOnly()
        assert model.buying_power(ledger, date(2026, 7, 1)) == Decimal("500000")

    def test_buying_power_increases_after_mature(self):
        ledger = _ledger("500000")
        ledger.pending.append(DatedCashFlow(Decimal("10000"), date(2026, 7, 1)))
        model = SettledCashOnly()
        assert model.buying_power(ledger, date(2026, 6, 30)) == Decimal("500000")
        ledger.mature(date(2026, 7, 1))
        assert model.buying_power(ledger, date(2026, 7, 1)) == Decimal("510000")
