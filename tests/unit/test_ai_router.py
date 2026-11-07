"""Tests choosing between Groq and the optional Gemini fallback, within daily allowances."""

import json
from datetime import datetime, timezone
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from backend.config import Settings
from backend.schemas.inference import ChatMessage
from backend.services.ai_router import (
    AIRouter,
    Provider,
    ProviderSpec,
    analyses_left_today,
    create_ai_router,
    provider_specs,
)
from backend.services.inference import (
    InferenceClient,
    InferenceNotConfiguredError,
    InferenceRateLimitError,
    InferenceResponseError,
)
from backend.services.usage import DailyLimitReached, UsageTracker

HELLO = [ChatMessage(role="user", content="hi")]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]


def reply(content='{"status": "ok"}', tokens=100) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": tokens // 2, "completion_tokens": tokens // 2, "total_tokens": tokens},
        },
    )


def error(status, headers=None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": "nope"}}, headers=headers)


class FakeProvider:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def client(self) -> InferenceClient:
        return InferenceClient(
            SecretStr("secret-key-123"), "https://ai.test/v1", "m",
            transport=httpx.MockTransport(self), sleep=lambda _s: None, max_attempts=1,
        )


def spec(name, requests=100, tokens=100_000) -> ProviderSpec:
    return ProviderSpec(name, "openai_compatible", SecretStr("k"), "https://ai.test/v1", "m", "schema", None, requests, tokens)


def make_router(tmp_path, primary: FakeProvider, fallback: FakeProvider | None = None, **limits):
    providers = [Provider(spec("primary", **limits), primary.client())]
    if fallback:
        providers.append(Provider(spec("fallback"), fallback.client()))
    return AIRouter(providers, UsageTracker(tmp_path))


def test_primary_answers_and_usage_is_recorded(tmp_path):
    primary = FakeProvider(reply(tokens=640))
    router = make_router(tmp_path, primary)

    assert router.chat_structured(HELLO, Answer).status == "ok"
    assert UsageTracker(tmp_path).used("primary") == (1, 640)


@pytest.mark.parametrize(
    "failure",
    [
        error(429, {"Retry-After": "86400"}),  # Daily quota used up.
        error(503),
        error(401),
        httpx.ReadTimeout("slow"),
    ],
    ids=["quota-exhausted", "unavailable", "bad-key", "timeout"],
)
def test_falls_back_when_another_provider_could_help(tmp_path, failure):
    primary, fallback = FakeProvider(failure), FakeProvider(reply())

    answer = make_router(tmp_path, primary, fallback).chat_structured(HELLO, Answer)

    assert answer.status == "ok"
    assert fallback.calls == 1
    assert UsageTracker(tmp_path).used("fallback")[0] == 1


def test_unusable_reply_is_not_sent_to_the_fallback(tmp_path):
    primary, fallback = FakeProvider(reply("not json")), FakeProvider(reply())

    with pytest.raises(InferenceResponseError):
        make_router(tmp_path, primary, fallback).chat_structured(HELLO, Answer)

    assert fallback.calls == 0  # The workflow's own repair loop handles this.
    assert UsageTracker(tmp_path).used("primary")[1] == 100  # Its tokens still count.


def test_without_a_fallback_the_original_error_is_raised(tmp_path):
    with pytest.raises(InferenceRateLimitError):
        make_router(tmp_path, FakeProvider(error(429, {"Retry-After": "86400"}))).chat_structured(HELLO, Answer)


def test_provider_out_of_tokens_is_skipped_without_a_request(tmp_path):
    primary, fallback = FakeProvider(), FakeProvider(reply())
    UsageTracker(tmp_path).record_tokens("primary", 5_000)

    make_router(tmp_path, primary, fallback, tokens=5_000).chat_structured(HELLO, Answer)

    assert primary.calls == 0
    assert fallback.calls == 1


def test_all_allowances_used_gives_a_clear_message(tmp_path):
    primary = FakeProvider()
    UsageTracker(tmp_path).record_request("primary")

    with pytest.raises(DailyLimitReached, match="resets at midnight UTC"):
        make_router(tmp_path, primary, requests=1).chat_structured(HELLO, Answer)

    assert primary.calls == 0


def test_router_needs_at_least_one_provider(tmp_path):
    with pytest.raises(InferenceNotConfiguredError):
        AIRouter([], UsageTracker(tmp_path))


def test_router_never_prints_keys(tmp_path):
    router = make_router(tmp_path, FakeProvider())

    assert "secret-key-123" not in repr(router)
    assert "secret-key-123" not in repr(router.providers[0].spec)


# --- Settings ---


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_groq_is_the_default_primary_provider():
    configured = settings(ai_api_key="g")

    assert configured.ai_base_url == "https://api.groq.com/openai/v1"
    assert configured.ai_model == "openai/gpt-oss-120b"
    assert [s.name for s in provider_specs(configured)] == ["primary"]


def test_gemini_fallback_is_used_only_when_its_key_is_set():
    with_fallback = provider_specs(settings(ai_api_key="g", ai_fallback_api_key="  gem  "))

    assert [s.name for s in with_fallback] == ["primary", "fallback"]
    assert with_fallback[1].base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert with_fallback[1].model == "gemini-3.8-flash"
    assert with_fallback[1].api_key.get_secret_value() == "gem"
    assert [s.name for s in provider_specs(settings(ai_api_key="g", ai_fallback_api_key=""))] == ["primary"]


def test_no_key_means_a_clear_setup_error(tmp_path):
    with pytest.raises(InferenceNotConfiguredError, match="AI_API_KEY"):
        create_ai_router(settings(), UsageTracker(tmp_path))


def test_fallback_address_must_be_encrypted():
    with pytest.raises(ValidationError, match="https"):
        settings(ai_fallback_base_url="http://example.com/v1")


def test_blank_reasoning_effort_means_none():
    assert settings(ai_reasoning_effort="").ai_reasoning_effort is None


def test_analyses_left_uses_the_tighter_of_requests_and_tokens(tmp_path):
    configured = settings(ai_api_key="g", ai_daily_request_limit=30, ai_daily_token_limit=1_000_000)
    usage = UsageTracker(tmp_path)

    assert analyses_left_today(configured, usage) == 10  # 30 requests / 3 per analysis.
    usage.record_tokens("primary", 1_000_000 - 24_000)
    assert analyses_left_today(configured, usage) == 2  # 24,000 tokens / 12,000 per analysis.


def test_analyses_left_adds_the_fallback_allowance(tmp_path):
    configured = settings(ai_api_key="g", ai_fallback_api_key="f")

    assert analyses_left_today(configured, UsageTracker(tmp_path)) == 15 + 41


def test_old_usage_file_format_is_handled(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    (tmp_path / "ai_usage.json").write_text(json.dumps({today: 7}))

    usage = UsageTracker(tmp_path)

    assert usage.used("primary") == (0, 0)
    usage.record_request("primary")
    assert usage.used("primary") == (1, 0)
