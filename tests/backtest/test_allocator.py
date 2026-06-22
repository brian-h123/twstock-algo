import pandas as pd
import pytest

from twstock.backtest.allocator import Allocator, EqualWeightTopN
from twstock.backtest.tiebreak import HoldAll, LiquidityTieBreak


def _scores(**kwargs: float) -> pd.Series:
    return pd.Series(kwargs, dtype=float)


def _bar(volume: float) -> pd.Series:
    return pd.Series({"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "volume": volume})


class TestAllocatorInterface:
    def test_is_abstract(self):
        assert issubclass(EqualWeightTopN, Allocator)


class TestEqualWeightTopNValidation:
    def test_raises_when_n_times_cap_lt_1(self):
        alloc = EqualWeightTopN(n=5)
        with pytest.raises(ValueError, match="can never fully invest"):
            alloc.allocate(_scores(A=1.0, B=0.9), max_position_pct=0.10)

    def test_does_not_raise_when_n_times_cap_eq_1(self):
        alloc = EqualWeightTopN(n=10)
        scores = _scores(**{str(i): float(i) for i in range(10)})
        result = alloc.allocate(scores, max_position_pct=0.10)
        assert len(result) == 10

    def test_does_not_raise_when_n_times_cap_gt_1(self):
        alloc = EqualWeightTopN(n=3)
        scores = _scores(A=0.9, B=0.7, C=0.5)
        result = alloc.allocate(scores, max_position_pct=0.40)
        assert len(result) == 3


class TestEqualWeightTopNSelection:
    def test_selects_top_n_by_score(self):
        alloc = EqualWeightTopN(n=2)
        scores = _scores(A=0.9, B=0.7, C=0.3, D=0.1)
        result = alloc.allocate(scores, max_position_pct=0.50)
        assert set(result.index) == {"A", "B"}

    def test_equal_weight_per_name(self):
        alloc = EqualWeightTopN(n=4)
        scores = _scores(A=0.9, B=0.7, C=0.5, D=0.3, E=0.1)
        result = alloc.allocate(scores, max_position_pct=0.25)
        assert all(abs(w - 0.25) < 1e-9 for w in result)

    def test_weights_sum_le_1(self):
        alloc = EqualWeightTopN(n=3)
        scores = _scores(A=0.9, B=0.7, C=0.5, D=0.3)
        result = alloc.allocate(scores, max_position_pct=0.40)
        assert result.sum() <= 1.0 + 1e-9

    def test_unselected_names_absent_not_zero(self):
        alloc = EqualWeightTopN(n=2)
        scores = _scores(A=0.9, B=0.7, C=0.3)
        result = alloc.allocate(scores, max_position_pct=0.50)
        assert "C" not in result.index

    def test_nan_scores_never_selected(self):
        alloc = EqualWeightTopN(n=2)
        scores = pd.Series({"A": 0.9, "B": float("nan"), "C": 0.5})
        result = alloc.allocate(scores, max_position_pct=0.50)
        assert "B" not in result.index
        assert set(result.index) == {"A", "C"}

    def test_returns_pd_series(self):
        alloc = EqualWeightTopN(n=2)
        scores = _scores(A=0.9, B=0.5)
        result = alloc.allocate(scores, max_position_pct=0.50)
        assert isinstance(result, pd.Series)


class TestEqualWeightTopNUndersupply:
    def test_fewer_eligible_than_n_holds_cash(self):
        alloc = EqualWeightTopN(n=5)
        scores = _scores(A=0.9, B=0.7)
        result = alloc.allocate(scores, max_position_pct=0.20)
        assert set(result.index) == {"A", "B"}
        assert result.sum() < 1.0

    def test_undersupply_weights_sum_to_2_over_n(self):
        # 2 eligible out of n=5: each gets 1/5 = 0.20, total = 0.40
        alloc = EqualWeightTopN(n=5)
        scores = _scores(A=0.9, B=0.7)
        result = alloc.allocate(scores, max_position_pct=0.20)
        assert abs(result.sum() - 0.40) < 1e-9

    def test_exactly_n_eligible_selects_all(self):
        alloc = EqualWeightTopN(n=3)
        scores = _scores(A=0.9, B=0.7, C=0.5)
        result = alloc.allocate(scores, max_position_pct=0.40)
        assert set(result.index) == {"A", "B", "C"}

    def test_all_nan_returns_empty_series(self):
        alloc = EqualWeightTopN(n=3)
        scores = pd.Series({"A": float("nan"), "B": float("nan")})
        result = alloc.allocate(scores, max_position_pct=0.40)
        assert len(result) == 0
        assert isinstance(result, pd.Series)


class TestEqualWeightTopNTieBreak:
    def test_tiebreak_invoked_at_cut(self):
        # A(0.9) is clear; B and C tie at 0.5 for the last slot
        alloc = EqualWeightTopN(n=2, tiebreak=LiquidityTieBreak())
        scores = _scores(A=0.9, B=0.5, C=0.5)
        panel = {"A": _bar(1_000), "B": _bar(5_000), "C": _bar(3_000)}
        result = alloc.allocate(scores, max_position_pct=0.50, panel_at_T=panel)
        # B has higher volume so wins the tie
        assert set(result.index) == {"A", "B"}

    def test_holdall_tiebreak_may_exceed_n(self):
        # A(0.9) clear; B and C tied at 0.5 for 1 slot; HoldAll returns both
        alloc = EqualWeightTopN(n=2, tiebreak=HoldAll())
        scores = _scores(A=0.9, B=0.5, C=0.5)
        result = alloc.allocate(scores, max_position_pct=0.50)
        assert set(result.index) == {"A", "B", "C"}

    def test_holdall_weights_diluted_by_actual_count(self):
        # 3 selected but n=2, so weight = 1/3 each
        alloc = EqualWeightTopN(n=2, tiebreak=HoldAll())
        scores = _scores(A=0.9, B=0.5, C=0.5)
        result = alloc.allocate(scores, max_position_pct=0.50)
        assert all(abs(w - 1 / 3) < 1e-9 for w in result)

    def test_no_tiebreak_needed_when_scores_all_distinct(self):
        alloc = EqualWeightTopN(n=2)
        scores = _scores(A=0.9, B=0.7, C=0.5, D=0.3)
        result = alloc.allocate(scores, max_position_pct=0.50)
        assert set(result.index) == {"A", "B"}

    def test_all_scores_tied_tiebreak_selects_n(self):
        alloc = EqualWeightTopN(n=2, tiebreak=LiquidityTieBreak())
        scores = _scores(A=0.5, B=0.5, C=0.5)
        panel = {"A": _bar(1_000), "B": _bar(5_000), "C": _bar(3_000)}
        result = alloc.allocate(scores, max_position_pct=0.50, panel_at_T=panel)
        assert len(result) == 2
        assert set(result.index) == {"B", "C"}


class TestEqualWeightTopNClamping:
    def test_weights_never_exceed_max_position_pct(self):
        # Validation ensures cap >= 1/n, so the clamp is a safety guard.
        # Verify weights stay within the cap regardless.
        alloc = EqualWeightTopN(n=3)
        scores = _scores(A=0.9, B=0.7, C=0.5, D=0.3)
        result = alloc.allocate(scores, max_position_pct=0.40)
        assert all(w <= 0.40 + 1e-9 for w in result)
