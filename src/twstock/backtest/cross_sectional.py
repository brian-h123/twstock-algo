from abc import ABC, abstractmethod

import pandas as pd

from twstock.backtest.strategy import Strategy


class CrossSectionalStrategy(ABC):
    @abstractmethod
    def generate_scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Date-indexed, ticker-columned float scores. NaN = not eligible.
        Higher = higher rank. Stateless pure function of `panel`; no holdings,
        no cross-bar state. Magnitude only where honestly produced; ties are
        permitted and resolved by the allocator's TieBreakPolicy."""

        ...

    @property
    def name(self) -> str:
        return type(self).__name__


class CrossSectionalAdapter(CrossSectionalStrategy):
    """Lifts a single-asset Strategy to BINARY panel scores: applies it per
    ticker, maps a non-NaN long signal to 1.0, else NaN. Cannot manufacture a
    magnitude — a strategy whose edge IS a magnitude must be native."""

    def __init__(self, strategy: Strategy) -> None:
        self._strategy = strategy

    def generate_scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        scores_dict: dict[str, pd.Series] = {}
        for ticker, bars in panel.items():
            ticker_signals = self._strategy.generate_signals(bars)
            ticker_signals = ticker_signals.where(ticker_signals == 1.0)
            scores_dict[ticker] = ticker_signals
        return pd.DataFrame(scores_dict)

    @property
    def name(self) -> str:
        return self._strategy.name
