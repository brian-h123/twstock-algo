import numpy as np
import pandas as pd

from twstock.backtest.strategy import Strategy


class LookaheadError(Exception):
    pass


def assert_no_lookahead(
    strategy: Strategy, bars: pd.DataFrame, perturbation_start: int = -50, tolerance: float = 1e-9
) -> None:
    """Run strategy.generate_signals(bars) and compare to running it
    on a perturbed copy where bars after index `perturbation_start`
    have prices scrambled. The first len(bars) + perturbation_start
    signals must be identical between the two runs.

    Raises LookaheadError with a diff if any signal differs.
    """

    original_signals = strategy.generate_signals(bars=bars)

    perturbed_bars = bars.copy()
    rng = np.random.default_rng(42)
    future_slice = bars.iloc[perturbation_start:].copy()
    perm = rng.permutation(len(future_slice))
    perturbed_bars.iloc[perturbation_start:] = future_slice.values[perm]
    perturbed_signals = strategy.generate_signals(bars=perturbed_bars)

    window = len(bars) + perturbation_start  # perturbation_start is negative
    orig_window = original_signals.iloc[:window]
    pert_window = perturbed_signals.iloc[:window]

    both_nan = orig_window.isna() & pert_window.isna()
    one_nan = orig_window.isna() ^ pert_window.isna()
    changed = ~both_nan & (one_nan | ((orig_window - pert_window).abs() > tolerance))

    if changed.any():
        diff = pd.DataFrame({"original": orig_window[changed], "perturbed": pert_window[changed]})
        raise LookaheadError(
            f"Strategy signals differ on {changed.sum()} bar(s) before "
            f"perturbation point (index {window}):\n{diff}"
        )
