from collections import deque
from dataclasses import dataclass
from enum import Enum

import pandas as pd

_COLUMNS = ["ticker", "entry_price", "exit_price", "shares", "ret"]


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


class Grouping(Enum):
    FILL_TO_FILL = "fill_to_fill"
    FLAT_TO_FLAT = "flat_to_flat"
    FLAT_TO_REDUCED = "flat_to_reduced"


class Matching(Enum):
    FIFO = "fifo"
    LIFO = "lifo"


class LotLedger:
    """Per-ticker lot tracker for incremental fill events.

    Maintains an open-lot deque and buffers closed slices so callers can
    compute round-trip P&L via round_trips() with either grouping mode
    after all fills are recorded.

    Args:
        matching: Lot-consumption order; FIFO or LIFO.
    """

    @dataclass
    class _Lot:
        shares: int
        basis: float

    def __init__(self, matching: Matching = Matching.FIFO) -> None:
        self._matching = matching
        self._lot_deque: deque[LotLedger._Lot] = deque()
        self._open_shares: int = 0
        self._close_slices: list[dict] = []
        self._generation: int = (
            0  # increments each time open_shares reaches 0 (lifecycle boundary for FLAT_TO_FLAT)
        )
        self._fill_idx: int = 0  # increments on every sell event (row index for FILL_TO_FILL)

    def on_fill(self, side: Side, shares: int, price: float, cost: float) -> None:
        """Record a single fill event and update lot state.

        Args:
            side: BUY appends a new lot; SELL consumes lots in matching order.
            shares: Number of shares filled.
            price: Gross per-share fill price (before costs).
            cost: Total brokerage cost (commission + tax) for the fill.
        """

        if side == Side.BUY:
            self._lot_deque.append(
                LotLedger._Lot(shares=shares, basis=(price * shares + cost) / shares)
            )
            self._open_shares += shares
        elif side == Side.SELL:
            remaining = shares

            while remaining > 0 and self._lot_deque:
                lot = self._lot_deque[0] if self._matching == Matching.FIFO else self._lot_deque[-1]
                consume = min(lot.shares, remaining)
                self._close_slices.append(
                    {
                        "shares": consume,
                        "basis": lot.basis,
                        "exit_price": (price * shares - cost) / shares,
                        "generation": self._generation,
                        "fill_index": self._fill_idx,
                    }
                )
                if consume < lot.shares:
                    lot.shares -= consume
                else:
                    if self._matching == Matching.FIFO:
                        self._lot_deque.popleft()
                    else:
                        self._lot_deque.pop()
                remaining -= consume

            consumed = shares - remaining
            self._open_shares -= consumed
            self._fill_idx += 1
            if self._open_shares == 0:
                self._generation += 1

    def on_split(self, ratio: float) -> None:
        """Apply a stock split to all open lots.

        Each lot's share count is adjusted by ``round(shares * ratio)``
        and its cost basis by ``basis / ratio``. ``_open_shares`` is
        recomputed from the rounded lot totals. A no-op when the deque
        is empty.

        Args:
            ratio: New shares per old share (e.g. 2.0 for a 2-for-1 split).
        """
        open_shares = 0
        for lot in self._lot_deque:
            lot.shares = round(lot.shares * ratio)
            lot.basis = lot.basis / ratio
            open_shares += lot.shares
        self._open_shares = open_shares

    @property
    def open_shares(self) -> int:
        """Total shares currently held in open lots."""
        return self._open_shares

    @property
    def avg_cost(self) -> float | None:
        """Share-weighted average cost basis across all open lots.

        Returns None when flat; forces explicit handling at the call site
        rather than silently propagating NaN.
        """
        if self._open_shares == 0:
            return None
        total_cost = 0
        for lot in self._lot_deque:
            total_cost += lot.shares * lot.basis
        return total_cost / self._open_shares

    def round_trips(self, grouping: Grouping = Grouping.FLAT_TO_FLAT) -> list[dict]:
        """Return round-trip dicts from the buffered closed slices.

        Args:
            grouping: FLAT_TO_FLAT groups slices by lifecycle boundary
                (_generation counter); FILL_TO_FILL emits one row per
                sell event (_fill_idx counter).

        Returns:
            List of dicts with keys ``entry_price``, ``exit_price``,
            ``shares``, ``ret``.  Empty list if no sells have been recorded.
        """

        if not self._close_slices:
            return []

        df = pd.DataFrame(self._close_slices)
        if grouping in (Grouping.FILL_TO_FILL, Grouping.FLAT_TO_REDUCED):
            df["notional_basis"] = df["shares"] * df["basis"]

            grp = df.groupby("fill_index").agg(
                shares=("shares", "sum"),
                notional_basis=("notional_basis", "sum"),
                exit_price=("exit_price", "first"),
            )
            grp["entry_price"] = grp["notional_basis"] / grp["shares"]
            grp["ret"] = grp["exit_price"] / grp["entry_price"] - 1
        elif grouping == Grouping.FLAT_TO_FLAT:
            df = df[df["generation"] < self._generation]  # exclude still-open lifecycle
            if df.empty:
                return []
            df["notional_basis"] = df["shares"] * df["basis"]
            df["notional_exit"] = df["shares"] * df["exit_price"]

            grp = df.groupby("generation").agg(
                shares=("shares", "sum"),
                notional_basis=("notional_basis", "sum"),
                notional_exit=("notional_exit", "sum"),
            )
            grp["entry_price"] = grp["notional_basis"] / grp["shares"]
            grp["exit_price"] = grp["notional_exit"] / grp["shares"]
            grp["ret"] = grp["exit_price"] / grp["entry_price"] - 1

        return grp.reset_index()[["entry_price", "exit_price", "shares", "ret"]].to_dict(
            orient="records"
        )


def build_round_trips(
    trades: pd.DataFrame,
    grouping: Grouping = Grouping.FLAT_TO_FLAT,
    matching: Matching = Matching.FIFO,
) -> pd.DataFrame:
    """Compute realised round-trip P&L for all fills in *trades*.

    Thin wrapper over LotLedger; one ledger per ticker. Pure function
    over result.trades.

    Args:
        trades: Engine trade log; must contain ``ticker``, ``timestamp``,
            ``side``, ``shares``, ``fill_price``, ``total_cost``.
        grouping: How closed slices are collapsed into round-trip rows.
        matching: Lot-consumption order; FIFO or LIFO.

    Returns:
        DataFrame with columns ``ticker``, ``entry_price``, ``exit_price``,
        ``shares``, ``ret``.  Empty if *trades* has no sells.
    """
    if trades.empty:
        return pd.DataFrame(columns=_COLUMNS)

    rows: list[dict] = []
    for ticker, grp in trades.groupby("ticker"):
        ledger = LotLedger(matching=matching)

        for _, row in grp.sort_values("timestamp").iterrows():
            ledger.on_fill(
                side=Side(row["side"]),
                shares=row["shares"],
                price=row["fill_price"],
                cost=row["total_cost"],
            )
        for rt in ledger.round_trips(grouping=grouping):
            rows.append({"ticker": ticker, **rt})
    return pd.DataFrame(rows, columns=_COLUMNS)
