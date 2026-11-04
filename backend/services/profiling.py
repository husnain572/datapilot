"""Works out what's in a dataset: its size, column types, gaps, and summaries."""

import io
import math
from typing import Any

import pandas as pd

from backend.schemas.datasets import ColumnProfile, DatasetProfile, NumericStats, TopValue

SAMPLE_RECORD_COUNT = 5
TOP_VALUE_COUNT = 5
# At least this share of a text column's values must parse as dates to call it a date column.
DATE_MATCH_THRESHOLD = 0.9


def load_dataframe(csv_text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv_text), low_memory=False)


def json_safe(value: Any) -> Any:
    """Convert a value from pandas into something JSON can represent."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item"):  # NumPy numbers become plain Python numbers.
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None  # JSON has no NaN or infinity.
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Turn rows into JSON-safe dictionaries."""
    return [
        {name: json_safe(value) for name, value in row.items()}
        for row in df.to_dict(orient="records")
    ]


def _parse_dates(series: pd.Series) -> pd.Series | None:
    """Return the column as dates if it really holds dates, otherwise None."""
    values = series.dropna().astype(str)
    if values.empty or not values.str.contains(r"\d").all():
        return None
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    if parsed.notna().mean() < DATE_MATCH_THRESHOLD:
        return None
    return parsed


def _numeric_stats(series: pd.Series) -> NumericStats:
    finite = pd.to_numeric(series, errors="coerce").dropna()
    finite = finite[finite.abs() != math.inf]
    if finite.empty:
        return NumericStats(min=None, max=None, mean=None, median=None, std=None, sum=None)
    return NumericStats(
        min=json_safe(finite.min()),
        max=json_safe(finite.max()),
        mean=json_safe(finite.mean()),
        median=json_safe(finite.median()),
        std=json_safe(finite.std()),  # Blank when there's only one value.
        sum=json_safe(finite.sum()),
    )


def _top_values(series: pd.Series) -> list[TopValue]:
    counts = series.dropna().astype(str).value_counts().head(TOP_VALUE_COUNT)
    return [TopValue(value=value[:200], count=int(count)) for value, count in counts.items()]


def _profile_column(name: str, series: pd.Series) -> ColumnProfile:
    row_count = len(series)
    missing = int(series.isna().sum())
    fields: dict[str, Any] = {
        "name": name,
        "dtype": str(series.dtype),
        "missing_count": missing,
        "missing_percent": round(missing / row_count * 100, 2),
        "unique_count": int(series.nunique(dropna=True)),
    }

    if missing == row_count:
        return ColumnProfile(kind="empty", **fields)
    if pd.api.types.is_bool_dtype(series):
        return ColumnProfile(kind="boolean", top_values=_top_values(series), **fields)
    if pd.api.types.is_numeric_dtype(series):
        return ColumnProfile(kind="numeric", numeric=_numeric_stats(series), **fields)

    dates = _parse_dates(series)
    if dates is not None:
        return ColumnProfile(
            kind="datetime",
            earliest=dates.min().isoformat(),
            latest=dates.max().isoformat(),
            **fields,
        )
    return ColumnProfile(kind="categorical", top_values=_top_values(series), **fields)


def profile_dataframe(df: pd.DataFrame) -> DatasetProfile:
    return DatasetProfile(
        row_count=len(df),
        column_count=len(df.columns),
        missing_cell_count=int(df.isna().sum().sum()),
        duplicate_row_count=int(df.duplicated().sum()),
        columns=[_profile_column(name, df[name]) for name in df.columns],
        sample_records=records(df.head(SAMPLE_RECORD_COUNT)),
    )
