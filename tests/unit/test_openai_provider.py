"""Tests the OpenAI provider using the real SDK with fake HTTP replies. No network, no cost."""

import json
import logging
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from backend.config import Settings
from backend.schemas.analysis import AnalysisDraft
from backend.schemas.inference import ChatMessage
from backend.services.ai_router import create_ai_router, provider_specs
from backend.services.inference import (
    InferenceAuthError,
    InferenceNotConfiguredError,
    InferenceRateLimitError,
    InferenceRequestError,
    InferenceResponseError,
    InferenceTimeoutError,
    InferenceUnavailableError,
)
from backend.services.openai_provider import OpenAIQuotaError, OpenAIResponsesClient
from backend.services.usage import UsageTracker

FAKE_KEY = "sk-test-not-a-real-key-123"
MESSAGES = [
    ChatMessage(role="system", content="You are DataPilot."),
    ChatMessage(role="user", content="private question about revenue"),
]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    steps: list[str]


VALID = '{"status": "ok", "steps": ["add up revenue"]}'


def response_body(text=VALID, status="completed", refusal=None, incomplete_reason=None) -> dict:
    content = (
        [{"type": "refusal", "refusal": refusal}]
        if refusal
        else [{"type": "output_text", "text": text, "annotations": []}]
    )
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": "gpt-test",
        "output": [{"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": content}],
        "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
        "error": None,
        "instructions": None,
        "metadata": {},
        "parallel_tool_calls": True,
        "temperature": None,
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "text": {"format": {"type": "text"}},
    }


def api_error(status: int, code: str, error_type: str = "error") -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": "details", "type": error_type, "code": code}})


class FakeOpenAI:
    """Records what the SDK sends and plays back fake replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    def client(self, **options) -> OpenAIResponsesClient:
        return OpenAIResponsesClient(
            SecretStr(FAKE_KEY),
            "gpt-test",
            max_attempts=1,
            http_client=httpx.Client(transport=httpx.MockTransport(self)),
            **options,
        )

    def body(self, index=0) -> dict:
        return json.loads(self.requests[index].content)


def test_uses_the_responses_api_with_a_strict_json_schema():
    fake = FakeOpenAI(response_body())

    plan = fake.client(max_output_tokens=900).chat_structured(MESSAGES, Plan, temperature=0)

    assert plan == Plan(status="ok", steps=["add up revenue"])
    request = fake.requests[0]
    assert request.url.path.endswith("/responses")
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    body = fake.body()
    assert body["model"] == "gpt-test"
    assert body["instructions"] == "You are DataPilot."
    assert body["input"] == [{"role": "user", "content": "private question about revenue"}]
    assert body["max_output_tokens"] == 900
    text_format = body["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    assert text_format["schema"]["additionalProperties"] is False
    assert "temperature" not in body  # Current OpenAI models are reasoning models.


def test_reasoning_effort_is_sent_only_when_set():
    with_effort, without = FakeOpenAI(response_body()), FakeOpenAI(response_body())

    with_effort.client(reasoning_effort="low").chat_structured(MESSAGES, Plan)
    without.client().chat_structured(MESSAGES, Plan)

    assert with_effort.body()["reasoning"] == {"effort": "low"}
    assert "reasoning" not in without.body()


def test_token_use_is_recorded_for_the_daily_budget():
    client = FakeOpenAI(response_body()).client()

    client.chat_structured(MESSAGES, Plan)

    assert client.last_usage.total_tokens == 150
    assert client.last_usage.prompt_tokens == 120


def test_datapilots_real_analysis_schema_is_accepted():
    draft = {
        "can_answer": True,
        "reason_if_not": "",
        "plan": {"goal": "Total", "steps": [{"description": "Sum", "columns": ["a"]}], "assumptions": []},
        "code": "emit_metric('x', 1)",
    }
    fake = FakeOpenAI(response_body(json.dumps(draft)))

    result = fake.client().chat_structured(MESSAGES, AnalysisDraft)

    assert result.code == "emit_metric('x', 1)"
    assert fake.body()["text"]["format"]["strict"] is True


@pytest.mark.parametrize(
    "body",
    [
        response_body("not json at all"),
        response_body('{"status": "ok"}'),
        response_body('{"status": "ok", "steps": [], "total_revenue": 999}'),
        response_body(refusal="I can't help with that."),
        response_body(status="incomplete", incomplete_reason="max_output_tokens"),
    ],
    ids=["not-json", "missing-field", "invented-field", "refusal", "cut-off"],
)
def test_unusable_answers_are_rejected(body):
    with pytest.raises(InferenceResponseError):
        FakeOpenAI(body).client().chat_structured(MESSAGES, Plan)


@pytest.mark.parametrize(
    ("reply", "error_type"),
    [
        (api_error(401, "invalid_api_key"), InferenceAuthError),
        (api_error(429, "rate_limit_exceeded"), InferenceRateLimitError),
        (api_error(429, "insufficient_quota", "insufficient_quota"), OpenAIQuotaError),
        (api_error(429, "project_spend_limit_exceeded"), OpenAIQuotaError),
        (api_error(500, "server_error"), InferenceUnavailableError),
        (api_error(503, "overloaded"), InferenceUnavailableError),
        (api_error(400, "invalid_request"), InferenceRequestError),
        (api_error(404, "model_not_found"), InferenceRequestError),
        (httpx.ConnectError("refused"), InferenceUnavailableError),
        (httpx.ReadTimeout("slow"), InferenceTimeoutError),
    ],
    ids=["bad-key", "rate-limit", "out-of-credit", "spend-limit", "500", "503", "400", "unknown-model", "network", "timeout"],
)
def test_sdk_errors_become_datapilot_errors(reply, error_type):
    with pytest.raises(error_type):
        FakeOpenAI(reply).client().chat_structured(MESSAGES, Plan)


def test_billing_errors_count_as_quota_so_the_fallback_is_tried():
    assert issubclass(OpenAIQuotaError, InferenceRateLimitError)


def test_key_and_prompt_never_reach_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeOpenAI(api_error(401, "invalid_api_key"))

    with pytest.raises(InferenceAuthError) as caught:
        fake.client().chat_structured(MESSAGES, Plan)

    visible = f"{caught.value} {caught.value!r} {caplog.text}"
    assert FAKE_KEY not in visible
    assert "private question" not in visible


def test_key_is_hidden_when_the_client_is_printed():
    client = FakeOpenAI().client()

    assert FAKE_KEY not in repr(client)


# --- Choosing OpenAI in settings ---


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_openai_is_chosen_with_ai_provider_setting():
    specs = provider_specs(settings(ai_provider="openai", openai_api_key=FAKE_KEY, openai_model="gpt-6-luna"))

    assert [(s.kind, s.model) for s in specs] == [("openai", "gpt-6-luna")]
    assert specs[0].daily_requests == 150
    assert specs[0].daily_tokens == 300_000


def test_default_provider_is_unchanged():
    assert settings().ai_provider == "openai_compatible"


def test_openai_needs_a_model(tmp_path):
    with pytest.raises(InferenceNotConfiguredError, match="OPENAI_MODEL"):
        create_ai_router(settings(ai_provider="openai", openai_api_key=FAKE_KEY), UsageTracker(tmp_path))


def test_openai_needs_a_key(tmp_path):
    with pytest.raises(InferenceNotConfiguredError, match="OPENAI_API_KEY"):
        create_ai_router(settings(ai_provider="openai", openai_model="gpt-6-luna"), UsageTracker(tmp_path))


def test_openai_settings_come_from_the_documented_variable_names(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")

    configured = Settings(_env_file=None)

    assert configured.openai_model == "gpt-6-luna"
    assert configured.openai_api_key.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(configured)


def test_router_builds_an_openai_client_with_gemini_still_available_as_fallback(tmp_path):
    router = create_ai_router(
        settings(ai_provider="openai", openai_api_key=FAKE_KEY, openai_model="gpt-6-luna", ai_fallback_api_key="g"),
        UsageTracker(tmp_path),
    )

    assert isinstance(router.providers[0].client, OpenAIResponsesClient)
    assert router.providers[1].spec.model == "gemini-3.8-flash"
    router.close()


def test_summary_uses_its_own_faster_reasoning_effort():
    fake = FakeOpenAI(response_body(), response_body())
    client = fake.client(reasoning_effort="low", summary_reasoning_effort="none")

    client.chat_structured(MESSAGES, Plan)
    client.chat_structured(MESSAGES, Plan, purpose="summary")

    assert fake.body(0)["reasoning"] == {"effort": "low"}
    assert fake.body(1)["reasoning"] == {"effort": "none"}


def test_faster_efforts_are_the_defaults():
    configured = settings()

    assert configured.openai_reasoning_effort == "low"
    assert configured.openai_summary_reasoning_effort == "none"
