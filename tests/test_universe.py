import re

from twstock.universe import get_top50_universe


def test_universe_length() -> None:
    assert len(get_top50_universe()) == 50


def test_universe_ticker_format() -> None:
    assert all(re.fullmatch(r"\d{4}", ticker) for ticker in get_top50_universe())
