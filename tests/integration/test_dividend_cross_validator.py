"""Cross-validate TaiwanStockDividend against TaiwanStockDividendResult.

TaiwanStockDividendResult is the source of truth for reference-price math
(it comes from the TWSE's own computed values). TaiwanStockDividend is our
primary ingestion source — it exposes the four-component breakdown needed for
combined cash+stock events. This test guards against silent upstream changes
to either dataset by asserting that the totals and reference-price formulas
agree within rounding tolerance.

Runs only with --run-integration (hits real FinMind API; needs FINMIND_TOKEN).
"""

from datetime import date

import pandas as pd
import pytest

from twstock.data.sources.base import NoDataError, SourceError
from twstock.data.sources.finmind import FinMindSource

pytestmark = pytest.mark.integration

_TICKERS = ["2330", "1101", "2317"]
_START = date(2018, 1, 1)

# NT$ tolerance for reference-price comparisons. TWSE rounds reference prices
# to tick size (up to NT$1 for stocks above NT$500). NT$0.5 covers all tickers
# in the Top 50 universe up to NT$1000.
_PRICE_TOL = 0.5


@pytest.fixture(scope="module")
def source():
    try:
        return FinMindSource()
    except SourceError:
        pytest.skip("FINMIND_TOKEN not set")


def _fetch(fm: FinMindSource, dataset: str, ticker: str) -> pd.DataFrame:
    try:
        # _request is internal but the cleanest way to get raw rows from both
        # datasets without going through the processing layer.
        return pd.DataFrame(fm._request(dataset, {"data_id": ticker, "start_date": _START}))
    except NoDataError:
        return pd.DataFrame()


@pytest.mark.parametrize("ticker", _TICKERS)
def test_dividend_cross_validates_with_result(source, ticker):
    div = _fetch(source, "TaiwanStockDividend", ticker)
    result = _fetch(source, "TaiwanStockDividendResult", ticker)

    if div.empty or result.empty:
        pytest.skip(f"No data for {ticker} in one or both datasets")

    div = div.copy()
    div["stock_face_total"] = div["StockEarningsDistribution"] + div["StockStatutorySurplus"]
    div["cash_total"] = div["CashEarningsDistribution"] + div["CashStatutorySurplus"]

    result = result.copy()
    result["ex_date"] = pd.to_datetime(result["date"])

    errors: list[str] = []

    for _, res_row in result.iterrows():
        ex_str = res_row["ex_date"].strftime("%Y-%m-%d")
        label = res_row["stock_or_cache_dividend"]  # '息', '權', or '權息'
        before_price = float(res_row["before_price"])
        ref_price = float(res_row["reference_price"])
        sacd = float(res_row["stock_and_cache_dividend"])

        # Match the Dividend row via the per-leg ex-date column.
        # '息' (cash) uses CashExDividendTradingDate; others use StockExDividendTradingDate.
        if label == "息":
            candidates = div[div["CashExDividendTradingDate"] == ex_str]
        else:
            candidates = div[div["StockExDividendTradingDate"] == ex_str]

        if candidates.empty:
            # Fallback: search both columns (handles rare same-date mismatches).
            candidates = div[
                (div["CashExDividendTradingDate"] == ex_str)
                | (div["StockExDividendTradingDate"] == ex_str)
            ]

        if candidates.empty:
            errors.append(f"{ticker} {ex_str} ({label}): no matching row in TaiwanStockDividend")
            continue

        row = candidates.iloc[0]
        cash_total = float(row["cash_total"])
        stock_face_total = float(row["stock_face_total"])
        has_rights = (
            float(row["TotalNumberOfCashCapitalIncrease"]) > 0
            and float(row["ParticipateDistributionOfTotalShares"]) > 0
        )

        # ------------------------------------------------------------------ #
        # Reference-price check: Result's TWSE-computed reference_price should
        # match the standard TWSE formula applied to Dividend's components.
        # stock_and_cache_dividend is an accounting identity: before - ref_price,
        # so expected_sacd is derived from expected_ref rather than from raw
        # face amounts (cash_total + stock_face_total != market-value deduction).
        # ------------------------------------------------------------------ #
        if label == "息":
            expected_ref: float | None = before_price - cash_total
        elif label == "權息":
            expected_ref = (before_price - cash_total) / (1.0 + stock_face_total / 10.0)
        elif label == "權" and has_rights:
            ratio = float(row["TotalNumberOfCashCapitalIncrease"]) / float(
                row["ParticipateDistributionOfTotalShares"]
            )
            sub_price = float(row["CashIncreaseSubscriptionpRrice"])
            expected_ref = (before_price + ratio * sub_price) / (1.0 + ratio)
        elif label == "權" and not has_rights:
            expected_ref = before_price / (1.0 + stock_face_total / 10.0)
        else:
            expected_ref = None

        if expected_ref is not None:
            expected_sacd = before_price - expected_ref
            if abs(expected_sacd - sacd) > _PRICE_TOL:
                errors.append(
                    f"{ticker} {ex_str} ({label}): "
                    f"Dividend total (before-ref) {expected_sacd:.4f} != "
                    f"Result stock_and_cache_dividend {sacd:.4f}"
                )
            if abs(expected_ref - ref_price) > _PRICE_TOL:
                errors.append(
                    f"{ticker} {ex_str} ({label}): "
                    f"reference_price formula {expected_ref:.4f} != "
                    f"Result reference_price {ref_price:.4f}"
                )

    assert not errors, f"Cross-validation failures for {ticker}:\n" + "\n".join(errors)
