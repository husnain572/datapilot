"""Recalculates reported numbers with the backend's own code, on its own copy of the data.

The sandbox's answers are compared against these calculations. A "verified" result means
the number really equals its stated formula on the uploaded data. Whether it was the right
formula for the question is a separate matter, which is why the formula is always shown.
"""

import math

import pandas as pd

from backend.schemas.analysis import MetricCheck, SandboxResult, SandboxTable, TableCheck, Verification

RELATIVE_TOLERANCE = 1e-6
ABSOLUTE_TOLERANCE = 1e-6


def _matches(reported: float, expected: float) -> bool:
    return math.isclose(reported, expected, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)


def _formula(check: MetricCheck) -> str:
    target = " × ".join(check.columns) or "all rows"
    text = f"{check.op}({target})"
    if isinstance(check, TableCheck):
        text += f" for each {check.group_by}"
        if check.time_grain:
            text += f" by {check.time_grain}"
    if check.drop_duplicates:
        text += ", without duplicate rows"
    return text


def _values(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    """One column's numbers, or the product of two. Rows with a blank value stay blank."""
    series = pd.to_numeric(df[columns[0]], errors="coerce")
    if len(columns) == 2:
        series = series * pd.to_numeric(df[columns[1]], errors="coerce")
    return series


def _compute(df: pd.DataFrame, check: MetricCheck) -> float:
    if check.op == "count_duplicates":
        return float(df.duplicated().sum())
    if check.op == "count_missing":
        data = df[check.columns[0]] if check.columns else df
        return float(data.isna().sum().sum() if not check.columns else data.isna().sum())
    if check.op == "count":
        return float(df[check.columns].notna().all(axis=1).sum() if check.columns else len(df))
    if check.op == "nunique":
        return float(df[check.columns[0]].nunique(dropna=True))
    return float(getattr(_values(df, check.columns), check.op)())


def _base(df: pd.DataFrame, check: MetricCheck) -> pd.DataFrame:
    missing = [c for c in check.columns if c not in df.columns]
    if missing:
        raise KeyError(f"No such column: {missing[0]}")
    return df.drop_duplicates() if check.drop_duplicates else df


def verify_metric(df: pd.DataFrame, name: str, value: float, check: MetricCheck | None) -> Verification:
    if check is None:
        return Verification(target=name, target_type="metric", status="not_checked")
    formula = _formula(check)
    try:
        expected = _compute(_base(df, check), check)
    except (KeyError, TypeError, ValueError) as error:
        return Verification(
            target=name, target_type="metric", status="mismatch", formula=formula, detail=f"Couldn't recalculate: {error}"
        )
    if math.isfinite(expected) and _matches(value, expected):
        return Verification(target=name, target_type="metric", status="verified", formula=formula)
    return Verification(
        target=name,
        target_type="metric",
        status="mismatch",
        formula=formula,
        detail=f"Reported {value}, but recalculated {expected}.",
    )


def _group_key(values: pd.Series, grain: str | None) -> pd.Series:
    """Text keys for grouping. Blank keys stay blank, and so are left out of groups."""
    if grain == "month":
        parsed = pd.to_datetime(values.astype("object"), errors="coerce", format="mixed")
        return parsed.dt.to_period("M").astype("object").where(parsed.notna()).map(
            lambda period: str(period) if pd.notna(period) else None
        )
    return values.map(lambda value: str(value) if pd.notna(value) else None)


def _expected_groups(df: pd.DataFrame, check: TableCheck) -> dict[str, float]:
    if check.group_by not in df.columns:
        raise KeyError(f"No such column: {check.group_by}")
    keys = _group_key(df[check.group_by], check.time_grain)
    expected: dict[str, float] = {}
    for key in keys.dropna().unique():
        expected[str(key)] = _compute(df[keys == key], check)
    return expected


def verify_table(df: pd.DataFrame, table: SandboxTable) -> Verification:
    check = table.check
    if check is None:
        return Verification(target=table.name, target_type="table", status="not_checked")
    formula = _formula(check)

    def mismatch(detail: str) -> Verification:
        return Verification(target=table.name, target_type="table", status="mismatch", formula=formula, detail=detail)

    if check.key_column not in table.columns or check.value_column not in table.columns:
        return mismatch("The check names a column that isn't in the table.")
    if not table.rows:
        return mismatch("The table has no rows to check.")

    try:
        expected = _expected_groups(_base(df, check), check)
    except (KeyError, TypeError, ValueError) as error:
        return mismatch(f"Couldn't recalculate: {error}")

    key_index = table.columns.index(check.key_column)
    value_index = table.columns.index(check.value_column)
    frame = pd.Series([row[key_index] for row in table.rows], dtype="object")
    reported_keys = _group_key(frame, check.time_grain)

    for position, row in enumerate(table.rows):
        key = reported_keys.iloc[position]
        value = row[value_index]
        if key is None or key not in expected:
            return mismatch(f"Row {position + 1}: '{row[key_index]}' isn't a group in the data.")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return mismatch(f"Row {position + 1}: the value isn't a number.")
        if not _matches(value, expected[key]):
            return mismatch(f"'{key}': reported {value}, but recalculated {expected[key]}.")
    return Verification(target=table.name, target_type="table", status="verified", formula=formula)


def verify_result(df: pd.DataFrame, result: SandboxResult) -> list[Verification]:
    checks = [verify_metric(df, m.name, float(m.value), m.check) for m in result.metrics]
    return checks + [verify_table(df, table) for table in result.tables]
