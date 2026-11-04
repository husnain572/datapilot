"""Tests that run the Streamlit page without opening a browser."""

from pathlib import Path
from unittest.mock import Mock

import httpx
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parents[2] / "frontend" / "app.py")


def test_shows_success_when_backend_is_healthy(monkeypatch):
    monkeypatch.setattr(httpx, "get", Mock(return_value=Mock(status_code=200)))

    page = AppTest.from_file(APP_PATH).run()

    assert not page.exception
    assert page.success[0].value == "Backend connected."
    assert not page.error


def test_shows_error_when_backend_is_unreachable(monkeypatch):
    monkeypatch.setattr(httpx, "get", Mock(side_effect=httpx.ConnectError("refused")))

    page = AppTest.from_file(APP_PATH).run()

    assert not page.exception
    assert "Can't reach the backend" in page.error[0].value
    assert not page.success
