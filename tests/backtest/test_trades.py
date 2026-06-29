import pandas as pd
import pytest

from twstock.backtest.trades import Grouping, LotLedger, Matching, Side, build_round_trips

_TRADES_COLS = [
    "timestamp",
    "ticker",
    "side",
    "shares",
    "fill_price",
    "commission",
    "tax",
    "notional",
    "total_cost",
]


def _make_trades(rows: list[tuple]) -> pd.DataFrame:
    """Build a trades DataFrame.

    Each row: (date_str, ticker, side, price, shares, commission, tax)
    """
    records = []
    for date_str, ticker, side, price, shares, commission, tax in rows:
        records.append(
            {
                "timestamp": pd.Timestamp(date_str),
                "ticker": ticker,
                "side": side,
                "shares": int(shares),
                "fill_price": float(price),
                "commission": float(commission),
                "tax": float(tax),
                "notional": float(price * shares),
                "total_cost": float(commission + tax),
            }
        )
    return pd.DataFrame(records)


# ── empty / no-sells ─────────────────────────────────────────────────────────


class TestEmpty:
    def test_empty_input_returns_correct_columns(self):
        result = build_round_trips(pd.DataFrame(columns=_TRADES_COLS))
        assert result.empty
        assert list(result.columns) == ["ticker", "entry_price", "exit_price", "shares", "ret"]

    def test_buys_only_returns_empty(self):
        # No sells → no closed round-trip regardless of grouping.
        trades = _make_trades([("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0)])
        assert build_round_trips(trades).empty


# ── regression invariant ─────────────────────────────────────────────────────
# FLAT_TO_FLAT + FIFO must reproduce old _round_trips on all-or-nothing trades.


class TestRegression:
    def test_single_win(self):
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["entry_price"] == pytest.approx(100.0)
        assert row["exit_price"] == pytest.approx(110.0)
        assert row["shares"] == 1000
        assert row["ret"] == pytest.approx(0.10)

    def test_single_loss(self):
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 90.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert result.iloc[0]["ret"] == pytest.approx(-0.10)

    def test_two_tickers_paired_independently(self):
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 0.0, 0.0),
                ("2020-01-02", "2454", "buy", 200.0, 500, 0.0, 0.0),
                ("2020-01-04", "2454", "sell", 180.0, 500, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert len(result) == 2
        rets = dict(zip(result["ticker"], result["ret"]))
        assert rets["2330"] == pytest.approx(0.10)
        assert rets["2454"] == pytest.approx(-0.10)

    def test_sequential_round_trips_emit_two_rows(self):
        # buy → sell → buy → sell: two separate lifecycles, two rows in order.
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 0.0, 0.0),
                ("2020-01-06", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-07", "2330", "sell", 90.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert len(result) == 2
        assert result.iloc[0]["ret"] == pytest.approx(0.10)
        assert result.iloc[1]["ret"] == pytest.approx(-0.10)


# ── FILL_TO_FILL grouping ─────────────────────────────────────────────────────


class TestFillToFill:
    def test_emits_one_row_per_sell_fill(self):
        # 1 buy, 2 partial sells → 2 rows (one per sell).
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 500, 0.0, 0.0),
                ("2020-01-06", "2330", "sell", 120.0, 500, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL, matching=Matching.FIFO)
        assert len(result) == 2
        assert result.iloc[0]["ret"] == pytest.approx(0.10)
        assert result.iloc[1]["ret"] == pytest.approx(0.20)

    def test_unpaired_buy_emits_row_for_completed_sell(self):
        # 2 buys, 1 sell → FILL_TO_FILL still emits 1 row for the sell fill.
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "buy", 105.0, 1000, 0.0, 0.0),
                ("2020-01-06", "2330", "sell", 110.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL, matching=Matching.FIFO)
        assert len(result) == 1
        # FIFO: first lot (basis=100) is consumed.
        assert result.iloc[0]["entry_price"] == pytest.approx(100.0)
        assert result.iloc[0]["ret"] == pytest.approx(0.10)

    def test_flat_to_reduced_same_as_fill_to_fill(self):
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 0.0, 0.0),
            ]
        )
        r1 = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL)
        r2 = build_round_trips(trades, grouping=Grouping.FLAT_TO_REDUCED)
        pd.testing.assert_frame_equal(r1, r2)


# ── FLAT_TO_FLAT grouping ─────────────────────────────────────────────────────


class TestFlatToFlat:
    def test_open_position_not_emitted(self):
        # Buy + partial sell leaves open_shares > 0 → no row emitted.
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 500, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert result.empty

    def test_unpaired_buy_returns_empty(self):
        # 2 buys, 1 sell → open_shares stays positive → no lifecycle closes.
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "buy", 105.0, 1000, 0.0, 0.0),
                ("2020-01-06", "2330", "sell", 110.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert result.empty

    def test_two_partial_sells_emit_one_row_on_close(self):
        # 1 buy, 2 partial sells → 1 row with weighted exit price.
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 500, 0.0, 0.0),
                ("2020-01-06", "2330", "sell", 120.0, 500, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert len(result) == 1
        row = result.iloc[0]
        # exit_price = (500 * 110 + 500 * 120) / 1000 = 115
        assert row["entry_price"] == pytest.approx(100.0)
        assert row["exit_price"] == pytest.approx(115.0)
        assert row["shares"] == 1000
        assert row["ret"] == pytest.approx(0.15)

    def test_weighted_entry_price_across_multiple_lots(self):
        # 2 buys at different prices → 1 sell closing all → weighted avg entry.
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 500, 0.0, 0.0),
                ("2020-01-03", "2330", "buy", 110.0, 500, 0.0, 0.0),
                ("2020-01-06", "2330", "sell", 120.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert len(result) == 1
        # entry_price = (500 * 100 + 500 * 110) / 1000 = 105
        assert result.iloc[0]["entry_price"] == pytest.approx(105.0)
        assert result.iloc[0]["ret"] == pytest.approx(120.0 / 105.0 - 1)


# ── FIFO vs LIFO matching ─────────────────────────────────────────────────────


class TestMatching:
    def test_fifo_consumes_oldest_lot_first(self):
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 500, 0.0, 0.0),  # Lot A (older)
                ("2020-01-03", "2330", "buy", 110.0, 500, 0.0, 0.0),  # Lot B (newer)
                ("2020-01-06", "2330", "sell", 120.0, 500, 0.0, 0.0),  # closes Lot A
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL, matching=Matching.FIFO)
        assert result.iloc[0]["entry_price"] == pytest.approx(100.0)
        assert result.iloc[0]["ret"] == pytest.approx(0.20)

    def test_lifo_consumes_newest_lot_first(self):
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 500, 0.0, 0.0),  # Lot A (older)
                ("2020-01-03", "2330", "buy", 110.0, 500, 0.0, 0.0),  # Lot B (newer)
                ("2020-01-06", "2330", "sell", 120.0, 500, 0.0, 0.0),  # closes Lot B
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL, matching=Matching.LIFO)
        assert result.iloc[0]["entry_price"] == pytest.approx(110.0)
        assert result.iloc[0]["ret"] == pytest.approx(120.0 / 110.0 - 1)

    def test_fifo_and_lifo_diverge_on_partial_sell(self):
        # Sanity: the two modes produce different entry prices when lots differ.
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 500, 0.0, 0.0),
                ("2020-01-03", "2330", "buy", 110.0, 500, 0.0, 0.0),
                ("2020-01-06", "2330", "sell", 120.0, 500, 0.0, 0.0),
            ]
        )
        fifo = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL, matching=Matching.FIFO)
        lifo = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL, matching=Matching.LIFO)
        assert fifo.iloc[0]["entry_price"] != lifo.iloc[0]["entry_price"]


# ── cost-adjusted basis (Option B) ───────────────────────────────────────────


class TestCostAdjusted:
    def test_buy_commission_raises_basis(self):
        # Buy 1000 @ 100 with commission=100 → basis = (100_000 + 100) / 1000 = 100.1
        # Sell 1000 @ 110 with no costs → exit = 110.0
        # ret = 110.0 / 100.1 - 1  (lower than gross 10%)
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 100.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        row = result.iloc[0]
        assert row["entry_price"] == pytest.approx(100.1)
        assert row["exit_price"] == pytest.approx(110.0)
        assert row["ret"] == pytest.approx(110.0 / 100.1 - 1)
        assert row["ret"] < 0.10  # strictly less than gross 10%

    def test_sell_costs_reduce_exit(self):
        # Buy 1000 @ 100 with no costs → basis = 100.0
        # Sell 1000 @ 110, commission=94.05, tax=330 → exit = (110_000 - 424.05) / 1000
        # ret = 109.57595 / 100.0 - 1  (lower than gross 10%)
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 94.05, 330.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        row = result.iloc[0]
        expected_exit = (110_000 - 424.05) / 1000  # = 109.57595
        assert row["exit_price"] == pytest.approx(expected_exit)
        assert row["ret"] == pytest.approx(expected_exit / 100.0 - 1)
        assert row["ret"] < 0.10

    def test_taiwan_costs_both_sides(self):
        # Realistic Taiwan: 0.0855% commission per side, 0.3% sell tax.
        # Buy 1000 @ 100: commission=85.5, tax=0 → basis=100.0855
        # Sell 1000 @ 110: commission=94.05, tax=330 → exit=109.57595
        # ret = 109.57595 / 100.0855 - 1  (meaningfully below gross 10%)
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 85.5, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 94.05, 330.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        row = result.iloc[0]
        expected_basis = (100_000 + 85.5) / 1000  # 100.0855
        expected_exit = (110_000 - 424.05) / 1000  # 109.57595
        assert row["entry_price"] == pytest.approx(expected_basis)
        assert row["exit_price"] == pytest.approx(expected_exit)
        assert row["ret"] == pytest.approx(expected_exit / expected_basis - 1)

    def test_fill_to_fill_uses_cost_adjusted_exit(self):
        # FILL_TO_FILL branch must also use the cost-adjusted exit (not raw fill_price).
        # Buy 1000 @ 100, no buy costs → basis=100.0
        # Sell 1000 @ 110, commission=94.05, tax=330 → exit=109.57595
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 94.05, 330.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FILL_TO_FILL, matching=Matching.FIFO)
        row = result.iloc[0]
        expected_exit = (110_000 - 424.05) / 1000
        assert row["exit_price"] == pytest.approx(expected_exit)
        assert row["ret"] == pytest.approx(expected_exit / 100.0 - 1)
        assert row["ret"] < 0.10  # not the gross 10%

    def test_zero_cost_unchanged(self):
        # Zero costs: Option B collapses to Option A (basis == fill_price).
        trades = _make_trades(
            [
                ("2020-01-02", "2330", "buy", 100.0, 1000, 0.0, 0.0),
                ("2020-01-03", "2330", "sell", 110.0, 1000, 0.0, 0.0),
            ]
        )
        result = build_round_trips(trades, grouping=Grouping.FLAT_TO_FLAT, matching=Matching.FIFO)
        assert result.iloc[0]["entry_price"] == pytest.approx(100.0)
        assert result.iloc[0]["exit_price"] == pytest.approx(110.0)
        assert result.iloc[0]["ret"] == pytest.approx(0.10)


# ── LotLedger.open_shares ────────────────────────────────────────────────────


class TestLotLedgerOpenShares:
    def test_starts_at_zero(self):
        assert LotLedger().open_shares == 0

    def test_buy_increments(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        assert ledger.open_shares == 1000

    def test_partial_sell_decrements(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=400, price=110.0, cost=0.0)
        assert ledger.open_shares == 600

    def test_full_close_reaches_zero(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=1000, price=110.0, cost=0.0)
        assert ledger.open_shares == 0


# ── LotLedger.avg_cost ───────────────────────────────────────────────────────


class TestLotLedgerAvgCost:
    def test_none_when_flat(self):
        assert LotLedger().avg_cost is None

    def test_single_lot_no_cost(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        assert ledger.avg_cost == pytest.approx(100.0)

    def test_buy_cost_raises_basis(self):
        # Buy 1000 @ 100 with commission=85.5 → basis = (100_000 + 85.5) / 1000
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=85.5)
        assert ledger.avg_cost == pytest.approx(100.0855)

    def test_two_lots_weighted_average(self):
        # 500 @ 100 + 500 @ 110 → avg = 105
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=500, price=100.0, cost=0.0)
        ledger.on_fill(Side.BUY, shares=500, price=110.0, cost=0.0)
        assert ledger.avg_cost == pytest.approx(105.0)

    def test_none_after_full_close(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=1000, price=110.0, cost=0.0)
        assert ledger.avg_cost is None

    def test_partial_sell_reflects_remaining_lots(self):
        # FIFO sell 500 from (500@100, 500@110) → lot A consumed → remaining avg = 110
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=500, price=100.0, cost=0.0)
        ledger.on_fill(Side.BUY, shares=500, price=110.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=120.0, cost=0.0)
        assert ledger.avg_cost == pytest.approx(110.0)


# ── LotLedger.round_trips ────────────────────────────────────────────────────


class TestLotLedgerRoundTrips:
    def test_no_sells_returns_empty(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        assert ledger.round_trips() == []

    def test_open_position_flat_to_flat_returns_empty(self):
        # Partial sell leaves lifecycle open → FLAT_TO_FLAT emits nothing.
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=110.0, cost=0.0)
        assert ledger.round_trips(Grouping.FLAT_TO_FLAT) == []

    def test_flat_to_flat_single_lifecycle(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=1000, price=110.0, cost=0.0)
        result = ledger.round_trips(Grouping.FLAT_TO_FLAT)
        assert len(result) == 1
        assert result[0]["entry_price"] == pytest.approx(100.0)
        assert result[0]["exit_price"] == pytest.approx(110.0)
        assert result[0]["shares"] == 1000
        assert result[0]["ret"] == pytest.approx(0.10)

    def test_flat_to_flat_two_partial_sells_one_row(self):
        # 1 buy, 2 partial sells close the position → 1 row with weighted exit.
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=110.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=120.0, cost=0.0)
        result = ledger.round_trips(Grouping.FLAT_TO_FLAT)
        assert len(result) == 1
        # exit_price = (500 * 110 + 500 * 120) / 1000 = 115
        assert result[0]["exit_price"] == pytest.approx(115.0)
        assert result[0]["ret"] == pytest.approx(0.15)

    def test_flat_to_flat_two_sequential_lifecycles(self):
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=1000, price=110.0, cost=0.0)
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=1000, price=90.0, cost=0.0)
        result = ledger.round_trips(Grouping.FLAT_TO_FLAT)
        assert len(result) == 2
        assert result[0]["ret"] == pytest.approx(0.10)
        assert result[1]["ret"] == pytest.approx(-0.10)

    def test_fill_to_fill_one_row_per_sell(self):
        # 1 buy, 2 partial sells → 2 rows regardless of open/closed state.
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=110.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=120.0, cost=0.0)
        result = ledger.round_trips(Grouping.FILL_TO_FILL)
        assert len(result) == 2
        assert result[0]["ret"] == pytest.approx(0.10)
        assert result[1]["ret"] == pytest.approx(0.20)

    def test_fill_to_fill_emits_row_with_open_position(self):
        # FILL_TO_FILL does not require the lifecycle to be closed.
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=110.0, cost=0.0)
        assert len(ledger.round_trips(Grouping.FILL_TO_FILL)) == 1

    def test_same_ledger_two_groupings(self):
        # Same closed slices, different grouping → different row counts.
        ledger = LotLedger()
        ledger.on_fill(Side.BUY, shares=1000, price=100.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=110.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=120.0, cost=0.0)
        assert len(ledger.round_trips(Grouping.FLAT_TO_FLAT)) == 1
        assert len(ledger.round_trips(Grouping.FILL_TO_FILL)) == 2


# ── LotLedger LIFO matching ──────────────────────────────────────────────────


class TestLotLedgerLIFO:
    def test_lifo_consumes_newest_lot_first(self):
        ledger = LotLedger(matching=Matching.LIFO)
        ledger.on_fill(Side.BUY, shares=500, price=100.0, cost=0.0)  # lot A (older)
        ledger.on_fill(Side.BUY, shares=500, price=110.0, cost=0.0)  # lot B (newer)
        ledger.on_fill(Side.SELL, shares=500, price=120.0, cost=0.0)  # consumes lot B
        result = ledger.round_trips(Grouping.FILL_TO_FILL)
        assert result[0]["entry_price"] == pytest.approx(110.0)
        assert result[0]["ret"] == pytest.approx(120.0 / 110.0 - 1)

    def test_lifo_avg_cost_reflects_remaining_older_lot(self):
        # After consuming the newer lot, only lot A (100.0) remains.
        ledger = LotLedger(matching=Matching.LIFO)
        ledger.on_fill(Side.BUY, shares=500, price=100.0, cost=0.0)
        ledger.on_fill(Side.BUY, shares=500, price=110.0, cost=0.0)
        ledger.on_fill(Side.SELL, shares=500, price=120.0, cost=0.0)
        assert ledger.avg_cost == pytest.approx(100.0)
