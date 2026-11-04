"""Tests the upload flow on the page, with fake backend responses."""

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parents[2] / "frontend" / "app.py")
DATASET_ID = str(uuid4())
DATASET = {
    "dataset_id": DATASET_ID,
    "filename": "sales.csv",
    "size_bytes": 100,
    "created_at": "2026-11-04T00:00:00Z",
    "expires_at": "2026-11-05T00:00:00Z",
    "profile": {
        "row_count": 2,
        "column_count": 2,
        "missing_cell_count": 1,
        "duplicate_row_count": 0,
        "sample_records": [],
        "columns": [
            {
                "name": "price",
                "kind": "numeric",
                "dtype": "float64",
                "missing_count": 1,
                "missing_percent": 50.0,
                "unique_count": 1,
                "numeric": {"min": 5, "max": 5, "mean": 5, "median": 5, "std": None, "sum": 5},
            },
            {
                "name": "region",
                "kind": "categorical",
                "dtype": "str",
                "missing_count": 0,
                "missing_percent": 0.0,
                "unique_count": 2,
                "top_values": [{"value": "North", "count": 1}],
            },
        ],
    },
}
PREVIEW = {
    "dataset_id": DATASET_ID,
    "total_rows": 2,
    "columns": ["price", "region"],
    "rows": [{"price": 5, "region": "North"}, {"price": None, "region": "South"}],
}


def fake_get(url, **_kwargs):
    if url.endswith("/preview"):
        return Mock(status_code=200, json=lambda: PREVIEW)
    return Mock(status_code=200, json=lambda: {"status": "ok"})


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get)


def button(page, label):
    return next(b for b in page.button if b.label == label)


def test_sample_button_uploads_and_shows_the_profile(monkeypatch):
    post = Mock(return_value=Mock(status_code=201, json=lambda: DATASET))
    monkeypatch.setattr(httpx, "post", post)
    page = AppTest.from_file(APP_PATH).run()

    button(page, "Try the sample sales dataset").click().run()

    assert not page.exception
    assert post.call_args.kwargs["files"]["file"][0] == "sales.csv"
    assert {metric.label: metric.value for metric in page.metric} == {
        "Rows": "2",
        "Columns": "2",
        "Missing values": "1",
        "Duplicate rows": "0",
    }


def test_failed_upload_shows_the_backend_message(monkeypatch):
    rejected = Mock(status_code=422, json=lambda: {"detail": "Line 3 has 1 values."})
    monkeypatch.setattr(httpx, "post", Mock(return_value=rejected))
    page = AppTest.from_file(APP_PATH).run()

    button(page, "Try the sample sales dataset").click().run()

    assert not page.exception
    assert page.error[0].value == "Line 3 has 1 values."
    assert not page.metric


def test_remove_button_deletes_the_dataset(monkeypatch):
    monkeypatch.setattr(httpx, "post", Mock(return_value=Mock(status_code=201, json=lambda: DATASET)))
    delete = Mock(return_value=Mock(status_code=204))
    monkeypatch.setattr(httpx, "delete", delete)
    page = AppTest.from_file(APP_PATH).run()
    button(page, "Try the sample sales dataset").click().run()

    button(page, "Remove dataset").click().run()

    assert not page.exception
    assert delete.call_args.args[0].endswith(f"/api/v1/datasets/{DATASET_ID}")
    assert not page.metric
