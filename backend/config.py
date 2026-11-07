"""App settings, read from environment variables or the private .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "production"] = "local"
    data_dir: Path = Path("data")

    # Upload limits. Uploaded files are untrusted, so everything has a cap.
    max_upload_bytes: int = 25 * 1024 * 1024
    max_rows: int = 500_000
    max_columns: int = 200
    dataset_ttl_hours: int = 24

    # --- Primary AI provider: Groq's free tier (https://console.groq.com/docs) ---
    # SecretStr hides the key if settings are ever printed or logged.
    ai_api_key: SecretStr | None = None
    ai_base_url: str = "https://api.groq.com/openai/v1"
    # A Groq production model (not a preview) that supports JSON schemas.
    ai_model: str = "openai/gpt-oss-120b"
    # "prompt" describes the JSON format in words, and Pydantic checks every reply either way.
    # "schema" sends response_format, which Groq documents for this model but which we haven't
    # tested yet with DataPilot's prompts.
    ai_structured_mode: Literal["schema", "prompt"] = "prompt"
    # Lower effort uses fewer of the free daily tokens. Leave blank for providers without it.
    ai_reasoning_effort: Literal["low", "medium", "high"] | None = "low"
    ai_timeout_seconds: float = Field(default=90, gt=0)
    ai_max_attempts: int = Field(default=3, ge=1, le=5)
    # Includes the model's thinking, so this also caps the cost of one call.
    ai_max_completion_tokens: int = Field(default=8192, ge=1)
    # Stop below the provider's free limits, so the app refuses politely before the provider does.
    # Groq's free tier for this model: about 1,000 requests and 200,000 tokens a day.
    ai_daily_request_limit: int = Field(default=900, ge=0)
    ai_daily_token_limit: int = Field(default=180_000, ge=0)

    # --- Optional fallback provider: Google Gemini, used only when a key is set ---
    ai_fallback_api_key: SecretStr | None = None
    ai_fallback_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    ai_fallback_model: str = "gemini-3.8-flash"
    ai_fallback_structured_mode: Literal["schema", "prompt"] = "prompt"
    ai_fallback_reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Gemini's free limits depend on the account, so set these from Google AI Studio.
    ai_fallback_daily_request_limit: int = Field(default=200, ge=0)
    ai_fallback_daily_token_limit: int = Field(default=500_000, ge=0)

    # Used only to estimate "analyses left today" from the remaining token budget.
    ai_tokens_per_analysis_estimate: int = Field(default=12_000, ge=1)

    # The private service that runs generated code in sandboxes.
    executor_url: str = "http://127.0.0.1:8100"
    executor_token: SecretStr | None = None
    # The site is open to everyone, so each visitor gets a fair share of the free AI allowance.
    visitor_analyses_per_hour: int = Field(default=4, ge=1)
    visitor_lab_runs_per_hour: int = Field(default=3, ge=1)

    @field_validator("ai_base_url", "ai_fallback_base_url")
    @classmethod
    def base_url_must_be_https(cls, value: str) -> str:
        """The API key travels with each request, so it must always be encrypted."""
        if not value.startswith("https://"):
            raise ValueError("AI_BASE_URL and AI_FALLBACK_BASE_URL must start with https://")
        return value

    @field_validator("ai_reasoning_effort", "ai_fallback_reasoning_effort", mode="before")
    @classmethod
    def blank_means_none(cls, value: object) -> object:
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings object, so .env is only read once."""
    return Settings()
