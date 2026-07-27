from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from twstock.backtest.trades import LotLedger
from twstock.broker.events import ActionType, CorporateAction


@dataclass(frozen=True)
class ShareMutation:
    ratio: float
    effective_date: date


@dataclass(frozen=True)
class CashMutation:
    amount: Decimal
    declared_date: date
    settle_date: date


DatedMutation = ShareMutation | CashMutation


def share_multiplier(action: CorporateAction) -> float:
    """New shares per old share, in LotLedger.on_split()'s convention.

    Valid only for STOCK_SPLIT, CAPITAL_REDUCTION, STOCK_DIVIDEND, and
    CASH_AND_STOCK_DIVIDEND (the four action types with a multiplicative
    share effect). CAPITAL_REDUCTION shares STOCK_SPLIT's ratio convention
    and formula (post_price / prior_price; ADR 0007, ADR 0012) rather than
    getting its own branch. Raises for any other action_type — callers must
    branch on action_type before calling this, not use it as a catch-all.
    """
    if action.action_type in (ActionType.STOCK_SPLIT, ActionType.CAPITAL_REDUCTION):
        if action.ratio is None or action.ratio <= 0:
            raise ValueError(f"{action.action_type} action has invalid ratio: {action}")
        return 1.0 / action.ratio
    elif action.action_type in (ActionType.STOCK_DIVIDEND, ActionType.CASH_AND_STOCK_DIVIDEND):
        if action.ratio is None or action.ratio <= 0:
            raise ValueError(f"{action.action_type} action has invalid ratio: {action}")
        return 1.0 + action.ratio
    else:
        raise ValueError(f"share_multiplier undefined for action_type: {action.action_type}")


def decompose(action: CorporateAction, position: LotLedger, t: date) -> list[DatedMutation]:
    """Decompose one corporate action into dated mutations against position.

    A no-op on a flat position: with nothing held, no share or cash
    entitlement can accrue. RIGHTS_ISSUE is always a no-op, held or flat:
    the lapse assumption (ADR 0012) treats the shareholder as neither
    subscribing nor selling the rights, so no share or cash mutation
    occurs — dilution is already reflected for free in the raw market
    price used for mark-to-market (the same treatment data/adjustments.py
    already applies on the signal side). STOCK_SPLIT, STOCK_DIVIDEND, and
    CAPITAL_REDUCTION produce a ShareMutation effective at t (the ex-date)
    — CAPITAL_REDUCTION shares STOCK_SPLIT's ratio convention and formula
    (ADR 0007, ADR 0012), not a lapse: every shareholder's share count
    changes automatically, so unlike RIGHTS_ISSUE there is no "do nothing"
    reading of the event. CASH_DIVIDEND produces a CashMutation sized off
    position.open_shares and settling at action.pay_date.
    CASH_AND_STOCK_DIVIDEND produces both. Pure: does not mutate position.

    Args:
        action: The corporate action to decompose.
        position: The ticker's current LotLedger, read (not mutated) for
            open_shares to size cash entitlements.
        t: The bar date on which the action is effective; by construction
            this is action.ex_date, since callers only pass an action for
            the bar it is attached to.

    Returns:
        Zero, one, or two DatedMutations depending on action_type and
        whether position is flat.

    Raises:
        ValueError: If a cash-bearing action is missing cash_amount or
            pay_date; or if a share-bearing action has an invalid ratio
            (via share_multiplier).
    """
    mutations: list[DatedMutation] = []
    if action.action_type == ActionType.RIGHTS_ISSUE:
        return []
    if position.open_shares == 0:
        return []
    if action.action_type in (
        ActionType.STOCK_SPLIT,
        ActionType.STOCK_DIVIDEND,
        ActionType.CASH_AND_STOCK_DIVIDEND,
        ActionType.CAPITAL_REDUCTION,
    ):
        mutations.append(ShareMutation(ratio=share_multiplier(action), effective_date=t))
    if action.action_type in (ActionType.CASH_DIVIDEND, ActionType.CASH_AND_STOCK_DIVIDEND):
        if action.cash_amount is None:
            raise ValueError(f"{action.action_type} action missing cash_amount: {action}")
        if action.pay_date is None:
            raise ValueError(f"{action.action_type} action missing pay_date: {action}")
        mutations.append(
            CashMutation(
                amount=action.cash_amount * position.open_shares,
                declared_date=t,
                settle_date=action.pay_date,
            )
        )
    return mutations
