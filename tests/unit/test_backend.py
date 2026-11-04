"""Tests for the health checks and settings."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings, get_settings
from backend.main import app


@pytest.fixture
def make_client():
    """Build a test client that uses a chosen data folder, ignoring any real .env."""

    def _make_client(data_dir: Path) -> TestClient:
        settings = Settings(_env_file=None, data_dir=data_dir)
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    yield _make_client
    app.dependency_overrides.clear()


def test_health_returns_ok(make_client, tmp_path):
    response = make_client(tmp_path).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_data_dir_is_writable(make_client, tmp_path):
    response = make_client(tmp_path).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"data_dir_writable": True}}


def test_not_ready_when_data_dir_is_missing(make_client, tmp_path):
    response = make_client(tmp_path / "does-not-exist").get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"data_dir_writable": False}}


def test_api_key_is_hidden_when_settings_are_printed():
    settings = Settings(_env_file=None, vultr_serverless_inference_api_key="not-a-real-key")

    assert "not-a-real-key" not in repr(settings)
    assert settings.vultr_serverless_inference_api_key.get_secret_value() == "not-a-real-key"
