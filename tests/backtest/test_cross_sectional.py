import pandas as pd
import pytest

from twstock.backtest.audit import LookaheadError, assert_no_lookahead_panel
from twstock.backtest.cross_sectional import CrossSectionalAdapter, CrossSectionalStrategy
from twstock.backtest.strategy import Strategy
from twstock.strategies.sma_crossover import SmaCrossover

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _AlwaysLong(Strategy):
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=bars.index)


class _AlwaysFlat(Strategy):
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        return pd.Series(0.0, index=bars.index)


class _AlwaysShort(Strategy):
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        return pd.Series(-1.0, index=bars.index)


class _AlwaysNaN(Strategy):
    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        return pd.Series(float("nan"), index=bars.index)


class _LookaheadStrategy(Strategy):
    """Returns 1.0 when the next bar's close is higher — reads future data."""

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        future_close = bars["close"].shift(-1)
        return (future_close > bars["close"]).astype(float)


# ---------------------------------------------------------------------------
# CrossSectionalStrategy ABC
# ---------------------------------------------------------------------------


def test_abstract_cross_sectional_raises_on_instantiation():
    class NoImpl(CrossSectionalStrategy):
        pass

    with pytest.raises(TypeError):
        NoImpl()


# ---------------------------------------------------------------------------
# CrossSectionalAdapter — signal mapping
# ---------------------------------------------------------------------------


def test_adapter_maps_long_to_1(panel_3t_100d):
    scores = CrossSectionalAdapter(_AlwaysLong()).generate_scores(panel_3t_100d)
    assert (scores == 1.0).all().all()


def test_adapter_maps_flat_to_nan(panel_3t_100d):
    scores = CrossSectionalAdapter(_AlwaysFlat()).generate_scores(panel_3t_100d)
    assert scores.isna().all().all()


def test_adapter_maps_short_to_nan(panel_3t_100d):
    scores = CrossSectionalAdapter(_AlwaysShort()).generate_scores(panel_3t_100d)
    assert scores.isna().all().all()


def test_adapter_maps_warmup_nan_to_nan(panel_3t_100d):
    scores = CrossSectionalAdapter(_AlwaysNaN()).generate_scores(panel_3t_100d)
    assert scores.isna().all().all()


# ---------------------------------------------------------------------------
# CrossSectionalAdapter — panel shape and structure
# ---------------------------------------------------------------------------


def test_adapter_panel_shape(panel_3t_100d):
    scores = CrossSectionalAdapter(_AlwaysLong()).generate_scores(panel_3t_100d)
    n_days = len(next(iter(panel_3t_100d.values())))
    n_tickers = len(panel_3t_100d)
    assert scores.shape == (n_days, n_tickers)


def test_adapter_columns_match_panel_keys(panel_3t_100d):
    scores = CrossSectionalAdapter(_AlwaysLong()).generate_scores(panel_3t_100d)
    assert list(scores.columns) == list(panel_3t_100d.keys())


def test_adapter_index_matches_bars_index(panel_3t_100d):
    scores = CrossSectionalAdapter(_AlwaysLong()).generate_scores(panel_3t_100d)
    expected_index = next(iter(panel_3t_100d.values())).index
    assert scores.index.equals(expected_index)


# ---------------------------------------------------------------------------
# CrossSectionalAdapter — statelessness
# ---------------------------------------------------------------------------


def test_adapter_is_stateless(panel_3t_100d):
    adapter = CrossSectionalAdapter(SmaCrossover(fast=2, slow=3))
    first = adapter.generate_scores(panel_3t_100d)
    second = adapter.generate_scores(panel_3t_100d)
    pd.testing.assert_frame_equal(first, second)


# ---------------------------------------------------------------------------
# CrossSectionalAdapter — name property
# ---------------------------------------------------------------------------


def test_adapter_name_delegates_to_inner_strategy():
    inner = SmaCrossover()
    assert CrossSectionalAdapter(inner).name == inner.name


# ---------------------------------------------------------------------------
# assert_no_lookahead_panel
# ---------------------------------------------------------------------------


def test_no_lookahead_panel_passes_for_causal_adapter(panel_3t_100d):
    adapter = CrossSectionalAdapter(SmaCrossover(fast=2, slow=3))
    assert_no_lookahead_panel(adapter, panel_3t_100d)  # must not raise


def test_no_lookahead_panel_raises_for_lookahead_strategy(panel_3t_100d):
    adapter = CrossSectionalAdapter(_LookaheadStrategy())
    with pytest.raises(LookaheadError):
        assert_no_lookahead_panel(adapter, panel_3t_100d)
