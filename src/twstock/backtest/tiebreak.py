from abc import ABC, abstractmethod

import pandas as pd


class TieBreakPolicy(ABC):
    """Resolves ties among equally-scored names competing for the last allocator slots.

    Implementations MUST NOT use return-predictive information. Alpha belongs in
    generate_scores where the lookahead audit covers it; a tiebreak that ranks on
    expected return is unaudited alpha (ADR 0006 §6)."""

    @abstractmethod
    def select(self, tied: pd.Index, n_slots: int, panel_at_T: dict) -> pd.Index:
        """Choose among equally-scored names competing for the last slots.
        MUST NOT use return-predictive information."""

        ...


class LiquidityTieBreak(TieBreakPolicy):
    """Prefer the most-traded tied names by volume at bar T.

    Falls back to alphabetical order when volume data is missing, ensuring
    deterministic output even when panel_at_T is sparse or empty."""

    def select(self, tied: pd.Index, n_slots: int, panel_at_T: dict) -> pd.Index:
        if n_slots >= len(tied):
            return tied

        def _key(ticker: str) -> tuple:
            bar = panel_at_T.get(ticker)
            vol = float(bar.get("volume", 0.0)) if bar is not None else 0.0
            return (-vol, ticker)

        selected = sorted(tied, key=_key)[:n_slots]
        return pd.Index(selected)


class HoldAll(TieBreakPolicy):
    """Return all tied names; may exceed n slots.

    The most honest baseline for binary strategies with many ties: rather than
    picking arbitrarily among equally-scored names, hold all of them. The
    allocator weights 1/len(selected) when this exceeds n."""

    def select(self, tied: pd.Index, n_slots: int, panel_at_T: dict) -> pd.Index:
        return tied
