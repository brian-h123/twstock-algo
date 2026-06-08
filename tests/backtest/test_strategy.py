import pandas as pd
import pytest

from twstock.backtest.strategy import BuyAndHold, Strategy


def _make_bars(n: int) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000,
        },
        index=idx,
    )


def test_buy_and_hold_signal_at_bar_zero():
    bars = _make_bars(10)
    signals = BuyAndHold().generate_signals(bars)
    assert signals.iloc[0] == 1


def test_buy_and_hold_all_other_bars_zero():
    bars = _make_bars(10)
    signals = BuyAndHold().generate_signals(bars)
    assert (signals.iloc[1:] == 0).all()


def test_buy_and_hold_index_matches_bars():
    bars = _make_bars(10)
    signals = BuyAndHold().generate_signals(bars)
    assert signals.index.equals(bars.index)


def test_abstract_strategy_raises_on_instantiation():
    class NoImpl(Strategy):
        pass

    with pytest.raises(TypeError):
        NoImpl()


def test_strategy_name_defaults_to_class_name():
    assert BuyAndHold().name == "BuyAndHold"


def test_custom_name_via_override():
    class MyStrat(Strategy):
        def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
            return pd.Series(0, index=bars.index, dtype=int)

        @property
        def name(self) -> str:
            return "custom-name"

    assert MyStrat().name == "custom-name"
