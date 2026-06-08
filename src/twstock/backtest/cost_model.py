from dataclasses import dataclass


@dataclass(frozen=True)
class TaiwanCostModel:
    """Cost model for Taiwan equities.

    All rates verified against primary sources (May 2026):

    - Securities transaction tax: Securities Transaction Tax Act Art. 2
      (https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340078).
      0.3% on sells of stocks (Art. 2(1), 千分之三),
      0.1% on sells of ETFs incl. 0050 (Art. 2(2), 千分之一).
      Sell-side only; buys are tax-free. Statutory, not negotiable.

    - Broker commission: Securities and Exchange Act Art. 85 sets a
      0.1425% per-side statutory maximum. Brokers may discount freely;
      the durable online-order baseline across major Taiwanese brokers
      (Fubon 富邦, Yuanta 元大, Cathay 國泰) is 6折 = 0.0855% effective.
      Stored as cap × discount so promotional rates (e.g. 1.8折 for
      Fubon new-account offers) are modeled by overriding the discount.

    - Minimum commission: broker-set, conventionally NT$20 per order for
      round-lot trades. Many brokers cut this to NT$1 for odd-lot orders.

    - Slippage: modeling assumption, 5 bps per side. Brackets a typical
      1-tick half-spread on TWSE Top-50 names (2.5–12.5 bps depending
      on price tier per the TWSE tick-size table).

    Documented simplifications (NOT modeled in Phase 3, see §9 Pitfalls):
    - T+2 cash settlement (普通交割). The Phase 3 engine treats cash as
      immediately available post-trade. Real settlement is two business
      days later, which can bind on high-turnover or fully-invested
      strategies. Phase 5 paper-sim will model this explicitly.
    - Day-trading tax halving (0.15% per Art. 2-2, extended through
      2027-12-31). Not relevant to a long-only daily-bar strategy.
    - Odd-lot trading rules (separate auction schedule, no margin/short,
      T+1-earliest sell of odd-lot buys). Phase 3 assumes round-lot
      trades. The odd_lot_commission_min_twd field is exposed on the
      dataclass for forward-compat with Phase 5 but is unused in v1.
    - TWSE exchange handling fee (0.0065%, Art. 132). Paid by the broker
      out of commission revenue, not separately billed to the investor;
      already inside `commission_cap`.
    """

    # Securities transaction tax (statutory, asymmetric, always on)
    stock_sell_tax: float = 0.003  # Art. 2(1), 千分之三
    etf_sell_tax: float = 0.001  # Art. 2(2), 千分之一

    # Broker commission (statutory cap × broker discount)
    commission_cap: float = 0.001425  # statutory maximum, both sides
    commission_discount: float = 0.6  # 6折 durable online baseline
    commission_min_twd: int = 20  # round-lot per-order minimum
    odd_lot_commission_min_twd: int = 1  # odd-lot minimum (Phase 5+)

    # Slippage (modeling assumption)
    slippage_bps_per_side: float = 5.0

    # ETF classification (drives tax-rate selection)
    etf_tickers: frozenset[str] = frozenset({"0050"})

    # Documentation metadata (not used in calculation)
    settlement: str = "T+2"

    @property
    def commission_pct(self) -> float:
        """Effective commission rate per side = cap × discount."""
        return self.commission_cap * self.commission_discount

    def apply_slippage(self, reference_price: float, side: str) -> float:
        """Return the worse-side fill price for buy/sell."""
        slippage = reference_price * (self.slippage_bps_per_side / 10_000)  # 1 bps = 1/10_000
        if side == "buy":
            return reference_price + slippage
        return reference_price - slippage

    def buy_costs(self, ticker: str, shares: int, reference_price: float) -> tuple[float, float]:
        """Returns (fill_price_after_slippage, commission_paid).
        Commission = max(notional × commission_pct, commission_min_twd).
        No tax on buys."""
        fill_price = self.apply_slippage(reference_price, "buy")
        notional = fill_price * shares
        commission = max(notional * self.commission_pct, self.commission_min_twd)
        return fill_price, commission

    def sell_costs(
        self, ticker: str, shares: int, reference_price: float
    ) -> tuple[float, float, float]:
        """Returns (fill_price_after_slippage, commission_paid, tax_paid).
        Tax rate = etf_sell_tax if ticker in etf_tickers else stock_sell_tax.
        Commission = max(notional × commission_pct, commission_min_twd)."""
        fill_price = self.apply_slippage(reference_price, "sell")
        notional = fill_price * shares
        commission = max(notional * self.commission_pct, self.commission_min_twd)
        tax_rate = self.etf_sell_tax if ticker in self.etf_tickers else self.stock_sell_tax
        tax = notional * tax_rate
        return fill_price, commission, tax
