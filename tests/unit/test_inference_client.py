"""Tests the AI client using fake replies, so they use no network and cost nothing.

The fake replies follow the OpenAI-compatible format that providers document.
"""

import gzip
import json
import logging
import time
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from backend.config import Settings
from backend.schemas.inference import ChatMessage
from backend.services.inference import (
    InferenceAuthError,
    InferenceClient,
    InferenceNotConfiguredError,
    InferenceRateLimitError,
    InferenceRequestError,
    InferenceResponseError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    create_inference_client,
)

FAKE_KEY = "fake-key-for-tests-only"
BASE_URL = "https://api.example.test/v1"
HELLO = [ChatMessage(role="user", content="private question about revenue")]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    steps: list[str]


def completion(content="Hello", finish_reason="stop", reasoning=None) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "reasoning": reasoning},
                "logprobs": None,
                "finish_reason": finish_reason,
                "native_finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def error_reply(status: int, message="Something failed", headers=None) -> httpx.Response:
    body = {"error": {"code": status, "message": message, "type": "error"}}
    return httpx.Response(status, json=body, headers=headers)


class FakeVultr:
    """Plays back a list of replies and records what the client sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def client(self, **options) -> InferenceClient:
        return InferenceClient(
            SecretStr(FAKE_KEY),
            BASE_URL,
            "test-model",
            transport=httpx.MockTransport(self),
            sleep=self.sleeps.append,
            **options,
        )

    def body(self, index=0) -> dict:
        return json.loads(self.requests[index].content)


def ok(content="Hello", **kwargs) -> httpx.Response:
    return httpx.Response(200, json=completion(content, **kwargs))


# --- Sending a request ---


def test_sends_the_documented_request_format():
    fake = FakeVultr(ok())

    fake.client(max_completion_tokens=500).chat(HELLO, temperature=0)

    request = fake.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert fake.body() == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "private question about revenue"}],
        "max_completion_tokens": 500,
        "temperature": 0,
    }


def test_leaves_out_temperature_and_format_unless_requested():
    fake = FakeVultr(ok())

    fake.client().chat(HELLO)

    assert "temperature" not in fake.body()
    assert "response_format" not in fake.body()


def test_redirects_are_not_followed_so_the_key_stays_put():
    fake = FakeVultr(httpx.Response(302, headers={"Location": "https://evil.example/steal"}))

    with pytest.raises(InferenceRequestError):
        fake.client().chat(HELLO)

    assert len(fake.requests) == 1


# --- Reading a reply ---


def test_returns_the_answer_and_token_usage():
    result = FakeVultr(ok("Total revenue")).client().chat(HELLO)

    assert result.content == "Total revenue"
    assert result.model == "test-model"
    assert result.finish_reason == "stop"
    assert result.usage.total_tokens == 15
    assert result.latency_seconds >= 0


def test_thinking_text_is_never_used_as_the_answer():
    result = FakeVultr(ok("The answer", reasoning="secret thoughts")).client().chat(HELLO)

    assert result.content == "The answer"


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_reply_with_only_thinking_or_no_text_is_rejected(content):
    fake = FakeVultr(ok(content, reasoning="I used all my tokens thinking"))

    with pytest.raises(InferenceResponseError, match="no answer text"):
        fake.client().chat(HELLO)


@pytest.mark.parametrize("reason", ["length", "content_filter", "error"])
def test_answers_that_stopped_early_are_rejected(reason):
    with pytest.raises(InferenceResponseError, match=reason):
        FakeVultr(ok("partial answ", finish_reason=reason)).client().chat(HELLO)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, json={"choices": "wrong type"}),
        httpx.Response(200, json=[1, 2, 3]),
    ],
    ids=["html", "missing-choices", "wrong-type", "list"],
)
def test_malformed_replies_are_rejected(reply):
    with pytest.raises(InferenceResponseError, match="expected format"):
        FakeVultr(reply).client().chat(HELLO)


def test_empty_choices_are_rejected():
    reply = httpx.Response(200, json={"choices": []})

    with pytest.raises(InferenceResponseError, match="no choices"):
        FakeVultr(reply).client().chat(HELLO)


# --- Structured output ---

VALID_PLAN = '{"status": "ok", "steps": ["add up revenue"]}'


def test_structured_request_uses_the_documented_json_schema_format():
    fake = FakeVultr(ok(VALID_PLAN))

    fake.client().chat_structured(HELLO, Plan)

    response_format = fake.body()["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "Plan"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["additionalProperties"] is False
    assert set(response_format["json_schema"]["schema"]["required"]) == {"status", "steps"}


@pytest.mark.parametrize(
    "content",
    [VALID_PLAN, f"```json\n{VALID_PLAN}\n```", f"```\n{VALID_PLAN}\n```", f"  {VALID_PLAN}\n"],
    ids=["plain", "json-fence", "bare-fence", "whitespace"],
)
def test_structured_answer_is_validated_into_the_model(content):
    plan = FakeVultr(ok(content)).client().chat_structured(HELLO, Plan)

    assert plan == Plan(status="ok", steps=["add up revenue"])


@pytest.mark.parametrize(
    "content",
    [
        "Sure! Here is your plan.",
        '{"status": "ok"}',
        '{"status": "bad", "steps": []}',
        '{"status": "ok", "steps": [], "total_revenue": 999}',
        '{"status": "ok", "steps": [',
    ],
    ids=["not-json", "missing-field", "invalid-value", "invented-field", "cut-off"],
)
def test_structured_answer_with_the_wrong_shape_is_rejected(content):
    with pytest.raises(InferenceResponseError, match="didn't match Plan") as caught:
        FakeVultr(ok(content)).client().chat_structured(HELLO, Plan)

    assert content not in str(caught.value)


def test_prompt_mode_describes_the_format_instead_of_sending_response_format():
    fake = FakeVultr(ok(VALID_PLAN))

    plan = fake.client(structured_mode="prompt").chat_structured(HELLO, Plan)

    assert plan == Plan(status="ok", steps=["add up revenue"])
    assert "response_format" not in fake.body()
    last = fake.body()["messages"][-1]
    assert last["role"] == "system"
    assert '"steps"' in last["content"]


def test_prompt_mode_still_rejects_the_wrong_shape():
    with pytest.raises(InferenceResponseError, match="didn't match Plan"):
        FakeVultr(ok('{"status": "ok", "total_revenue": 999}')).client(structured_mode="prompt").chat_structured(HELLO, Plan)


def test_json_is_found_inside_a_sentence():
    plan = FakeVultr(ok(f"Here is the plan: {VALID_PLAN} Hope that helps!")).client().chat_structured(HELLO, Plan)

    assert plan.status == "ok"


# --- Errors that must not be retried ---


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, InferenceRequestError),
        (401, InferenceAuthError),
        (404, InferenceRequestError),
        (413, InferenceRequestError),
    ],
)
def test_request_errors_fail_at_once_without_retrying(status, error_type):
    fake = FakeVultr(error_reply(status, "Unknown model"))

    with pytest.raises(error_type, match=f"HTTP {status}: Unknown model"):
        fake.client().chat(HELLO)

    assert len(fake.requests) == 1
    assert fake.sleeps == []


# --- Errors that are retried ---


def test_429_waits_for_retry_after_then_succeeds():
    fake = FakeVultr(error_reply(429, headers={"Retry-After": "7"}), ok())

    assert fake.client().chat(HELLO).content == "Hello"
    assert fake.sleeps == [7]


def test_429_without_a_usable_retry_after_uses_backoff():
    fake = FakeVultr(error_reply(429, headers={"Retry-After": "soon"}), error_reply(429), ok())

    fake.client().chat(HELLO)

    assert fake.sleeps == [1, 2]


def test_429_with_a_very_long_wait_fails_instead_of_hanging():
    fake = FakeVultr(error_reply(429, headers={"Retry-After": "3600"}), ok())

    with pytest.raises(InferenceRateLimitError):
        fake.client(max_retry_wait_seconds=30).chat(HELLO)

    assert fake.sleeps == []


@pytest.mark.parametrize("status", [502, 503, 504])
def test_temporary_failures_retry_with_doubling_waits(status):
    fake = FakeVultr(error_reply(status), error_reply(status), ok())

    assert fake.client(max_attempts=3).chat(HELLO).content == "Hello"
    assert fake.sleeps == [1, 2]


def test_gives_up_after_the_attempt_limit():
    fake = FakeVultr(error_reply(503), error_reply(503), error_reply(503), ok())

    with pytest.raises(InferenceUnavailableError):
        fake.client(max_attempts=3).chat(HELLO)

    assert len(fake.requests) == 3


def test_500_is_retried_only_once():
    fake = FakeVultr(error_reply(500), error_reply(500), ok())

    with pytest.raises(InferenceUnavailableError):
        fake.client(max_attempts=3).chat(HELLO)

    assert len(fake.requests) == 2


def test_error_page_that_is_not_json_is_handled():
    fake = FakeVultr(httpx.Response(502, text="<html>Bad Gateway</html>"), ok())

    assert fake.client().chat(HELLO).content == "Hello"


def test_network_failure_is_retried():
    fake = FakeVultr(httpx.ConnectError("no route"), ok())

    assert fake.client().chat(HELLO).content == "Hello"
    assert fake.sleeps == [1]


def test_network_failure_gives_up_after_the_attempt_limit():
    fake = FakeVultr(*[httpx.ConnectTimeout("slow") for _ in range(3)])

    with pytest.raises(InferenceUnavailableError, match="ConnectTimeout"):
        fake.client(max_attempts=3).chat(HELLO)


def test_timeout_waiting_for_an_answer_is_not_retried():
    fake = FakeVultr(httpx.ReadTimeout("too slow"), ok())

    with pytest.raises(InferenceTimeoutError):
        fake.client().chat(HELLO)

    assert len(fake.requests) == 1


def test_endless_keep_alive_bytes_cannot_hang_a_request():
    """A real bug: a provider sent 200 OK, then only keep-alive bytes for over 10 minutes."""

    class EndlessStream(httpx.SyncByteStream):
        def __iter__(self):
            while True:
                time.sleep(0.01)
                yield b" "

    fake = FakeVultr(httpx.Response(200, stream=EndlessStream()))
    started = time.monotonic()

    with pytest.raises(InferenceTimeoutError, match="No complete answer"):
        fake.client(timeout_seconds=0.2).chat(HELLO)

    assert time.monotonic() - started < 5
    assert len(fake.requests) == 1


def test_compressed_replies_are_read_correctly():
    """A real bug: real providers compress replies, which the plain fake replies didn't show."""
    compressed = gzip.compress(json.dumps(completion("Hello")).encode())
    reply = httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=compressed)

    assert FakeVultr(reply).client().chat(HELLO).content == "Hello"


def test_endless_reply_data_is_cut_off():
    class HugeStream(httpx.SyncByteStream):
        def __iter__(self):
            while True:
                yield b"x" * 1024 * 1024

    with pytest.raises(InferenceResponseError, match="too large"):
        FakeVultr(httpx.Response(200, stream=HugeStream())).client().chat(HELLO)


def test_reasoning_effort_is_sent_only_when_set():
    with_effort, without = FakeVultr(ok()), FakeVultr(ok())

    with_effort.client(reasoning_effort="low").chat(HELLO)
    without.client().chat(HELLO)

    assert with_effort.body()["reasoning_effort"] == "low"
    assert "reasoning_effort" not in without.body()


def test_token_use_of_the_last_call_is_remembered():
    client = FakeVultr(ok()).client()

    client.chat(HELLO)

    assert client.last_usage.total_tokens == 15


# --- Keeping secrets and user data private ---


def test_key_and_prompt_never_reach_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG, logger="backend.services.inference")
    fake = FakeVultr(
        error_reply(503, f"Bad key {FAKE_KEY}"),
        error_reply(401, f"Invalid key: {FAKE_KEY}"),
    )

    with pytest.raises(InferenceAuthError) as caught:
        fake.client().chat(HELLO)

    visible = f"{caught.value} {caught.value!r} {caught.value.user_message} {caplog.text}"
    assert "[hidden]" in visible
    assert FAKE_KEY not in visible
    assert "private question" not in visible


def test_successful_calls_log_no_prompt_or_answer(caplog):
    caplog.set_level(logging.DEBUG, logger="backend.services.inference")

    FakeVultr(ok("secret answer")).client().chat(HELLO)

    assert "Inference call finished" in caplog.text
    assert "private question" not in caplog.text
    assert "secret answer" not in caplog.text


def test_key_is_hidden_when_the_client_is_printed():
    client = FakeVultr().client()

    assert FAKE_KEY not in repr(client)
    assert FAKE_KEY not in str(client)


def test_long_error_messages_are_capped():
    with pytest.raises(InferenceRequestError) as caught:
        FakeVultr(error_reply(400, "x" * 5000)).client().chat(HELLO)

    assert len(str(caught.value)) < 400


def test_every_error_has_a_message_safe_to_show_users():
    errors = [
        InferenceAuthError("HTTP 401: internal detail"),
        InferenceRateLimitError("HTTP 429: internal detail"),
        InferenceResponseError("internal detail"),
    ]

    assert all("internal detail" not in error.user_message for error in errors)


# --- Catalog ---


def test_lists_model_ids_from_the_catalog():
    fake = FakeVultr(httpx.Response(200, json={"data": [{"id": "glm-5.3", "name": "GLM"}, {"id": "other"}]}))

    assert fake.client().list_model_ids() == ["glm-5.3", "other"]
    assert str(fake.requests[0].url) == f"{BASE_URL}/models"


# --- Settings ---


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_gives_a_clear_error(key, monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)

    with pytest.raises(InferenceNotConfiguredError, match="AI_API_KEY"):
        create_inference_client(settings(ai_api_key=key))


def test_client_is_built_from_settings():
    client = create_inference_client(
        settings(ai_api_key=FAKE_KEY, ai_model="chosen-model")
    )

    assert client.model == "chosen-model"
    client.close()


def test_key_copied_with_stray_spaces_still_works():
    client = create_inference_client(settings(ai_api_key=f"  {FAKE_KEY}\n"))

    assert client._api_key.get_secret_value() == FAKE_KEY
    client.close()


@pytest.mark.parametrize("url", ["http://api.vultrinference.com/v1", "api.vultrinference.com/v1", ""])
def test_unencrypted_base_url_is_refused(url):
    with pytest.raises(ValidationError, match="https"):
        settings(ai_base_url=url)


@pytest.mark.parametrize(
    "values",
    [
        {"ai_timeout_seconds": 0},
        {"ai_max_attempts": 0},
        {"ai_max_attempts": 99},
        {"ai_max_completion_tokens": 0},
    ],
)
def test_unsafe_limits_are_refused(values):
    with pytest.raises(ValidationError):
        settings(**values)
