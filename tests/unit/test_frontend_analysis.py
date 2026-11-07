"""Tests the analysis screens on the page, with fake backend responses."""

import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parents[2] / "frontend" / "app.py")
DATASET = {
    "dataset_id": str(uuid4()),
    "filename": "sales.csv",
    "size_bytes": 10,
    "created_at": "2026-11-04T00:00:00Z",
    "expires_at": "2026-11-05T00:00:00Z",
    "profile": {
        "row_count": 2,
        "column_count": 1,
        "missing_cell_count": 0,
        "duplicate_row_count": 0,
        "sample_records": [],
        "columns": [
            {
                "name": "a",
                "kind": "numeric",
                "dtype": "int64",
                "missing_count": 0,
                "missing_percent": 0,
                "unique_count": 2,
                "numeric": {"min": 1, "max": 2, "mean": 1.5, "median": 1.5, "std": 0.7, "sum": 3},
            }
        ],
    },
}
ANALYSIS_ID = str(uuid4())
COMPLETED = {
    "analysis_id": ANALYSIS_ID,
    "dataset_id": DATASET["dataset_id"],
    "question": "Total revenue?",
    "parent_id": None,
    "status": "completed",
    "stage": "Finished",
    "created_at": "2026-11-04T00:00:00Z",
    "updated_at": "2026-11-04T00:00:01Z",
    "plan": {"goal": "Add up", "steps": [{"description": "Sum", "columns": ["a"]}], "assumptions": []},
    "code": "emit_metric('Total', 3)",
    "attempts": [{"attempt": 1, "outcome": "succeeded", "code": "x", "duration_seconds": 0.5, "logs": "", "problem": None}],
    "result": {
        "status": "ok",
        "metrics": [{"name": "Total revenue", "value": 1234.5, "unit": None, "check": None}],
        "tables": [
            {"name": "By product", "columns": ["product", "revenue"], "rows": [["Pen", 1234.5]], "total_rows": 1, "check": None}
        ],
        "charts": [
            {"name": "c1", "kind": "bar", "title": "Top", "x_label": "product", "y_label": "revenue", "x": ["Pen"], "y": [1234.5]},
            {"name": "c2", "kind": "line", "title": None, "x_label": "m", "y_label": "r", "x": ["2025-01"], "y": [1]},
            {"name": "c3", "kind": "pie", "title": None, "x_label": "m", "y_label": "r", "x": ["A", "B"], "y": [1, None]},
            {"name": "c4", "kind": "scatter", "title": None, "x_label": "m", "y_label": "r", "x": [1], "y": [2]},
        ],
    },
    "verifications": [
        {"target": "Total revenue", "target_type": "metric", "status": "verified", "formula": "sum(a)", "detail": None},
        {"target": "By product", "target_type": "table", "status": "not_checked", "formula": None, "detail": None},
    ],
    "insights": {
        "observations": ["See ![x](https://evil.example/steal) and [click](https://evil.example)"],
        "assumptions": ["Blanks skipped."],
        "recommendations": [{"action": "Promote pens.", "evidence": "Pens earned 1234.5."}],
    },
    "removed_claims": ["Made up 999."],
    "message": None,
    "ai_requests": 2,
}


class FakeBackend:
    def __init__(self, record=None, status_code=202):
        self.record = record or COMPLETED
        self.status_code = status_code
        self.posts = []
        self.detail = "The analysis couldn't start."
        self.left_today = 5

    def get(self, url, **_kwargs):
        if url.endswith("/health"):
            return Mock(status_code=200, json=lambda: {"status": "ok"})
        if url.endswith("/report"):
            return Mock(status_code=200, text="# Report")
        if url.endswith("/usage"):
            return Mock(status_code=200, json=lambda: {"analyses_left_today": self.left_today})
        if url.endswith("/preview"):
            return Mock(status_code=200, json=lambda: {"total_rows": 2, "columns": ["a"], "rows": [{"a": 1}]})
        return Mock(status_code=200, json=lambda: self.record)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return Mock(status_code=self.status_code, json=lambda: {"analysis_id": ANALYSIS_ID, "detail": self.detail})


@pytest.fixture
def run_page(monkeypatch):
    def _run_page(backend: FakeBackend, *, question="Total revenue?", analyze=True) -> AppTest:
        monkeypatch.setattr(httpx, "get", backend.get)
        monkeypatch.setattr(httpx, "post", backend.post)
        page = AppTest.from_file(APP_PATH, default_timeout=20)
        page.session_state["dataset"] = DATASET
        page.run()
        if analyze:
            page.text_area(key="question").input(question)
            next(b for b in page.button if b.label == "Analyze").click().run()
        return page

    return _run_page


def labels(elements) -> list[str]:
    return [element.label for element in elements]


def test_completed_analysis_shows_results_verification_and_insights(run_page):
    page = run_page(FakeBackend())

    assert not page.exception
    assert any("1,234.50" == metric.value for metric in page.metric)
    assert any("1 of 2 figures were recalculated" in item.value for item in page.markdown)
    assert any(">Verified<" in item.value and "sum&#40;a&#41;" in item.value for item in page.markdown)
    assert not any("Not independently checked" in item.value for item in page.markdown)
    assert not page.success  # No large green banner.
    assert any("Promote pens" in item.value for item in page.markdown)
    assert any("don't prove" in item.value for item in page.info)
    assert any("1 AI statement" in item.value for item in page.caption)
    assert "Analysis plan" in labels(page.tabs)
    assert "Generated Python code" in labels(page.tabs)
    assert any(label.startswith("Execution log") for label in labels(page.tabs))
    assert page.code[0].value == COMPLETED["code"]


def test_links_and_images_from_the_ai_are_shown_as_plain_text(run_page):
    page = run_page(FakeBackend())

    observation = next(item.value for item in page.markdown if "evil" in item.value)
    assert "![x](" not in observation
    assert "[click](" not in observation
    assert "https://" not in observation
    assert "&#33;&#91;x&#93;&#40;" in observation


def test_no_access_code_is_asked_for(run_page):
    backend = FakeBackend()
    page = run_page(backend)

    assert not any("access code" in field.label.lower() for field in page.text_input)
    assert backend.posts[0][1]["json"]["question"] == "Total revenue?"
    assert "X-Visitor-Id" in backend.posts[0][1]["headers"]
    assert "X-Access-Code" not in backend.posts[0][1]["headers"]


def test_limit_message_from_the_backend_is_shown(run_page):
    backend = FakeBackend(status_code=429)
    backend.detail = "You've reached the limit of 4 an hour."

    page = run_page(backend)

    assert any("limit of 4 an hour" in item.value for item in page.error)
    assert not any(metric.label == "Total revenue" for metric in page.metric)


def test_usage_counts_are_not_shown_to_users(run_page):
    page = run_page(FakeBackend(), analyze=False)

    assert not any("analyses left" in item.value for item in [*page.caption, *page.markdown])


def test_no_analyses_left_shows_a_clear_message(run_page):
    backend = FakeBackend()
    backend.left_today = 0

    page = run_page(backend, analyze=False)

    assert any("analysis limit has been reached" in item.value for item in page.info)


def test_example_button_shows_the_saved_example(run_page):
    backend = FakeBackend({**COMPLETED, "analysis_id": "00000000-0000-4000-8000-00000000da7a"})
    page = run_page(backend, analyze=False)

    next(b for b in page.button if b.label == "See an example analysis").click().run()

    assert not page.exception
    assert any("saved example" in item.value for item in page.info)
    assert any(metric.value == "1,234.50" for metric in page.metric)
    assert backend.posts == []  # No analysis was started, so no AI was used.


def test_converted_files_show_where_they_came_from(run_page, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(httpx, "get", backend.get)
    monkeypatch.setattr(httpx, "post", backend.post)
    page = AppTest.from_file(APP_PATH, default_timeout=20)
    page.session_state["dataset"] = {**DATASET, "source_format": "Excel", "source_note": "Sheet 'Sales'"}
    page.run()

    assert any("Converted from Excel" in item.value for item in page.caption)


def test_empty_question_is_stopped_on_the_page(run_page):
    backend = FakeBackend()

    page = run_page(backend, question="  ")

    assert backend.posts == []
    assert any("type a question" in item.value for item in page.error)


def test_sample_question_button_fills_the_box(run_page):
    page = run_page(FakeBackend(), analyze=False)

    next(b for b in page.button if b.label == "Use the sample question").click().run()

    assert "top five products" in page.text_area(key="question").value


def test_refused_question_shows_the_reason_and_no_results(run_page):
    record = {**COMPLETED, "status": "refused", "message": "There's no age column.", "result": None, "insights": None, "code": None, "plan": None}

    page = run_page(FakeBackend(record))

    assert any("no age column" in item.value for item in page.warning)
    assert not any(metric.label == "Total revenue" for metric in page.metric)


def test_failed_analysis_never_shows_results(run_page):
    record = {**COMPLETED, "status": "failed", "message": "It didn't work."}

    page = run_page(FakeBackend(record))

    assert any("It didn" in item.value for item in page.error)
    assert not any(metric.label == "Total revenue" for metric in page.metric)
    assert not any("Promote pens" in item.value for item in page.markdown)


def test_mismatch_is_shown_prominently(run_page):
    verifications = [{**COMPLETED["verifications"][0], "status": "mismatch"}]

    page = run_page(FakeBackend({**COMPLETED, "verifications": verifications}))

    assert any("failed independent recalculation" in item.value for item in page.error)


def test_explanation_failure_still_shows_the_numbers(run_page):
    record = {**COMPLETED, "insights": None, "message": "The explanation couldn't be generated."}

    page = run_page(FakeBackend(record))

    assert any("couldn't be generated" in item.value for item in page.warning)
    assert any(metric.value == "1,234.50" for metric in page.metric)


def test_second_question_can_follow_up_on_the_first(run_page):
    backend = FakeBackend()
    page = run_page(backend)

    page.text_area(key="question").input("And by region?")
    next(b for b in page.button if b.label == "Analyze").click().run()

    assert backend.posts[1][1]["json"]["parent_id"] == ANALYSIS_ID


def test_containment_lab_shows_results(run_page, monkeypatch):
    backend = FakeBackend()
    page = run_page(backend, analyze=False)
    next(b for b in page.sidebar.button if b.label == "Security").click().run()
    report = {
        "all_contained": True,
        "results": [
            {"name": "Infinite loop", "attack": "Runs forever", "contained": True, "evidence": "Stopped.", "duration_seconds": 6}
        ],
    }
    monkeypatch.setattr(httpx, "post", Mock(return_value=Mock(status_code=200, json=lambda: report)))

    next(b for b in page.button if b.label == "Run the attacks").click().run()

    assert not page.exception
    assert any("All 1 attacks" in item.value for item in page.success)


def test_spreadsheet_formulas_in_downloads_are_neutralized():
    source = Path(APP_PATH).read_text()
    namespace: dict = {}
    start, end = source.index("def safe_csv"), source.index("def backend_is_healthy")
    exec("import csv, io\n" + source[start:end], namespace)  # noqa: S102 - our own source code.

    output = namespace["safe_csv"](["=col"], [["=HYPERLINK(1)"], [5], ["@x"], ["safe"]])

    assert "'=col" in output
    assert "'=HYPERLINK(1)" in output
    assert "'@x" in output
    assert "\n5" in output.replace("\r", "")
    assert json.dumps("safe")[1:-1] in output


def test_html_in_ai_text_cannot_become_page_markup(run_page):
    hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    record = {**COMPLETED, "insights": {**COMPLETED["insights"], "observations": [hostile]}}

    page = run_page(FakeBackend(record))

    shown = " ".join(item.value for item in page.markdown if "alert" in item.value)
    assert "<script" not in shown
    assert "<img" not in shown


def test_apostrophes_in_ai_text_are_encoded_once(run_page):
    record = {**COMPLETED, "insights": {**COMPLETED["insights"], "observations": ["The server's memory & more"]}}

    page = run_page(FakeBackend(record))

    shown = next(item.value for item in page.markdown if "memory" in item.value)
    assert "server&#39;s" in shown
    assert "&#38; more" in shown
    assert "&#35;" not in shown  # Double encoding is what displayed "server&#x27;s".


CHARTS = {
    "date_column": "order_date",
    "trend": {"Number of rows": {"labels": ["2025-01", "2025-02"], "values": [10, 12]}},
    "categories": {"product": {"Number of rows": {"labels": ["Pen", "Book"], "values": [7, 5]}}},
    "histograms": {"a": {"labels": ["0 – 1", "1 – 2"], "counts": [3, 4]}},
    "missing": {"labels": ["a"], "values": [0]},
}


def test_dashboard_shows_charts_from_the_backend(run_page, monkeypatch):
    backend = FakeBackend()

    def get(url, **kwargs):
        if url.endswith("/charts"):
            return Mock(status_code=200, json=lambda: CHARTS)
        return backend.get(url, **kwargs)

    monkeypatch.setattr(httpx, "get", get)
    page = AppTest.from_file(APP_PATH, default_timeout=20)
    page.session_state["dataset"] = DATASET
    page.session_state["page"] = "Dashboard"
    page.run()

    assert not page.exception
    assert {metric.label for metric in page.metric} == {"Rows", "Columns", "Missing values", "Duplicate rows"}
    assert len(page.get("plotly_chart")) == 3  # Trend, top categories, distribution.
    assert any("No missing values" in item.value for item in page.markdown)


def test_sidebar_navigation_switches_pages(run_page):
    page = run_page(FakeBackend(), analyze=False)

    next(b for b in page.sidebar.button if b.label == "Upload data").click().run()

    assert any("Upload Your Data" in item.value for item in page.markdown)
    assert not page.exception


def test_suggested_questions_fill_the_question_box(run_page):
    page = run_page(FakeBackend(), analyze=False)

    suggestion = next(b for b in page.button if "data quality" in b.label)
    suggestion.click().run()

    assert "missing values" in page.text_area(key="question").value


def test_result_charts_are_shown_without_opening_a_tab(run_page):
    page = run_page(FakeBackend())

    assert len(page.get("plotly_chart")) == 4


def test_shares_are_shown_as_percentages(run_page):
    metrics = [{"name": "Top five revenue share", "value": 0.7728, "unit": None, "check": None}]
    record = {**COMPLETED, "result": {**COMPLETED["result"], "metrics": metrics}}

    page = run_page(FakeBackend(record))

    assert any(metric.value == "77.3%" for metric in page.metric)


def test_no_emoji_or_tick_marks_on_the_page(run_page):
    import re

    page = run_page(FakeBackend())
    text = " ".join(item.value for item in [*page.markdown, *page.caption])

    assert not re.search("[\u2600-\u27BF\U0001F300-\U0001FAFF\u25CF]", text)
