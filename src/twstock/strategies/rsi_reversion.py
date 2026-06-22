import pandas as pd

from twstock.backtest.strategy import Strategy


class RsiReversion(Strategy):
    """Long when RSI crosses below 30, flat when RSI crosses above 50.

    Uses asymmetric enter/exit thresholds (mean-reversion). Emits a sparse
    signal: 1.0 on entry, 0.0 on exit, NaN otherwise. NaN during the first
    `period` bars while RSI accumulates enough history.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self.period = period

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        from twstock.indicators.core import rsi

        rsi_vals = rsi(bars["close"], self.period)
        signal = pd.Series(float("nan"), index=bars.index)

        in_position = False  # local — resets every call

        for i, rsi_val in enumerate(rsi_vals):
            if pd.isna(rsi_val):
                continue  # warmup: leave NaN, don't touch in_position

            if not in_position and rsi_val < 30:
                signal.iloc[i] = 1.0
                in_position = True
            elif in_position and rsi_val > 50:
                signal.iloc[i] = 0.0
                in_position = False
            # else: grey zone — NaN, no state change

        return signal
