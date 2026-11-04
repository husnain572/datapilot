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
    # Picks an available free model that supports what each request needs.
    ai_model: str = "openrouter/free"
    # Generous, because free models can queue requests when they're busy.
    ai_timeout_seconds: float = Field(default=180, gt=0)
    ai_max_attempts: int = Field(default=3, ge=1, le=5)
    # Includes the model's thinking, so this also caps the cost of one call.
    ai_max_completion_tokens: int = Field(default=8192, ge=1)

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
