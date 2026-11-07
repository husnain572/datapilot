"""Summaries for the dataset dashboard, calculated directly from the data with pandas.

No AI is involved, so these charts appear instantly and use no AI requests.
"""

import math

import numpy as np
import pandas as pd
from pydantic import BaseModel

from backend.schemas.datasets import DatasetProfile

MAX_NUMERIC = 6
MAX_CATEGORY_COLUMNS = 6
MAX_CATEGORY_LABELS = 10
MAX_CATEGORY_UNIQUE = 100
HISTOGRAM_BINS = 12
# Whole-number columns with this many distinct values or fewer get one bar per value.
MAX_DISCRETE_VALUES = 30
ROW_COUNT = "Number of rows"


class Series(BaseModel):
    labels: list[str]
    values: list[float]


class Histogram(BaseModel):
    labels: list[str]
    counts: list[int]


class DatasetCharts(BaseModel):
    date_column: str | None
    # Measure name -> values for each month.
    trend: dict[str, Series]
    # Column -> measure name -> top groups.
    categories: dict[str, dict[str, Series]]
    histograms: dict[str, Histogram]
    missing: Series


def _finite(value: float) -> float:
    return float(value) if math.isfinite(value) else 0.0


def _short(number: float) -> str:
    if abs(number) >= 1e6:
        return f"{number / 1e6:.1f}M"
    if abs(number) >= 1e3:
        return f"{number / 1e3:.1f}k"
    return f"{number:.4g}"


def _series(values: pd.Series) -> Series:
    return Series(labels=[str(label) for label in values.index], values=[_finite(v) for v in values.to_numpy()])


def build_dashboard(df: pd.DataFrame, profile: DatasetProfile) -> DatasetCharts:
    numeric = [c.name for c in profile.columns if c.kind == "numeric"][:MAX_NUMERIC]
    numbers = {name: pd.to_numeric(df[name], errors="coerce") for name in numeric}
    measures = {name: f"Total {name}" for name in numeric}

    date_column = next((c.name for c in profile.columns if c.kind == "datetime"), None)
    trend: dict[str, Series] = {}
    if date_column:
        dates = pd.to_datetime(df[date_column].astype("object"), errors="coerce", format="mixed")
        months = dates.dt.to_period("M").astype("object").where(dates.notna())
        valid = months.notna()
        trend[ROW_COUNT] = _series(months[valid].astype(str).value_counts().sort_index())
        for name, values in numbers.items():
            trend[measures[name]] = _series(values[valid].groupby(months[valid].astype(str)).sum().sort_index())

    categories: dict[str, dict[str, Series]] = {}
    category_columns = [
        c.name for c in profile.columns if c.kind == "categorical" and 1 < c.unique_count <= MAX_CATEGORY_UNIQUE
    ][:MAX_CATEGORY_COLUMNS]
    for column in category_columns:
        keys = df[column].astype("object").where(df[column].notna()).dropna().astype(str)
        groups = {ROW_COUNT: _series(keys.value_counts().head(MAX_CATEGORY_LABELS))}
        for name, values in numbers.items():
            totals = values.loc[keys.index].groupby(keys).sum().sort_values(ascending=False)
            groups[measures[name]] = _series(totals.head(MAX_CATEGORY_LABELS))
        categories[column] = groups

    histograms: dict[str, Histogram] = {}
    for name, values in numbers.items():
        finite = values[np.isfinite(values)].to_numpy(dtype=float)
        if finite.size == 0:
            continue
        distinct = np.unique(finite)
        if np.all(distinct == np.round(distinct)) and distinct.size <= MAX_DISCRETE_VALUES:
            # For example, quantities 1 to 8: one bar per value reads better than ranges.
            values_counted = pd.Series(finite).value_counts().sort_index()
            labels = [f"{value:g}" for value in values_counted.index]
            histograms[name] = Histogram(labels=labels, counts=[int(count) for count in values_counted.to_numpy()])
            continue
        counts, edges = np.histogram(finite, bins=HISTOGRAM_BINS)
        labels = [f"{_short(edges[i])} – {_short(edges[i + 1])}" for i in range(len(counts))]
        histograms[name] = Histogram(labels=labels, counts=[int(count) for count in counts])

    missing = sorted(((c.name, c.missing_count) for c in profile.columns), key=lambda item: item[1], reverse=True)
    return DatasetCharts(
        date_column=date_column,
        trend=trend,
        categories=categories,
        histograms=histograms,
        missing=Series(labels=[name for name, _ in missing], values=[float(count) for _, count in missing]),
    )
