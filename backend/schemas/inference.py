"""The data sent to and received from the Vultr Serverless Inference API."""

from typing import Literal

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResult(BaseModel):
    content: str
    model: str
    finish_reason: str | None
    usage: TokenUsage
    latency_seconds: float


# --- The parts of Vultr's reply that we use. Any other fields are ignored. ---


class ApiMessage(BaseModel):
    # Thinking text arrives separately in `reasoning`. We deliberately never read it as the answer.
    content: str | None = None


class ApiChoice(BaseModel):
    message: ApiMessage
    finish_reason: str | None = None


class ApiChatResponse(BaseModel):
    model: str | None = None
    choices: list[ApiChoice]
    usage: TokenUsage | None = None


class ApiModel(BaseModel):
    id: str


class ApiModelList(BaseModel):
    data: list[ApiModel]
