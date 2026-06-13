from dataclasses import dataclass
from typing import Literal

import pandas as pd

from twstock.backtest.cost_model import TaiwanCostModel


class InsufficientCashError(Exception):
    pass


@dataclass
class Position:
    ticker: str
    shares: int
    avg_cost: float  # cost basis per share, post-slippage, post-commission


@dataclass
class Trade:
    timestamp: pd.Timestamp
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    fill_price: float
    commission: float
    tax: float
    notional: float  # shares * fill_price
    total_cost: float  # commission + tax + slippage_cost_implied


class Portfolio:
    def __init__(self, initial_capital: float, cost_model: TaiwanCostModel) -> None:
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._cost_model = cost_model
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []
        self._equity = initial_capital

    def buy(
        self, ticker: str, shares: int, reference_price: float, timestamp: pd.Timestamp
    ) -> Trade:
        if shares <= 0:
            raise ValueError(f"shares must be a positive integer, got {shares}")

        fill_price, commission = self._cost_model.buy_costs(
            ticker=ticker, shares=shares, reference_price=reference_price
        )
        notional = shares * fill_price
        total_cash_out = notional + commission

        if self._cash < total_cash_out:
            raise InsufficientCashError(
                f"Need {total_cash_out:.2f} but only {self._cash:.2f} available"
            )
        self._cash -= total_cash_out

        if ticker in self._positions:
            existing = self._positions[ticker]
            new_shares = existing.shares + shares
            existing.avg_cost = (existing.shares * existing.avg_cost + total_cash_out) / new_shares
            existing.shares = new_shares
        else:
            self._positions[ticker] = Position(ticker, shares, total_cash_out / shares)

        trade = Trade(
            timestamp=timestamp,
            ticker=ticker,
            side="buy",
            shares=shares,
            fill_price=fill_price,
            commission=commission,
            tax=0,
            notional=notional,
            total_cost=commission,
        )
        self._trades.append(trade)
        return trade

    def sell(
        self, ticker: str, shares: int, reference_price: float, timestamp: pd.Timestamp
    ) -> Trade:
        if shares <= 0:
            raise ValueError(f"shares must be a positive integer, got {shares}")

        if ticker not in self._positions:
            raise ValueError(f"No position in {ticker} to sell")
        if shares > self._positions[ticker].shares:
            raise ValueError(
                f"Cannot sell {shares} shares; only hold {self._positions[ticker].shares}"
            )

        fill_price, commission, tax = self._cost_model.sell_costs(
            ticker=ticker, shares=shares, reference_price=reference_price
        )
        notional = shares * fill_price
        total_cash_in = notional - commission - tax
        self._cash += total_cash_in

        existing = self._positions[ticker]
        if shares == existing.shares:
            self._positions.pop(ticker)
        else:
            existing.shares -= shares

        trade = Trade(
            timestamp=timestamp,
            ticker=ticker,
            side="sell",
            shares=shares,
            fill_price=fill_price,
            commission=commission,
            tax=tax,
            notional=notional,
            total_cost=commission + tax,
        )
        self._trades.append(trade)
        return trade

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """Update positions' marks, return current total equity
        (cash + sum of position values at given prices)."""

        position_values = 0

        for ticker in self._positions:
            if ticker not in prices:
                raise KeyError(f"No price provided for held position: {ticker}")
            position_values += self._positions[ticker].shares * prices[ticker]

        self._equity = self._cash + position_values
        return self._equity

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)
