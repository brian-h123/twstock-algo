import pandas as pd

from twstock.backtest.cross_sectional import CrossSectionalStrategy


class CrossSectionalMomentum(CrossSectionalStrategy):
    """Rank stocks by past returns; hold the recent winners.

    Score at T = total return from T-skip-lookback to T-skip — the standard
    12-1 month convention. The `skip` excludes the most recent period because
    short-term returns tend to reverse; the signal is cleaner once that noise
    has settled.

    Higher score = stronger momentum = ranked higher by the allocator.
    NaN = insufficient history = ticker ineligible that bar.
    Cardinal scores (magnitude matters), so must be native — not adapter-lifted.
    """

    def __init__(self, lookback: int = 252, skip: int = 21) -> None:
        if lookback < 1:
            raise ValueError("lookback should at least be 1 day")
        if skip < 0:
            raise ValueError("skip days must be non-negative")
        self.lookback = lookback
        self.skip = skip

    def generate_scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        close = pd.DataFrame({ticker: df["close"] for ticker, df in panel.items()})
        return close.pct_change(self.lookback).shift(self.skip)
