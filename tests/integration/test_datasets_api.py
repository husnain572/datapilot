"""Tests the dataset API through real HTTP requests to the app."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings, get_settings
from backend.main import app
from backend.schemas.datasets import DatasetInfo
from backend.storage.datasets import DatasetStore, clean_display_name

GOOD_CSV = b"product,quantity\nPen,2\nBook,3\n"
SAMPLE = Path(__file__).parents[2] / "sample_data" / "sales.csv"


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, max_upload_bytes=1024 * 1024)


@pytest.fixture
def client(settings):
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, content=GOOD_CSV, filename="sales.csv"):
    return client.post("/api/v1/datasets", files={"file": (filename, content, "text/csv")})


def test_upload_returns_id_and_profile(client):
    response = upload(client)

    assert response.status_code == 201
    body = response.json()
    assert UUID(body["dataset_id"])
    assert body["filename"] == "sales.csv"
    assert body["profile"]["row_count"] == 2
    assert body["profile"]["column_count"] == 2


def test_each_upload_gets_a_different_id(client):
    assert upload(client).json()["dataset_id"] != upload(client).json()["dataset_id"]


def test_get_returns_the_uploaded_dataset(client):
    dataset_id = upload(client).json()["dataset_id"]

    response = client.get(f"/api/v1/datasets/{dataset_id}")

    assert response.status_code == 200
    assert response.json()["dataset_id"] == dataset_id


def test_preview_returns_rows_up_to_the_limit(client):
    dataset_id = upload(client).json()["dataset_id"]

    body = client.get(f"/api/v1/datasets/{dataset_id}/preview", params={"limit": 1}).json()

    assert body["total_rows"] == 2
    assert body["columns"] == ["product", "quantity"]
    assert body["rows"] == [{"product": "Pen", "quantity": 2}]


def test_preview_with_missing_values_is_valid_json(client):
    dataset_id = upload(client, b"a,b\n1,\n,2\n").json()["dataset_id"]

    rows = client.get(f"/api/v1/datasets/{dataset_id}/preview").json()["rows"]

    assert rows == [{"a": 1, "b": None}, {"a": None, "b": 2}]


def test_preview_limit_is_capped(client):
    dataset_id = upload(client).json()["dataset_id"]

    assert client.get(f"/api/v1/datasets/{dataset_id}/preview", params={"limit": 999}).status_code == 422


def test_delete_removes_the_dataset_and_its_files(client, settings):
    dataset_id = upload(client).json()["dataset_id"]
    folder = settings.data_dir / "datasets" / dataset_id
    assert folder.is_dir()

    assert client.delete(f"/api/v1/datasets/{dataset_id}").status_code == 204

    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 404
    assert not folder.exists()


def test_unknown_id_returns_404(client):
    missing = uuid4()

    assert client.get(f"/api/v1/datasets/{missing}").status_code == 404
    assert client.get(f"/api/v1/datasets/{missing}/preview").status_code == 404
    assert client.delete(f"/api/v1/datasets/{missing}").status_code == 404


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "..%2F..%2Fetc", "1"])
def test_ids_that_are_not_uuids_are_rejected(client, bad_id):
    assert client.get(f"/api/v1/datasets/{bad_id}").status_code in (404, 422)


def test_rejects_files_that_are_not_csv_by_name(client):
    response = upload(client, filename="report.pdf")

    assert response.status_code == 415


def test_rejects_invalid_content_with_a_clear_message(client):
    response = upload(client, b"a,b\n1\n")

    assert response.status_code == 422
    assert "Line 2" in response.json()["detail"]


def test_rejects_files_over_the_size_limit(client):
    response = upload(client, b"a\n" + b"1\n" * (1024 * 1024))

    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]


def test_rejected_uploads_leave_nothing_on_disk(client, settings):
    upload(client, b"a,b\n1\n")

    root = settings.data_dir / "datasets"
    assert not root.exists() or not any(root.iterdir())


def test_malicious_filename_cannot_escape_the_data_folder(client, settings):
    response = upload(client, filename="../../../evil.csv")

    assert response.status_code == 201
    assert response.json()["filename"] == "evil.csv"
    assert not (settings.data_dir.parent / "evil.csv").exists()
    stored = list((settings.data_dir / "datasets").rglob("*"))
    assert all(path.name != "evil.csv" for path in stored)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sales.csv", "sales.csv"),
        ("C:\\Users\\x\\sales.csv", "sales.csv"),
        ("/etc/passwd", "passwd"),
        ("bad\x00na\nme.csv", "badname.csv"),
        (None, "dataset.csv"),
        ("", "dataset.csv"),
    ],
)
def test_clean_display_name(raw, expected):
    assert clean_display_name(raw) == expected


def test_expired_datasets_are_hidden_and_purged(client, settings):
    dataset_id = upload(client).json()["dataset_id"]
    folder = settings.data_dir / "datasets" / dataset_id
    info_path = folder / "info.json"
    info = DatasetInfo.model_validate_json(info_path.read_text())
    info.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    info_path.write_text(info.model_dump_json())

    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 404

    store = DatasetStore(settings.data_dir, settings.dataset_ttl_hours)
    assert store.purge_expired() == 1
    assert not folder.exists()


def test_purge_keeps_datasets_that_have_not_expired(client, settings):
    dataset_id = upload(client).json()["dataset_id"]

    store = DatasetStore(settings.data_dir, settings.dataset_ttl_hours)

    assert store.purge_expired() == 0
    assert (settings.data_dir / "datasets" / dataset_id).is_dir()


def test_sample_dataset_matches_an_independent_calculation(client):
    """Compare the API's profile with a separate calculation using only the csv module."""
    import csv

    with SAMPLE.open(newline="", encoding="utf-8") as file:
        rows = list(csv.reader(file))
    header, data = rows[0], rows[1:]
    expected_missing = sum(value == "" for row in data for value in row)
    expected_duplicates = len(data) - len({tuple(row) for row in data})

    response = upload(client, SAMPLE.read_bytes())

    assert response.status_code == 201
    profile = response.json()["profile"]
    assert profile["row_count"] == 1010
    assert [c["name"] for c in profile["columns"]] == header
    assert expected_missing == 30
    assert profile["missing_cell_count"] == expected_missing
    assert profile["duplicate_row_count"] == expected_duplicates
    assert expected_duplicates >= 10
    kinds = {c["name"]: c["kind"] for c in profile["columns"]}
    assert kinds == {
        "order_date": "datetime",
        "product": "categorical",
        "quantity": "numeric",
        "unit_price": "numeric",
        "region": "categorical",
    }
