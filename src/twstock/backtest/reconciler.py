import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from twstock.backtest.portfolio import Portfolio


@dataclass(frozen=True)
class Order:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int


def reconcile(
    target_weights: pd.Series, portfolio: Portfolio, prices: dict[str, float]
) -> list[Order]:
    """Diff target vs current book -> orders, ALL SELLS BEFORE ALL BUYS.
    shares = floor(equity * weight / price). Odd-lot sizing (ADR 0006 §7)."""

    clean_weights = target_weights.fillna(0.0)
    all_tickers = set(portfolio.positions.keys()) | set(clean_weights.index)

    sells = []
    buys = []

    for ticker in all_tickers:
        current_shares = portfolio.positions[ticker].shares if ticker in portfolio.positions else 0
        target_weight = clean_weights.get(ticker, 0.0)
        if ticker not in prices:
            continue
        price = prices[ticker]
        target_shares = math.floor(portfolio.equity * target_weight / price)
        delta = target_shares - current_shares

        if delta == 0:
            continue
        elif delta < 0:
            sells.append(Order(ticker=ticker, side="sell", shares=-delta))
        elif delta > 0:
            buys.append(Order(ticker=ticker, side="buy", shares=delta))

    return sells + buys
