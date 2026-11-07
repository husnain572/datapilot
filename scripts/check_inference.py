"""Makes a few real, very small calls to each configured AI provider.

Uses your .env file. It uses a few of the free daily requests, and never prints your keys.

Run with: uv run python scripts/check_inference.py
"""

import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.schemas.inference import ChatMessage  # noqa: E402
from backend.services.ai_router import create_client, provider_specs  # noqa: E402
from backend.services.inference import InferenceClient, InferenceError  # noqa: E402
from backend.services.openai_provider import OpenAIResponsesClient  # noqa: E402


class ConnectionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


def check(name: str, client: InferenceClient) -> bool:
    print(f"\n[{name}] model: {client.model}")
    try:
        available = client.list_model_ids()
        if client.model in available:
            print(f"1. Catalog: OK ({len(available)} models, includes this one)")
        else:
            print(f"1. Catalog: WARNING, '{client.model}' isn't in the provider's list of {len(available)} models")
        plain = client.chat(
            [ChatMessage(role="user", content="Reply with exactly one word: ready")],
            temperature=0,
            max_completion_tokens=2000,
        )
        print(f"2. Chat: OK, answered {plain.content.strip()[:80]!r} in {plain.latency_seconds}s, "
              f"{plain.usage.total_tokens} tokens")
        structured = client.chat_structured(
            [ChatMessage(role="user", content='Return JSON with the field "status" set to "ok".')],
            ConnectionCheck,
            temperature=0,
            max_completion_tokens=2000,
        )
        print(f"3. Structured output: OK, validated as {structured.model_dump()}")
        return True
    except InferenceError as error:
        print(f"FAILED ({type(error).__name__}): {error}")
        return False


def check_openai(name: str, client: OpenAIResponsesClient) -> bool:
    print(f"\n[{name}] OpenAI Responses API, model: {client.model}")
    try:
        structured = client.chat_structured(
            [ChatMessage(role="user", content='Return JSON with the field "status" set to "ok".')],
            ConnectionCheck,
            max_completion_tokens=2000,
        )
        print(f"Structured output: OK, validated as {structured.model_dump()}, {client.last_usage.total_tokens} tokens")
        return True
    except InferenceError as error:
        print(f"FAILED ({type(error).__name__}): {error}")
        return False


def main() -> int:
    settings = get_settings()
    try:
        specs = provider_specs(settings)
    except InferenceError as error:
        print(f"FAILED: {error}")
        return 1
    if not specs:
        print("FAILED: no AI provider key is set in the .env file.")
        return 1
    results = []
    for spec in specs:
        if spec.kind == "openai":
            with create_client(spec, settings) as client:
                results.append(check_openai(spec.name, client))
            continue
        with InferenceClient(
            spec.api_key,
            spec.base_url,
            spec.model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_attempts=1,
            structured_mode=spec.structured_mode,
            reasoning_effort=spec.reasoning_effort,
        ) as client:
            results.append(check(spec.name, client))
    print("\nAll checks passed." if all(results) else "\nSome checks failed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
