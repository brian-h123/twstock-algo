from abc import ABC, abstractmethod

import pandas as pd

from twstock.backtest.tiebreak import LiquidityTieBreak, TieBreakPolicy


class Allocator(ABC):
    """Abstract allocator interface.

    Maps one bar's cross-sectional scores to target portfolio weights.
    Holdings-agnostic: implementations never see current positions; diffing
    against the book is the reconciler's responsibility.
    """

    @abstractmethod
    def allocate(self, scores_at_T: pd.Series, *, max_position_pct: float) -> pd.Series:
        """One bar's scores -> target weights (sum ≤ 1.0). Holdings-agnostic."""

        ...


class EqualWeightTopN(Allocator):
    """Equal-weight allocator that selects the top-N scored names.

    Ranks scores descending and selects the top N eligible names. Ties at the
    cut are resolved by the injected TieBreakPolicy. Each selected name
    receives weight 1/N, clamped to max_position_pct. Names with NaN scores
    are never selected; when fewer than N eligible names exist, the remainder
    stays in cash.

    Args:
        n: Number of names to hold.
        tiebreak: Policy for resolving equal-score ties at the selection cut.

    Raises:
        ValueError: If n * max_position_pct < 1.0, a config that can never
            fully invest.
    """

    def __init__(self, n: int, tiebreak: TieBreakPolicy = LiquidityTieBreak()) -> None:
        self.n = n
        self.tiebreak = tiebreak

    def allocate(
        self, scores_at_T: pd.Series, *, max_position_pct: float, panel_at_T: dict | None = None
    ) -> pd.Series:
        """Allocate one bar's scores to target weights.

        Args:
            scores_at_T: Ticker-indexed float scores for bar T. NaN = not eligible.
            max_position_pct: Maximum weight allowed per name (from Engine config).
            panel_at_T: Raw bar data for bar T, forwarded to the TieBreakPolicy.

        Returns:
            Ticker-indexed Series of target weights. Sum is ≤ 1.0; names not
            selected are absent (not zero-weighted).
        """
        if self.n * max_position_pct < 1.0:
            raise ValueError(
                f"n={self.n} * max_position_pct={max_position_pct} < 1.0: "
                "this config can never fully invest"
            )

        eligible = scores_at_T.dropna()

        if len(eligible) <= self.n:
            selected = eligible.index
        else:
            sorted_scores = eligible.sort_values(ascending=False)
            cut = sorted_scores.iloc[self.n - 1]
            above_cut = sorted_scores[sorted_scores > cut].index
            tied = sorted_scores[sorted_scores == cut].index
            n_slots_remaining = self.n - len(above_cut)

            tiebreak_result = self.tiebreak.select(
                tied=tied,
                n_slots=n_slots_remaining,
                panel_at_T=panel_at_T if panel_at_T is not None else {},
            )
            selected = pd.concat([above_cut.to_series(), tiebreak_result.to_series()]).index

        if len(selected) == 0:
            return pd.Series(dtype=float)

        base_weight = 1 / len(selected)
        weight = min(base_weight, max_position_pct)
        return pd.Series(weight, index=selected)
