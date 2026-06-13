import pandas as pd
import pytest

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.portfolio import InsufficientCashError, Portfolio, Trade

CAPITAL = 1_000_000.0
TS = pd.Timestamp("2024-01-02 09:00:00")

# Pre-computed expected values for default TaiwanCostModel
# Buy: ticker="2330", shares=1000, reference_price=100.0
#   fill_price = 100.0 * (1 + 5/10000) = 100.05
#   notional   = 1000 * 100.05 = 100_050.0
#   commission = 100_050 * (0.001425 * 0.6) = 100_050 * 0.000855 = 85.54275
#   total_cash_out = 100_050 + 85.54275 = 100_135.54275
#   avg_cost       = 100_135.54275 / 1000 = 100.13554275
BUY_FILL = 100.05
BUY_COMM = 85.54275
BUY_TOTAL = 100_135.54275
BUY_AVG = 100.13554275

# Sell: ticker="2330", shares=1000, reference_price=110.0
#   fill_price = 110.0 * (1 - 5/10000) = 109.945
#   notional   = 1000 * 109.945 = 109_945.0
#   commission = 109_945 * 0.000855 = 94.002975
#   tax        = 109_945 * 0.003   = 329.835
#   total_cash_in = 109_945 - 94.002975 - 329.835 = 109_521.162025
SELL_FILL = 109.945
SELL_COMM = 94.002975
SELL_TAX = 329.835
SELL_IN = 109_521.162025


@pytest.fixture
def cost_model():
    return TaiwanCostModel()


@pytest.fixture
def portfolio(cost_model):
    return Portfolio(initial_capital=CAPITAL, cost_model=cost_model)


class TestBuy:
    def test_creates_new_position(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        assert "2330" in portfolio.positions
        assert portfolio.positions["2330"].shares == 1000

    def test_cash_deducted_correctly(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        assert portfolio.cash == pytest.approx(CAPITAL - BUY_TOTAL, rel=1e-9)

    def test_avg_cost_is_all_in(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        assert portfolio.positions["2330"].avg_cost == pytest.approx(BUY_AVG, rel=1e-9)

    def test_adds_to_existing_position_shares(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.buy("2330", 500, 105.0, TS)
        assert portfolio.positions["2330"].shares == 1500

    def test_adds_to_existing_position_avg_cost(self, portfolio):
        # Second buy: fill=105.0525, notional=52526.25, commission=44.90994375
        # total_cash_out_2 = 52571.15994375
        # new_avg_cost = (1000 * BUY_AVG + 52571.15994375) / 1500
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.buy("2330", 500, 105.0, TS)
        total_cost_2 = 500 * 105.0 * 1.0005 + 500 * 105.0525 * 0.000855
        expected = (1000 * BUY_AVG + total_cost_2) / 1500
        assert portfolio.positions["2330"].avg_cost == pytest.approx(expected, rel=1e-9)

    def test_returns_trade_object(self, portfolio):
        trade = portfolio.buy("2330", 1000, 100.0, TS)
        assert isinstance(trade, Trade)
        assert trade.side == "buy"
        assert trade.ticker == "2330"
        assert trade.shares == 1000
        assert trade.timestamp == TS

    def test_buy_trade_has_no_tax(self, portfolio):
        trade = portfolio.buy("2330", 1000, 100.0, TS)
        assert trade.tax == 0

    def test_buy_total_cost_is_commission_only(self, portfolio):
        trade = portfolio.buy("2330", 1000, 100.0, TS)
        assert trade.total_cost == pytest.approx(BUY_COMM, rel=1e-9)

    def test_trade_recorded_in_ledger(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        assert len(portfolio.trades) == 1
        assert portfolio.trades[0].side == "buy"

    def test_insufficient_cash_raises(self, portfolio):
        with pytest.raises(InsufficientCashError):
            portfolio.buy("2330", 100_000, 100.0, TS)  # ~10M on 1M capital

    def test_zero_shares_raises(self, portfolio):
        with pytest.raises(ValueError):
            portfolio.buy("2330", 0, 100.0, TS)

    def test_negative_shares_raises(self, portfolio):
        with pytest.raises(ValueError):
            portfolio.buy("2330", -100, 100.0, TS)


class TestSell:
    def test_reduces_position_shares(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.sell("2330", 400, 110.0, TS)
        assert portfolio.positions["2330"].shares == 600

    def test_full_sell_removes_position(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.sell("2330", 1000, 110.0, TS)
        assert "2330" not in portfolio.positions

    def test_cash_increases_after_sell(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        cash_before = portfolio.cash
        portfolio.sell("2330", 1000, 110.0, TS)
        assert portfolio.cash == pytest.approx(cash_before + SELL_IN, rel=1e-9)

    def test_avg_cost_unchanged_after_partial_sell(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        avg_cost_before = portfolio.positions["2330"].avg_cost
        portfolio.sell("2330", 400, 110.0, TS)
        assert portfolio.positions["2330"].avg_cost == avg_cost_before

    def test_sell_trade_tax_applied(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        trade = portfolio.sell("2330", 1000, 110.0, TS)
        assert trade.tax > 0

    def test_stock_sell_tax_rate(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        trade = portfolio.sell("2330", 1000, 110.0, TS)
        assert trade.tax == pytest.approx(SELL_TAX, rel=1e-9)

    def test_etf_lower_tax_rate(self, portfolio):
        # ETF tax is 0.1% vs stock 0.3%
        portfolio.buy("0050", 1000, 100.0, TS)
        trade = portfolio.sell("0050", 1000, 110.0, TS)
        # fill_price = 109.945, notional = 109_945, etf_tax = 109_945 * 0.001
        assert trade.tax == pytest.approx(109_945 * 0.001, rel=1e-9)

    def test_sell_total_cost_is_commission_plus_tax(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        trade = portfolio.sell("2330", 1000, 110.0, TS)
        assert trade.total_cost == pytest.approx(SELL_COMM + SELL_TAX, rel=1e-9)

    def test_trade_recorded_in_ledger(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.sell("2330", 1000, 110.0, TS)
        assert len(portfolio.trades) == 2
        assert portfolio.trades[1].side == "sell"

    def test_no_position_raises(self, portfolio):
        with pytest.raises(ValueError, match="No position"):
            portfolio.sell("2330", 100, 110.0, TS)

    def test_insufficient_shares_raises(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        with pytest.raises(ValueError, match="Cannot sell"):
            portfolio.sell("2330", 1001, 110.0, TS)

    def test_zero_shares_raises(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        with pytest.raises(ValueError):
            portfolio.sell("2330", 0, 110.0, TS)


class TestMarkToMarket:
    def test_no_positions_returns_cash(self, portfolio):
        assert portfolio.mark_to_market({}) == pytest.approx(CAPITAL)

    def test_with_single_position(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        equity = portfolio.mark_to_market({"2330": 115.0})
        assert equity == pytest.approx(portfolio.cash + 1000 * 115.0, rel=1e-9)

    def test_with_multiple_positions(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.buy("2454", 500, 200.0, TS)
        cash = portfolio.cash
        equity = portfolio.mark_to_market({"2330": 115.0, "2454": 210.0})
        assert equity == pytest.approx(cash + 1000 * 115.0 + 500 * 210.0, rel=1e-9)

    def test_updates_equity_property(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.mark_to_market({"2330": 115.0})
        assert portfolio.equity == pytest.approx(portfolio.cash + 115_000.0, rel=1e-9)

    def test_missing_price_raises(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        with pytest.raises(KeyError):
            portfolio.mark_to_market({})

    def test_extra_prices_ignored(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        equity = portfolio.mark_to_market({"2330": 115.0, "2454": 200.0})
        assert equity == pytest.approx(portfolio.cash + 115_000.0, rel=1e-9)


class TestProperties:
    def test_initial_cash_equals_capital(self, portfolio):
        assert portfolio.cash == CAPITAL

    def test_initial_equity_equals_capital(self, portfolio):
        assert portfolio.equity == CAPITAL

    def test_positions_dict_is_isolated(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        snap = portfolio.positions
        snap["FAKE"] = None
        assert "FAKE" not in portfolio.positions

    def test_trades_list_is_isolated(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        snap = portfolio.trades
        snap.clear()
        assert len(portfolio.trades) == 1


class TestRoundTrip:
    def test_profitable_trade_increases_cash(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.sell("2330", 1000, 110.0, TS)
        assert portfolio.cash > CAPITAL

    def test_costs_reduce_gross_return(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.sell("2330", 1000, 110.0, TS)
        gross_profit = 1000 * (110.0 - 100.0)
        assert portfolio.cash < CAPITAL + gross_profit

    def test_ledger_has_both_trades(self, portfolio):
        portfolio.buy("2330", 1000, 100.0, TS)
        portfolio.sell("2330", 1000, 110.0, TS)
        assert len(portfolio.trades) == 2
        assert portfolio.trades[0].side == "buy"
        assert portfolio.trades[1].side == "sell"
