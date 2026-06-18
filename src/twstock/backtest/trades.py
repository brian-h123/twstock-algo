from collections import deque
from dataclasses import dataclass
from enum import Enum

import pandas as pd

_COLUMNS = ["ticker", "entry_price", "exit_price", "shares", "ret"]


class Grouping(Enum):
    FILL_TO_FILL = "fill_to_fill"
    FLAT_TO_FLAT = "flat_to_flat"
    FLAT_TO_REDUCED = "flat_to_reduced"


class Matching(Enum):
    FIFO = "fifo"
    LIFO = "lifo"


def build_round_trips(
    trades: pd.DataFrame,
    grouping: Grouping = Grouping.FLAT_TO_FLAT,
    matching: Matching = Matching.FIFO,
) -> pd.DataFrame:
    """Consume per-ticker lot queues; emit realised P&L per closed slice.
    Replaces tearsheet._round_trips. Pure function over result.trades (D10)."""

    if trades.empty:
        return pd.DataFrame(columns=_COLUMNS)

    @dataclass
    class _Lot:
        shares: int
        basis: float

    rows: list[dict] = []

    for ticker, grp in trades.groupby("ticker"):
        lot_deque: deque[_Lot] = deque()
        open_shares = 0
        lifecycle_slices: list[dict] = []

        for _, row in grp.sort_values("timestamp").iterrows():
            if row["side"] == "buy":
                lot_deque.append(_Lot(shares=row.shares, basis=row.fill_price))
                open_shares += row.shares

            elif row["side"] == "sell":
                remaining = row.shares
                fill_slices: list[dict] = []

                while remaining > 0 and lot_deque:
                    lot = lot_deque[0] if matching == Matching.FIFO else lot_deque[-1]
                    consume = min(lot.shares, remaining)
                    fill_slices.append(
                        {
                            "shares": consume,
                            "basis": lot.basis,
                            "exit_price": row.fill_price,
                            # "pnl": (row.fill_price - lot.basis) * consume
                        }
                    )
                    if consume < lot.shares:
                        lot.shares -= consume
                    else:
                        lot_deque.popleft() if matching == Matching.FIFO else lot_deque.pop()
                    remaining -= consume

                consumed = sum(s["shares"] for s in fill_slices)
                open_shares -= consumed
                lifecycle_slices.extend(fill_slices)

                if grouping in (Grouping.FILL_TO_FILL, Grouping.FLAT_TO_REDUCED):
                    entry_price = sum(s["shares"] * s["basis"] for s in fill_slices) / consumed
                    exit_price = row.fill_price
                    rows.append(
                        {
                            "ticker": ticker,
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "shares": consumed,
                            "ret": exit_price / entry_price - 1,
                        }
                    )
                elif grouping == Grouping.FLAT_TO_FLAT and open_shares == 0:
                    shares = sum(s["shares"] for s in lifecycle_slices)
                    entry_price = sum(s["shares"] * s["basis"] for s in lifecycle_slices) / shares
                    exit_price = (
                        sum(s["shares"] * s["exit_price"] for s in lifecycle_slices) / shares
                    )
                    rows.append(
                        {
                            "ticker": ticker,
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "shares": shares,
                            "ret": exit_price / entry_price - 1,
                        }
                    )
                    lifecycle_slices.clear()

    return pd.DataFrame(rows, columns=_COLUMNS)
