from datetime import date
from decimal import Decimal

import pytest

from twstock.backtest.trades import LotLedger, Side
from twstock.broker.corporate_actions import (
    CashMutation,
    ShareMutation,
    decompose,
    share_multiplier,
)
from twstock.broker.events import ActionType, CorporateAction

# ── helpers ───────────────────────────────────────────────────────────────────


def _action(
    action_type: ActionType,
    *,
    ex_date: date = date(2026, 1, 2),
    cash_amount: Decimal | None = None,
    ratio: float | None = None,
    pay_date: date | None = None,
) -> CorporateAction:
    return CorporateAction(
        action_type=action_type,
        ex_date=ex_date,
        cash_amount=cash_amount,
        ratio=ratio,
        pay_date=pay_date,
        subscription_price=None,
    )


def _position(shares: int = 1000, price: float = 100.0) -> LotLedger:
    ledger = LotLedger()
    if shares > 0:
        ledger.on_fill(Side.BUY, shares, price, 0.0)
    return ledger


_ALL_ACTION_TYPES = [
    ActionType.STOCK_SPLIT,
    ActionType.STOCK_DIVIDEND,
    ActionType.CASH_DIVIDEND,
    ActionType.CASH_AND_STOCK_DIVIDEND,
    ActionType.RIGHTS_ISSUE,
    ActionType.CAPITAL_REDUCTION,
]


def _sample(action_type: ActionType) -> CorporateAction:
    """Build a well-formed action of the given type for the flat-position sweep."""
    if action_type == ActionType.STOCK_SPLIT:
        return _action(action_type, ratio=0.25)
    if action_type == ActionType.STOCK_DIVIDEND:
        return _action(action_type, ratio=0.1)
    if action_type == ActionType.CASH_DIVIDEND:
        return _action(action_type, cash_amount=Decimal("2"), pay_date=date(2026, 2, 1))
    if action_type == ActionType.CASH_AND_STOCK_DIVIDEND:
        return _action(action_type, cash_amount=Decimal("2"), ratio=0.1, pay_date=date(2026, 2, 1))
    if action_type == ActionType.RIGHTS_ISSUE:
        return _action(action_type, ratio=1.0)
    if action_type == ActionType.CAPITAL_REDUCTION:
        return _action(action_type, ratio=0.5)
    raise AssertionError(f"no sample builder for {action_type}")


# ── share_multiplier ─────────────────────────────────────────────────────────


class TestShareMultiplier:
    def test_stock_split_returns_inverse_of_price_ratio(self):
        """Real event shape: 2327 4:1 split, ADR 0007 — schema ratio 0.25."""
        assert share_multiplier(_action(ActionType.STOCK_SPLIT, ratio=0.25)) == 4.0

    def test_stock_dividend_returns_one_plus_ratio(self):
        assert share_multiplier(_action(ActionType.STOCK_DIVIDEND, ratio=0.1)) == 1.1

    def test_cash_and_stock_dividend_uses_same_formula_as_stock_dividend(self):
        assert (
            share_multiplier(
                _action(ActionType.CASH_AND_STOCK_DIVIDEND, cash_amount=Decimal("2"), ratio=0.1)
            )
            == 1.1
        )

    def test_capital_reduction_uses_same_formula_as_stock_split(self):
        """ADR 0012: CAPITAL_REDUCTION shares STOCK_SPLIT's ratio convention
        (post_price / prior_price, ADR 0007) — ratio=2.0 -> half as many
        shares (1 / 2.0), the same shape as a 1:2 reverse split."""
        assert share_multiplier(_action(ActionType.CAPITAL_REDUCTION, ratio=2.0)) == 0.5

    @pytest.mark.parametrize("bad_ratio", [None, 0, -0.5])
    def test_stock_split_invalid_ratio_raises(self, bad_ratio):
        with pytest.raises(ValueError):
            share_multiplier(_action(ActionType.STOCK_SPLIT, ratio=bad_ratio))

    @pytest.mark.parametrize("bad_ratio", [None, 0, -2.0])
    def test_capital_reduction_invalid_ratio_raises(self, bad_ratio):
        with pytest.raises(ValueError):
            share_multiplier(_action(ActionType.CAPITAL_REDUCTION, ratio=bad_ratio))

    @pytest.mark.parametrize("bad_ratio", [None, 0, -0.1])
    def test_stock_dividend_invalid_ratio_raises(self, bad_ratio):
        with pytest.raises(ValueError):
            share_multiplier(_action(ActionType.STOCK_DIVIDEND, ratio=bad_ratio))

    @pytest.mark.parametrize("action_type", [ActionType.CASH_DIVIDEND, ActionType.RIGHTS_ISSUE])
    def test_undefined_for_non_share_multiplicative_types(self, action_type):
        with pytest.raises(ValueError):
            share_multiplier(_action(action_type))


# ── decompose: flat position ─────────────────────────────────────────────────


class TestDecomposeFlatPosition:
    """With nothing held, no share or cash entitlement can accrue for any
    action type — including CAPITAL_REDUCTION, which otherwise produces a
    real ShareMutation on a held position (see TestDecomposeCapitalReduction).
    RIGHTS_ISSUE returns empty regardless of position state (ADR 0012 lapse
    assumption), so it's included here too."""

    @pytest.mark.parametrize("action_type", _ALL_ACTION_TYPES)
    def test_flat_position_returns_empty_regardless_of_action_type(self, action_type):
        flat = _position(shares=0)
        assert decompose(_sample(action_type), flat, date(2026, 1, 2)) == []


# ── decompose: stock split (O8) ──────────────────────────────────────────────


class TestDecomposeStockSplit:
    def test_produces_share_mutation_with_inverse_ratio_only(self):
        position = _position(shares=1000)
        result = decompose(_action(ActionType.STOCK_SPLIT, ratio=0.25), position, date(2026, 1, 2))
        assert result == [ShareMutation(ratio=4.0, effective_date=date(2026, 1, 2))]

    def test_does_not_mutate_position(self):
        """decompose is pure — the mutation is applied later, when the
        emitted SPLIT event is folded, not by decompose itself."""
        position = _position(shares=1000, price=100.0)
        open_before, avg_cost_before = position.open_shares, position.avg_cost
        decompose(_action(ActionType.STOCK_SPLIT, ratio=0.25), position, date(2026, 1, 2))
        assert position.open_shares == open_before
        assert position.avg_cost == avg_cost_before

    @pytest.mark.parametrize("bad_ratio", [None, 0, -0.25])
    def test_invalid_ratio_raises(self, bad_ratio):
        position = _position(shares=1000)
        with pytest.raises(ValueError):
            decompose(_action(ActionType.STOCK_SPLIT, ratio=bad_ratio), position, date(2026, 1, 2))


# ── decompose: stock dividend ────────────────────────────────────────────────


class TestDecomposeStockDividend:
    def test_produces_share_mutation_with_one_plus_ratio_only(self):
        position = _position(shares=1000)
        result = decompose(
            _action(ActionType.STOCK_DIVIDEND, ratio=0.1), position, date(2026, 1, 2)
        )
        assert result == [ShareMutation(ratio=1.1, effective_date=date(2026, 1, 2))]


# ── decompose: cash dividend ─────────────────────────────────────────────────


class TestDecomposeCashDividend:
    def test_cash_mutation_sized_by_open_shares(self):
        position = _position(shares=1000)
        action = _action(
            ActionType.CASH_DIVIDEND, cash_amount=Decimal("2"), pay_date=date(2026, 2, 1)
        )
        result = decompose(action, position, date(2026, 1, 2))
        assert result == [
            CashMutation(
                amount=Decimal("2000"),
                declared_date=date(2026, 1, 2),
                settle_date=date(2026, 2, 1),
            )
        ]

    def test_declared_at_ex_date_settles_at_pay_date(self):
        """Ex-date and pay-date are different events (§9 pitfalls) — the
        mutation must carry both distinctly, not collapse them."""
        position = _position(shares=1000)
        action = _action(
            ActionType.CASH_DIVIDEND, cash_amount=Decimal("2"), pay_date=date(2026, 3, 15)
        )
        [mutation] = decompose(action, position, date(2026, 1, 2))
        assert mutation.declared_date == date(2026, 1, 2)
        assert mutation.settle_date == date(2026, 3, 15)
        assert mutation.declared_date != mutation.settle_date

    def test_missing_cash_amount_raises(self):
        position = _position(shares=1000)
        action = _action(ActionType.CASH_DIVIDEND, cash_amount=None, pay_date=date(2026, 2, 1))
        with pytest.raises(ValueError):
            decompose(action, position, date(2026, 1, 2))

    def test_missing_pay_date_raises(self):
        position = _position(shares=1000)
        action = _action(ActionType.CASH_DIVIDEND, cash_amount=Decimal("2"), pay_date=None)
        with pytest.raises(ValueError):
            decompose(action, position, date(2026, 1, 2))


# ── decompose: combined cash + stock dividend (O9) ──────────────────────────


class TestDecomposeCombinedDividend:
    def test_produces_both_mutations_in_share_then_cash_order(self):
        position = _position(shares=1000)
        action = _action(
            ActionType.CASH_AND_STOCK_DIVIDEND,
            cash_amount=Decimal("2"),
            ratio=0.1,
            pay_date=date(2026, 2, 1),
        )
        result = decompose(action, position, date(2026, 1, 2))
        assert result == [
            ShareMutation(ratio=1.1, effective_date=date(2026, 1, 2)),
            CashMutation(
                amount=Decimal("2000"),
                declared_date=date(2026, 1, 2),
                settle_date=date(2026, 2, 1),
            ),
        ]

    def test_missing_cash_amount_raises(self):
        position = _position(shares=1000)
        action = _action(
            ActionType.CASH_AND_STOCK_DIVIDEND,
            cash_amount=None,
            ratio=0.1,
            pay_date=date(2026, 2, 1),
        )
        with pytest.raises(ValueError):
            decompose(action, position, date(2026, 1, 2))

    def test_missing_pay_date_raises(self):
        position = _position(shares=1000)
        action = _action(
            ActionType.CASH_AND_STOCK_DIVIDEND,
            cash_amount=Decimal("2"),
            ratio=0.1,
            pay_date=None,
        )
        with pytest.raises(ValueError):
            decompose(action, position, date(2026, 1, 2))

    def test_invalid_ratio_raises(self):
        position = _position(shares=1000)
        action = _action(
            ActionType.CASH_AND_STOCK_DIVIDEND,
            cash_amount=Decimal("2"),
            ratio=0,
            pay_date=date(2026, 2, 1),
        )
        with pytest.raises(ValueError):
            decompose(action, position, date(2026, 1, 2))


# ── decompose: capital reduction (ADR 0012: same path as stock split) ───────


class TestDecomposeCapitalReduction:
    def test_produces_share_mutation_with_inverse_ratio_only(self):
        position = _position(shares=1000)
        result = decompose(
            _action(ActionType.CAPITAL_REDUCTION, ratio=2.0), position, date(2026, 1, 2)
        )
        assert result == [ShareMutation(ratio=0.5, effective_date=date(2026, 1, 2))]

    def test_does_not_mutate_position(self):
        position = _position(shares=1000, price=100.0)
        open_before, avg_cost_before = position.open_shares, position.avg_cost
        decompose(_action(ActionType.CAPITAL_REDUCTION, ratio=2.0), position, date(2026, 1, 2))
        assert position.open_shares == open_before
        assert position.avg_cost == avg_cost_before

    @pytest.mark.parametrize("bad_ratio", [None, 0, -2.0])
    def test_invalid_ratio_raises(self, bad_ratio):
        position = _position(shares=1000)
        with pytest.raises(ValueError):
            decompose(
                _action(ActionType.CAPITAL_REDUCTION, ratio=bad_ratio), position, date(2026, 1, 2)
            )


# ── decompose: rights issue (ADR 0012 lapse assumption) ─────────────────────


class TestDecomposeRightsIssue:
    def test_returns_empty_on_held_position(self):
        position = _position(shares=1000)
        result = decompose(_action(ActionType.RIGHTS_ISSUE, ratio=1.0), position, date(2026, 1, 2))
        assert result == []

    def test_does_not_mutate_position(self):
        position = _position(shares=1000, price=100.0)
        open_before, avg_cost_before = position.open_shares, position.avg_cost
        decompose(_action(ActionType.RIGHTS_ISSUE, ratio=1.0), position, date(2026, 1, 2))
        assert position.open_shares == open_before
        assert position.avg_cost == avg_cost_before
