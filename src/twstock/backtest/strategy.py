from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """Abstract strategy interface for single-ticker strategies.

    A strategy maps OHLCV bars for a *single* ticker to target positions.
    It has no awareness of other tickers — ``generate_signals`` receives
    one ticker's bars in isolation. Cross-ticker ranking or scoring belongs
    in ``CrossSectionalStrategy``.

    Signals at bar T must be computable using only bars[:T+1] (data up to
    and including T). Execution is performed by the Engine at bar T+1 open.
    """

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        """Given a date-indexed OHLCV DataFrame, return a Series of
        target positions in {-1, 0, +1} indexed by the same dates.
        NaN means 'no signal — preserve previous state'.

        Two valid signal idioms exist; choose based on strategy shape:

        **Trigger-based** (e.g. BuyAndHold): emit a non-NaN value only
        when the desired position *changes*. All other bars are NaN,
        which tells the engine to hold whatever position it already has.
        Example: [1, NaN, NaN, NaN, 0, NaN, ...] — enter once, exit once.

        **State-based** (e.g. SmaCrossover): emit a non-NaN value on
        *every* bar after the indicator warmup period; consecutive
        identical values (e.g. [1, 1, 1]) are no-ops in the engine
        because it only acts on transitions. Emit NaN explicitly during
        the warmup period (before the indicator has enough history),
        rather than relying on 0 (which means "go flat") as a proxy.

        Both idioms are semantically equivalent to the engine. The rule
        of thumb: use trigger-based for event-driven entries; use
        state-based for continuous indicator comparisons.

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
        signals = pd.Series(dtype=float, index=bars.index)  # all NaN = preserve state
        signals.iloc[0] = 1
        return signals
