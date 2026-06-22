import pandas as pd

from twstock.backtest.tiebreak import HoldAll, LiquidityTieBreak


def _bar(volume: float) -> pd.Series:
    return pd.Series({"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "volume": volume})


class TestLiquidityTieBreak:
    def test_selects_highest_volume_first(self):
        tied = pd.Index(["A", "B", "C"])
        panel = {"A": _bar(1_000), "B": _bar(5_000), "C": _bar(3_000)}
        result = LiquidityTieBreak().select(tied, n_slots=2, panel_at_T=panel)
        assert list(result) == ["B", "C"]

    def test_returns_all_when_n_slots_gte_tied(self):
        tied = pd.Index(["A", "B"])
        panel = {"A": _bar(500), "B": _bar(1_000)}
        result = LiquidityTieBreak().select(tied, n_slots=2, panel_at_T=panel)
        assert set(result) == {"A", "B"}

    def test_returns_all_when_n_slots_exceeds_tied(self):
        tied = pd.Index(["A", "B"])
        panel = {"A": _bar(500), "B": _bar(1_000)}
        result = LiquidityTieBreak().select(tied, n_slots=5, panel_at_T=panel)
        assert set(result) == {"A", "B"}

    def test_alphabetical_fallback_when_panel_empty(self):
        tied = pd.Index(["C", "A", "B"])
        result = LiquidityTieBreak().select(tied, n_slots=2, panel_at_T={})
        assert list(result) == ["A", "B"]

    def test_alphabetical_stability_on_equal_volumes(self):
        tied = pd.Index(["C", "A", "B"])
        panel = {t: _bar(500) for t in ["A", "B", "C"]}
        result = LiquidityTieBreak().select(tied, n_slots=2, panel_at_T=panel)
        assert list(result) == ["A", "B"]

    def test_missing_ticker_in_panel_treated_as_zero_volume(self):
        # "A" has no entry in panel_at_T → vol = 0.0 → ranked last
        tied = pd.Index(["A", "B"])
        panel = {"B": _bar(1_000)}
        result = LiquidityTieBreak().select(tied, n_slots=1, panel_at_T=panel)
        assert list(result) == ["B"]

    def test_returns_pd_index(self):
        tied = pd.Index(["A", "B"])
        panel = {"A": _bar(100), "B": _bar(200)}
        result = LiquidityTieBreak().select(tied, n_slots=1, panel_at_T=panel)
        assert isinstance(result, pd.Index)


class TestHoldAll:
    def test_returns_all_tied(self):
        tied = pd.Index(["A", "B", "C"])
        result = HoldAll().select(tied, n_slots=1, panel_at_T={})
        assert set(result) == {"A", "B", "C"}

    def test_may_exceed_n_slots(self):
        tied = pd.Index(["A", "B", "C"])
        result = HoldAll().select(tied, n_slots=1, panel_at_T={})
        assert len(result) == 3

    def test_ignores_panel_at_T(self):
        tied = pd.Index(["X", "Y"])
        result = HoldAll().select(tied, n_slots=1, panel_at_T={"X": _bar(9_999)})
        assert set(result) == {"X", "Y"}

    def test_returns_pd_index(self):
        tied = pd.Index(["X", "Y"])
        result = HoldAll().select(tied, n_slots=1, panel_at_T={})
        assert isinstance(result, pd.Index)
