"""Tests the checks on generated code, sandbox output, recalculation, and written claims."""

import pandas as pd
import pytest
from pydantic import ValidationError

from backend.schemas.analysis import Insights, MetricCheck, Recommendation, SandboxResult, SandboxTable, TableCheck
from backend.services.code_safety import find_problems
from backend.services.number_guard import find_invented, invented_numbers, remove_invented
from backend.services.verification import verify_metric, verify_result, verify_table

# Hand-checked: revenue is 2*10=20, 3*10=30, 1*5=5, and a blank quantity adds nothing.
# Totals: 55 overall. Pen 50, Book 5. January 20, February 35. 1 blank cell, 1 duplicate.
DF = pd.DataFrame(
    {
        "order_date": ["2025-01-15", "2025-02-01", "2025-02-20", "2025-03-01", "2025-03-01"],
        "product": ["Pen", "Pen", "Book", "Lamp", "Lamp"],
        "quantity": [2, 3, 1, None, None],
        "unit_price": [10.0, 10.0, 5.0, 7.0, 7.0],
    }
)
REVENUE = ["quantity", "unit_price"]


def result(**values) -> SandboxResult:
    return SandboxResult(status="ok", **{"metrics": [], "tables": [], "charts": [], **values})


def metric(value=1):
    return {"name": "m", "value": value}


def table_of(rows, check=None, columns=("product", "revenue")) -> SandboxTable:
    return SandboxTable(name="t", columns=list(columns), rows=rows, total_rows=len(rows), check=check)


# --- Quick check on generated code ---


@pytest.mark.parametrize(
    "code",
    [
        "x = df['a'].sum()\nemit_metric('x', x)",
        "import pandas as pd\nimport numpy as np\nfrom collections import Counter",
        "import datetime\nfrom math import floor",
    ],
)
def test_normal_analysis_code_is_allowed(code):
    assert find_problems(code) == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("import os", "'os'"),
        ("import subprocess", "'subprocess'"),
        ("from socket import socket", "'socket'"),
        ("import os.path", "'os.path'"),
        ("open('/etc/passwd')", "'open'"),
        ("eval('1')", "'eval'"),
        ("exec('x=1')", "'exec'"),
        ("__import__('os')", "'__import__'"),
        ("().__class__.__bases__", "__class__"),
        ("getattr(df, 'x')", "'getattr'"),
        ("pd.read_csv('/etc/passwd')", "read_csv"),
        ("df.to_csv('/tmp/x')", "to_csv"),
        ("def broken(:", "syntax error"),
        ("x = 1\0", "syntax error"),
    ],
)
def test_risky_or_broken_code_is_flagged(code, expected):
    assert any(expected in problem for problem in find_problems(code))


# --- Validating sandbox output ---


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "ok", "metrics": [], "tables": [], "charts": []},
        {"status": "ok", "metrics": [metric()], "tables": [], "charts": [], "secret": 1},
        {"status": "ok", "metrics": [metric("12")], "tables": [], "charts": []},
        {"status": "ok", "metrics": [metric(True)], "tables": [], "charts": []},
        {"status": "ok", "metrics": [metric(float("nan"))], "tables": [], "charts": []},
        {"status": "ok", "metrics": [metric(float("inf"))], "tables": [], "charts": []},
        {"status": "ok", "metrics": [{"name": "", "value": 1}], "tables": [], "charts": []},
        {"status": "ok", "metrics": [metric()] * 31, "tables": [], "charts": []},
        {"status": "ok", "metrics": [{**metric(), "check": {"op": "drop_tables"}}], "tables": [], "charts": []},
        {"status": "ok", "metrics": [{**metric(), "check": {"op": "sum", "columns": []}}], "tables": [], "charts": []},
        {
            "status": "ok",
            "metrics": [],
            "tables": [{"name": "t", "columns": ["a", "a"], "rows": [], "total_rows": 0}],
            "charts": [],
        },
        {
            "status": "ok",
            "metrics": [],
            "tables": [{"name": "t", "columns": ["a"], "rows": [[1, 2]], "total_rows": 1}],
            "charts": [],
        },
        {
            "status": "ok",
            "metrics": [metric()],
            "tables": [],
            "charts": [{"name": "c", "kind": "html", "x_label": "x", "y_label": "y", "x": [], "y": []}],
        },
        {
            "status": "ok",
            "metrics": [metric()],
            "tables": [],
            "charts": [{"name": "c", "kind": "bar", "x_label": "x", "y_label": "y", "x": [1, 2], "y": [1]}],
        },
    ],
    ids=[
        "empty",
        "unexpected-field",
        "text-number",
        "boolean-number",
        "nan",
        "infinity",
        "blank-name",
        "too-many",
        "unknown-check",
        "check-without-columns",
        "duplicate-columns",
        "uneven-row",
        "unknown-chart-kind",
        "uneven-chart",
    ],
)
def test_invalid_sandbox_output_is_rejected(bad):
    with pytest.raises(ValidationError):
        SandboxResult.model_validate(bad)


# --- Recalculating metrics ---


@pytest.mark.parametrize(
    ("check", "expected"),
    [
        (MetricCheck(op="sum", columns=REVENUE), 55),
        (MetricCheck(op="sum", columns=["quantity"]), 6),
        (MetricCheck(op="mean", columns=["quantity"]), 2),
        (MetricCheck(op="min", columns=["unit_price"]), 5),
        (MetricCheck(op="max", columns=["unit_price"]), 10),
        (MetricCheck(op="count"), 5),
        (MetricCheck(op="count", columns=["quantity"]), 3),
        (MetricCheck(op="nunique", columns=["product"]), 3),
        (MetricCheck(op="count_missing"), 2),
        (MetricCheck(op="count_missing", columns=["quantity"]), 2),
        (MetricCheck(op="count_duplicates"), 1),
        (MetricCheck(op="count", drop_duplicates=True), 4),
    ],
    ids=lambda value: value.op + "-" + "-".join(value.columns) if isinstance(value, MetricCheck) else str(value),
)
def test_metric_recalculation_matches_hand_calculation(check, expected):
    assert verify_metric(DF, "m", expected, check).status == "verified"


def test_a_wrong_number_is_caught():
    outcome = verify_metric(DF, "Total revenue", 56, MetricCheck(op="sum", columns=REVENUE))

    assert outcome.status == "mismatch"
    assert "recalculated 55" in outcome.detail


def test_tiny_rounding_differences_are_allowed():
    assert verify_metric(DF, "m", 55.00000001, MetricCheck(op="sum", columns=REVENUE)).status == "verified"


def test_a_small_real_difference_is_caught():
    assert verify_metric(DF, "m", 55.01, MetricCheck(op="sum", columns=REVENUE)).status == "mismatch"


def test_check_naming_a_missing_column_is_a_mismatch():
    assert verify_metric(DF, "m", 1, MetricCheck(op="sum", columns=["invented"])).status == "mismatch"


def test_text_column_cannot_pass_as_a_number():
    assert verify_metric(DF, "m", 55, MetricCheck(op="sum", columns=["product"])).status == "mismatch"


def test_metric_without_a_check_is_labelled_not_checked():
    assert verify_metric(DF, "m", 1, None).status == "not_checked"


def test_formula_is_shown_for_people_to_read():
    assert verify_metric(DF, "m", 55, MetricCheck(op="sum", columns=REVENUE)).formula == "sum(quantity × unit_price)"


# --- Recalculating tables ---

BY_PRODUCT = TableCheck(op="sum", columns=REVENUE, group_by="product", key_column="product", value_column="revenue")
BY_MONTH = TableCheck(
    op="sum", columns=REVENUE, group_by="order_date", key_column="month", value_column="revenue", time_grain="month"
)


def test_correct_group_totals_are_verified():
    assert verify_table(DF, table_of([["Pen", 50], ["Book", 5]], BY_PRODUCT)).status == "verified"


def test_top_n_tables_can_show_only_some_groups():
    assert verify_table(DF, table_of([["Pen", 50]], BY_PRODUCT)).status == "verified"


def test_one_wrong_row_fails_the_whole_table():
    outcome = verify_table(DF, table_of([["Pen", 50], ["Book", 6]], BY_PRODUCT))

    assert outcome.status == "mismatch"
    assert "'Book'" in outcome.detail


def test_a_made_up_group_is_caught():
    outcome = verify_table(DF, table_of([["Hat", 0]], BY_PRODUCT))

    assert outcome.status == "mismatch"
    assert "isn't a group" in outcome.detail


@pytest.mark.parametrize("label", ["2025-02", "2025-02-01", "2025-02-01 00:00:00"])
def test_monthly_totals_are_verified_with_different_date_formats(label):
    table = table_of([[label, 35]], BY_MONTH, columns=("month", "revenue"))

    assert verify_table(DF, table).status == "verified"


def test_wrong_monthly_total_is_caught():
    table = table_of([["2025-01", 21]], BY_MONTH, columns=("month", "revenue"))

    assert verify_table(DF, table).status == "mismatch"


def test_check_naming_a_column_not_in_the_table_is_a_mismatch():
    assert verify_table(DF, table_of([["Pen", 50]], BY_PRODUCT, columns=("item", "total"))).status == "mismatch"


def test_table_value_that_is_not_a_number_is_a_mismatch():
    assert verify_table(DF, table_of([["Pen", "fifty"]], BY_PRODUCT)).status == "mismatch"


def test_verify_result_covers_everything():
    outcome = verify_result(
        DF,
        result(
            metrics=[{"name": "Total", "value": 55, "check": {"op": "sum", "columns": REVENUE}}, metric()],
            tables=[table_of([["Pen", 50]], BY_PRODUCT).model_dump()],
        ),
    )

    assert [v.status for v in outcome] == ["verified", "not_checked", "verified"]


# --- Number check on written claims ---

RESULT = result(
    metrics=[
        {"name": "Total revenue", "value": 1234567.891},
        {"name": "Share", "value": 0.3456},
        {"name": "Growth pct", "value": 12.5},
    ],
    tables=[
        table_of([["USB-C 3000 Cable", 987.5], ["Pen", 50]]).model_dump(),
    ],
)
NUMBERS = [1234567.891, 0.3456, 12.5, 987.5, 50.0, 2.0]
LABELS = ["USB-C 3000 Cable"]


@pytest.mark.parametrize(
    "text",
    [
        "Total revenue was 1,234,567.89.",
        "Total revenue was $1,234,567.891.",
        "Revenue reached $1,234,568.",
        "Revenue reached $1.23 million.",
        "Revenue reached 1.2M.",
        "Its share was 34.56%.",
        "Its share was 35%.",
        "Its share was 34.6%.",
        "Total revenue was 1,234,567.89, and Pen earned 987.50.",
        "Growth was 12.5%.",
        "The USB-C 3000 Cable earned 987.5.",
        "In 2025-03, sales rose.",
        "In March 2025, sales rose.",
        "Consider the top 5 products and 3 actions.",
        "No numbers at all.",
    ],
)
def test_numbers_from_results_are_accepted(text):
    assert invented_numbers(text, NUMBERS, LABELS) == []


@pytest.mark.parametrize(
    "text",
    [
        "Total revenue was 1,300,000.",
        "Revenue reached $2.5 million.",
        "Growth was 18%.",
        "Sales rose by 47.",
        "The average order was $123.45.",
        "Returns cost 5.5 units.",
    ],
)
def test_invented_numbers_are_caught(text):
    assert invented_numbers(text, NUMBERS, LABELS) != []


def test_numbers_quoted_from_the_question_are_accepted():
    assert invented_numbers("You asked about the top 100.", NUMBERS, LABELS, "Show the top 100") == []


def test_claims_with_invented_numbers_are_removed_and_counted():
    insights = Insights(
        observations=["Total revenue was 1,234,567.89.", "Revenue will reach 9,999,999 next year."],
        assumptions=["Blank values were skipped."],
        recommendations=[
            Recommendation(action="Stock more of the top seller.", evidence="Pen earned 50."),
            Recommendation(action="Expect 777 more sales.", evidence="Pen earned 50."),
        ],
    )

    cleaned, removed = remove_invented(insights, RESULT)

    assert cleaned.observations == ["Total revenue was 1,234,567.89."]
    assert cleaned.assumptions == ["Blank values were skipped."]
    assert [r.action for r in cleaned.recommendations] == ["Stock more of the top seller."]
    assert len(removed) == 2
    assert find_invented(cleaned, RESULT) == []
