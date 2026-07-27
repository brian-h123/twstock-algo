import math

import pytest

from twstock.backtest.cost_model import TaiwanCostModel


@pytest.fixture
def model() -> TaiwanCostModel:
    return TaiwanCostModel()


class TestCommissionPct:
    def test_effective_rate_is_cap_times_discount(self, model: TaiwanCostModel):
        assert model.commission_pct == pytest.approx(0.001425 * 0.6)

    def test_effective_rate_value(self, model: TaiwanCostModel):
        assert model.commission_pct == pytest.approx(0.0008550)


class TestApplySlippage:
    def test_buy_increases_price(self, model: TaiwanCostModel):
        fill = model.apply_slippage(100.0, "buy")
        assert fill > 100.0

    def test_sell_decreases_price(self, model: TaiwanCostModel):
        fill = model.apply_slippage(100.0, "sell")
        assert fill < 100.0

    def test_buy_slippage_magnitude(self, model: TaiwanCostModel):
        fill = model.apply_slippage(100.0, "buy")
        assert fill == pytest.approx(100.0 * (1 + 5 / 10_000))

    def test_sell_slippage_magnitude(self, model: TaiwanCostModel):
        fill = model.apply_slippage(100.0, "sell")
        assert fill == pytest.approx(100.0 * (1 - 5 / 10_000))


class TestBuyCosts:
    def test_no_tax_returned(self, model: TaiwanCostModel):
        result = model.buy_costs("2330", 1000, 100.0)
        assert len(result) == 2

    def test_fill_price_above_reference(self, model: TaiwanCostModel):
        fill_price, _ = model.buy_costs("2330", 1000, 100.0)
        assert fill_price > 100.0

    def test_commission_percentage_applied(self, model: TaiwanCostModel):
        fill_price, commission = model.buy_costs("2330", 1000, 100.0)
        notional = fill_price * 1000
        assert commission == math.floor(notional * model.commission_pct)

    def test_minimum_commission_binds_on_small_order(self, model: TaiwanCostModel):
        # 1 share at NT$100 → notional ~NT$100 → pct commission ~NT$0.09, below NT$20 min
        _, commission = model.buy_costs("2330", 1, 100.0)
        assert commission == model.commission_min_twd


class TestSellCosts:
    def test_three_values_returned(self, model: TaiwanCostModel):
        result = model.sell_costs("2330", 1000, 100.0)
        assert len(result) == 3

    def test_fill_price_below_reference(self, model: TaiwanCostModel):
        fill_price, _, _ = model.sell_costs("2330", 1000, 100.0)
        assert fill_price < 100.0

    def test_stock_sell_tax_is_0_3_pct(self, model: TaiwanCostModel):
        fill_price, _, tax = model.sell_costs("2330", 1000, 100.0)
        notional = fill_price * 1000
        assert tax == math.floor(notional * 0.003)

    def test_etf_sell_tax_is_0_1_pct(self, model: TaiwanCostModel):
        fill_price, _, tax = model.sell_costs("0050", 1000, 100.0)
        notional = fill_price * 1000
        assert tax == math.floor(notional * 0.001)

    def test_etf_tax_lower_than_stock_tax(self, model: TaiwanCostModel):
        _, _, stock_tax = model.sell_costs("2330", 1000, 100.0)
        _, _, etf_tax = model.sell_costs("0050", 1000, 100.0)
        assert etf_tax < stock_tax

    def test_commission_applied_on_sell(self, model: TaiwanCostModel):
        fill_price, commission, _ = model.sell_costs("2330", 1000, 100.0)
        notional = fill_price * 1000
        assert commission == math.floor(notional * model.commission_pct)

    def test_minimum_commission_binds_on_small_sell(self, model: TaiwanCostModel):
        _, commission, _ = model.sell_costs("2330", 1, 100.0)
        assert commission == model.commission_min_twd

    def test_non_etf_ticker_uses_stock_tax(self, model: TaiwanCostModel):
        fill_price, _, tax = model.sell_costs("2454", 1000, 50.0)
        notional = fill_price * 1000
        assert tax == math.floor(notional * 0.003)


class TestCustomRates:
    def test_custom_commission_discount(self):
        promotional = TaiwanCostModel(commission_discount=0.18)
        assert promotional.commission_pct == pytest.approx(0.001425 * 0.18)

    def test_custom_etf_tickers(self):
        custom = TaiwanCostModel(etf_tickers=frozenset({"0050", "0056"}))
        _, _, tax = custom.sell_costs("0056", 1000, 100.0)
        fill_price = custom.apply_slippage(100.0, "sell")
        notional = fill_price * 1000
        assert tax == math.floor(notional * custom.etf_sell_tax)


class TestFloorRounding:
    """Floor rounding is applied to commission and tax before the NT$20 min comparison."""

    def test_buy_commission_floored_not_raw_float(self, model: TaiwanCostModel):
        # 500 shares at 548.0: commission_raw ≈ 234.39 NT$ → floor → 234
        fill_price, commission = model.buy_costs("2330", 500, 548.0)
        notional = fill_price * 500
        assert commission == math.floor(notional * model.commission_pct)
        assert isinstance(commission, int)

    def test_sell_commission_floored_not_raw_float(self, model: TaiwanCostModel):
        # 500 shares at 548.0 sold: commission_raw ≈ 234.15 NT$ → floor → 234
        fill_price, commission, _ = model.sell_costs("2330", 500, 548.0)
        notional = fill_price * 500
        assert commission == math.floor(notional * model.commission_pct)
        assert isinstance(commission, int)

    def test_sell_tax_floored_not_raw_float(self, model: TaiwanCostModel):
        # 500 shares at 548.0 sold: tax_raw ≈ 821.59 NT$ → floor → 821
        fill_price, _, tax = model.sell_costs("2330", 500, 548.0)
        notional = fill_price * 500
        assert tax == math.floor(notional * model.stock_sell_tax)
        assert isinstance(tax, int)

    def test_floor_before_min_commission_on_buy(self, model: TaiwanCostModel):
        # 200 shares at NT$1: commission_raw ≈ 0.17, floor → 0; min=20 must win
        _, commission = model.buy_costs("2330", 200, 1.0)
        assert commission == model.commission_min_twd

    def test_floor_before_min_commission_on_sell(self, model: TaiwanCostModel):
        # 200 shares at NT$1: commission_raw ≈ 0.17, floor → 0; min=20 must win
        _, commission, _ = model.sell_costs("2330", 200, 1.0)
        assert commission == model.commission_min_twd
