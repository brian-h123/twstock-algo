import pandas as pd


def _compute_factor(action: pd.Series, last_close: float) -> float:
    action_type = action["action_type"]
    if action_type == "cash_dividend":
        cash_amount = action["cash_amount"]
        if cash_amount >= last_close:
            raise ValueError(
                f"cash_amount ({cash_amount}) >= last_close ({last_close}) "
                f"for {action['ticker']} on {action['ex_date']} — bad corporate actions data"
            )
        return (last_close - cash_amount) / last_close
    elif action_type == "stock_dividend":
        return 1.0 / (1.0 + action["ratio"])
    elif action_type == "split":
        return 1.0 / action["ratio"]
    elif action_type == "rights_issue":
        return 1.0 / (1.0 + action["ratio"])
    else:
        raise ValueError(
            f"Unknown action_type '{action_type}' for {action['ticker']} on {action['ex_date']}"
        )


def adjust(raw_ohlcv: pd.DataFrame, corporate_actions: pd.DataFrame) -> pd.DataFrame:
    """Apply back-adjustment to raw OHLCV prices using corporate actions.

    Adjusts open, high, low, and close for cash dividends, stock dividends,
    splits, and rights issues. Volume is left unchanged. Current prices are
    never modified — only bars prior to each ex_date are scaled.

    Pure: no I/O, same inputs always produce the same output.

    Args:
        raw_ohlcv: Raw daily bars conforming to OHLCV_SCHEMA.
        corporate_actions: Corporate actions conforming to CORPORATE_ACTIONS_SCHEMA.

    Returns:
        DataFrame with the same shape and columns as raw_ohlcv, with
        open/high/low/close back-adjusted.

    Raises:
        ValueError: If a cash_dividend action has cash_amount >= last_close,
            indicating bad corporate actions data.
    """
    if corporate_actions.empty:
        return raw_ohlcv.copy()
    df = raw_ohlcv.copy().sort_values("date")
    actions = corporate_actions.copy().sort_values("ex_date")

    for _, action in actions.iterrows():
        affected_rows = df[df["date"] < action["ex_date"]]
        if affected_rows.empty:
            continue
        last_close = affected_rows.iloc[-1]["close"]
        factor = _compute_factor(action, last_close)
        df.loc[df["date"] < action["ex_date"], ["open", "high", "low", "close"]] *= factor

    return df
