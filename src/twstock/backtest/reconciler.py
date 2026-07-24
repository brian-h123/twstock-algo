import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.portfolio import Portfolio


@dataclass(frozen=True)
class Order:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int


@dataclass(frozen=True)
class CashShortfall:
    ticker: str
    target_notional: float
    allocated_notional: float


def budget_weights(
    target_weights: pd.Series,
    current_weights: pd.Series,
    equity: float,
    gross_buying_power: float,
    prices: dict[str, float],
    cost_model: TaiwanCostModel,
) -> tuple[pd.Series, list[CashShortfall]]:
    """Proportionally scale buy-side target weights to fit within cash on
    hand plus this bar's own anticipated sell proceeds (T-close estimate).
    Sell-side weights are never touched. Buys that scale below the cost of
    the smallest possible trade (1 share + the commission floor) are
    dropped entirely and recorded as a CashShortfall."""

    if equity <= 0:
        return target_weights, []

    # sorted(): set union/intersection order depends on Python's per-process
    # string hash randomization -- without this, allocated/shortfalls order
    # (and, transitively, reconcile()'s submission order) is nondeterministic
    # across runs (ADR 0013).
    all_tickers = sorted(
        (set(target_weights.index) | set(current_weights.index)) & set(prices.keys())
    )

    target_notional = {t: equity * target_weights.get(t, 0) for t in all_tickers}
    current_notional = {t: equity * current_weights.get(t, 0) for t in all_tickers}
    delta = {t: target_notional[t] - current_notional[t] for t in all_tickers}

    sells = {t: -d for t, d in delta.items() if d < 0}
    buys = {t: d for t, d in delta.items() if d > 0}

    if not buys:
        return target_weights, []

    def sell_cost(t, notional):
        commission = max(notional * cost_model.commission_pct, cost_model.commission_min_twd)
        tax_rate = (
            cost_model.etf_sell_tax if t in cost_model.etf_tickers else cost_model.stock_sell_tax
        )
        return commission + notional * tax_rate

    estimated_sell_proceeds = sum(
        notional - sell_cost(t, notional) for t, notional in sells.items()
    )

    def buy_cost(notional):
        return max(notional * cost_model.commission_pct, cost_model.commission_min_twd)

    buying_power_net = (
        gross_buying_power + estimated_sell_proceeds - sum(buy_cost(n) for n in buys.values())
    )
    total_buy_demand = sum(buys.values())

    shortfalls = []
    allocated = dict(buys)

    if total_buy_demand > buying_power_net:
        scale = max(0.0, buying_power_net) / total_buy_demand
        allocated = {t: n * scale for t, n in buys.items()}

        for t, scaled_notional in list(allocated.items()):
            min_viable = prices[t] * 1 + cost_model.commission_min_twd
            if scaled_notional < min_viable:
                shortfalls.append(CashShortfall(t, target_notional[t], 0.0))
                allocated[t] = 0.0
            elif scaled_notional < buys[t]:
                shortfalls.append(CashShortfall(t, target_notional[t], scaled_notional))

    adjusted_weights = target_weights.copy()
    for t, notional in allocated.items():
        adjusted_weights[t] = (current_notional[t] + notional) / equity

    return adjusted_weights, shortfalls


def reconcile(
    target_weights: pd.Series, portfolio: Portfolio, prices: dict[str, float]
) -> list[Order]:
    """Diff target vs current book -> orders, ALL SELLS BEFORE ALL BUYS.
    shares = floor(equity * weight / price). Odd-lot sizing (ADR 0006 §7)."""

    clean_weights = target_weights.fillna(0.0)
    # sorted(): fixes the within-sells / within-buys submission order, which
    # otherwise depends on Python's per-process string hash randomization
    # (ADR 0013) -- the sells-before-buys invariant itself is unaffected.
    all_tickers = sorted(set(portfolio.positions.keys()) | set(clean_weights.index))

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
