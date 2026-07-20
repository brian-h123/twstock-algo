from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
BROKER_SESSION_LOG_DIR = LOG_DIR / "broker_sessions"
UNIVERSE_SNAPSHOT_DATE = date(2026, 5, 8)
BACKTEST_CAPITAL_NTD = 1_000_000
MAX_POSITION_PCT = 0.10
