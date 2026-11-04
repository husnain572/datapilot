"""The shapes of AI answers, sandbox results, and stored analyses."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

MAX_TEXT = 2000
FiniteFloat = Annotated[StrictFloat, Field(allow_inf_nan=False)]
Number = StrictInt | FiniteFloat
Cell = StrictStr | StrictBool | StrictInt | FiniteFloat | None


class Strict(BaseModel):
    """Reject any field we didn't ask for, so nothing unexpected gets through."""

    model_config = ConfigDict(extra="forbid")


# --- What the AI returns. No length rules in the schema itself, because some models
# reject them. They're checked in validators, after the answer arrives. ---


class PlanStep(Strict):
    description: str
    columns: list[str]


class AnalysisPlan(Strict):
    goal: str
    steps: list[PlanStep]
    assumptions: list[str]


class AnalysisDraft(Strict):
    can_answer: bool
    reason_if_not: str
    plan: AnalysisPlan
    code: str

    @model_validator(mode="after")
    def check_contents(self) -> "AnalysisDraft":
        if self.can_answer and not self.code.strip():
            raise ValueError("An answerable question needs code.")
        if self.can_answer and not self.plan.steps:
            raise ValueError("An answerable question needs a plan.")
        if not self.can_answer and not self.reason_if_not.strip():
            raise ValueError("Say why the question can't be answered.")
        return self


class Recommendation(Strict):
    action: str
    evidence: str


class Insights(Strict):
    observations: list[str]
    assumptions: list[str]
    recommendations: list[Recommendation]


# --- What the sandbox returns. All of it is untrusted until it passes these models. ---

CheckOp = Literal["sum", "mean", "min", "max", "count", "nunique", "count_missing", "count_duplicates"]


class MetricCheck(Strict):
    """How the backend can recalculate a number by itself: `columns` holds one column,
    or two to be multiplied together. No code is run, only these fixed operations."""

    op: CheckOp
    columns: list[StrictStr] = Field(default_factory=list, max_length=2)
    drop_duplicates: StrictBool = False

    @model_validator(mode="after")
    def check_columns(self) -> "MetricCheck":
        if self.op in ("sum", "mean", "min", "max", "nunique") and not self.columns:
            raise ValueError(f"'{self.op}' needs at least one column.")
        if self.op in ("nunique", "count", "count_missing") and len(self.columns) > 1:
            raise ValueError(f"'{self.op}' takes at most one column.")
        if self.op == "count_duplicates" and self.columns:
            raise ValueError("'count_duplicates' applies to whole rows, so it takes no columns.")
        return self


class TableCheck(MetricCheck):
    group_by: StrictStr
    key_column: StrictStr
    value_column: StrictStr
    time_grain: Literal["month"] | None = None


class SandboxMetric(Strict):
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    value: Number
    unit: Annotated[StrictStr, Field(max_length=30)] | None = None
    check: MetricCheck | None = None


class SandboxTable(Strict):
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    columns: Annotated[list[Annotated[StrictStr, Field(max_length=200)]], Field(min_length=1, max_length=30)]
    rows: Annotated[list[list[Cell]], Field(max_length=200)]
    total_rows: Annotated[StrictInt, Field(ge=0)]
    check: TableCheck | None = None

    @model_validator(mode="after")
    def check_shape(self) -> "SandboxTable":
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Table column names must be unique.")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every table row must match the number of columns.")
        return self


class SandboxChart(Strict):
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    kind: Literal["bar", "line", "pie", "scatter"]
    title: Annotated[StrictStr, Field(max_length=200)] | None = None
    x_label: Annotated[StrictStr, Field(max_length=200)]
    y_label: Annotated[StrictStr, Field(max_length=200)]
    x: Annotated[list[Cell], Field(max_length=500)]
    y: Annotated[list[Number | None], Field(max_length=500)]

    @model_validator(mode="after")
    def check_lengths(self) -> "SandboxChart":
        if len(self.x) != len(self.y):
            raise ValueError("A chart's x and y values must be the same length.")
        return self


class SandboxResult(Strict):
    status: Literal["ok"]
    metrics: Annotated[list[SandboxMetric], Field(max_length=30)]
    tables: Annotated[list[SandboxTable], Field(max_length=30)]
    charts: Annotated[list[SandboxChart], Field(max_length=30)]

    @model_validator(mode="after")
    def check_not_empty(self) -> "SandboxResult":
        if not self.metrics and not self.tables:
            raise ValueError("The code must report at least one metric or table.")
        return self


class SandboxFailure(Strict):
    status: Literal["error"]
    error_type: Annotated[StrictStr, Field(max_length=200)]
    message: Annotated[StrictStr, Field(max_length=2000)]
    line: StrictInt | None = None


# --- Independent recalculation ---


class Verification(BaseModel):
    target: str
    target_type: Literal["metric", "table"]
    status: Literal["verified", "mismatch", "not_checked"]
    formula: str | None = None
    detail: str | None = None


# --- Stored analyses ---

AnalysisStatus = Literal["queued", "running", "completed", "failed", "refused"]


class AttemptLog(BaseModel):
    attempt: int
    outcome: str
    code: str | None = None
    duration_seconds: float | None = None
    logs: str = ""
    problem: str | None = None


class AnalysisRecord(BaseModel):
    analysis_id: UUID
    dataset_id: UUID
    question: str
    parent_id: UUID | None = None
    status: AnalysisStatus = "queued"
    stage: str = "Waiting to start"
    created_at: datetime
    updated_at: datetime
    plan: AnalysisPlan | None = None
    code: str | None = None
    attempts: list[AttemptLog] = Field(default_factory=list)
    result: SandboxResult | None = None
    verifications: list[Verification] = Field(default_factory=list)
    insights: Insights | None = None
    removed_claims: list[str] = Field(default_factory=list)
    message: str | None = None
    ai_requests: int = 0


class AnalysisRequest(BaseModel):
    dataset_id: UUID
    question: Annotated[str, Field(min_length=3, max_length=1000)]
    parent_id: UUID | None = None


class ContainmentResult(BaseModel):
    name: str
    attack: str
    contained: bool
    evidence: str
    duration_seconds: float


class ContainmentReport(BaseModel):
    all_contained: bool
    results: list[ContainmentResult]
