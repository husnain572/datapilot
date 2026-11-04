"""Runs one piece of generated code inside the sandbox and reports the results.

Input (read-only):  /input/analysis.py and /input/data.csv
Output:             one JSON document on the original standard output

The generated code gets a DataFrame called `df` and three functions for reporting
results: emit_metric, emit_table, and emit_chart. Anything it prints goes to standard
error and becomes the run's logs, so printing can't corrupt the result document.

Generated code runs in this same process, so it could tamper with these helpers.
That's acceptable: the code could report any numbers anyway, and the backend treats
everything from this script as untrusted and checks it.
"""

import json
import math
import os
import sys
import traceback
from typing import Any

import numpy as np
import pandas as pd

CODE_PATH = "/input/analysis.py"
DATA_PATH = "/input/data.csv"
MAX_ROWS = 200
MAX_COLUMNS = 30
MAX_ITEMS = 30
MAX_CHART_POINTS = 500
MAX_ERROR_LENGTH = 1500
CHART_KINDS = {"bar", "line", "pie", "scatter"}


def _plain(value: Any) -> Any:
    """Convert a value from pandas or NumPy into something JSON can represent."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (bool, int, float, str)):
        return value[:500] if isinstance(value, str) else value
    return str(value)[:500]


def _table(frame: Any) -> pd.DataFrame:
    if isinstance(frame, pd.Series):
        frame = frame.to_frame()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Expected a pandas DataFrame or Series.")
    if not isinstance(frame.index, pd.RangeIndex):
        frame = frame.reset_index()
    return frame


class Results:
    def __init__(self) -> None:
        self.metrics: list[dict] = []
        self.tables: list[dict] = []
        self.charts: list[dict] = []

    def emit_metric(self, name: str, value: Any, *, unit: str | None = None, check: dict | None = None) -> None:
        """Report one number. `check` tells the backend how to recalculate it."""
        plain = _plain(value)
        if isinstance(plain, bool) or not isinstance(plain, (int, float)):
            raise TypeError(f"Metric '{name}' must be a finite number, not {plain!r}.")
        self._add(self.metrics, {"name": str(name), "value": plain, "unit": unit, "check": check})

    def emit_table(self, name: str, table: Any, *, check: dict | None = None) -> None:
        """Report a table. `check` tells the backend how to recalculate its rows."""
        frame = _table(table)
        columns = [str(column) for column in frame.columns][:MAX_COLUMNS]
        rows = [[_plain(value) for value in row[:MAX_COLUMNS]] for row in frame.head(MAX_ROWS).values.tolist()]
        self._add(
            self.tables,
            {
                "name": str(name),
                "columns": columns,
                "rows": rows,
                "total_rows": len(frame),
                "check": check,
            },
        )

    def emit_chart(
        self, name: str, kind: str, data: Any, x: str, y: str, *, title: str | None = None
    ) -> None:
        """Describe a chart as data. The frontend draws it, so no code leaves the sandbox."""
        if kind not in CHART_KINDS:
            raise ValueError(f"Chart kind must be one of {sorted(CHART_KINDS)}.")
        frame = _table(data)
        for column in (x, y):
            if column not in frame.columns:
                raise KeyError(f"Chart '{name}' has no column '{column}'.")
        points = frame.head(MAX_CHART_POINTS)
        self._add(
            self.charts,
            {
                "name": str(name),
                "kind": kind,
                "title": title,
                "x_label": str(x),
                "y_label": str(y),
                "x": [_plain(value) for value in points[x].tolist()],
                "y": [_plain(value) for value in points[y].tolist()],
            },
        )

    @staticmethod
    def _add(items: list[dict], item: dict) -> None:
        if len(items) >= MAX_ITEMS:
            raise ValueError(f"At most {MAX_ITEMS} results of each type can be reported.")
        items.append(item)


def _describe_error(error: BaseException) -> dict:
    """Keep only the generated code's own lines, so internal paths stay out of the report."""
    frames = traceback.extract_tb(error.__traceback__)
    lines = [frame.lineno for frame in frames if frame.filename == "analysis.py"]
    return {
        "status": "error",
        "error_type": type(error).__name__,
        "message": str(error)[:MAX_ERROR_LENGTH],
        "line": lines[-1] if lines else None,
    }


def main() -> None:
    # Keep the real output for ourselves, and send everything else to the logs.
    result_stream = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    results = Results()
    try:
        with open(CODE_PATH, encoding="utf-8") as file:
            code = compile(file.read(), "analysis.py", "exec")
        namespace = {
            "__name__": "__main__",
            "df": pd.read_csv(DATA_PATH, low_memory=False),
            "pd": pd,
            "np": np,
            "emit_metric": results.emit_metric,
            "emit_table": results.emit_table,
            "emit_chart": results.emit_chart,
        }
        exec(code, namespace)  # noqa: S102 - this file exists to run untrusted code in a sandbox.
        document = {
            "status": "ok",
            "metrics": results.metrics,
            "tables": results.tables,
            "charts": results.charts,
        }
    except BaseException as error:  # noqa: BLE001 - including SystemExit from generated code.
        document = _describe_error(error)

    json.dump(document, result_stream, allow_nan=False)
    result_stream.flush()
    # Skip normal shutdown, so generated code's leftover threads can't change the result.
    os._exit(0)


if __name__ == "__main__":
    main()
