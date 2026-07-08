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

    Valid only for STOCK_SPLIT, STOCK_DIVIDEND, and CASH_AND_STOCK_DIVIDEND
    (the three action types with a multiplicative share effect). Raises for
    any other action_type — callers must branch on action_type before
    calling this, not use it as a catch-all.
    """
    if action.action_type == ActionType.STOCK_SPLIT:
        if action.ratio is None or action.ratio <= 0:
            raise ValueError(f"STOCK_SPLIT action has invalid ratio: {action}")
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
    entitlement can accrue, so an unmodeled action_type (RIGHTS_ISSUE,
    CAPITAL_REDUCTION) is also silently skipped rather than raised in that
    case. STOCK_SPLIT and STOCK_DIVIDEND produce a ShareMutation effective
    at t (the ex-date). CASH_DIVIDEND produces a CashMutation sized off
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
        ValueError: If action_type is RIGHTS_ISSUE or CAPITAL_REDUCTION on
            a held position (D15: unmodeled, evidence-gated); or if a
            cash-bearing action is missing cash_amount or pay_date; or if
            a share-bearing action has an invalid ratio (via
            share_multiplier).
    """
    mutations: list[DatedMutation] = []
    if position.open_shares == 0:
        return []
    if action.action_type in (ActionType.RIGHTS_ISSUE, ActionType.CAPITAL_REDUCTION):
        raise ValueError(f"unmodeled action type for decompose: {action.action_type}")
    if action.action_type in (
        ActionType.STOCK_SPLIT,
        ActionType.STOCK_DIVIDEND,
        ActionType.CASH_AND_STOCK_DIVIDEND,
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
