"""Client for Vultr Serverless Inference, the only way DataPilot calls an AI model.

API reference: https://api.vultrinference.com/docs

Retry rules follow Vultr's error guide:
- 429 (too many requests): wait for the time in the Retry-After header.
- 502, 503, 504 (temporary trouble): wait, doubling the wait each time.
- 500 (server failure): retry once only.
- Anything else means the request itself is wrong, so retrying won't help.

The API key and prompts never go into logs or error messages. Prompts contain
details about users' data, so only sizes, timings, and status codes are logged.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from backend.config import Settings
from backend.schemas.inference import (
    ApiChatResponse,
    ApiModelList,
    ChatMessage,
    ChatResult,
    TokenUsage,
)

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

MAX_ERROR_TEXT_LENGTH = 300
# Reasons an answer can't be used. "length" means it was cut off partway.
UNUSABLE_FINISH_REASONS = {"length", "content_filter", "error"}


class InferenceError(Exception):
    """Base class. str(error) is for logs; `user_message` is safe to show on screen."""

    user_message = "The AI service had a problem. Please try again."


class InferenceNotConfiguredError(InferenceError):
    user_message = "The AI service isn't set up yet."


class InferenceAuthError(InferenceError):
    user_message = "The AI service rejected our credentials."


class InferenceRequestError(InferenceError):
    """Vultr says the request is wrong, for example an unknown model. Retrying won't help."""

    user_message = "The AI service couldn't accept this request."


class InferenceRateLimitError(InferenceError):
    user_message = "The AI service is busy. Please try again in a minute."


class InferenceUnavailableError(InferenceError):
    user_message = "The AI service is temporarily unavailable. Please try again."


class InferenceTimeoutError(InferenceError):
    user_message = "The AI service took too long to answer. Please try again."


class InferenceResponseError(InferenceError):
    """The reply arrived but can't be used: malformed, empty, cut off, or the wrong shape."""

    user_message = "The AI service returned an answer we couldn't use. Please try again."


def _strip_code_fence(text: str) -> str:
    """Some models wrap JSON in a Markdown ```json block, even when asked not to."""
    match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n?\s*```\s*", text, flags=re.DOTALL)
    return match.group(1) if match else text


class InferenceClient:
    def __init__(
        self,
        api_key: SecretStr,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 180,
        max_attempts: int = 3,
        max_retry_wait_seconds: float = 30,
        max_completion_tokens: int = 8192,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._max_attempts = max_attempts
        self._max_retry_wait_seconds = max_retry_wait_seconds
        self._max_completion_tokens = max_completion_tokens
        self._sleep = sleep
        # Redirects stay off, so the key can't be forwarded to another address.
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=10),
            follow_redirects=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"InferenceClient(model={self.model!r})"

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "InferenceClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> ChatResult:
        """Send a conversation to the model and return its complete answer."""
        body: dict = {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "max_completion_tokens": max_completion_tokens or self._max_completion_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "strict": True,
                    "schema": response_schema.model_json_schema(),
                },
            }

        started = time.monotonic()
        response = self._request("POST", "/chat/completions", body)
        latency = round(time.monotonic() - started, 3)

        parsed = self._parse(response, ApiChatResponse)
        if not parsed.choices:
            raise InferenceResponseError("The reply contained no choices.")
        choice = parsed.choices[0]

        if choice.finish_reason in UNUSABLE_FINISH_REASONS:
            raise InferenceResponseError(f"The answer stopped early: {choice.finish_reason}.")
        if not choice.message.content or not choice.message.content.strip():
            raise InferenceResponseError("The reply had no answer text.")

        usage = parsed.usage or TokenUsage()
        logger.info(
            "Inference call finished: model=%s latency=%.1fs prompt_tokens=%d completion_tokens=%d",
            self.model,
            latency,
            usage.prompt_tokens,
            usage.completion_tokens,
        )
        return ChatResult(
            content=choice.message.content,
            model=parsed.model or self.model,
            finish_reason=choice.finish_reason,
            usage=usage,
            latency_seconds=latency,
        )

    def chat_structured(
        self,
        messages: list[ChatMessage],
        schema: type[SchemaT],
        *,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
    ) -> SchemaT:
        """Ask for an answer matching a Pydantic model, and check that it really does."""
        result = self.chat(
            messages,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            response_schema=schema,
        )
        try:
            return schema.model_validate_json(_strip_code_fence(result.content))
        except ValidationError as error:
            # Only the count: Pydantic's full message quotes the model's output.
            raise InferenceResponseError(
                f"The answer didn't match {schema.__name__}: {error.error_count()} problem(s)."
            ) from None

    def list_model_ids(self) -> list[str]:
        """Ask Vultr's live catalog which models exist."""
        return [model.id for model in self._parse(self._request("GET", "/models"), ApiModelList).data]

    def _request(self, method: str, path: str, body: dict | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

        for attempt in range(1, self._max_attempts + 1):
            backoff = 2.0 ** (attempt - 1)  # 1, 2, 4... seconds.
            try:
                response = self._http.request(method, path, json=body, headers=headers)
            except (httpx.ReadTimeout, httpx.WriteTimeout) as error:
                # The full timeout has passed already, so waiting again could take many minutes.
                raise InferenceTimeoutError("The request timed out.") from error
            except httpx.TransportError as error:
                failure: InferenceError = InferenceUnavailableError(
                    f"Network problem: {type(error).__name__}."
                )
                delay: float | None = backoff
            else:
                if response.is_success:
                    return response
                failure = self._error_from(response)
                delay = self._retry_delay(response, attempt, backoff)

            logger.warning("Inference attempt %d/%d failed: %s", attempt, self._max_attempts, failure)
            out_of_attempts = attempt == self._max_attempts
            if delay is None or out_of_attempts or delay > self._max_retry_wait_seconds:
                raise failure
            self._sleep(delay)

        raise InferenceError("No request attempts were made.")  # Only if max_attempts < 1.

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int, backoff: float) -> float | None:
        """How long to wait before retrying, or None if this failure shouldn't be retried."""
        status = response.status_code
        if status == 429:
            try:
                return max(float(response.headers["Retry-After"]), 0.0)
            except (KeyError, ValueError):
                return backoff
        if status in (502, 503, 504):
            return backoff
        if status == 500 and attempt == 1:
            return backoff
        return None

    def _error_from(self, response: httpx.Response) -> InferenceError:
        status = response.status_code
        message = f"HTTP {status}: {self._safe_error_text(response)}"
        if status == 401:
            return InferenceAuthError(message)
        if status == 429:
            return InferenceRateLimitError(message)
        if status >= 500:
            return InferenceUnavailableError(message)
        return InferenceRequestError(message)

    def _safe_error_text(self, response: httpx.Response) -> str:
        """Get Vultr's error message, with any copy of our key removed and the length capped."""
        try:
            text = str(response.json()["error"]["message"])
        except (ValueError, KeyError, TypeError):
            text = "no error details"
        text = text.replace(self._api_key.get_secret_value(), "[hidden]")
        return text[:MAX_ERROR_TEXT_LENGTH]

    @staticmethod
    def _parse(response: httpx.Response, shape: type[SchemaT]) -> SchemaT:
        try:
            return shape.model_validate(response.json())
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            raise InferenceResponseError("The reply wasn't in the expected format.") from None


def create_inference_client(settings: Settings) -> InferenceClient:
    """Build a client from settings, or explain what's missing."""
    api_key = settings.vultr_serverless_inference_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise InferenceNotConfiguredError(
            "VULTR_SERVERLESS_INFERENCE_API_KEY is not set. Add it to the .env file."
        )
    return InferenceClient(
        SecretStr(api_key.get_secret_value().strip()),
        settings.vultr_inference_base_url,
        settings.vultr_inference_model,
        timeout_seconds=settings.vultr_inference_timeout_seconds,
        max_attempts=settings.vultr_inference_max_attempts,
        max_completion_tokens=settings.vultr_inference_max_completion_tokens,
    )
