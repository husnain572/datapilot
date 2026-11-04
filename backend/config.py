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
    vultr_serverless_inference_api_key: SecretStr | None = None
    vultr_inference_base_url: str = "https://api.vultrinference.com/v1"
    vultr_inference_model: str = "glm-5.3"
    # Generous, because Vultr may hold a request for up to 2 minutes when busy.
    vultr_inference_timeout_seconds: float = Field(default=180, gt=0)
    vultr_inference_max_attempts: int = Field(default=3, ge=1, le=5)
    # Includes the model's thinking, so this also caps the cost of one call.
    vultr_inference_max_completion_tokens: int = Field(default=8192, ge=1)

    @field_validator("vultr_inference_base_url")
    @classmethod
    def base_url_must_be_https(cls, value: str) -> str:
        """The API key travels with each request, so it must always be encrypted."""
        if not value.startswith("https://"):
            raise ValueError("VULTR_INFERENCE_BASE_URL must start with https://")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings object, so .env is only read once."""
    return Settings()
