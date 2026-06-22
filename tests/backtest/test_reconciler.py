"""Unit tests for twstock.backtest.reconciler.reconcile.

Tests use a minimal mock for Portfolio so they focus solely on the reconciler's
diffing logic without Portfolio machinery (cost model, trade recording, etc.).
All tests run without network calls or disk I/O.

Coverage:
  - All sell orders precede all buy orders in the returned list
  - Share sizing: floor(equity × weight / price), not round
  - Ticker held but not targeted → full-position sell
  - Ticker targeted but not held → full buy
  - Delta == 0 → no order emitted
  - Ticker absent from prices → silently skipped (stale/halted)
  - Rotation regression: buy sized on full equity, not near-zero cash
    (guards the silent-under-investment bug when a same-bar sell funds a buy)
"""

import math
from unittest.mock import MagicMock

import pandas as pd

from twstock.backtest.reconciler import Order, reconcile


def _portfolio(equity: float, positions: dict[str, int]) -> MagicMock:
    """Minimal Portfolio stand-in exposing only .equity and .positions."""
    mock = MagicMock()
    mock.equity = equity
    mock.positions = {t: MagicMock(shares=s) for t, s in positions.items()}
    return mock


# ─── Order ordering ───────────────────────────────────────────────────────────


def test_all_sells_precede_all_buys() -> None:
    """All sell orders must appear before any buy order in the returned list.

    The engine iterates pending_orders in list order; sells-first ensures
    that cash freed by a sell is available when the subsequent buy executes.
    """
    portfolio = _portfolio(equity=100_000, positions={"A": 100, "B": 50})
    target_weights = pd.Series({"C": 0.30, "D": 0.20})  # exit A+B, enter C+D
    prices = {"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0}

    orders = reconcile(target_weights, portfolio, prices)

    sides = [o.side for o in orders]
    sell_indices = [i for i, s in enumerate(sides) if s == "sell"]
    buy_indices = [i for i, s in enumerate(sides) if s == "buy"]

    assert sell_indices, "expected at least one sell order"
    assert buy_indices, "expected at least one buy order"
    assert max(sell_indices) < min(buy_indices), "all sells must precede all buys"


# ─── Share sizing ─────────────────────────────────────────────────────────────


def test_share_sizing_uses_floor_not_round() -> None:
    """target_shares = floor(equity × weight / price) — never rounded up.

    Uses numbers where floor and round differ: 10_000 × 0.50 / 30 = 166.667.
    floor → 166, round → 167. The order must be for 166 shares.
    """
    portfolio = _portfolio(equity=10_000, positions={})
    target_weights = pd.Series({"A": 0.50})
    prices = {"A": 30.0}

    orders = reconcile(target_weights, portfolio, prices)

    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert orders[0].shares == 166  # floor(10_000 * 0.50 / 30) = floor(166.667)


# ─── Held-not-targeted → full sell ───────────────────────────────────────────


def test_held_not_targeted_emits_full_sell() -> None:
    """A ticker currently held but absent from target weights gets a full-position sell."""
    portfolio = _portfolio(equity=50_000, positions={"A": 200})
    target_weights = pd.Series(dtype=float)  # empty — exit everything
    prices = {"A": 100.0}

    orders = reconcile(target_weights, portfolio, prices)

    assert orders == [Order(ticker="A", side="sell", shares=200)]


# ─── Targeted-not-held → buy ─────────────────────────────────────────────────


def test_targeted_not_held_emits_buy() -> None:
    """A ticker in target weights but not currently held gets a buy order."""
    portfolio = _portfolio(equity=100_000, positions={})
    target_weights = pd.Series({"A": 0.20})
    prices = {"A": 50.0}

    orders = reconcile(target_weights, portfolio, prices)

    expected_shares = math.floor(100_000 * 0.20 / 50.0)  # = 400
    assert orders == [Order(ticker="A", side="buy", shares=expected_shares)]


# ─── Delta == 0 → no order ───────────────────────────────────────────────────


def test_no_order_when_target_matches_current_shares() -> None:
    """No order is emitted when current shares already equal the target shares."""
    equity = 100_000
    price = 50.0
    weight = 0.20
    target_shares = math.floor(equity * weight / price)  # = 400

    portfolio = _portfolio(equity=equity, positions={"A": target_shares})
    target_weights = pd.Series({"A": weight})
    prices = {"A": price}

    orders = reconcile(target_weights, portfolio, prices)

    assert orders == []


# ─── Missing price → skip ────────────────────────────────────────────────────


def test_ticker_absent_from_prices_is_skipped() -> None:
    """Tickers not present in `prices` are silently skipped (stale/halted ticker)."""
    portfolio = _portfolio(equity=100_000, positions={"A": 100})
    target_weights = pd.Series({"A": 0.30, "B": 0.20})
    prices = {"A": 100.0}  # B absent — e.g., trading halt today

    orders = reconcile(target_weights, portfolio, prices)

    order_tickers = {o.ticker for o in orders}
    assert "B" not in order_tickers


# ─── Rotation regression ─────────────────────────────────────────────────────


def test_rotation_buy_sized_on_equity_not_cash() -> None:
    """Buy is sized on full portfolio equity even when available cash is near zero.

    This is the silent-under-investment guard: if buy shares were computed from
    available cash (≈ 0 when fully invested in A), the buy order for B would be
    too small or zero. Correct behaviour: size on equity and rely on the engine
    executing the sell of A before the buy of B so that cash is available at fill.
    """
    equity = 100_000
    # Portfolio fully invested in A — cash is effectively 0
    portfolio = _portfolio(equity=equity, positions={"A": 1_000})
    target_weights = pd.Series({"B": 0.50})  # full exit of A, enter B at 50%
    prices = {"A": 100.0, "B": 200.0}

    orders = reconcile(target_weights, portfolio, prices)

    sells = [o for o in orders if o.side == "sell"]
    buys = [o for o in orders if o.side == "buy"]

    # Exit A fully
    assert len(sells) == 1
    assert sells[0] == Order(ticker="A", side="sell", shares=1_000)

    # Enter B sized on full equity (not near-zero cash)
    expected_buy_shares = math.floor(equity * 0.50 / 200.0)  # = 250
    assert len(buys) == 1
    assert buys[0] == Order(ticker="B", side="buy", shares=expected_buy_shares)

    # Sell precedes buy in the list
    sell_pos = next(i for i, o in enumerate(orders) if o.side == "sell")
    buy_pos = next(i for i, o in enumerate(orders) if o.side == "buy")
    assert sell_pos < buy_pos
