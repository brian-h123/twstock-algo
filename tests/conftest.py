import pathlib

import pytest

# ---------------------------------------------------------------------------
# Fixtures dir
# ---------------------------------------------------------------------------


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# VCR / pytest-recording config
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vcr_cassette_dir():
    return str(pathlib.Path(__file__).parent / "cassettes")


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "filter_headers": ["Authorization", "X-API-KEY"],
        "filter_query_parameters": ["token"],
    }


# ---------------------------------------------------------------------------
# Integration marker + --run-integration CLI flag
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.integration (hits real APIs).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that hit real external APIs (deselected by default).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="pass --run-integration to run this test")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip)
