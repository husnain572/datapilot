"""Chooses which AI provider answers each request, within each provider's free allowance.

The primary provider (Groq by default, or OpenAI when AI_PROVIDER=openai) is tried first.
If it's rate-limited, out of its daily quota, or unavailable, and a
fallback (Google Gemini) is configured, the request goes there instead. A reply that arrives
but can't be used is not passed to the fallback: the analysis workflow handles it as a failed
attempt, the same way as before.

Keys are read only from backend settings and never leave the backend.
"""

import logging
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, SecretStr

from backend.config import Settings
from backend.schemas.inference import ChatMessage
from backend.services.inference import (
    InferenceAuthError,
    InferenceClient,
    InferenceError,
    InferenceNotConfiguredError,
    InferenceRateLimitError,
    InferenceTimeoutError,
    InferenceUnavailableError,
)
from backend.services.openai_provider import OpenAIResponsesClient
from backend.services.usage import REQUESTS_PER_ANALYSIS, DailyLimitReached, UsageTracker

logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)

# Failures where trying a different provider can help.
FALLBACK_ERRORS = (InferenceRateLimitError, InferenceUnavailableError, InferenceTimeoutError, InferenceAuthError)


@dataclass(frozen=True)
class ProviderSpec:
    """One provider's settings. The key stays a SecretStr so it's never printed."""

    name: str
    kind: str  # "openai_compatible" or "openai"
    api_key: SecretStr
    base_url: str
    model: str
    structured_mode: str
    reasoning_effort: str | None
    daily_requests: int
    daily_tokens: int


def _has_key(key: SecretStr | None) -> bool:
    return key is not None and bool(key.get_secret_value().strip())


def provider_specs(settings: Settings) -> list[ProviderSpec]:
    """The configured providers, in the order they're tried."""
    specs = []
    if settings.ai_provider == "openai":
        if _has_key(settings.openai_api_key):
            if not settings.openai_model:
                raise InferenceNotConfiguredError("OPENAI_MODEL is not set. Add it to the backend's .env file.")
            specs.append(
                ProviderSpec(
                    name="primary",
                    kind="openai",
                    api_key=SecretStr(settings.openai_api_key.get_secret_value().strip()),
                    base_url="https://api.openai.com/v1",
                    model=settings.openai_model,
                    structured_mode="schema",
                    reasoning_effort=settings.openai_reasoning_effort,
                    daily_requests=settings.openai_daily_request_limit,
                    daily_tokens=settings.openai_daily_token_limit,
                )
            )
    elif _has_key(settings.ai_api_key):
        specs.append(
            ProviderSpec(
                name="primary",
                kind="openai_compatible",
                api_key=SecretStr(settings.ai_api_key.get_secret_value().strip()),
                base_url=settings.ai_base_url,
                model=settings.ai_model,
                structured_mode=settings.ai_structured_mode,
                reasoning_effort=settings.ai_reasoning_effort,
                daily_requests=settings.ai_daily_request_limit,
                daily_tokens=settings.ai_daily_token_limit,
            )
        )
    if _has_key(settings.ai_fallback_api_key):
        specs.append(
            ProviderSpec(
                name="fallback",
                kind="openai_compatible",
                api_key=SecretStr(settings.ai_fallback_api_key.get_secret_value().strip()),
                base_url=settings.ai_fallback_base_url,
                model=settings.ai_fallback_model,
                structured_mode=settings.ai_fallback_structured_mode,
                reasoning_effort=settings.ai_fallback_reasoning_effort,
                daily_requests=settings.ai_fallback_daily_request_limit,
                daily_tokens=settings.ai_fallback_daily_token_limit,
            )
        )
    return specs


def analyses_left_today(settings: Settings, usage: UsageTracker) -> int:
    """An estimate for the page, from each provider's remaining requests and tokens."""
    total = 0
    for spec in provider_specs(settings):
        requests, tokens = usage.used(spec.name)
        by_requests = max(spec.daily_requests - requests, 0) // REQUESTS_PER_ANALYSIS
        by_tokens = max(spec.daily_tokens - tokens, 0) // settings.ai_tokens_per_analysis_estimate
        total += min(by_requests, by_tokens)
    return total


@dataclass
class Provider:
    spec: ProviderSpec
    # Any client with chat_structured(), last_usage, and close().
    client: InferenceClient | OpenAIResponsesClient

    def has_allowance(self, usage: UsageTracker) -> bool:
        requests, tokens = usage.used(self.spec.name)
        return requests < self.spec.daily_requests and tokens < self.spec.daily_tokens


class AIRouter:
    def __init__(self, providers: list[Provider], usage: UsageTracker) -> None:
        if not providers:
            raise InferenceNotConfiguredError("No AI provider is configured.")
        self.providers = providers
        self.usage = usage

    def __repr__(self) -> str:
        return f"AIRouter(models={[p.spec.model for p in self.providers]!r})"

    def close(self) -> None:
        for provider in self.providers:
            provider.client.close()

    def chat_structured(
        self, messages: list[ChatMessage], schema: type[SchemaT], *, temperature: float | None = None
    ) -> SchemaT:
        """Ask the first provider with allowance left, moving on only when another could help."""
        last_error: InferenceError | None = None
        for provider in self.providers:
            if not provider.has_allowance(self.usage):
                logger.info("Skipping AI provider %s: its daily allowance is used up.", provider.spec.name)
                continue
            self.usage.record_request(provider.spec.name)
            try:
                result = provider.client.chat_structured(messages, schema, temperature=temperature)
            except FALLBACK_ERRORS as error:
                logger.warning("AI provider %s failed (%s), trying the next one if any.", provider.spec.name, error)
                last_error = error
                continue
            finally:
                # Count tokens even when the reply turns out to be unusable.
                self.usage.record_tokens(provider.spec.name, provider.client.last_usage.total_tokens)
                provider.client.last_usage = type(provider.client.last_usage)()
            return result

        if last_error is not None:
            raise last_error
        raise DailyLimitReached("Today's free AI allowance has been used up. It resets at midnight UTC.")


def create_client(spec: ProviderSpec, settings: Settings) -> InferenceClient | OpenAIResponsesClient:
    """Build the right client for a provider's kind."""
    if spec.kind == "openai":
        return OpenAIResponsesClient(
            spec.api_key,
            spec.model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_attempts=settings.ai_max_attempts,
            max_output_tokens=settings.openai_max_output_tokens,
            reasoning_effort=spec.reasoning_effort,
        )
    return InferenceClient(
        spec.api_key,
        spec.base_url,
        spec.model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_attempts=settings.ai_max_attempts,
        max_completion_tokens=settings.ai_max_completion_tokens,
        structured_mode=spec.structured_mode,
        reasoning_effort=spec.reasoning_effort,
    )


def create_ai_router(settings: Settings, usage: UsageTracker) -> AIRouter:
    """Build clients for every configured provider."""
    providers = [Provider(spec, create_client(spec, settings)) for spec in provider_specs(settings)]
    if not providers:
        key = "OPENAI_API_KEY" if settings.ai_provider == "openai" else "AI_API_KEY"
        raise InferenceNotConfiguredError(f"{key} is not set. Add it to the backend's .env file.")
    return AIRouter(providers, usage)
