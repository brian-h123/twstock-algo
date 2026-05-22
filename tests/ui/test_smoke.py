import importlib
import unittest.mock as mock


def test_app_importable_without_side_effects():
    """app.py must be importable without triggering any Streamlit calls."""
    with mock.patch.dict("sys.modules", {"streamlit": mock.MagicMock()}):
        mod = importlib.import_module("twstock.ui.app")
        assert mod is not None
