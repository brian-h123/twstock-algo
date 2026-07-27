"""Phase 5 daily live-smoke convenience wrapper.

Wires up the three steps needed to exercise LiveFeed's live-connectivity
path against genuinely fresh data on a single trading day: refresh 2330's
OHLCV + corporate actions, refresh the suspension-halt store, then run the
live connectivity smoke test itself (--skip-single-asset --skip-momentum so
nothing slow runs).

Meant to be run manually once per trading day for ~14 sessions, per
PHASE_5.md's live-connectivity exit criterion. This is intentionally NOT a
scheduled/cron deliverable of Phase 5 -- automating the daily run is
deferred to Phase 8's live-integration work, alongside real broker
connectivity. This script only removes the friction of the three manual
steps into one command; running it via backfill.py's batch replay of an
already-accumulated window would not exercise the same behavior (see
journal/decisions -- LiveFeed's daily freshness check only fires when each
day's data is fetched and replayed separately).

Usage:
    uv run python scripts/phase_5_live_smoke.py
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Must match scripts/phase_5_paper_run.py's SINGLE_ASSET_TICKER -- kept as a
# plain literal here rather than imported, since scripts/ isn't a package.
SINGLE_ASSET_TICKER = "2330"

_STEPS: list[list[str]] = [
    ["scripts/backfill.py", "--tickers", SINGLE_ASSET_TICKER],
    ["scripts/fetch_suspensions.py"],
    ["scripts/phase_5_paper_run.py", "--skip-single-asset", "--skip-momentum"],
]


def main() -> None:
    for step in _STEPS:
        command = [sys.executable, *step]
        print(f"\n$ {' '.join(command)}", flush=True)
        try:
            subprocess.run(command, cwd=REPO_ROOT, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"\nFAIL: {' '.join(step)} exited {exc.returncode}")
            sys.exit(exc.returncode)

    print("\nPASS: live smoke test completed without crashing.")


if __name__ == "__main__":
    main()
