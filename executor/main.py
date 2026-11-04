"""The executor: a small private service and the only part of DataPilot that uses Docker.

It listens only on the server's own loopback address, and needs a token. It has no
access to the AI key, because it runs as its own user with its own settings file.

Start with: uvicorn executor.main:app --host 127.0.0.1 --port 8100
"""

import hmac
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from executor.docker_runner import (
    RunStatus,
    SandboxLimits,
    remove_leftover_containers,
    run_in_sandbox,
)

logger = logging.getLogger(__name__)

MAX_CODE_BYTES = 100 * 1024


class ExecutorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXECUTOR_", extra="ignore")

    token: SecretStr = SecretStr("")
    work_root: Path = Path("/var/lib/datapilot-executor")
    image: str = "datapilot-sandbox:1"
    runtime: str | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_concurrent_runs: int = Field(default=2, ge=1, le=4)
    max_dataset_bytes: int = 30 * 1024 * 1024


class RunResponse(BaseModel):
    status: RunStatus
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str


settings = ExecutorSettings()
limits = SandboxLimits(image=settings.image, runtime=settings.runtime, timeout_seconds=settings.timeout_seconds)
run_slots = threading.BoundedSemaphore(settings.max_concurrent_runs)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if not settings.token.get_secret_value():
        raise RuntimeError("EXECUTOR_TOKEN must be set.")
    removed = remove_leftover_containers()
    if removed:
        logger.warning("Removed %d leftover sandbox containers.", removed)
    yield


app = FastAPI(title="DataPilot Executor", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def require_token(authorization: Annotated[str, Header()] = "") -> None:
    expected = f"Bearer {settings.token.get_secret_value()}"
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run", dependencies=[Depends(require_token)])
def run(
    code: Annotated[str, Form(max_length=MAX_CODE_BYTES)],
    dataset: Annotated[UploadFile, File()],
    timeout_seconds: Annotated[float | None, Form(gt=0)] = None,
) -> RunResponse:
    """Run code against a dataset in a new sandbox. The settings can't be changed by callers."""
    data = dataset.file.read(settings.max_dataset_bytes + 1)
    if len(data) > settings.max_dataset_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The dataset is too large.")

    if not run_slots.acquire(blocking=False):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "All sandboxes are busy.")
    try:
        result = run_in_sandbox(code, data, limits, settings.work_root, timeout_seconds)
    finally:
        run_slots.release()
    return RunResponse(
        status=result.status,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        stdout=result.stdout,
        stderr=result.stderr,
    )
