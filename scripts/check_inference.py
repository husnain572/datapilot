"""Makes a few real, very small calls to confirm the AI connection works.

Uses your .env file. With a free model it costs nothing, but counts toward the
free daily limit. It never prints your key.

Run with: uv run python scripts/check_inference.py
"""

import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.schemas.inference import ChatMessage  # noqa: E402
from backend.services.inference import InferenceError, create_inference_client  # noqa: E402


class ConnectionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


def main() -> int:
    try:
        with create_inference_client(get_settings()) as client:
            print(f"Model: {client.model}")

            available = client.list_model_ids()
            if client.model not in available:
                print(f"FAILED: '{client.model}' isn't in the provider's catalog of {len(available)} models.")
                return 1
            print(f"1. Catalog: OK ({len(available)} models, includes this one)")

            plain = client.chat(
                [ChatMessage(role="user", content="Reply with exactly one word: ready")],
                temperature=0,
                max_completion_tokens=2000,
            )
            print(
                f"2. Chat: OK, answered {plain.content.strip()[:80]!r} in "
                f"{plain.latency_seconds}s, {plain.usage.total_tokens} tokens"
            )

            structured = client.chat_structured(
                [ChatMessage(role="user", content='Return JSON with the field "status" set to "ok".')],
                ConnectionCheck,
                temperature=0,
                max_completion_tokens=2000,
            )
            print(f"3. Structured output: OK, validated as {structured.model_dump()}")
    except InferenceError as error:
        print(f"FAILED ({type(error).__name__}): {error}")
        return 1

    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
