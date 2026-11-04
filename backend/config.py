"""App settings, read from environment variables or the private .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "production"] = "local"
    data_dir: Path = Path("data")

    # SecretStr hides the key if settings are ever printed or logged.
    vultr_serverless_inference_api_key: SecretStr | None = None
    vultr_inference_base_url: str = "https://api.vultrinference.com/v1"
    vultr_inference_model: str = "glm-5.3"


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings object, so .env is only read once."""
    return Settings()
