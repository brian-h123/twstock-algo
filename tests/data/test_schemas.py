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
# CORPORATE_ACTIONS_SCHEMA — happy path (all six types)
# ---------------------------------------------------------------------------


def test_corporate_actions_valid_df_passes():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(
                ["2023-07-18", "2022-09-20", "2022-07-21", "2021-06-15", "2020-08-10", "2025-06-18"]
            ),
            "ticker": ["2330"] * 5 + ["0050"],
            "action_type": [
                "cash_dividend",
                "stock_dividend",
                "cash_and_stock_dividend",
                "rights_issue",
                "capital_reduction",
                "stock_split",
            ],
            "cash_amount": [3.0, None, 1.5, None, None, None],
            "pay_date": pd.to_datetime(["2023-08-10", "NaT", "2022-08-15", "NaT", "NaT", "NaT"]),
            "ratio": [None, 0.05, 0.02, 0.18, 1.225, 0.25],
            "subscription_price": [None, None, None, 17.0, None, None],
        }
    )
    result = validate(df, CORPORATE_ACTIONS_SCHEMA)
    assert len(result) == 6


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
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [float("nan")],
            "subscription_price": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_corporate_actions_rejects_split_as_invalid_type():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-18"]),
            "ticker": ["2330"],
            "action_type": ["split"],  # renamed to capital_reduction; split is no longer valid
            "cash_amount": [float("nan")],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [2.0],
            "subscription_price": [float("nan")],
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
            "pay_date": pd.to_datetime(["2023-08-10"]),
            "ratio": [float("nan")],
            "subscription_price": [float("nan")],
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
            "pay_date": pd.to_datetime(["2023-08-10"]),
            "ratio": [float("nan")],
            "subscription_price": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_cash_dividend_requires_pay_date():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2023-07-18"]),
            "ticker": ["2330"],
            "action_type": ["cash_dividend"],
            "cash_amount": [3.0],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),  # violation
            "ratio": [float("nan")],
            "subscription_price": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_cash_and_stock_dividend_requires_cash_amount():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2022-07-21"]),
            "ticker": ["1101"],
            "action_type": ["cash_and_stock_dividend"],
            "cash_amount": [float("nan")],  # violation
            "pay_date": pd.to_datetime(["2022-08-15"]),
            "ratio": [0.02],
            "subscription_price": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_cash_and_stock_dividend_requires_pay_date():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2022-07-21"]),
            "ticker": ["1101"],
            "action_type": ["cash_and_stock_dividend"],
            "cash_amount": [1.5],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),  # violation
            "ratio": [0.02],
            "subscription_price": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_cash_and_stock_dividend_requires_ratio():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2022-07-21"]),
            "ticker": ["1101"],
            "action_type": ["cash_and_stock_dividend"],
            "cash_amount": [1.5],
            "pay_date": pd.to_datetime(["2022-08-15"]),
            "ratio": [float("nan")],  # violation
            "subscription_price": [float("nan")],
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
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [float("nan")],  # violation
            "subscription_price": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_rights_issue_requires_ratio():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2021-06-15"]),
            "ticker": ["3312"],
            "action_type": ["rights_issue"],
            "cash_amount": [float("nan")],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [float("nan")],  # violation
            "subscription_price": [17.0],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_rights_issue_requires_subscription_price():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2021-06-15"]),
            "ticker": ["3312"],
            "action_type": ["rights_issue"],
            "cash_amount": [float("nan")],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [0.18],
            "subscription_price": [float("nan")],  # violation
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_capital_reduction_requires_ratio():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2020-08-10"]),
            "ticker": ["2330"],
            "action_type": ["capital_reduction"],
            "cash_amount": [float("nan")],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [float("nan")],  # violation
            "subscription_price": [float("nan")],
        }
    )
    with pytest.raises(ValueError):
        validate(df, CORPORATE_ACTIONS_SCHEMA)


def test_stock_split_valid_row_passes():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2025-06-18"]),
            "ticker": ["0050"],
            "action_type": ["stock_split"],
            "cash_amount": [float("nan")],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [0.25],
            "subscription_price": [float("nan")],
        }
    )
    result = validate(df, CORPORATE_ACTIONS_SCHEMA)
    assert len(result) == 1


def test_stock_split_requires_ratio():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2025-06-18"]),
            "ticker": ["0050"],
            "action_type": ["stock_split"],
            "cash_amount": [float("nan")],
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [float("nan")],  # violation
            "subscription_price": [float("nan")],
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
            "pay_date": pd.to_datetime(["2023-08-10"]),
            "ratio": [float("nan")],  # ratio not required for cash_dividend
            "subscription_price": [float("nan")],
        }
    )
    result = validate(df, CORPORATE_ACTIONS_SCHEMA)
    assert len(result) == 1


def test_rights_issue_null_cash_amount_is_ok():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2021-06-15"]),
            "ticker": ["3312"],
            "action_type": ["rights_issue"],
            "cash_amount": [float("nan")],  # not required for rights_issue
            "pay_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "ratio": [0.18],
            "subscription_price": [17.0],
        }
    )
    result = validate(df, CORPORATE_ACTIONS_SCHEMA)
    assert len(result) == 1


def test_non_cash_rows_allow_null_pay_date():
    df = pd.DataFrame(
        {
            "ex_date": pd.to_datetime(["2022-09-20", "2021-06-15", "2020-08-10", "2025-06-18"]),
            "ticker": ["2330", "3312", "2330", "0050"],
            "action_type": ["stock_dividend", "rights_issue", "capital_reduction", "stock_split"],
            "cash_amount": [float("nan"), float("nan"), float("nan"), float("nan")],
            "pay_date": pd.Series([pd.NaT, pd.NaT, pd.NaT, pd.NaT], dtype="datetime64[ns]"),
            "ratio": [0.05, 0.18, 1.225, 0.25],
            "subscription_price": [float("nan"), 17.0, float("nan"), float("nan")],
        }
    )
    result = validate(df, CORPORATE_ACTIONS_SCHEMA)
    assert len(result) == 4


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
