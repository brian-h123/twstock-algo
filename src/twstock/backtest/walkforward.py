from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta


@dataclass
class WalkForwardSplit:
    train_years: int = 3
    test_years: int = 1
    step_years: int = 1
    anchored: bool = False

    def split(self, start: date, end: date) -> Iterator[tuple[date, date, date, date]]:
        """Yield (train_start, train_end, test_start, test_end) tuples.

        Rolling (anchored=False): train window slides forward by step_years.
        Anchored (anchored=True): train_start is fixed; train_end grows.

        Test windows are non-overlapping by default (step == test_years).
        Setting step < test_years produces overlapping test windows, useful
        for diagnostic plots but not for fold aggregation.
        """

        if relativedelta(end, start).years < self.train_years + self.test_years:
            raise ValueError(
                f"date range {start}–{end} is too short: need at least "
                f"{self.train_years + self.test_years} years, got "
                f"{relativedelta(end, start).years}"
            )
        test_end = start + relativedelta(years=self.train_years + self.test_years)
        while test_end <= end:
            test_start = test_end - relativedelta(years=self.test_years)
            train_end = test_start
            if self.anchored:
                train_start = start
            else:
                train_start = train_end - relativedelta(years=self.train_years)

            yield (train_start, train_end, test_start, test_end)

            test_end += relativedelta(years=self.step_years)
