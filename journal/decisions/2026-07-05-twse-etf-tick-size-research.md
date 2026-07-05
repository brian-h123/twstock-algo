# TWSE Tick-Size Regulations: ETF vs. Common Stock — Technical Journal Note

**Date:** 2026-07-05
**File:** journal/decisions/2026-07-05-twse-etf-tick-size-research.md
**Status:** Empirical finding CONFIRMED, with two clarifications

## Context

Local historical OHLCV data (2016–2026) for TWSE Top 50 constituents plus the 0050 ETF (Yuanta/元大 Taiwan Top 50) was used to reverse-engineer tick size per price tier, by testing what fraction of thousands of real closing prices are exact multiples of candidate ticks.

For ordinary common stocks (validated against 24,000+ and 33,000+ real closes per tier, 100% conformance):
- < NT$10 → 0.01; NT$10–50 → 0.05; NT$50–100 → 0.1; NT$100–500 → 0.5; NT$500–1000 → 1; NT$1000+ → 5.

For the 0050 ETF (1,200+ real closes per tier, 100% conformance, no transition point across 2016–2026):
- NT$50–100 → 0.05 (not 0.1); NT$100–500 → 0.05 (not 0.5).

The question: does documented TWSE policy confirm these findings, what are the exact rule citations, and since when has the ETF-specific schedule been in effect?

## Findings

### 1. Common-stock tick schedule — CONFIRMED

The governing rule is **Article 62 of the Operating Rules of the Taiwan Stock Exchange Corporation (臺灣證券交易所股份有限公司營業細則第62條)**. The official English "Trading Mechanism Introduction" restates it verbatim: "Where the market price of a stock is less than 10 dollars per share, the tick shall be 1 cent, or 5 cents if the price is from 10 dollars to less than 50 dollars, or 10 cents if the price is from 50 dollars to less than 100 dollars, or 50 cents if the price is from 100 dollars to less than 500 dollars, or 1 dollar if the price is from 500 dollars to less than 1000 dollars, or 5 dollars if the price is 1,000 dollars or more."

This six-tier schedule matches the empirical common-stock finding exactly across all six tiers. It took effect **1 March 2005 (民國94年3月1日)**, replacing the prior tick schedule that had been in place since November 1987 (民國76年11月) — the 2005 reform notice states the schedule had gone "逾17年未檢討" (over 17 years without revision) before that change. Because the reform predates the 2016 data by over a decade, no transition is expected in the stored common-stock history — consistent with the 100% conformance observed.

Note the Article 62 wording groups "每股" (per share of stock) and "受益憑證每受益權單位" (per beneficiary unit of beneficiary certificate) together — i.e., at the Operating Rules level the stock schedule is the default that also applies to beneficiary certificates *unless* a more specific rule overrides it. That override exists for ETFs (see below). (The companion Article 63 governs the daily price-fluctuation limit.)

### 2. ETF-specific tick schedule — SEPARATE RULE, CONFIRMED

There is a dedicated ETF/beneficiary-certificate trading regulation: the **Taiwan Stock Exchange Corporation Rules Governing Trading of Beneficial Certificates (臺灣證券交易所股份有限公司受益憑證買賣辦法)**, adopted under Article 55 of the Operating Rules.

**Article 7 (第7條)** sets the tick size, verbatim (official English translation): *"The minimum tick size of ETF beneficial certificates, active ETF beneficial certificates and real estate investment trust beneficial securities is one cent, or NT$0.01, for beneficial units quoted less than NT$50, and five cents, or NT$0.05, for beneficial units quoted at more than or equal to NT$50."* In the Chinese original: 「未滿五十元者為一分，五十元以上者為五分。」

This is a flat **two-tier** schedule:
- Market price **< NT$50 → NT$0.01**
- Market price **≥ NT$50 → NT$0.05**

Article 7 further states that beneficiary certificates *other than* those enumerated (e.g., closed-end fund beneficiary certificates) fall back to the listed-stock tick rules mutatis mutandis.

This is materially flatter than the stock schedule and confirms the 0050 finding: at NT$50–100 the ETF tick is 0.05 (vs. 0.1 for stocks) and at NT$100–500 it is 0.05 (vs. 0.5 for stocks).

**Effective date: 1 June 2015 (民國104年6月1日).** The standalone Article 7 amendment was promulgated by TWSE letter 臺證交字第1040005841號 on 2 April 2015, approved by FSC letter 金管證交字第1040007034號 dated 31 March 2015, and implemented from 1 June 2015 — the same date the market-wide daily price limit widened from 7% to 10%. This was the only amendment in the rule's history that revised Article 7 alone, which identifies it as the discrete ETF tick-size reform. Before 1 June 2015, ETF beneficiary certificates used the six-tier stock schedule.

### 3. ETF sub-category distinctions

The Rules Governing Trading of Beneficial Certificates (Article 2-1) classify ETFs into domestic-component, foreign-component, leveraged/inverse, futures-trust, offshore, and active ETFs. For tick size, Article 7 does **not** distinguish among them — the 0.01/0.05 two-tier schedule applies uniformly to all securities-investment-trust ETF, active-ETF, and REIT beneficiary units (foreign-currency-denominated ETFs quote the same tick in the listing currency). The sub-category distinctions matter primarily for price limits, lot size, and risk-disclosure requirements, not tick size.

### 4. Price limits, lot sizes, settlement (for a paper-trading simulator)

**Daily price fluctuation limit:**
- Common stocks: **±10%**, effective **1 June 2015**. Per Taiwan's Financial Supervisory Commission, "the price variation limit will be increased from 7% to 10% on June 1, 2015," part of the FSC's February 2015 market-uplift package. (Historical context: the TWSE limit was ±5% at its founding, widened to ±7% in 1989, and to ±10% on 1 June 2015.) Confirmed.
- Domestic-component ETFs: **±10%** (Beneficial Certificate Rules Article 8 applies the stock limit mutatis mutandis). Same for domestic ETNs, REITs, and futures ETFs tracking a domestic index.
- **No price limit** for: foreign-component securities-investment-trust ETFs and their underlying (feeder) certificates; futures ETFs tracking a foreign futures index; offshore ETFs; active ETFs with foreign components. Per TWSE's *2025 Guide to Investing in Taiwan*, the exceptions to the 10% limit "include the first five trading days of newly listed stocks, cross-listed ETFs, ETFs comprising foreign securities, ETNs comprising foreign securities, foreign bonds and government bonds." Confirmed.
- **Leveraged/inverse ETFs (all-domestic components): ±10% × the fund multiple** (e.g., a 2× fund is ±20%; a −1× fund is ±10%). If components include one or more foreign securities: no price limit.
- Bonds: ±5%. First 5 trading days of newly listed common stock: no limit.

**Trading units (lot sizes):**
- Per TWSE's *2025 Guide to Investing in Taiwan*: "One trading unit is 1,000 shares for stocks, warrants, domestic ETFs and ETNs. Some listed securities, such as cross-listed ETFs and foreign stocks, are not limited to 1,000 shares for one trading unit and are, instead, subject to the issuer's choice."
- Foreign-component ETFs, futures ETFs, leveraged/inverse ETFs, REITs, closed-end beneficiary certificates: 1,000 units (Beneficial Certificate Rules Article 6). Offshore ETFs and secondary-listed foreign stocks: issuer-determined.
- Odd lots: 1–999 units, tradable in the intraday odd-lot session (9:00–13:30) and after-hours odd-lot session (13:40–14:30).

**Settlement:** T+2 for both stocks and ETFs (Beneficial Certificate Rules Article 22 applies the Operating Rules' clearing/settlement provisions mutatis mutandis).

**Securities transaction tax (seller-paid):** Per Taiwan's Securities Transaction Tax Act, Article 2, the tax is "0.3% for shares or share certificates" and "0.1% for corporate bonds and other securities approved by the government" — the 0.1% rate applies to ETFs, beneficiary certificates, and warrants. Bond ETFs are tax-exempt: the Act suspends securities-transaction tax "on the beneficial certificates of those [bond] funds publicly offered and issued by the securities investment trust enterprises ... during the period from January 1, 2017 to December 31, 2026." REITs are also exempt. Relevant if the simulator models net P&L.

## Conclusion

**The empirical findings are confirmed by documented TWSE policy, with two clarifications.**

1. **Common stocks:** The reverse-engineered six-tier schedule matches Operating Rules Article 62 exactly, in effect since 1 March 2005. No discrepancy.

2. **0050 ETF:** The observed 0.05 tick across the NT$50–500 range is confirmed by Article 7 of the Rules Governing Trading of Beneficial Certificates. Two clarifications:
   - **(a) The official ETF schedule is even flatter than framed.** It is not a multi-tier table but a single break at NT$50: 0.01 below, **0.05 for everything ≥ NT$50 with no further tiers.** So the 0.05 tick continues above NT$500 and above NT$1000 — it does *not* step up to 1 or 5 as stocks do. The data only spans 0050's historical ~NT$50–200 range, so the higher tiers were never observed, but a simulator must apply 0.05 for any ETF price ≥ NT$50 without exception.
   - **(b) The rule took effect 1 June 2015, not at the fund's 2003 launch.** It predates the 2016 data start, which is why no transition appears in the stored history — confirming the "no transition point" observation for 2016–2026. However, from 0050's 2003 launch until 31 May 2015, 0050 used the stock schedule (0.1 at NT$50–100, 0.5 at NT$100–500). If the dataset is ever extended before June 2015, a tick-size regime change at 1 June 2015 should be expected.

**Recommendations for the paper-trading simulator:**
- Model common stocks with the six-tier Article 62 schedule and ±10% limit.
- Model domestic ETFs (including 0050) with the two-tier 0.01/0.05 schedule (single break at NT$50) and ±10% limit.
- Treat foreign-component, offshore, and foreign-index futures ETFs as having **no** price limit; apply the leverage-multiplied limit (±10% × multiple) for all-domestic leveraged/inverse ETFs.
- Use 1,000-unit lots and T+2 settlement for both stocks and domestic ETFs.
- Apply the reduced 0.1% seller-side transaction tax to ETFs (vs. 0.3% for stocks); treat bond ETFs/REITs as tax-exempt through 31 Dec 2026.
- **Benchmark that would change the ETF-tick logic:** if the simulation window is extended before 1 June 2015, switch pre-2015-06-01 ETF ticks to the six-tier stock schedule.

## Caveats

- Article numbers cited for the Operating Rules (62 for tick, 63 for price limit, 55 as the enabling article for the Beneficial Certificate Rules) reflect the current consolidated text; the Operating Rules are periodically renumbered, so verify against the live full-text page if citing a specific historical version.
- The exact verbatim *before/after* wording of the 1 June 2015 Article 7 revision could not be pulled from a free source (rootlaw's diff view is members-only); the effective date and current text are firmly sourced, but if an exact prior-wording comparison is required, consult the TWSE official announcement 臺證交字第1040005841號 (2 April 2015) or a paid legal database.
- English translations of TWSE rules are by Baker McKenzie (Taipei) and are for reference; the Chinese text governs in case of conflict.

## Sources / Citations

- Operating Rules of the TWSE (營業細則), full text — https://twse-regulation.twse.com.tw/m/LawContent.aspx?FID=FL007304 (Article 62 tick size; Article 63 price limit)
- TWSE "Trading Mechanism Introduction" (English), tick table, price-limit table, lot sizes — https://www.twse.com.tw/en/products/system/trading.html
- TWSE 集中市場交易制度介紹 (Chinese), Article 62/63 examples — https://www.twse.com.tw/zh/products/system/trading.html
- Rules Governing Trading of Beneficial Certificates (受益憑證買賣辦法), English — https://twse-regulation.twse.com.tw/m/en/LawContent.aspx?FID=FL007114 (Article 6 lot size; Article 7 ETF tick; Article 8 price limits; Article 2-1 ETF sub-categories)
- Rules Governing Trading of Beneficial Certificates, Chinese full text — https://twse-regulation.twse.com.tw/m/LawContent.aspx?FID=FL007114
- TWSE 2005 tick-size reform notice (94年3月1日實施; prior schedule since 76年11月) — https://www.twse.com.tw/downloads/zh/trading/introduce/introduce004.pdf
- Amendment history of 受益憑證買賣辦法 (Article 7 amended 104/4/2, effective 104/6/1) — https://www.rootlaw.com.tw/LawHistory.aspx?LawID=A040390051026800-1131231
- TWSE 放寬漲跌幅度專區 (7%→10% effective 2015-06-01) — https://www.twse.com.tw/zh/page/trading/information8.html
- CNA news on 7%→10% limit widening (31 May 2015) — https://www.cna.com.tw/news/firstnews/201505310038.aspx
- TWSE "2025 Guide to Investing in Taiwan" (lot sizes, price-limit exceptions, tax) — https://www.twse.com.tw/en/about/company/guide.html