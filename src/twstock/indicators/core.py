import pandas as pd


def _validate(close: pd.Series) -> None:
    if not isinstance(close, pd.Series):
        raise TypeError(f"expected pd.Series, got {type(close).__name__}")
    if not isinstance(close.index, pd.DatetimeIndex):
        raise TypeError(f"expected DatetimeIndex, got {type(close.index).__name__}")
    if close.dtype != "float64":
        raise TypeError(f"expected float64 dtype, got {close.dtype}")


def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average. Value at index T uses close[T-window+1:T+1]."""
    _validate(close)
    return close.rolling(window, min_periods=window).mean()


def ema(close: pd.Series, window: int) -> pd.Series:
    """Exponential moving average using pandas' adjust=False convention
    (matches standard trading-platform definition)."""
    _validate(close)
    return close.ewm(span=window, adjust=False, min_periods=window).mean()


def bollinger_bands(
    close: pd.Series, window: int = 20, k: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (lower, middle, upper). Middle = sma(close, window).
    Bands at middle ± k * rolling_std(window, ddof=0)."""
    _validate(close)
    middle = sma(close=close, window=window)
    std = close.rolling(window, min_periods=window).std(ddof=0)
    lower = middle - k * std
    upper = middle + k * std
    return (lower, middle, upper)


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI. Returns values in [0, 100], NaN for the first `window` rows."""
    _validate(close)
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_gain = gains.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    result = result.fillna(100)  # not a warmup fill; avg_loss=0 means RSI is mathematically 100
    result.iloc[:window] = float("nan")
    return result


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram).
    macd_line = ema(close, fast) - ema(close, slow);
    signal_line = ema(macd_line, signal); histogram = macd_line - signal_line."""
    _validate(close)
    macd_line = ema(close=close, window=fast) - ema(close=close, window=slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return (macd_line, signal_line, histogram)


def donchian_channels(
    high: pd.Series, low: pd.Series, window: int = 20
) -> tuple[pd.Series, pd.Series]:
    """Returns (lower, upper). upper = high.rolling(window).max();
    lower = low.rolling(window).min(). Value at T uses bars[T-window+1:T+1]."""
    _validate(high)
    _validate(low)
    upper = high.rolling(window=window, min_periods=window).max()
    lower = low.rolling(window=window, min_periods=window).min()
    return (lower, upper)
