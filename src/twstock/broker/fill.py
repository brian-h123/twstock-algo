import math
from abc import ABC, abstractmethod


class FillModel(ABC):
    """Abstract fill-sizing policy: how many shares an order fills this bar.

    A FillModel decides size only; price (T+1 open + worse-side slippage)
    is the caller's concern via TaiwanCostModel. Implementations are pure
    functions of their inputs, with no engine state of their own.
    """

    @abstractmethod
    def fillable(self, remaining: int, trailing_adv: float) -> int:
        """Return the number of shares fillable on this bar.

        Args:
            remaining: Shares still unfilled on the order.
            trailing_adv: Average daily volume computed from data as of T
                (never T+1's own volume — using the fill bar's own volume
                would be lookahead, since it does not exist yet at T+1's
                open). The caller is responsible for computing this from
                data strictly prior to the fill bar.

        Returns:
            Shares to fill this bar; always <= remaining.
        """
        ...


class VolumeParticipationFiller(FillModel):
    """Pessimistic fill model: capped at a fraction of trailing ADV.

    The simulator's default execution-realism preset (ADR 0010). Models
    the assumption that a single order cannot consume more than a small
    slice of a ticker's typical daily liquidity without moving the market.

    Args:
        participation_rate: Fraction of trailing ADV fillable in one bar.
            Locked canonical default 0.05 (5%); configurable per run.
    """

    def __init__(self, participation_rate: float = 0.05) -> None:
        self.participation_rate = participation_rate

    def fillable(self, remaining: int, trailing_adv: float) -> int:
        return min(remaining, math.floor(self.participation_rate * trailing_adv))


class FullFill(FillModel):
    """Frictionless fill model: always fills the entire remaining order.

    The control/reconciliation-reference preset (ADR 0008) — reproduces
    the backtester's unlimited-liquidity assumption. Ignores trailing_adv
    entirely.
    """

    def fillable(self, remaining: int, trailing_adv: float) -> int:
        return remaining
