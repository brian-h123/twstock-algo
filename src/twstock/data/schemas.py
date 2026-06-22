import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError
from pandera.pandas import Check, Column

OHLCV_SCHEMA = pa.DataFrameSchema(
    columns={
        "date": Column(pa.DateTime, nullable=False),
        "ticker": Column(pa.String, Check.str_matches(r"^\d{4}$")),
        "open": Column(pa.Float64, Check.greater_than(0)),
        "high": Column(pa.Float64, Check.greater_than(0)),
        "low": Column(pa.Float64, Check.greater_than(0)),
        "close": Column(pa.Float64, Check.greater_than(0)),
        "volume": Column(pa.Int64, Check.ge(0)),
        "value": Column(pa.Float64, Check.ge(0), required=False, nullable=True),
        "fetched_at": Column(pa.DateTime, required=False, nullable=True),
    },
    checks=[
        Check(lambda df: (df["high"] >= df["open"]).all(), error="high < open"),
        Check(lambda df: (df["high"] >= df["low"]).all(), error="high < low"),
        Check(lambda df: (df["high"] >= df["close"]).all(), error="high < close"),
        Check(lambda df: (df["low"] <= df["open"]).all(), error="low > open"),
        Check(lambda df: (df["low"] <= df["close"]).all(), error="low > close"),
    ],
)

CORPORATE_ACTIONS_SCHEMA = pa.DataFrameSchema(
    columns={
        "ex_date": Column(pa.DateTime, nullable=False),
        "ticker": Column(pa.String, Check.str_matches(r"^\d{4}$")),
        "action_type": Column(
            pa.String,
            Check.isin(
                [
                    "cash_dividend",
                    "stock_dividend",
                    "cash_and_stock_dividend",
                    "rights_issue",
                    "capital_reduction",
                    "stock_split",
                ]
            ),
        ),
        "cash_amount": Column(pa.Float64, Check.ge(0), nullable=True),
        "ratio": Column(pa.Float64, Check.greater_than(0), nullable=True),
        "subscription_price": Column(pa.Float64, Check.greater_than(0), nullable=True),
    },
    checks=[
        Check(
            lambda df: (
                df.loc[
                    df["action_type"].isin(["cash_dividend", "cash_and_stock_dividend"]),
                    "cash_amount",
                ]
                .notna()
                .all()
            ),
            error="cash_amount required for cash_dividend and cash_and_stock_dividend",
        ),
        Check(
            lambda df: (
                df.loc[
                    df["action_type"].isin(
                        [
                            "stock_dividend",
                            "cash_and_stock_dividend",
                            "rights_issue",
                            "capital_reduction",
                            "stock_split",
                        ]
                    ),
                    "ratio",
                ]
                .notna()
                .all()
            ),
            error="ratio required for all action_types except cash_dividend",
        ),
        Check(
            lambda df: (
                df.loc[df["action_type"] == "rights_issue", "subscription_price"].notna().all()
            ),
            error="subscription_price required when action_type == rights_issue",
        ),
    ],
)


def validate(df: pd.DataFrame, schema: pa.DataFrameSchema) -> pd.DataFrame:
    """Validate a DataFrame against a pandera schema.

    Args:
        df: DataFrame to validate.
        schema: Pandera schema to validate against.

    Returns:
        The validated (and possibly coerced) DataFrame.

    Raises:
        ValueError: If the DataFrame fails schema validation.
    """

    try:
        return schema.validate(df)
    except SchemaError as e:
        raise ValueError(f"Schema validation failed: {e.args[0]}") from e
