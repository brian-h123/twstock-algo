import os
import time
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
        # Clamp open to [low, high] so schema invariants hold for pre-TWSE rows
        # where FinMind encodes the previous day's average as "open" (TPEx/emerging
        # market period). Proper fix is FU-05: restrict fetch to TWSE listing date.
        df["high"] = df[["high", "open"]].max(axis=1)
        df["low"] = df[["low", "open"]].min(axis=1)
        return validate(df, OHLCV_SCHEMA)

    def _build_dividends(self, dividend_data: list[dict]) -> pd.DataFrame:
        df_div = pd.DataFrame(dividend_data)[
            ["date", "stock_id", "stock_and_cache_dividend", "stock_or_cache_dividend"]
        ]

        TYPE_MAP = {"息": "cash_dividend", "權": "stock_dividend", "權息": "cash_dividend"}
        df_div["action_type"] = df_div["stock_or_cache_dividend"].map(TYPE_MAP)
        is_cash = df_div["action_type"] == "cash_dividend"
        df_div["cash_amount"] = df_div["stock_and_cache_dividend"].where(is_cash)
        df_div["ratio"] = (df_div["stock_and_cache_dividend"] / 10).where(~is_cash)

        df_div = df_div.rename(columns={"date": "ex_date", "stock_id": "ticker"})[
            ["ex_date", "ticker", "action_type", "cash_amount", "ratio"]
        ].astype({"ex_date": "datetime64[ns]", "cash_amount": "float64", "ratio": "float64"})
        return df_div

    def _build_reductions(self, reduction_data: list[dict]) -> pd.DataFrame:
        df_red = pd.DataFrame(reduction_data)[
            ["date", "stock_id", "ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice"]
        ]
        df_red["action_type"] = "split"
        df_red["ratio"] = (
            df_red["PostReductionReferencePrice"] / df_red["ClosingPriceonTheLastTradingDay"]
        )
        df_red["cash_amount"] = float("nan")
        df_red = df_red.rename(columns={"date": "ex_date", "stock_id": "ticker"})[
            ["ex_date", "ticker", "action_type", "cash_amount", "ratio"]
        ].astype({"ex_date": "datetime64[ns]", "cash_amount": "float64", "ratio": "float64"})
        return df_red

    def get_corporate_actions(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        try:
            dividend_data = self._request(
                "TaiwanStockDividendResult", {"data_id": ticker, "start_date": start}
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
                }
            )

        parts = []
        if dividend_data:
            parts.append(self._build_dividends(dividend_data))
        if reduction_data:
            parts.append(self._build_reductions(reduction_data))

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
