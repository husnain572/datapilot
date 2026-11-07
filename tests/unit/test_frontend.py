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
    assert not page.error
    assert not any("Backend connected" in item.value for item in page.markdown)


def test_shows_error_when_backend_is_unreachable(monkeypatch):
    monkeypatch.setattr(httpx, "get", Mock(side_effect=httpx.ConnectError("refused")))

    page = AppTest.from_file(APP_PATH).run()

    assert not page.exception
    assert "temporarily unavailable" in page.error[0].value


def test_streamlit_never_loads_the_backends_ai_keys(monkeypatch, tmp_path):
    """The frontend must not read .env, which holds the backend's AI keys."""
    import os

    (tmp_path / ".env").write_text("AI_API_KEY=should-not-load\nAI_FALLBACK_API_KEY=nor-this\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_FALLBACK_API_KEY", raising=False)
    monkeypatch.setattr(httpx, "get", Mock(return_value=Mock(status_code=200, json=lambda: {})))

    page = AppTest.from_file(APP_PATH).run()

    assert not page.exception
    assert "AI_API_KEY" not in os.environ
    assert "AI_FALLBACK_API_KEY" not in os.environ
