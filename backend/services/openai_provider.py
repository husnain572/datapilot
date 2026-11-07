"""AI provider using OpenAI's official Python SDK and its Responses API.

Docs: https://developers.openai.com/api/docs/guides/structured-outputs

It offers the same `chat_structured` method as the other providers, so the router and the
analysis workflow don't change. Replies are parsed into DataPilot's Pydantic models by the SDK,
then checked again by the same models, exactly as with other providers.

The OpenAI API is paid. Daily request and token limits in settings cap what it can spend.
The key is used only inside this backend process, and never logged.
"""

import logging
from typing import TypeVar

import httpx
import openai
from pydantic import BaseModel, SecretStr, ValidationError

from backend.schemas.inference import ChatMessage, TokenUsage
from backend.services.inference import (
    InferenceAuthError,
    InferenceError,
    InferenceRateLimitError,
    InferenceRequestError,
    InferenceResponseError,
    InferenceTimeoutError,
    InferenceUnavailableError,
)

logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)

# OpenAI's codes for "out of money or quota". Retrying these won't help.
BILLING_CODES = {
    "insufficient_quota",
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
}


class OpenAIQuotaError(InferenceRateLimitError):
    user_message = "The AI service's credit or spending limit has been reached."


def _error_code(error: openai.APIStatusError) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return str(body.get("code") or body.get("type") or "")
    return ""


def translate_error(error: Exception) -> InferenceError:
    """Turn SDK errors into DataPilot's error types, so fallback and messages work the same.

    Only the error class and code are kept, never OpenAI's message text, which can quote input.
    """
    if isinstance(error, openai.AuthenticationError):
        return InferenceAuthError("OpenAI rejected the API key.")
    if isinstance(error, openai.RateLimitError):
        code = _error_code(error)
        if code in BILLING_CODES:
            return OpenAIQuotaError(f"OpenAI quota or billing limit reached ({code}).")
        return InferenceRateLimitError(f"OpenAI rate limit reached ({code or 'rate_limit'}).")
    if isinstance(error, openai.APITimeoutError):
        return InferenceTimeoutError("OpenAI took too long to answer.")
    if isinstance(error, openai.APIConnectionError):
        return InferenceUnavailableError("Couldn't connect to OpenAI.")
    if isinstance(error, openai.InternalServerError):
        return InferenceUnavailableError(f"OpenAI server error (HTTP {error.status_code}).")
    if isinstance(error, openai.APIStatusError):
        if error.status_code >= 500:
            return InferenceUnavailableError(f"OpenAI server error (HTTP {error.status_code}).")
        return InferenceRequestError(f"OpenAI refused the request (HTTP {error.status_code}, {_error_code(error)}).")
    if isinstance(error, (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError)):
        return InferenceResponseError(f"OpenAI's answer stopped early: {type(error).__name__}.")
    return InferenceUnavailableError(f"Unexpected OpenAI error: {type(error).__name__}.")


class OpenAIResponsesClient:
    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        *,
        timeout_seconds: float = 90,
        max_attempts: int = 3,
        max_output_tokens: int = 8192,
        reasoning_effort: str | None = None,
        summary_reasoning_effort: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.last_usage = TokenUsage()
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._summary_reasoning_effort = summary_reasoning_effort
        # The SDK retries rate limits and server errors itself, with growing waits.
        self._client = openai.OpenAI(
            api_key=api_key.get_secret_value(),
            timeout=timeout_seconds,
            max_retries=max_attempts - 1,
            http_client=http_client,
        )

    def __repr__(self) -> str:
        return f"OpenAIResponsesClient(model={self.model!r})"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenAIResponsesClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def chat_structured(
        self,
        messages: list[ChatMessage],
        schema: type[SchemaT],
        *,
        temperature: float | None = None,  # Not sent: OpenAI's current models are reasoning models.
        max_completion_tokens: int | None = None,
        purpose: str = "analysis",
    ) -> SchemaT:
        """Ask for an answer matching a Pydantic model, and check that it really does.

        `purpose` is "analysis" for planning and code, or "summary" for explaining verified results.
        """
        request: dict = {
            "model": self.model,
            "instructions": "\n\n".join(m.content for m in messages if m.role == "system") or None,
            "input": [{"role": m.role, "content": m.content} for m in messages if m.role != "system"],
            "text_format": schema,
            "max_output_tokens": max_completion_tokens or self._max_output_tokens,
        }
        effort = self._summary_reasoning_effort if purpose == "summary" else self._reasoning_effort
        if effort:
            request["reasoning"] = {"effort": effort}

        try:
            response = self._client.responses.parse(**request)
        except openai.OpenAIError as error:
            translated = translate_error(error)
            logger.warning("OpenAI request failed: %s", translated)
            raise translated from None
        except ValidationError:
            # The SDK couldn't fit the reply to the schema.
            raise InferenceResponseError(f"The answer didn't match {schema.__name__}.") from None

        if response.usage is not None:
            self.last_usage = TokenUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            )
        logger.info(
            "OpenAI call finished: model=%s status=%s tokens=%d", self.model, response.status, self.last_usage.total_tokens
        )

        if response.status != "completed":
            reason = getattr(response.incomplete_details, "reason", None) or response.status
            raise InferenceResponseError(f"OpenAI's answer stopped early: {reason}.")
        for item in response.output:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    raise InferenceResponseError("The model declined to answer.")

        parsed = response.output_parsed
        if parsed is None:
            raise InferenceResponseError("OpenAI's reply had no answer.")
        try:
            # Check again with DataPilot's own model, so every provider is held to the same rules.
            return schema.model_validate(parsed.model_dump())
        except ValidationError:
            raise InferenceResponseError(f"The answer didn't match {schema.__name__}.") from None
