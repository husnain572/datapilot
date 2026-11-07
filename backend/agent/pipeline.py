"""The analysis workflow, written as a plain sequence of steps with fixed limits.

Question → plan and code → quick check → sandbox → validate → recalculate → explain.

The first attempt and at most two repairs each get a new sandbox. The AI model never sees
a run it could influence directly: everything it returns is checked before use.
"""

import json
import logging
from dataclasses import dataclass
from typing import TypeVar

import pandas as pd
from pydantic import BaseModel, ValidationError

from backend.agent import prompts
from backend.schemas.analysis import (
    AnalysisDraft,
    AnalysisRecord,
    AttemptLog,
    Insights,
    SandboxFailure,
    SandboxResult,
)
from backend.schemas.datasets import DatasetProfile
from backend.schemas.inference import ChatMessage
from backend.services.code_safety import find_problems
from backend.services.executor_client import ExecutionResult, ExecutorClient, ExecutorError
from backend.services.inference import InferenceError, InferenceResponseError
from backend.services.number_guard import find_invented, remove_invented
from backend.services.profiling import load_dataframe
from backend.services.ai_router import AIRouter
from backend.services.usage import DailyLimitReached
from backend.services.verification import verify_result
from backend.storage.analyses import AnalysisStore

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # One first attempt plus two repairs.
MAX_LOG_LENGTH = 4000
RUN_FAILURES = {
    "timeout": "The code took too long and was stopped. Use simpler vectorized pandas operations.",
    "out_of_memory": "The code used too much memory and was stopped. Avoid making large copies of the data.",
    "output_too_large": "The code reported too much output. Report summaries, not raw rows.",
    "crashed": "The code crashed the Python process.",
}
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class AnalysisStopped(Exception):
    """The analysis can't continue. The message is safe to show to users."""


@dataclass
class Outcome:
    """What one attempt produced: a validated result, or a problem to send back to the AI."""

    result: SandboxResult | None = None
    problem: str | None = None


def check_plan_columns(draft: AnalysisDraft, columns: set[str]) -> str | None:
    """The plan may only name columns that really exist."""
    invented = sorted({c for step in draft.plan.steps for c in step.columns if c not in columns})
    if invented:
        return f"The plan names columns that don't exist: {invented}. The real columns are {sorted(columns)}."
    return None


def read_run(run: ExecutionResult) -> Outcome:
    """Turn a sandbox run into a validated result or a clear, sanitized problem."""
    if run.status != "completed":
        return Outcome(problem=RUN_FAILURES[run.status])
    try:
        document = json.loads(run.stdout)
    except ValueError:
        return Outcome(problem="The code produced no readable results.")

    if isinstance(document, dict) and document.get("status") == "error":
        try:
            failure = SandboxFailure.model_validate(document)
        except ValidationError:
            return Outcome(problem="The code failed, and its error report was unreadable.")
        where = f" on line {failure.line}" if failure.line else ""
        return Outcome(problem=f"{failure.error_type}{where}: {failure.message}")

    try:
        return Outcome(result=SandboxResult.model_validate(document))
    except ValidationError as error:
        # Locations and reasons only: no reported values go back to the model.
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()[:5]
        )
        return Outcome(problem=f"The reported results were invalid: {details}")


class AnalysisPipeline:
    def __init__(self, ai: AIRouter, executor: ExecutorClient, store: AnalysisStore) -> None:
        self.ai = ai
        self.executor = executor
        self.store = store

    def run(self, record: AnalysisRecord, csv_text: str, profile: DatasetProfile, parent: AnalysisRecord | None) -> None:
        """Run the whole analysis. The outcome is always saved on the record."""
        record.status = "running"
        try:
            df = load_dataframe(csv_text)
            if self._investigate(record, csv_text, df, profile, parent):
                self._explain(record)
                record.status = "completed"
                record.stage = "Finished"
        except AnalysisStopped as stop:
            record.status = "failed"
            record.stage = "Stopped"
            record.message = str(stop)
        except Exception:  # noqa: BLE001 - never leave an analysis stuck, whatever went wrong.
            logger.exception("Analysis %s failed unexpectedly", record.analysis_id)
            record.status = "failed"
            record.stage = "Stopped"
            record.message = "Something went wrong on our side. Please try again."
        self.store.save(record)

    def _stage(self, record: AnalysisRecord, stage: str) -> None:
        record.stage = stage
        self.store.save(record)

    def _ask(
        self, record: AnalysisRecord, messages: list[ChatMessage], schema: type[SchemaT], purpose: str = "analysis"
    ) -> SchemaT:
        record.ai_requests += 1
        try:
            return self.ai.chat_structured(messages, schema, temperature=0, purpose=purpose)
        except DailyLimitReached as error:
            raise AnalysisStopped(str(error)) from error

    def _investigate(
        self,
        record: AnalysisRecord,
        csv_text: str,
        df: pd.DataFrame,
        profile: DatasetProfile,
        parent: AnalysisRecord | None,
    ) -> bool:
        """Get working code and verified results. Returns False if the question was refused."""
        columns = {column.name for column in profile.columns}
        messages = [
            ChatMessage(role="system", content=prompts.DRAFT_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompts.draft_request(record.question, profile, parent)),
        ]
        problem: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._stage(record, f"Planning and writing code (attempt {attempt} of {MAX_ATTEMPTS})")
            log = AttemptLog(attempt=attempt, outcome="started")
            record.attempts.append(log)

            draft = self._get_draft(record, messages, log)
            if draft is None:
                problem = log.problem
                continue

            if not draft.can_answer:
                problem = None
                log.outcome = "refused"
                record.status = "refused"
                record.stage = "Finished"
                record.message = draft.reason_if_not
                return False

            log.code = draft.code
            record.plan = draft.plan
            record.code = draft.code
            problem = check_plan_columns(draft, columns) or "; ".join(find_problems(draft.code)) or None
            if problem:
                log.outcome = "rejected before running"
            else:
                self._stage(record, f"Running code in the secure sandbox (attempt {attempt} of {MAX_ATTEMPTS})")
                problem = self._run_and_check(record, csv_text, df, draft.code, log)
                if problem is None:
                    log.outcome = "succeeded"
                    return True

            log.problem = problem
            messages = messages[:2] + [
                ChatMessage(role="user", content=prompts.repair_request(draft.code, problem))
            ]

        # Anything that failed its checks on the last attempt must never be displayed.
        record.result = None
        record.verifications = []
        raise AnalysisStopped(
            f"The analysis didn't succeed after {MAX_ATTEMPTS} attempts. Try rephrasing your question. Last problem: {problem}"
        )

    def _get_draft(self, record: AnalysisRecord, messages: list[ChatMessage], log: AttemptLog) -> AnalysisDraft | None:
        try:
            return self._ask(record, messages, AnalysisDraft)
        except InferenceResponseError as error:
            log.outcome = "unusable AI answer"
            log.problem = str(error)
            return None
        except InferenceError as error:
            logger.warning("AI request failed: %s", error)
            raise AnalysisStopped(error.user_message) from error

    def _run_and_check(
        self, record: AnalysisRecord, csv_text: str, df: pd.DataFrame, code: str, log: AttemptLog
    ) -> str | None:
        """Run the code, then validate and recalculate. Returns a problem, or None if all's well."""
        try:
            run = self.executor.run(code, csv_text)
        except ExecutorError as error:
            raise AnalysisStopped(str(error)) from error

        log.duration_seconds = run.duration_seconds
        log.logs = run.stderr[:MAX_LOG_LENGTH]
        log.outcome = f"sandbox: {run.status}"

        outcome = read_run(run)
        if outcome.result is None:
            return outcome.problem

        self._stage(record, "Validating and recalculating results")
        record.result = outcome.result
        record.verifications = verify_result(df, outcome.result)
        mismatches = [v for v in record.verifications if v.status == "mismatch"]
        if mismatches:
            log.outcome = "recalculation disagreed"
            return "Independent recalculation disagreed. " + " ".join(
                f"{v.target} [{v.formula}]: {v.detail}" for v in mismatches[:5]
            ) + " Fix the calculation, or correct its check if the check doesn't describe it."
        return None

    def _explain(self, record: AnalysisRecord) -> None:
        """Explain the validated results. If this fails, the verified numbers still stand."""
        self._stage(record, "Explaining the verified results")
        messages = [
            ChatMessage(role="system", content=prompts.EXPLAIN_SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=prompts.explain_request(record.question, record.result, record.verifications),
            ),
        ]
        try:
            insights = self._ask(record, messages, Insights, purpose="summary")
            invented = find_invented(insights, record.result, record.question)
            if invented:
                messages.append(
                    ChatMessage(role="user", content=prompts.retry_explain_request(insights, invented))
                )
                insights = self._ask(record, messages, Insights, purpose="summary")
            record.insights, record.removed_claims = remove_invented(insights, record.result, record.question)
        except (InferenceError, AnalysisStopped) as error:
            logger.warning("Explanation failed: %s", error)
            record.message = "The numbers below are verified, but the written explanation couldn't be generated."
