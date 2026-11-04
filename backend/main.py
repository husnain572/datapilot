"""DataPilot's backend. Start it with: uv run uvicorn backend.main:app --reload"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.datasets import router as datasets_router
from backend.api.health import router as health_router
from backend.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare what the app needs before it starts accepting requests."""
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="DataPilot API",
    description="AI data analyst that runs generated code in a sandbox and verifies its results.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(datasets_router)
