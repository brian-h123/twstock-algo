import pandas as pd

from twstock.backtest.strategy import Strategy


class SmaCrossover(Strategy):
    """Long when SMA(fast) > SMA(slow), flat otherwise.

    Signal at T uses SMA values computed on close[:T+1] (causal).
    Engine executes the position change at T+1's open.

    State-based strategy: emits dense 0/1 signals after warmup, NaN during
    the first `slow` bars while the slow MA accumulates enough history.
    """

    def __init__(self, fast: int = 20, slow: int = 60) -> None:
        if fast < 1 or slow < 1:
            raise ValueError("fast and slow must be positive")
        if fast >= slow:
            raise ValueError("fast must be strictly less than slow")
        self.fast = fast
        self.slow = slow

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        from twstock.indicators.core import sma

        fast_ma = sma(bars["close"], self.fast)
        slow_ma = sma(bars["close"], self.slow)

        signal = pd.Series(float("nan"), index=bars.index)
        valid = slow_ma.notna()  # slow MA is the binding warmup constraint
        signal[valid] = (fast_ma[valid] > slow_ma[valid]).astype(float)
        return signal
