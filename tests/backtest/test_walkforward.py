from datetime import date

import pytest

from twstock.backtest.walkforward import WalkForwardSplit

# ---------------------------------------------------------------------------
# Rolling (anchored=False)
# ---------------------------------------------------------------------------


def test_rolling_basic_fold_dates():
    # 6 years of data, train=3, test=1, step=1 → 3 folds
    # Fold 1: train 2016–2019, test 2019–2020
    # Fold 2: train 2017–2020, test 2020–2021
    # Fold 3: train 2018–2021, test 2021–2022
    wf = WalkForwardSplit(train_years=3, test_years=1, step_years=1, anchored=False)
    folds = list(wf.split(date(2016, 1, 1), date(2022, 1, 1)))

    assert len(folds) == 3
    assert folds[0] == (date(2016, 1, 1), date(2019, 1, 1), date(2019, 1, 1), date(2020, 1, 1))
    assert folds[1] == (date(2017, 1, 1), date(2020, 1, 1), date(2020, 1, 1), date(2021, 1, 1))
    assert folds[2] == (date(2018, 1, 1), date(2021, 1, 1), date(2021, 1, 1), date(2022, 1, 1))


def test_rolling_train_end_equals_test_start():
    # train_end and test_start must be the same date for every fold
    wf = WalkForwardSplit(train_years=3, test_years=1, step_years=1)
    for train_start, train_end, test_start, test_end in wf.split(
        date(2016, 1, 1), date(2022, 1, 1)
    ):
        assert train_end == test_start


def test_rolling_yields_at_least_3_folds_on_decade():
    # Exit criterion from PHASE_3.md: ≥3 folds on 2016–2026
    wf = WalkForwardSplit(train_years=3, test_years=1, step_years=1, anchored=False)
    folds = list(wf.split(date(2016, 1, 1), date(2026, 1, 1)))
    assert len(folds) >= 3


# ---------------------------------------------------------------------------
# Anchored (anchored=True)
# ---------------------------------------------------------------------------


def test_anchored_train_start_fixed():
    # train_start must always equal the original start date
    wf = WalkForwardSplit(train_years=3, test_years=1, step_years=1, anchored=True)
    folds = list(wf.split(date(2016, 1, 1), date(2022, 1, 1)))

    assert len(folds) == 3
    for train_start, _, _, _ in folds:
        assert train_start == date(2016, 1, 1)


def test_anchored_fold_dates():
    # Fold 1: train 2016–2019, test 2019–2020
    # Fold 2: train 2016–2020, test 2020–2021  (train_start pinned)
    wf = WalkForwardSplit(train_years=3, test_years=1, step_years=1, anchored=True)
    folds = list(wf.split(date(2016, 1, 1), date(2021, 1, 1)))

    assert len(folds) == 2
    assert folds[0] == (date(2016, 1, 1), date(2019, 1, 1), date(2019, 1, 1), date(2020, 1, 1))
    assert folds[1] == (date(2016, 1, 1), date(2020, 1, 1), date(2020, 1, 1), date(2021, 1, 1))


# ---------------------------------------------------------------------------
# Overlapping test windows (step < test_years)
# ---------------------------------------------------------------------------


def test_overlapping_test_windows():
    # step=1, test_years=2 → consecutive test windows overlap by 1 year
    # Fold 1: test 2019-01-01 → 2021-01-01
    # Fold 2: test 2020-01-01 → 2022-01-01  (overlap: 2020–2021)
    wf = WalkForwardSplit(train_years=3, test_years=2, step_years=1, anchored=False)
    folds = list(wf.split(date(2016, 1, 1), date(2022, 1, 1)))

    assert len(folds) == 2
    assert folds[0][2] == date(2019, 1, 1) and folds[0][3] == date(2021, 1, 1)
    assert folds[1][2] == date(2020, 1, 1) and folds[1][3] == date(2022, 1, 1)
    # test windows overlap
    assert folds[0][3] > folds[1][2]


# ---------------------------------------------------------------------------
# Exact-fit boundary
# ---------------------------------------------------------------------------


def test_exact_fit_yields_one_fold():
    # Exactly train_years + test_years of data → exactly 1 fold
    wf = WalkForwardSplit(train_years=3, test_years=1, step_years=1)
    folds = list(wf.split(date(2016, 1, 1), date(2020, 1, 1)))

    assert len(folds) == 1
    assert folds[0] == (date(2016, 1, 1), date(2019, 1, 1), date(2019, 1, 1), date(2020, 1, 1))


# ---------------------------------------------------------------------------
# Insufficient data → ValueError
# ---------------------------------------------------------------------------


def test_insufficient_data_raises():
    wf = WalkForwardSplit(train_years=3, test_years=1)
    with pytest.raises(ValueError):
        list(wf.split(date(2016, 1, 1), date(2018, 1, 1)))  # only 2 years, need 4


def test_insufficient_data_message_mentions_years():
    wf = WalkForwardSplit(train_years=3, test_years=1)
    with pytest.raises(ValueError, match="4"):
        list(wf.split(date(2016, 1, 1), date(2018, 1, 1)))
