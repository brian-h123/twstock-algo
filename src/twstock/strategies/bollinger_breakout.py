import pandas as pd

from twstock.backtest.strategy import Strategy


class BollingerBreakout(Strategy):
    """Bollinger Band breakout strategy (momentum variant).

    Enters long when the close breaks above the upper band, interpreting the
    breach as evidence of strong upward momentum. Exits when the close falls
    back below the middle band (the SMA), treating that as a signal that the
    trend has stalled.

    This is the *momentum* reading of Bollinger Bands, not the mean-reversion
    reading. The mean-reversion variant does the opposite: it buys when price
    touches the lower band (treating it as "oversold") and sells when price
    reaches the upper band (treating it as "overbought"). That interpretation
    assumes price reverts to the mean. This strategy assumes the opposite —
    that a breakout above the upper band signals the start of a trend, not the
    end of one.

    The middle band acts as a trailing exit threshold. A position is held as
    long as the close stays above the SMA; once it drops below, the trend is
    considered over and the position is closed. The grey zone (close between
    middle and upper while already long) emits NaN — the strategy holds
    without re-signalling.

    Signal convention: 1.0 on entry bar, 0.0 on exit bar, NaN otherwise
    (including the first `window-1` bars while the bands warm up).
    """

    def __init__(self, window: int = 20, k: float = 2.0) -> None:
        if window < 2:
            raise ValueError("window must be at least 2")
        if k <= 0:
            raise ValueError("k must be greater than 0")
        self.window = window
        self.k = k

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        from twstock.indicators.core import bollinger_bands

        close = bars["close"]
        _, middle, upper = bollinger_bands(close, self.window, self.k)
        signal = pd.Series(float("nan"), index=bars.index)

        in_position = False

        for i, middle_val in enumerate(middle):
            if pd.isna(middle_val):
                continue

            if not in_position and close.iloc[i] > upper.iloc[i]:
                signal.iloc[i] = 1.0
                in_position = True
            elif in_position and close.iloc[i] < middle_val:
                signal.iloc[i] = 0.0
                in_position = False

        return signal
