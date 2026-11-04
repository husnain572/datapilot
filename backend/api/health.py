"""Health checks, which let people and tools ask whether the app is OK."""

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from backend.config import Settings, get_settings
from backend.schemas.health import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> HealthResponse:
    """Is the process running at all?"""
    return HealthResponse()


@router.get("/ready")
def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadyResponse:
    """Can the app do real work? Returns 503 if something it needs is unavailable."""
    data_dir = settings.data_dir
    checks = {"data_dir_writable": data_dir.is_dir() and os.access(data_dir, os.W_OK)}

    if all(checks.values()):
        return ReadyResponse(status="ready", checks=checks)

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="not_ready", checks=checks)
