from twstock.ui.names import TICKER_NAMES, display_label
from twstock.universe import get_top50_universe


def test_all_universe_tickers_have_names() -> None:
    missing = [t for t in get_top50_universe() if t not in TICKER_NAMES]
    assert missing == [], f"Missing names for tickers: {missing}"


def test_all_names_are_nonempty_strings() -> None:
    for ticker, name in TICKER_NAMES.items():
        assert isinstance(name, str) and name, f"Empty or non-string name for {ticker}"


def test_display_label_known_ticker() -> None:
    label = display_label("2330")
    assert label == "2330 — 台積電"


def test_display_label_unknown_ticker_returns_ticker() -> None:
    assert display_label("9999") == "9999"
