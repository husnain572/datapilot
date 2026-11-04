"""The shapes of the dataset API's responses."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

ColumnKind = Literal["numeric", "datetime", "boolean", "categorical", "empty"]


class NumericStats(BaseModel):
    min: float | None
    max: float | None
    mean: float | None
    median: float | None
    std: float | None
    sum: float | None


class TopValue(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    kind: ColumnKind
    dtype: str
    missing_count: int
    missing_percent: float
    unique_count: int
    numeric: NumericStats | None = None
    top_values: list[TopValue] | None = None
    earliest: str | None = None
    latest: str | None = None


class DatasetProfile(BaseModel):
    row_count: int
    column_count: int
    missing_cell_count: int
    duplicate_row_count: int
    columns: list[ColumnProfile]
    sample_records: list[dict[str, Any]]


class DatasetInfo(BaseModel):
    dataset_id: UUID
    filename: str
    size_bytes: int
    created_at: datetime
    expires_at: datetime
    profile: DatasetProfile


class DatasetPreview(BaseModel):
    dataset_id: UUID
    total_rows: int
    columns: list[str]
    rows: list[dict[str, Any]]
