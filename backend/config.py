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

    # SecretStr hides the key if settings are ever printed or logged.
    ai_api_key: SecretStr | None = None
    ai_base_url: str = "https://openrouter.ai/api/v1"
    # Tested with the real prompts on Nov 5, 2026. `openrouter/free` also works, but varies.
    ai_model: str = "nvidia/nemotron-3.5-super-vl:free"
    # "prompt" describes the JSON format in words, which free models need, because they
    # hung when sent response_format in testing. Use "schema" for Vultr Serverless Inference.
    ai_structured_mode: Literal["schema", "prompt"] = "prompt"
    ai_timeout_seconds: float = Field(default=90, gt=0)
    ai_max_attempts: int = Field(default=3, ge=1, le=5)
    # Includes the model's thinking, so this also caps the cost of one call.
    ai_max_completion_tokens: int = Field(default=8192, ge=1)

    # Free accounts get 50 requests a day, so stop before that and keep some in reserve.
    ai_daily_request_limit: int = Field(default=40, ge=0)

    # The private service that runs generated code in sandboxes.
    executor_url: str = "http://127.0.0.1:8100"
    executor_token: SecretStr | None = None
    # The site is open to everyone, so each visitor gets a fair share of the free AI allowance.
    visitor_analyses_per_hour: int = Field(default=4, ge=1)
    visitor_lab_runs_per_hour: int = Field(default=3, ge=1)

    @field_validator("ai_base_url")
    @classmethod
    def base_url_must_be_https(cls, value: str) -> str:
        """The API key travels with each request, so it must always be encrypted."""
        if not value.startswith("https://"):
            raise ValueError("AI_BASE_URL must start with https://")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings object, so .env is only read once."""
    return Settings()
