import pandas as pd
import pytest

from twstock.data.schemas import CORPORATE_ACTIONS_SCHEMA, OHLCV_SCHEMA, validate


def _valid_ohlcv_row() -> dict:
    return {
        "date": pd.to_datetime(["2023-01-03"]),
        "ticker": ["2330"],
        "open": [500.0],
        "high": [510.0],
        "low": [495.0],
        "close": [505.0],
        "volume": [10_000_000],
    }


# ---------------------------------------------------------------------------
# OHLCV_SCHEMA — happy path
# ---------------------------------------------------------------------------


def test_ohlcv_valid_df_passes(fixtures_dir):
    df = pd.read_csv(
        fixtures_dir / "2330_2023_clean.csv",
        parse_dates=["date"],
        dtype={"ticker": str},
    )
    result = validate(df, OHLCV_SCHEMA)
    assert len(result) == 5


# ---------------------------------------------------------------------------
# OHLCV_SCHEMA — column checks
# ---------------------------------------------------------------------------


def test_ohlcv_rejects_zero_price():
    df = pd.DataFrame(_valid_ohlcv_row() | {"open": [0.0]})
    with pytest.raises(ValueError):
        validate(df, OHLCV_SCHEMA)


def test_ohlcv_rejects_negative_volume():
    df = pd.DataFrame(_valid_ohlcv_row() | {"volume": [-1]})
    with pytest.raises(ValueError):
        validate(df, OHLCV_SCHEMA)


def test_ohlcv_rejects_bad_ticker_format():
    df = pd.DataFrame(_valid_ohlcv_row() | {"ticker": ["50"]})
    with pytest.raises(ValueError):
        validate(df, OHLCV_SCHEMA)


def test_ohlcv_valid_ticker_with_leading_zero():
    df = pd.DataFrame(_valid_ohlcv_row() | {"ticker": ["0050"]})
    result = validate(df, OHLCV_SCHEMA)
    assert len(result) == 1


def test_ohlcv_optional_columns_absent():
    df = pd.DataFrame(_valid_ohlcv_row())  # no value or fetched_at
    result = validate(df, OHLCV_SCHEMA)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# OHLCV_SCHEMA — cross-column checks
# ---------------------------------------------------------------------------


def test_ohlcv_rejects_high_below_low():
    # high=490 < low=495; also set open and close within [490,495] to isolate intent
    df = pd.DataFrame(_valid_ohlcv_row() | {"open": [492.0], "high": [490.0], "close": [491.0]})
    with pytest.raises(ValueError):
        validate(df, OHLCV_SCHEMA)


def test_ohlcv_rejects_high_below_open():
    # open=520 > high=510; low and close stay below high
    df = pd.DataFrame(_valid_ohlcv_row() | {"open": [520.0]})
    with pytest.raises(ValueError):
        validate(df, OHLCV_SCHEMA)


def test_ohlcv_rejects_high_below_close():
    # close=515 > high=510; open stays below high
    df = pd.DataFrame(_valid_ohlcv_row() | {"close": [515.0]})
    with pytest.raises(ValueError):
        validate(df, OHLCV_SCHEMA)


# ---------------------------------------------------------------------------
# CORPORATE_ACTIONS_SCHEMA — happy path
# ---------------------------------------------------------------------------


def test_corporate_actions_valid_df_passes():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-18", "2022-09-20", "2021-10-05"]),
            "ticker": ["2330", "2330", "2330"],
            "action_type": ["cash_dividend", "stock_dividend", "split"],
            "cash_amount": [3.0, None, None],
            "ratio": [None, 1.05, 2.0],
        }
    )
    result = validate(df, CORPORATE_ACTIONS_SCHEMA)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# CORPORATE_ACTIONS_SCHEMA — column checks
# ---------------------------------------------------------------------------


def test_corporate_actions_rejects_invalid_action_type():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-18"]),
            "ticker": ["2330"],
            "action_type": ["buyback"],  # not in the allowed set
            "cash_amount": [float("nan")],
            "ratio": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_corporate_actions_rejects_bad_ticker():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-18"]),
            "ticker": ["50"],  # too short, fails ^\d{4}$
            "action_type": ["cash_dividend"],
            "cash_amount": [3.0],
            "ratio": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


# ---------------------------------------------------------------------------
# CORPORATE_ACTIONS_SCHEMA — conditional checks
# ---------------------------------------------------------------------------


def test_cash_dividend_requires_cash_amount():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-18"]),
            "ticker": ["2330"],
            "action_type": ["cash_dividend"],
            "cash_amount": [float("nan")],  # violation
            "ratio": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_split_requires_ratio():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2021-10-05"]),
            "ticker": ["2330"],
            "action_type": ["split"],
            "cash_amount": [float("nan")],
            "ratio": [float("nan")],  # violation
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_stock_dividend_requires_ratio():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2022-09-20"]),
            "ticker": ["2330"],
            "action_type": ["stock_dividend"],
            "cash_amount": [float("nan")],
            "ratio": [float("nan")],  # violation
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_cash_dividend_null_ratio_is_ok():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-18"]),
            "ticker": ["2330"],
            "action_type": ["cash_dividend"],
            "cash_amount": [3.0],
            "ratio": [float("nan")],  # ratio not required for cash_dividend
        }
    )
    result = validate(df, CORPORATE_ACTIONS_SCHEMA)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# validate() helper
# ---------------------------------------------------------------------------


def test_validate_returns_dataframe():
    df = pd.DataFrame(_valid_ohlcv_row())
    result = validate(df, OHLCV_SCHEMA)
    assert isinstance(result, pd.DataFrame)


def test_validate_raises_value_error_not_schema_error():
    from pandera.errors import SchemaError

    df = pd.DataFrame(_valid_ohlcv_row() | {"open": [0.0]})
    try:
        validate(df, OHLCV_SCHEMA)
        pytest.fail("Expected ValueError to be raised")
    except SchemaError:
        pytest.fail("SchemaError leaked out of validate()")
    except ValueError:
        pass  # expected
