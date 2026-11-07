"""Endpoints for asking questions, reading results, and running the Containment Lab.

The site is open to everyone, with no login. Limits per visitor and per day keep the free AI
allowance and the server from being used up.
"""

import threading
import uuid
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel

from backend.agent.pipeline import AnalysisPipeline
from backend.api.datasets import SettingsDep, StoreDep
from backend.schemas.analysis import AnalysisRecord, AnalysisRequest, ContainmentReport
from backend.schemas.datasets import DatasetProfile
from backend.services.containment import run_containment_lab
from backend.services.executor_client import ExecutorClient, ExecutorError
from backend.services.ai_router import AIRouter, analyses_left_today, create_ai_router
from backend.services.inference import InferenceError
from backend.services.report import build_report
from backend.services.usage import UsageTracker, VisitorLimiter, VisitorLimitReached, next_reset
from backend.storage.analyses import AnalysisStore, load_example

router = APIRouter(prefix="/api/v1", tags=["analyses"])

# A small server can only do a little at once.
analysis_slots = threading.BoundedSemaphore(2)
lab_slot = threading.BoundedSemaphore(1)


def visitor_id(x_visitor_id: Annotated[str, Header(max_length=200)] = "") -> str:
    """Who is asking. The frontend passes the visitor's network address. The backend isn't
    reachable from the internet, so only the frontend can set this."""
    return x_visitor_id or "unknown"


VisitorDep = Annotated[str, Depends(visitor_id)]


def usage_tracker(settings: SettingsDep) -> UsageTracker:
    return UsageTracker(settings.data_dir)


def check_visitor(settings: SettingsDep, visitor: str, action: str, hourly_limit: int) -> None:
    try:
        VisitorLimiter(settings.data_dir).record(visitor, action, hourly_limit)
    except VisitorLimitReached as error:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(error)) from error


def get_analysis_store(settings: SettingsDep) -> AnalysisStore:
    return AnalysisStore(settings.data_dir, settings.dataset_ttl_hours)


def create_ai(settings: SettingsDep) -> AIRouter:
    try:
        return create_ai_router(settings, usage_tracker(settings))
    except InferenceError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, error.user_message) from error


def create_executor(settings: SettingsDep) -> ExecutorClient:
    if settings.executor_token is None or not settings.executor_token.get_secret_value():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The secure sandbox isn't set up yet.")
    return ExecutorClient(settings.executor_url, settings.executor_token)


AnalysisStoreDep = Annotated[AnalysisStore, Depends(get_analysis_store)]


def _run_analysis(
    pipeline: AnalysisPipeline,
    record: AnalysisRecord,
    csv_text: str,
    profile: DatasetProfile,
    parent: AnalysisRecord | None,
    limiter: VisitorLimiter,
    visitor: str,
) -> None:
    try:
        pipeline.run(record, csv_text, profile, parent)
        if record.status == "refused":
            # Questions the data can't answer (or greetings) don't run any analysis, so don't count them.
            limiter.release(visitor, "analysis")
    finally:
        pipeline.ai.close()
        pipeline.executor.close()
        analysis_slots.release()


class UsageSummary(BaseModel):
    analyses_left_today: int
    resets_at: datetime


@router.get("/usage")
def get_usage(settings: SettingsDep) -> UsageSummary:
    """How many analyses can still run today, shared by everyone."""
    left = analyses_left_today(settings, usage_tracker(settings))
    return UsageSummary(analyses_left_today=left, resets_at=next_reset())


@router.get("/examples/sales")
def get_example() -> AnalysisRecord:
    """A saved, real analysis of the sample data, shown instantly with no AI requests."""
    record = load_example()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The example isn't available.")
    return record


@router.post("/analyses", status_code=status.HTTP_202_ACCEPTED)
def create_analysis(
    request: AnalysisRequest,
    background: BackgroundTasks,
    settings: SettingsDep,
    visitor: VisitorDep,
    datasets: StoreDep,
    analyses: AnalysisStoreDep,
    ai: Annotated[AIRouter, Depends(create_ai)],
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

    if analyses_left_today(settings, usage_tracker(settings)) < 1:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Today's analysis limit has been reached. It resets at midnight UTC. "
            "You can still see the example analysis.",
        )
    check_visitor(settings, visitor, "analysis", settings.visitor_analyses_per_hour)

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

    pipeline = AnalysisPipeline(ai, executor, analyses)
    background.add_task(
        _run_analysis,
        pipeline,
        record,
        datasets.read_csv(request.dataset_id),
        info.profile,
        parent,
        VisitorLimiter(settings.data_dir),
        visitor,
    )
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


@router.post("/containment-tests")
def containment_tests(
    settings: SettingsDep,
    visitor: VisitorDep,
    executor: Annotated[ExecutorClient, Depends(create_executor)],
) -> ContainmentReport:
    """Run fixed attack scripts in the real sandbox and report whether each was contained."""
    try:
        check_visitor(settings, visitor, "lab", settings.visitor_lab_runs_per_hour)
    except HTTPException:
        executor.close()
        raise
    if not lab_slot.acquire(blocking=False):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The Containment Lab is already running.")
    try:
        return run_containment_lab(executor)
    except ExecutorError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    finally:
        lab_slot.release()
        executor.close()
