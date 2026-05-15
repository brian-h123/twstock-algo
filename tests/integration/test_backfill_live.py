import argparse
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
import backfill  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("twstock.data.storage.DATA_DIR", tmp_path)
    return tmp_path


def test_backfill_two_tickers_one_year(storage_dir, tmp_path):
    import os

    if not os.getenv("FINMIND_TOKEN"):
        pytest.skip("FINMIND_TOKEN not set")

    args = argparse.Namespace(
        tickers="2330,2317",
        start=date(2023, 1, 1),
        end=date(2023, 12, 31),
        throttle_ms=200,
        force=False,
        source="finmind",
        skip_actions=False,
        log_dir=tmp_path / "logs",
        verbose=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        backfill.main(args)

    assert exc_info.value.code == 0
    assert (storage_dir / "by_ticker" / "2330.parquet").exists()
    assert (storage_dir / "by_ticker" / "2317.parquet").exists()
