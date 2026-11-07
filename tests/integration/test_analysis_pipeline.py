"""Tests the whole workflow and its API with a scripted AI and a scripted sandbox."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.agent.pipeline import MAX_ATTEMPTS, AnalysisPipeline
from backend.api.analyses import create_ai, create_executor
from backend.config import Settings, get_settings
from backend.main import app
from backend.schemas.analysis import AnalysisRecord
from backend.services.containment import ATTACKS, run_containment_lab
from backend.services.executor_client import ExecutionResult, ExecutorClient, ExecutorError
from backend.services.inference import InferenceClient
from backend.services.profiling import load_dataframe, profile_dataframe
from backend.services.ai_router import AIRouter, Provider, ProviderSpec
from backend.services.usage import UsageTracker
from backend.storage.analyses import AnalysisStore

# Hand-checked: 2*10 + 3*5 = 35 total, and one blank quantity adds nothing.
CSV = "product,quantity,unit_price\nPen,2,10\nBook,3,5\nLamp,,7\n"
CODE = "emit_metric('Total revenue', 35)"
CHECK = {"op": "sum", "columns": ["quantity", "unit_price"]}
RESULT_OK = {
    "status": "ok",
    "metrics": [{"name": "Total revenue", "value": 35, "unit": None, "check": CHECK}],
    "tables": [],
    "charts": [],
}


def draft(code=CODE, columns=("quantity", "unit_price"), can_answer=True, reason=""):
    return {
        "can_answer": can_answer,
        "reason_if_not": reason,
        "plan": {
            "goal": "Find total revenue",
            "steps": [{"description": "Multiply and add", "columns": list(columns)}] if can_answer else [],
            "assumptions": [],
        },
        "code": code,
    }


def insights(text="Total revenue was 35."):
    return {
        "observations": [text],
        "assumptions": ["Blank quantities add nothing."],
        "recommendations": [{"action": "Promote pens.", "evidence": "Total revenue was 35."}],
    }


def ai_reply(answer) -> httpx.Response:
    content = answer if isinstance(answer, str) else json.dumps(answer)
    return httpx.Response(
        200,
        json={
            "model": "test",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


class ScriptedAI:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        answer = self.answers.pop(0)
        return answer if isinstance(answer, httpx.Response) else ai_reply(answer)

    def client(self) -> InferenceClient:
        return InferenceClient(
            SecretStr("fake"), "https://ai.test/v1", "m", transport=httpx.MockTransport(self), sleep=lambda _s: None
        )

    def text_sent(self) -> str:
        return json.dumps(self.requests)


def run_of(document=None, status="completed", stderr="") -> ExecutionResult:
    stdout = "" if document is None else (document if isinstance(document, str) else json.dumps(document))
    return ExecutionResult(status=status, exit_code=0, duration_seconds=0.5, stdout=stdout, stderr=stderr)


class ScriptedSandbox:
    def __init__(self, *runs):
        self.runs = list(runs)
        self.codes: list[str] = []

    def run(self, code, csv_text, timeout_seconds=None):
        self.codes.append(code)
        run = self.runs.pop(0)
        if isinstance(run, Exception):
            raise run
        return run

    def close(self):
        pass


def router(client: InferenceClient, data_dir, daily_requests=40, daily_tokens=10**9) -> AIRouter:
    """One scripted provider, with its own daily allowance."""
    spec = ProviderSpec("primary", "openai_compatible", SecretStr("fake"), "https://ai.test/v1", "m", "schema", None, daily_requests, daily_tokens)
    return AIRouter([Provider(spec, client)], UsageTracker(data_dir))


@pytest.fixture
def analyze(tmp_path):
    def _analyze(ai: ScriptedAI, sandbox: ScriptedSandbox, question="What is total revenue?", limit=40, parent=None):
        now = datetime.now(timezone.utc)
        record = AnalysisRecord(analysis_id=uuid4(), dataset_id=uuid4(), question=question, created_at=now, updated_at=now)
        store = AnalysisStore(tmp_path, 24)
        pipeline = AnalysisPipeline(router(ai.client(), tmp_path, limit), sandbox, store)
        pipeline.run(record, CSV, profile_dataframe(load_dataframe(CSV)), parent)
        return store.get(record.analysis_id)

    return _analyze


# --- Successful path ---


def test_full_success_produces_verified_results_and_explanation(analyze):
    ai, sandbox = ScriptedAI(draft(), insights()), ScriptedSandbox(run_of(RESULT_OK, stderr="a log line"))

    record = analyze(ai, sandbox)

    assert record.status == "completed"
    assert record.plan.goal == "Find total revenue"
    assert record.code == CODE
    assert record.verifications[0].status == "verified"
    assert record.insights.observations == ["Total revenue was 35."]
    assert record.attempts[0].logs == "a log line"
    assert record.ai_requests == 2


def test_only_a_summary_of_the_dataset_is_sent_to_the_ai(analyze):
    big_csv_marker = "Lamp"
    ai = ScriptedAI(draft(), insights())

    analyze(ai, ScriptedSandbox(run_of(RESULT_OK)))

    draft_message = ai.requests[0]["messages"][1]["content"]
    assert "Dataset details" in draft_message
    assert big_csv_marker in draft_message  # A common value in the summary.
    assert CSV not in ai.text_sent()


def test_code_goes_to_the_sandbox_and_nowhere_else(analyze):
    sandbox = ScriptedSandbox(run_of(RESULT_OK))

    analyze(ScriptedAI(draft(), insights()), sandbox)

    assert sandbox.codes == [CODE]


# --- Repairs and limits ---


def test_failed_code_is_repaired_with_the_error_message(analyze):
    failure = {"status": "error", "error_type": "KeyError", "message": "'qty'", "line": 3}
    ai = ScriptedAI(draft("bad()"), draft(), insights())
    sandbox = ScriptedSandbox(run_of(failure), run_of(RESULT_OK))

    record = analyze(ai, sandbox)

    assert record.status == "completed"
    assert len(record.attempts) == 2
    repair = ai.requests[1]["messages"][-1]["content"]
    assert "KeyError on line 3: 'qty'" in repair
    assert "bad()" in repair


@pytest.mark.parametrize("status", ["timeout", "out_of_memory", "output_too_large", "crashed"])
def test_sandbox_stops_are_repaired(analyze, status):
    record = analyze(ScriptedAI(draft(), draft(), insights()), ScriptedSandbox(run_of(status=status), run_of(RESULT_OK)))

    assert record.status == "completed"
    assert record.attempts[0].outcome == f"sandbox: {status}"


def test_gives_up_after_the_first_attempt_plus_two_repairs(analyze):
    failure = run_of({"status": "error", "error_type": "E", "message": "m", "line": None})
    ai = ScriptedAI(*[draft()] * 10)
    sandbox = ScriptedSandbox(*[failure] * 10)

    record = analyze(ai, sandbox)

    assert MAX_ATTEMPTS == 3
    assert record.status == "failed"
    assert len(sandbox.codes) == 3
    assert len(ai.requests) == 3
    assert record.result is None


def test_blocked_code_never_reaches_the_sandbox(analyze):
    ai = ScriptedAI(draft("import os\nos.system('rm -rf /')"), draft(), insights())
    sandbox = ScriptedSandbox(run_of(RESULT_OK))

    record = analyze(ai, sandbox)

    assert sandbox.codes == [CODE]
    assert record.attempts[0].outcome == "rejected before running"
    assert "os" in record.attempts[0].problem


def test_plans_with_invented_columns_are_rejected(analyze):
    ai = ScriptedAI(draft(columns=["profit_margin"]), draft(), insights())
    sandbox = ScriptedSandbox(run_of(RESULT_OK))

    record = analyze(ai, sandbox)

    assert sandbox.codes == [CODE]
    assert "profit_margin" in record.attempts[0].problem


def test_question_the_data_cannot_answer_is_refused_without_running_code(analyze):
    ai = ScriptedAI(draft("", can_answer=False, reason="There's no customer age column."))
    sandbox = ScriptedSandbox()

    record = analyze(ai, sandbox, question="What's the average customer age?")

    assert record.status == "refused"
    assert record.message == "There's no customer age column."
    assert sandbox.codes == []
    assert record.result is None


@pytest.mark.parametrize(
    "bad_answer",
    ["I think revenue is about 100.", {"can_answer": True}, {**draft(), "made_up": 1}],
    ids=["prose", "missing-fields", "extra-field"],
)
def test_unusable_ai_answers_use_up_an_attempt_and_recover(analyze, bad_answer):
    record = analyze(ScriptedAI(bad_answer, draft(), insights()), ScriptedSandbox(run_of(RESULT_OK)))

    assert record.status == "completed"
    assert record.attempts[0].outcome == "unusable AI answer"


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        {"status": "ok", "metrics": [], "tables": [], "charts": []},
        {"status": "ok", "metrics": [{"name": "x", "value": "99"}], "tables": [], "charts": []},
        {"status": "hacked"},
        [1, 2, 3],
    ],
    ids=["not-json", "empty", "fake-number", "unknown-status", "list"],
)
def test_malformed_sandbox_output_is_never_shown(analyze, stdout):
    ai = ScriptedAI(*[draft()] * 3)

    record = analyze(ai, ScriptedSandbox(*[run_of(stdout)] * 3))

    assert record.status == "failed"
    assert record.result is None
    assert record.insights is None


# --- Numbers can't be invented ---


def test_a_wrong_number_from_the_code_triggers_a_repair(analyze):
    wrong = {**RESULT_OK, "metrics": [{"name": "Total revenue", "value": 999, "check": CHECK}]}
    ai = ScriptedAI(draft(), draft(), insights())

    record = analyze(ai, ScriptedSandbox(run_of(wrong), run_of(RESULT_OK)))

    assert record.status == "completed"
    assert record.attempts[0].outcome == "recalculation disagreed"
    assert "recalculated 35" in ai.requests[1]["messages"][-1]["content"]
    assert record.result.metrics[0].value == 35


def test_numbers_that_keep_failing_recalculation_are_never_explained(analyze):
    wrong = {
        **RESULT_OK,
        "metrics": [
            {"name": "Total revenue", "value": 999, "check": CHECK},
            {"name": "Good", "value": 2, "check": {"op": "count", "columns": ["quantity"]}},
        ],
    }
    ai = ScriptedAI(*[draft()] * 3)

    record = analyze(ai, ScriptedSandbox(*[run_of(wrong)] * 3))

    assert record.status == "failed"
    assert record.insights is None
    assert len(ai.requests) == 3  # No explanation request was made.
    assert record.result is None  # Numbers that failed recalculation are discarded.
    assert record.verifications == []


def test_invented_number_in_the_explanation_gets_one_rewrite(analyze):
    ai = ScriptedAI(draft(), insights("Total revenue was 5000."), insights())

    record = analyze(ai, ScriptedSandbox(run_of(RESULT_OK)))

    assert record.insights.observations == ["Total revenue was 35."]
    assert record.removed_claims == []
    assert "5000" in ai.requests[2]["messages"][-1]["content"]


def test_claims_still_invented_after_the_rewrite_are_removed(analyze):
    ai = ScriptedAI(draft(), insights("Revenue was 5000."), insights("Revenue was 5000."))

    record = analyze(ai, ScriptedSandbox(run_of(RESULT_OK)))

    assert record.status == "completed"
    assert record.insights.observations == []
    assert record.removed_claims == ["Revenue was 5000."]


# --- Failures ---


def test_explanation_failure_keeps_the_verified_numbers(analyze):
    ai = ScriptedAI(draft(), *[httpx.Response(503, json={"error": {"message": "busy"}})] * 3)

    record = analyze(ai, ScriptedSandbox(run_of(RESULT_OK)))

    assert record.status == "completed"
    assert record.result.metrics[0].value == 35
    assert record.insights is None
    assert "verified" in record.message


def test_ai_outage_gives_a_clear_failure(analyze):
    ai = ScriptedAI(*[httpx.Response(401, json={"error": {"message": "bad key"}})])

    record = analyze(ai, ScriptedSandbox())

    assert record.status == "failed"
    assert record.message == "The AI service rejected our credentials."
    assert "bad key" not in record.message


def test_sandbox_outage_gives_a_clear_failure(analyze):
    record = analyze(ScriptedAI(draft()), ScriptedSandbox(ExecutorError("The secure sandbox isn't reachable right now.")))

    assert record.status == "failed"
    assert "sandbox" in record.message


def test_daily_limit_stops_ai_requests(analyze):
    ai = ScriptedAI(draft())

    record = analyze(ai, ScriptedSandbox(), limit=0)

    assert record.status == "failed"
    assert "allowance has been used up" in record.message
    assert ai.requests == []


def test_usage_tracker_counts_per_provider_and_survives_restarts(tmp_path):
    tracker = UsageTracker(tmp_path)

    tracker.record_request("primary")
    tracker.record_tokens("primary", 1500)
    UsageTracker(tmp_path).record_request("fallback")

    assert UsageTracker(tmp_path).used("primary") == (1, 1500)
    assert UsageTracker(tmp_path).used("fallback") == (1, 0)


def test_follow_up_questions_include_earlier_context(analyze):
    first = analyze(ScriptedAI(draft(), insights()), ScriptedSandbox(run_of(RESULT_OK)))
    ai = ScriptedAI(draft(), insights())

    analyze(ai, ScriptedSandbox(run_of(RESULT_OK)), question="Why?", parent=first)

    message = ai.requests[0]["messages"][1]["content"]
    assert "follow-up" in message
    assert "What is total revenue?" in message


# --- API ---


@pytest.fixture
def api(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, ai_api_key="test-key", visitor_analyses_per_hour=2, visitor_lab_runs_per_hour=1
    )
    scripted = {"ai": ScriptedAI(draft(), insights()), "sandbox": ScriptedSandbox(run_of(RESULT_OK))}
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[create_ai] = lambda: router(scripted["ai"].client(), tmp_path)
    app.dependency_overrides[create_executor] = lambda: scripted["sandbox"]
    yield TestClient(app), scripted
    app.dependency_overrides.clear()


CODE_HEADER = {"X-Visitor-Id": "203.0.113.7"}


def upload(client) -> str:
    response = client.post("/api/v1/datasets", files={"file": ("sales.csv", CSV.encode(), "text/csv")})
    return response.json()["dataset_id"]


def test_api_runs_an_analysis_and_serves_its_report(api):
    client, _ = api
    dataset_id = upload(client)

    started = client.post(
        "/api/v1/analyses", json={"dataset_id": dataset_id, "question": "Total revenue?"}, headers=CODE_HEADER
    )

    assert started.status_code == 202
    analysis_id = started.json()["analysis_id"]
    record = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert record["status"] == "completed"
    assert record["result"]["metrics"][0]["value"] == 35

    report = client.get(f"/api/v1/analyses/{analysis_id}/report")
    assert report.status_code == 200
    assert "Total revenue" in report.text
    assert "Verified ✓" in report.text
    assert "sum(quantity × unit_price)" in report.text
    assert "don't prove" in report.text
    assert CODE in report.text


def ask(client, dataset_id, visitor="203.0.113.7"):
    return client.post(
        "/api/v1/analyses", json={"dataset_id": dataset_id, "question": "Total?"}, headers={"X-Visitor-Id": visitor}
    )


def test_no_code_or_login_is_needed(api):
    client, _ = api

    response = client.post("/api/v1/analyses", json={"dataset_id": upload(client), "question": "Total?"})

    assert response.status_code == 202


def test_each_visitor_has_an_hourly_limit(api):
    client, scripted = api
    scripted["ai"].answers = [draft(), insights()] * 3
    scripted["sandbox"].runs = [run_of(RESULT_OK)] * 3
    dataset_id = upload(client)

    assert ask(client, dataset_id).status_code == 202
    assert ask(client, dataset_id).status_code == 202
    blocked = ask(client, dataset_id)

    assert blocked.status_code == 429
    assert "limit of 2 an hour" in blocked.json()["detail"]
    assert ask(client, dataset_id, visitor="198.51.100.9").status_code == 202  # Someone else isn't affected.
    assert len(scripted["ai"].requests) == 6  # The blocked request made no AI calls.


def test_visitor_addresses_are_not_stored(api, tmp_path):
    client, _ = api

    ask(client, upload(client), visitor="203.0.113.99")

    assert "203.0.113.99" not in (tmp_path / "visitor_usage.json").read_text()


def test_lab_has_its_own_hourly_limit(api):
    client, scripted = api
    scripted["sandbox"].runs = [run_of(status="timeout")] * 20

    assert client.post("/api/v1/containment-tests", headers=CODE_HEADER).status_code == 200
    blocked = client.post("/api/v1/containment-tests", headers=CODE_HEADER)

    assert blocked.status_code == 429


def test_analysis_is_refused_when_the_daily_allowance_is_nearly_gone(api, tmp_path):
    client, scripted = api
    UsageTracker(tmp_path).record_tokens("primary", 175_000)  # Less than one analysis's worth left.

    response = ask(client, upload(client))

    assert response.status_code == 429
    assert "resets at midnight UTC" in response.json()["detail"]
    assert scripted["ai"].requests == []
    assert client.get("/api/v1/usage").json()["analyses_left_today"] == 0


def test_usage_shows_analyses_left_today(api):
    client, _ = api

    usage = client.get("/api/v1/usage").json()

    assert usage["analyses_left_today"] == 15  # 180,000 tokens a day, about 12,000 per analysis.
    assert usage["resets_at"].endswith(("Z", "+00:00"))


def test_saved_example_is_served_without_any_ai(api, monkeypatch, tmp_path):
    import backend.storage.analyses as storage

    client, scripted = api
    example = analyze_example(tmp_path)
    path = tmp_path / "example.json"
    path.write_text(example.model_dump_json())
    monkeypatch.setattr(storage, "EXAMPLE_FILE", path)

    served = client.get("/api/v1/examples/sales")
    by_id = client.get(f"/api/v1/analyses/{storage.EXAMPLE_ID}")
    report = client.get(f"/api/v1/analyses/{storage.EXAMPLE_ID}/report")

    assert served.status_code == by_id.status_code == report.status_code == 200
    assert by_id.json()["result"]["metrics"][0]["value"] == 35
    assert "Verified ✓" in report.text
    assert scripted["ai"].requests == []


def test_missing_example_gives_404(api, monkeypatch, tmp_path):
    import backend.storage.analyses as storage

    client, _ = api
    monkeypatch.setattr(storage, "EXAMPLE_FILE", tmp_path / "missing.json")

    assert client.get("/api/v1/examples/sales").status_code == 404


def test_the_real_saved_example_is_valid_and_verified():
    import backend.storage.analyses as storage

    if not storage.EXAMPLE_FILE.exists():
        pytest.skip("The example is created from a real run on the server.")
    example = storage.load_example()

    assert example is not None
    assert example.analysis_id == storage.EXAMPLE_ID
    assert example.status == "completed"
    assert all(v.status != "mismatch" for v in example.verifications)


def analyze_example(tmp_path) -> AnalysisRecord:
    """A completed analysis, saved under the example's fixed ID."""
    import backend.storage.analyses as storage

    now = datetime.now(timezone.utc)
    record = AnalysisRecord(analysis_id=storage.EXAMPLE_ID, dataset_id=uuid4(), question="Total revenue?", created_at=now, updated_at=now)
    store = AnalysisStore(tmp_path / "example-run", 24)
    AnalysisPipeline(
        router(ScriptedAI(draft(), insights()).client(), tmp_path / "x"), ScriptedSandbox(run_of(RESULT_OK)), store
    ).run(record, CSV, profile_dataframe(load_dataframe(CSV)), None)
    return record


def test_unknown_dataset_is_rejected(api):
    client, _ = api

    response = client.post("/api/v1/analyses", json={"dataset_id": str(uuid4()), "question": "Total?"}, headers=CODE_HEADER)

    assert response.status_code == 404


def test_follow_up_cannot_use_another_datasets_analysis(api):
    client, _ = api
    first = client.post(
        "/api/v1/analyses", json={"dataset_id": upload(client), "question": "Total?"}, headers=CODE_HEADER
    ).json()

    response = client.post(
        "/api/v1/analyses",
        json={"dataset_id": upload(client), "question": "And then?", "parent_id": first["analysis_id"]},
        headers=CODE_HEADER,
    )

    assert response.status_code == 404


@pytest.mark.parametrize("question", ["", "ab", "x" * 1001], ids=["empty", "too-short", "too-long"])
def test_bad_questions_are_rejected(api, question):
    client, _ = api

    response = client.post("/api/v1/analyses", json={"dataset_id": upload(client), "question": question}, headers=CODE_HEADER)

    assert response.status_code == 422


def test_unknown_analysis_returns_404(api):
    client, _ = api

    assert client.get(f"/api/v1/analyses/{uuid4()}").status_code == 404
    assert client.get(f"/api/v1/analyses/{uuid4()}/report").status_code == 404


def test_missing_ai_key_or_token_gives_a_clear_error(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, ai_api_key=None, executor_token=None)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    dataset_id = upload(client)

    response = client.post("/api/v1/analyses", json={"dataset_id": dataset_id, "question": "Total?"})

    app.dependency_overrides.clear()
    assert response.status_code == 503


# --- Executor client ---


def executor_client(handler) -> ExecutorClient:
    return ExecutorClient("http://executor.test", SecretStr("token"), transport=httpx.MockTransport(handler))


def test_executor_client_sends_the_token_and_reads_the_result():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(
            200, json={"status": "completed", "exit_code": 0, "duration_seconds": 1, "stdout": "{}", "stderr": ""}
        )

    assert executor_client(handler).run("code", "a\n1\n").status == "completed"
    assert seen["auth"] == "Bearer token"


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (httpx.Response(429), "busy"),
        (httpx.Response(401), "refused"),
        (httpx.Response(500), "refused"),
        (httpx.Response(200, text="not json"), "unreadable"),
        (httpx.Response(200, json={"status": "weird"}), "unreadable"),
    ],
)
def test_executor_client_turns_failures_into_clear_errors(reply, expected):
    with pytest.raises(ExecutorError, match=expected):
        executor_client(lambda _request: reply).run("code", "a\n1\n")


def test_executor_client_handles_being_unreachable():
    def handler(_request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ExecutorError, match="reachable"):
        executor_client(handler).run("code", "a\n1\n")


# --- Containment Lab judging ---


class AllContained:
    def run(self, code, csv_text, timeout_seconds=None):
        return run_of(status="timeout")


def test_lab_never_reports_contained_without_evidence():
    report = run_containment_lab(AllContained())
    names = {result.name: result.contained for result in report.results}

    assert names["Infinite loop"] is True
    assert names["Delete everything"] is False
    assert names["Send data out"] is False
    assert names["Steal secrets"] is False
    assert report.all_contained is False
    assert len(report.results) == len(ATTACKS)


def test_lab_flags_a_successful_attack():
    class Leaky:
        def run(self, code, csv_text, timeout_seconds=None):
            return run_of(
                {
                    "status": "ok",
                    "metrics": [
                        {"name": "connections", "value": 3},
                        {"name": "interfaces", "value": 1},
                        {"name": "env_matches", "value": 1},
                        {"name": "paths_found", "value": 1},
                    ],
                }
            )

    results = {r.name: r for r in run_containment_lab(Leaky()).results}

    assert results["Send data out"].contained is False
    assert results["Steal secrets"].contained is False
