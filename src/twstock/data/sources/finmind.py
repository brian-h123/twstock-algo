import os
import time
import warnings
from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv

from twstock.data.schemas import CORPORATE_ACTIONS_SCHEMA, OHLCV_SCHEMA, validate
from twstock.data.sources.base import (
    DataSource,
    NoDataError,
    RateLimitError,
    SourceError,
    SourceUnavailableError,
)


class FinMindSource(DataSource):
    def __init__(self, throttle_s: float = 0.2) -> None:
        load_dotenv()
        token = os.getenv("FINMIND_TOKEN")
        if not token:
            raise SourceError("FINMIND_TOKEN not set")
        self._token = token
        self._throttle_s = throttle_s
        self._last_call: float = 0.0

    def _request(self, dataset: str, params: dict | None = None) -> list[dict]:
        elapsed = time.time() - self._last_call
        if elapsed < self._throttle_s:
            time.sleep(self._throttle_s - elapsed)

        payload = {"dataset": dataset}
        if params:
            payload.update(params)

        response = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "twstock-algo/0.1",
            },
            timeout=30,
        )
        self._last_call = time.time()

        if response.status_code == 402:
            raise RateLimitError("request exceed rate limit")
        if response.status_code >= 500:
            raise SourceUnavailableError("FINMIND unreachable")
        response.raise_for_status()

        body = response.json()
        data = body.get("data", [])
        if not data:
            raise NoDataError("No data found")
        return data

    def get_daily_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        data = self._request(
            "TaiwanStockPrice", {"data_id": ticker, "start_date": start, "end_date": end}
        )
        df = (
            pd.DataFrame(data)[
                [
                    "date",
                    "stock_id",
                    "open",
                    "max",
                    "min",
                    "close",
                    "Trading_Volume",
                    "Trading_money",
                ]
            ]
            .rename(
                columns={
                    "stock_id": "ticker",
                    "max": "high",
                    "min": "low",
                    "Trading_Volume": "volume",
                    "Trading_money": "value",
                }
            )
            .astype({"date": "datetime64[ns]", "volume": "int64", "value": "float64"})
        )
        # Drop rows where open or close is 0 — trading halts and anomalous
        # listing/restructuring days where FinMind has no valid price.
        df = df[(df["open"] != 0) & (df["close"] != 0)]
        return validate(df, OHLCV_SCHEMA)

    def _build_dividends(self, dividend_data: list[dict]) -> pd.DataFrame:
        _EMPTY = pd.DataFrame(
            {
                "ex_date": pd.Series(dtype="datetime64[ns]"),
                "ticker": pd.Series(dtype="str"),
                "action_type": pd.Series(dtype="str"),
                "cash_amount": pd.Series(dtype="float64"),
                "ratio": pd.Series(dtype="float64"),
                "subscription_price": pd.Series(dtype="float64"),
            }
        )
        rows: list[dict] = []
        for raw in dividend_data:
            stock_face_total = raw["StockEarningsDistribution"] + raw["StockStatutorySurplus"]
            cash_total = raw["CashEarningsDistribution"] + raw["CashStatutorySurplus"]
            has_rights = (
                raw["TotalNumberOfCashCapitalIncrease"] > 0
                and raw["ParticipateDistributionOfTotalShares"] > 0
            )
            has_cash = cash_total > 0
            has_stock = stock_face_total > 0
            ticker = raw["stock_id"]
            cash_ex = raw["CashExDividendTradingDate"]
            stock_ex = raw["StockExDividendTradingDate"]

            if raw["year"] == "不適用" and not has_rights:
                continue
            if not has_cash and not has_stock and not has_rights:
                continue

            if has_rights:
                # Dilution ratio from share counts, not CashIncreaseSubscriptionRate.
                # CashIncreaseSubscriptionRate appears to be the existing-shareholder
                # subscription rate after 公司法 §267 employee carve-outs and underwriter
                # allocation — not total dilution to existing shareholders.
                rights_ratio = (
                    raw["TotalNumberOfCashCapitalIncrease"]
                    / raw["ParticipateDistributionOfTotalShares"]
                )
                rights_sub = raw["CashIncreaseSubscriptionpRrice"]
            else:
                rights_ratio = float("nan")
                rights_sub = float("nan")

            nan = float("nan")

            if has_cash and has_stock and not has_rights:
                if cash_ex != stock_ex:
                    warnings.warn(
                        f"cash_and_stock_dividend: cash ex-date {cash_ex} != stock ex-date "
                        f"{stock_ex} for {ticker}. Skipping row.",
                        stacklevel=2,
                    )
                    continue
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "cash_and_stock_dividend",
                        "cash_amount": cash_total,
                        "ratio": stock_face_total / 10,
                        "subscription_price": nan,
                    }
                )
            elif has_cash and not has_stock and not has_rights:
                rows.append(
                    {
                        "ex_date": cash_ex,
                        "ticker": ticker,
                        "action_type": "cash_dividend",
                        "cash_amount": cash_total,
                        "ratio": nan,
                        "subscription_price": nan,
                    }
                )
            elif has_stock and not has_cash and not has_rights:
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "stock_dividend",
                        "cash_amount": nan,
                        "ratio": stock_face_total / 10,
                        "subscription_price": nan,
                    }
                )
            elif has_rights and has_cash and not has_stock:
                rows.append(
                    {
                        "ex_date": cash_ex,
                        "ticker": ticker,
                        "action_type": "cash_dividend",
                        "cash_amount": cash_total,
                        "ratio": nan,
                        "subscription_price": nan,
                    }
                )
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "rights_issue",
                        "cash_amount": nan,
                        "ratio": rights_ratio,
                        "subscription_price": rights_sub,
                    }
                )
            elif has_rights and has_stock and not has_cash:
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "stock_dividend",
                        "cash_amount": nan,
                        "ratio": stock_face_total / 10,
                        "subscription_price": nan,
                    }
                )
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "rights_issue",
                        "cash_amount": nan,
                        "ratio": rights_ratio,
                        "subscription_price": rights_sub,
                    }
                )
            elif has_rights and has_cash and has_stock:
                warnings.warn(
                    f"Three-component row (cash+stock+rights) for {ticker} on {stock_ex}: "
                    "emitting three separate rows.",
                    stacklevel=2,
                )
                rows.append(
                    {
                        "ex_date": cash_ex,
                        "ticker": ticker,
                        "action_type": "cash_dividend",
                        "cash_amount": cash_total,
                        "ratio": nan,
                        "subscription_price": nan,
                    }
                )
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "stock_dividend",
                        "cash_amount": nan,
                        "ratio": stock_face_total / 10,
                        "subscription_price": nan,
                    }
                )
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "rights_issue",
                        "cash_amount": nan,
                        "ratio": rights_ratio,
                        "subscription_price": rights_sub,
                    }
                )
            else:
                # has_rights and not has_cash and not has_stock
                rows.append(
                    {
                        "ex_date": stock_ex,
                        "ticker": ticker,
                        "action_type": "rights_issue",
                        "cash_amount": nan,
                        "ratio": rights_ratio,
                        "subscription_price": rights_sub,
                    }
                )

        if not rows:
            return _EMPTY

        return pd.DataFrame(rows).astype(
            {
                "ex_date": "datetime64[ns]",
                "cash_amount": "float64",
                "ratio": "float64",
                "subscription_price": "float64",
            }
        )[["ex_date", "ticker", "action_type", "cash_amount", "ratio", "subscription_price"]]

    def _build_capital_reductions(self, reduction_data: list[dict]) -> pd.DataFrame:
        df_red = pd.DataFrame(reduction_data)[
            ["date", "stock_id", "ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice"]
        ]
        df_red["action_type"] = "capital_reduction"
        df_red["ratio"] = (
            df_red["PostReductionReferencePrice"] / df_red["ClosingPriceonTheLastTradingDay"]
        )
        df_red["cash_amount"] = float("nan")
        df_red["subscription_price"] = float("nan")
        df_red = df_red.rename(columns={"date": "ex_date", "stock_id": "ticker"})[
            ["ex_date", "ticker", "action_type", "cash_amount", "ratio", "subscription_price"]
        ].astype(
            {
                "ex_date": "datetime64[ns]",
                "cash_amount": "float64",
                "ratio": "float64",
                "subscription_price": "float64",
            }
        )
        return df_red

    def get_corporate_actions(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        try:
            dividend_data = self._request(
                "TaiwanStockDividend", {"data_id": ticker, "start_date": start}
            )
        except NoDataError:
            dividend_data = []
        try:
            reduction_data = self._request(
                "TaiwanStockCapitalReductionReferencePrice",
                {"data_id": ticker, "start_date": start},
            )
        except NoDataError:
            reduction_data = []

        if not dividend_data and not reduction_data:
            return pd.DataFrame(
                {
                    "ex_date": pd.Series(dtype="datetime64[ns]"),
                    "ticker": pd.Series(dtype="str"),
                    "action_type": pd.Series(dtype="str"),
                    "cash_amount": pd.Series(dtype="float64"),
                    "ratio": pd.Series(dtype="float64"),
                    "subscription_price": pd.Series(dtype="float64"),
                }
            )

        parts = []
        if dividend_data:
            parts.append(self._build_dividends(dividend_data))
        if reduction_data:
            parts.append(self._build_capital_reductions(reduction_data))

        df = parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)
        return validate(df, CORPORATE_ACTIONS_SCHEMA)

    def list_universe(self) -> list[str]:
        data = self._request("TaiwanStockInfo")
        df = pd.DataFrame(data)
        mask = (
            (df["type"] == "twse")
            & (df["industry_category"] != "ETF")
            & df["stock_id"].str.match(r"^\d{4}$")
        )
        return df.loc[mask, "stock_id"].tolist()
