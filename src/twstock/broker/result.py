from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class SimResult:
    strategy_name: str
    tickers: tuple[str, ...]
    start: date
    end: date
    initial_capital: Decimal
    final_equity: Decimal
    equity_curve: pd.Series
    trades: pd.DataFrame
    positions_daily: pd.DataFrame
    total_commission: Decimal
    total_tax: Decimal
    total_slippage_cost: Decimal
    settled_cash_curve: pd.Series  # DatedLedger.settled_cash at each bar's mark point
    unsettled_cash_curve: pd.Series  # pending sum + bar_net at each bar's mark point
    event_log_path: Path
