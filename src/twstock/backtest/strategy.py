from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """Abstract strategy interface.

    A strategy maps OHLCV bars to target positions. Signals at bar T
    must be computable using only bars[:T+1] (data up to and including T).
    Execution is performed by the Engine at bar T+1 open.
    """

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        """Given a date-indexed OHLCV DataFrame, return a Series of
        target positions in {-1, 0, +1} indexed by the same dates.
        NaN means 'no signal — preserve previous state'.

        Implementations must use only causal operations. Any use of
        bars.iloc[T+k] for k > 0 to compute signal[T] is lookahead bias.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable name for tear sheet labeling. Default: class name."""
        return type(self).__name__


class BuyAndHold(Strategy):
    """Reference strategy: enter at bar 0 (executed at bar 1 open) and
    hold until end. Used for the 0050 benchmark and as a smoke test."""

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=bars.index, dtype=int)
        signals.iloc[0] = 1
        return signals
