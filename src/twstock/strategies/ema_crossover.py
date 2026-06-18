import pandas as pd

from twstock.backtest.strategy import Strategy


class EmaCrossover(Strategy):
    """Long when EMA(fast) > EMA(slow), flat otherwise.

    Signal at T uses EMA values computed on close[:T+1] (causal).
    Engine executes the position change at T+1's open.

    State-based strategy: emits dense 0/1 signals after warmup, NaN during
    the first `slow` bars while the slow EMA accumulates enough history.
    EMA reacts faster to recent price changes than SMA, so crossovers
    trigger earlier but may whipsaw more in choppy markets.
    """

    def __init__(self, fast: int = 20, slow: int = 60) -> None:
        if fast >= slow:
            raise ValueError("fast must be strictly less than slow")
        self.fast = fast
        self.slow = slow

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        from twstock.indicators.core import ema

        fast_ma = ema(bars["close"], self.fast)
        slow_ma = ema(bars["close"], self.slow)

        signal = pd.Series(float("nan"), index=bars.index)
        valid = slow_ma.notna()
        signal[valid] = (fast_ma[valid] > slow_ma[valid]).astype(float)
        return signal
