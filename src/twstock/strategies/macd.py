import pandas as pd

from twstock.backtest.strategy import Strategy


class Macd(Strategy):
    """MACD signal-line crossover strategy (12/26/9).

    Implements the most common MACD trading rule: long when the MACD line is
    above the signal line, flat otherwise. The three MACD components are:

    - MACD line  = ema(close, fast) − ema(close, slow): measures momentum
      direction and strength as the spread between two EMAs.
    - Signal line = ema(MACD line, signal): a smoothed version of the MACD line,
      used as a trigger.
    - Histogram   = MACD line − signal line: the divergence between the two.

    This strategy uses the crossover between the MACD line and the signal line.
    The histogram is not used as a separate input because it is algebraically
    identical to the crossover condition — MACD line > signal line if and only if
    histogram > 0. Other MACD variants (zero-line crossover, histogram momentum,
    divergence) would require different signal logic.

    Signal convention: 1.0 when macd_line > signal_line, 0.0 otherwise,
    NaN during the first (slow + signal − 2) bars while the indicator warms up.
    """

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        if fast < 1 or slow < 1 or signal < 1:
            raise ValueError("fast, slow, and signal must all be positive")
        if fast >= slow:
            raise ValueError("fast must be strictly less than slow")
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        from twstock.indicators.core import macd

        macd_line, signal_line, _ = macd(bars["close"], self.fast, self.slow, self.signal)
        signal = pd.Series(float("nan"), index=bars.index)
        valid = signal_line.notna()
        signal[valid] = (macd_line[valid] > signal_line[valid]).astype(float)
        return signal
