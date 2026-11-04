"""Endpoints for asking questions, reading results, and running the Containment Lab."""

import hmac
import threading
import uuid
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response, status

from backend.agent.pipeline import AnalysisPipeline
from backend.api.datasets import SettingsDep, StoreDep
from backend.schemas.analysis import AnalysisRecord, AnalysisRequest, ContainmentReport
from backend.schemas.datasets import DatasetProfile
from backend.services.containment import run_containment_lab
from backend.services.executor_client import ExecutorClient, ExecutorError
from backend.services.inference import InferenceClient, InferenceError, create_inference_client
from backend.services.report import build_report
from backend.services.usage import UsageTracker
from backend.storage.analyses import AnalysisStore

router = APIRouter(prefix="/api/v1", tags=["analyses"])

# A small server can only do a little at once.
analysis_slots = threading.BoundedSemaphore(2)
lab_slot = threading.BoundedSemaphore(1)


def require_access_code(settings: SettingsDep, x_access_code: Annotated[str, Header()] = "") -> None:
    """Features that use the AI or sandbox need the demo code, so strangers can't exhaust them."""
    if settings.access_code is None or not settings.access_code.get_secret_value():
        return  # No code configured, as when developing locally.
    if not hmac.compare_digest(x_access_code.encode(), settings.access_code.get_secret_value().encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That access code isn't right.")


def get_analysis_store(settings: SettingsDep) -> AnalysisStore:
    return AnalysisStore(settings.data_dir, settings.dataset_ttl_hours)


def create_ai(settings: SettingsDep) -> InferenceClient:
    try:
        return create_inference_client(settings)
    except InferenceError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, error.user_message) from error


def create_executor(settings: SettingsDep) -> ExecutorClient:
    if settings.executor_token is None or not settings.executor_token.get_secret_value():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The secure sandbox isn't set up yet.")
    return ExecutorClient(settings.executor_url, settings.executor_token)


AnalysisStoreDep = Annotated[AnalysisStore, Depends(get_analysis_store)]
AccessDep = Depends(require_access_code)


def _run_analysis(
    pipeline: AnalysisPipeline,
    record: AnalysisRecord,
    csv_text: str,
    profile: DatasetProfile,
    parent: AnalysisRecord | None,
) -> None:
    try:
        pipeline.run(record, csv_text, profile, parent)
    finally:
        pipeline.ai.close()
        pipeline.executor.close()
        analysis_slots.release()


@router.post("/analyses", status_code=status.HTTP_202_ACCEPTED, dependencies=[AccessDep])
def create_analysis(
    request: AnalysisRequest,
    background: BackgroundTasks,
    settings: SettingsDep,
    datasets: StoreDep,
    analyses: AnalysisStoreDep,
    ai: Annotated[InferenceClient, Depends(create_ai)],
    executor: Annotated[ExecutorClient, Depends(create_executor)],
) -> AnalysisRecord:
    """Start analyzing a question. Check progress with GET /api/v1/analyses/{id}."""
    info = datasets.get(request.dataset_id)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found. It may have expired.")

    parent = None
    if request.parent_id is not None:
        parent = analyses.get(request.parent_id)
        if parent is None or parent.dataset_id != request.dataset_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "The earlier analysis wasn't found for this dataset.")

    if not analysis_slots.acquire(blocking=False):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The server is busy. Please try again in a minute.")

    now = datetime.now(timezone.utc)
    record = AnalysisRecord(
        analysis_id=uuid.uuid4(),
        dataset_id=request.dataset_id,
        question=request.question.strip(),
        parent_id=request.parent_id,
        created_at=now,
        updated_at=now,
    )
    analyses.purge_expired()
    analyses.save(record)

    pipeline = AnalysisPipeline(
        ai, executor, analyses, UsageTracker(settings.data_dir, settings.ai_daily_request_limit)
    )
    background.add_task(_run_analysis, pipeline, record, datasets.read_csv(request.dataset_id), info.profile, parent)
    return record


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: UUID, analyses: AnalysisStoreDep) -> AnalysisRecord:
    """Get an analysis's progress and results."""
    record = analyses.get(analysis_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found. It may have expired.")
    return record


@router.get("/analyses/{analysis_id}/report")
def get_report(analysis_id: UUID, analyses: AnalysisStoreDep) -> Response:
    """Download a finished analysis as a Markdown report."""
    record = analyses.get(analysis_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found. It may have expired.")
    if record.status != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "The report is available once the analysis finishes.")
    return Response(
        build_report(record),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="datapilot-report.md"'},
    )


@router.post("/containment-tests", dependencies=[AccessDep])
def containment_tests(executor: Annotated[ExecutorClient, Depends(create_executor)]) -> ContainmentReport:
    """Run fixed attack scripts in the real sandbox and report whether each was contained."""
    if not lab_slot.acquire(blocking=False):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The Containment Lab is already running.")
    try:
        return run_containment_lab(executor)
    except ExecutorError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    finally:
        lab_slot.release()
        executor.close()
