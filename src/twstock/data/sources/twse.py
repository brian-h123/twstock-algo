import sys
from collections.abc import Iterator
from datetime import date

import pandas as pd
import requests

from twstock.data.schemas import OHLCV_SCHEMA, validate
from twstock.data.sources.base import DataSource, NoDataError, SourceUnavailableError
from twstock.universe import get_top50_universe


def _roc_to_gregorian(s):
    y, m, d = s.split("/")
    return date(int(y) + 1911, int(m), int(d))


def fetch_suspension_halts(start: date, end: date) -> pd.DataFrame:
    """Fetch TWTAWU trading-halt records for [start, end], all tickers.

    Returns a DataFrame with columns matching SUSPENSIONS_SCHEMA (minus
    fetched_at). Filtering to the Top-50 universe is the caller's job —
    TWTAWU is a bulk endpoint, unlike the per-ticker STOCK_DAY fetch.
    """
    response = requests.get(
        "https://www.twse.com.tw/rwd/zh/afterTrading/TWTAWU",
        params={
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
            "stockNo": "",
            "selectType": "",
            "querytype": "3",
            "response": "json",
        },
        headers={"User-Agent": "twstock-algo/0.1"},
        timeout=30,
    )
    if response.status_code >= 500:
        raise SourceUnavailableError("TWSE unavailable")
    response.raise_for_status()

    body = response.json()
    if body.get("stat") != "OK":
        raise NoDataError(f"no TWTAWU data betweem {start} and {end}")

    df = pd.DataFrame(body["data"], columns=body["fields"])
    df = df.rename(
        columns={
            "證券代號": "ticker",
            "暫停交易日期": "halt_date",
            "恢復交易日期": "resume_date",
        }
    )

    # TWTAWU's bulk feed includes long-expired warrants that were halted and
    # never resumed (resume_date == ""). Our model has no way to represent an
    # open-ended halt, so drop rows missing either date — this is a completeness
    # filter, unrelated to which universe the caller ultimately cares about.
    before = len(df)
    df = df[(df["halt_date"] != "") & (df["resume_date"] != "")]
    dropped = before - len(df)
    if dropped:
        print(
            f"fetch_suspension_halts: dropped {dropped} still-halted row(s) with no resume_date",
            file=sys.stderr,
        )

    df["halt_date"] = pd.to_datetime(df["halt_date"].map(_roc_to_gregorian))
    df["resume_date"] = pd.to_datetime(df["resume_date"].map(_roc_to_gregorian))
    df["source"] = "TWTAWU"

    return df[["ticker", "halt_date", "resume_date", "source"]]


class TWSESource(DataSource):
    def _month_starts(self, start: date, end: date) -> Iterator[date]:
        current = start.replace(day=1)
        while current <= end:
            yield current
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

    def _fetch_month(self, ticker: str, month_start: date) -> pd.DataFrame:
        response = requests.get(
            f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={month_start.strftime('%Y%m%d')}&stockNo={ticker}",
            timeout=10,
        )
        if response.status_code >= 500:
            raise SourceUnavailableError("TWSE unavailable")
        response.raise_for_status()

        body = response.json()
        if body.get("stat") != "OK":
            raise NoDataError(f"No data for {ticker} {month_start}")

        df = pd.DataFrame(body["data"], columns=body["fields"])
        df = df[df["收盤價"] != "--"]

        for col in ["成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]:
            df[col] = df[col].str.replace(",", "", regex=False)

        df = df.rename(
            columns={
                "開盤價": "open",
                "最高價": "high",
                "最低價": "low",
                "收盤價": "close",
                "成交股數": "volume",
                "成交金額": "value",
            }
        )

        df["date"] = pd.to_datetime(df["日期"].map(_roc_to_gregorian))
        df["ticker"] = ticker

        return df[["date", "ticker", "open", "high", "low", "close", "volume", "value"]].astype(
            {
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "int64",
                "value": "float64",
            }
        )

    def get_daily_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        frames = []
        for month_start in self._month_starts(start, end):
            try:
                frames.append(self._fetch_month(ticker, month_start))
            except NoDataError:
                continue

        if not frames:
            raise NoDataError(f"No data for {ticker} between {start} and {end}")

        df = pd.concat(frames, ignore_index=True)
        df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
        return validate(df, OHLCV_SCHEMA)

    def get_corporate_actions(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError

    def list_universe(self):
        return get_top50_universe()
