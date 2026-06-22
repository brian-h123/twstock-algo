import pandas as pd


def _compute_factor(action: pd.Series, ref_close: float) -> float:
    action_type = action["action_type"]
    if action_type == "cash_dividend":
        cash_amount = action["cash_amount"]
        if cash_amount >= ref_close:
            raise ValueError(
                f"cash_amount ({cash_amount}) >= ref_close ({ref_close}) "
                f"for {action['ticker']} on {action['ex_date']} — bad corporate actions data"
            )
        # Standard TWSE 還原權息 back-adjustment factor: Y / (Y + D)
        # ref_close is the T close (close ON the ex_date, not T-1).
        # Using T close ensures adj_close[T-1] / adj_close[T] = X / (Y + D)
        # regardless of how much the stock moved beyond the dividend.
        return ref_close / (ref_close + cash_amount)
    elif action_type == "stock_dividend":
        return 1.0 / (1.0 + action["ratio"])
    elif action_type == "cash_and_stock_dividend":
        cash_amount = action["cash_amount"]
        ratio = action["ratio"]
        if cash_amount >= ref_close:
            raise ValueError(
                f"cash_amount ({cash_amount}) >= ref_close ({ref_close}) "
                f"for {action['ticker']} on {action['ex_date']} — bad corporate actions data"
            )
        # Single combined factor avoids floating-point accumulation from chaining
        # and eliminates any ordering ambiguity between the cash and stock legs.
        return (ref_close / (ref_close + cash_amount)) * (1.0 / (1.0 + ratio))
    elif action_type == "rights_issue":
        ratio = action["ratio"]
        sub_price = action["subscription_price"]
        # TWSE rights-issue back-adjustment: new reference price =
        # (Y + ratio * sub_price) / (1 + ratio); factor adjusts pre-ex prices
        # so they are on the same scale as that reference.
        return (ref_close + ratio * sub_price) / (ref_close * (1.0 + ratio))
    elif action_type == "capital_reduction":
        # ratio = PostReductionReferencePrice / ClosingPriceonTheLastTradingDay.
        # Applied directly: pre-event prices are scaled by this ratio so that
        # the T-1 bar continuity-adjusts to the post-reduction reference price.
        return action["ratio"]
    elif action_type == "stock_split":
        return action["ratio"]
    else:
        raise ValueError(
            f"Unknown action_type '{action_type}' for {action['ticker']} on {action['ex_date']}"
        )


def adjust(raw_ohlcv: pd.DataFrame, corporate_actions: pd.DataFrame) -> pd.DataFrame:
    """Apply back-adjustment to raw OHLCV prices using corporate actions.

    Adjusts open, high, low, and close for all six action types: cash_dividend,
    stock_dividend, cash_and_stock_dividend, rights_issue, capital_reduction, and stock_split.
    Volume is left unchanged. Current prices are never modified — only bars prior
    to each ex_date are scaled.

    Pure: no I/O, same inputs always produce the same output.

    Args:
        raw_ohlcv: Raw daily bars conforming to OHLCV_SCHEMA.
        corporate_actions: Corporate actions conforming to CORPORATE_ACTIONS_SCHEMA.

    Returns:
        DataFrame with the same shape and columns as raw_ohlcv, with
        open/high/low/close back-adjusted.

    Raises:
        ValueError: If a cash_dividend or cash_and_stock_dividend action has
            cash_amount >= ref_close, indicating bad corporate actions data.
    """
    if corporate_actions.empty:
        return raw_ohlcv.copy()
    df = raw_ohlcv.copy().sort_values("date")
    actions = corporate_actions.copy().sort_values("ex_date")

    for _, action in actions.iterrows():
        affected_rows = df[df["date"] < action["ex_date"]]
        if affected_rows.empty:
            continue
        # For cash_dividend the factor is Y/(Y+D) — requires T close (Y), not T-1 close.
        # The bar at action["ex_date"] is unmodified at this loop iteration because we
        # only multiply bars strictly before ex_date. Fall back to T-1 close only if
        # ex_date has no bar in the OHLCV (data gap or future event).
        ex_rows = df[df["date"] == action["ex_date"]]
        ref_close = float(
            ex_rows.iloc[0]["close"] if not ex_rows.empty else affected_rows.iloc[-1]["close"]
        )
        factor = _compute_factor(action, ref_close)
        df.loc[df["date"] < action["ex_date"], ["open", "high", "low", "close"]] *= factor

    return df
