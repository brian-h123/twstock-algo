import pandas as pd

from twstock.backtest.strategy import Strategy


class DonchianBreakout(Strategy):
    """Donchian channel breakout strategy.

    Enters long when today's close exceeds the *prior bar's* upper Donchian
    channel (the rolling high of the previous `window` days), interpreting the
    breakout as evidence of a new upward trend. Exits when today's close falls
    below the prior bar's lower channel (the rolling low), treating that as
    confirmation the trend has reversed.

    Why compare against the *prior* bar's channel rather than today's? The
    Donchian upper band at bar T is the rolling maximum of highs over bars
    [T-window+1 … T], which includes today's own high. Since close ≤ high by
    definition, ``close > upper_today`` is mathematically impossible in clean
    OHLCV data — today's high is already baked into the threshold. Shifting by
    one bar sets the threshold from data fully known before today's bar opens,
    making a genuine breakout measurable and keeping the signal causal.

    The strategy uses asymmetric enter/exit thresholds (like ``RsiReversion``):
    entry fires when a breakout above the prior upper occurs; exit fires when a
    breakdown below the prior lower occurs. The grey zone between the two
    channels emits NaN — the position is held without re-signalling.

    Signal convention: 1.0 on entry bar, 0.0 on exit bar, NaN otherwise
    (including the first ``window`` bars while the channel warms up, plus the
    one additional bar consumed by the shift).
    """

    def __init__(self, window: int = 20) -> None:
        if window <= 0:
            raise ValueError("window must be greater than 0")
        self.window = window

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        from twstock.indicators.core import donchian_channels

        lower, upper = donchian_channels(bars["high"], bars["low"], self.window)
        signal = pd.Series(float("nan"), index=bars.index)

        in_position = False
        close = bars["close"]
        prev_upper = upper.shift(1)
        prev_lower = lower.shift(1)

        for i, prev_upper_val in enumerate(prev_upper):
            if pd.isna(prev_upper_val):
                continue

            if not in_position and close.iloc[i] > prev_upper_val:
                signal.iloc[i] = 1.0
                in_position = True
            elif in_position and close.iloc[i] < prev_lower.iloc[i]:
                signal.iloc[i] = 0.0
                in_position = False

        return signal
