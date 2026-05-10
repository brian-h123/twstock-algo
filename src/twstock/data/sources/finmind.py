from twstock.data.sources.base import DataSource


class FinMindSource(DataSource):
    def get_daily_ohlcv(self, ticker, start, end):
        raise NotImplementedError

    def get_corporate_actions(self, ticker, start, end):
        raise NotImplementedError

    def list_universe(self, snapshot_date):
        raise NotImplementedError
