"""Checks that every number in the AI's written explanation came from validated results."""

import math
import re

from backend.schemas.analysis import Insights, Recommendation, SandboxResult

SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
# Dates such as 2025-03 or 2025-03-14 aren't quantities.
DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}-\d{1,2}(?:-\d{1,2})?\b")
NUMBER_PATTERN = re.compile(
    r"(?<![\w.,])[-+]?\$?(\d[\d,]*(?:\.\d+)?)(?:\s?(%|percent\b|thousand\b|million\b|billion\b|[kKmMbB]\b))?"
)
# Small whole numbers usually count things ("top 5", "three actions"), and years are labels.
SMALL_COUNT_LIMIT = 12


def allowed_numbers(result: SandboxResult) -> list[float]:
    """All the numbers the explanation may use."""
    numbers: list[float] = []
    for metric in result.metrics:
        numbers.append(float(metric.value))
    for table in result.tables:
        numbers.append(float(table.total_rows))
        for row in table.rows:
            numbers += [float(cell) for cell in row if isinstance(cell, (int, float)) and not isinstance(cell, bool)]
    return numbers


def allowed_labels(result: SandboxResult) -> list[str]:
    """Text from the results, such as product names, which can contain digits."""
    labels = {metric.name for metric in result.metrics}
    for table in result.tables:
        labels |= set(table.columns)
        for row in table.rows:
            labels |= {cell for cell in row if isinstance(cell, str)}
    return sorted((label for label in labels if any(ch.isdigit() for ch in label)), key=len, reverse=True)


def _is_rounded_form(shown: float, decimals: int, scale: float, numbers: list[float]) -> bool:
    """True if `shown` is what some allowed number looks like when rounded and scaled."""
    margin = 0.5 * 10 ** (-decimals) * (1 + 1e-9)
    return any(abs(abs(number) / scale - shown) <= margin for number in numbers)


def invented_numbers(text: str, numbers: list[float], labels: list[str], question: str = "") -> list[str]:
    """Return the numbers in `text` that don't match any validated result."""
    for label in labels:
        text = text.replace(label, " ")
    text = DATE_PATTERN.sub(" ", text)
    question_numbers = {match.group(1).replace(",", "") for match in NUMBER_PATTERN.finditer(question)}

    invented = []
    for match in NUMBER_PATTERN.finditer(text):
        digits = match.group(1).replace(",", "")
        unit = (match.group(2) or "").lower()
        shown = float(digits)
        decimals = len(digits.split(".")[1]) if "." in digits else 0

        if not unit and decimals == 0:
            if shown <= SMALL_COUNT_LIMIT or 1900 <= shown <= 2100 or digits in question_numbers:
                continue

        if unit in ("%", "percent"):
            # A share may be stored as 34.5 or as 0.345.
            valid = _is_rounded_form(shown, decimals, 1, numbers) or _is_rounded_form(shown, decimals, 0.01, numbers)
        else:
            valid = _is_rounded_form(shown, decimals, SCALES.get(unit, 1), numbers)
        if not valid:
            invented.append(match.group(0).strip())
    return invented


def _all_claims(insights: Insights) -> list[str]:
    claims = list(insights.observations) + list(insights.assumptions)
    for item in insights.recommendations:
        claims += [item.action, item.evidence]
    return claims


def find_invented(insights: Insights, result: SandboxResult, question: str = "") -> list[str]:
    """Every claim that has a number we can't trace to the results."""
    numbers, labels = allowed_numbers(result), allowed_labels(result)
    return [claim for claim in _all_claims(insights) if invented_numbers(claim, numbers, labels, question)]


def remove_invented(insights: Insights, result: SandboxResult, question: str = "") -> tuple[Insights, list[str]]:
    """Remove claims with untraceable numbers. Returns the clean version and what was removed."""
    numbers, labels = allowed_numbers(result), allowed_labels(result)

    def clean(text: str) -> bool:
        return not invented_numbers(text, numbers, labels, question)

    kept_recommendations: list[Recommendation] = [
        item for item in insights.recommendations if clean(item.action) and clean(item.evidence)
    ]
    cleaned = Insights(
        observations=[text for text in insights.observations if clean(text)],
        assumptions=[text for text in insights.assumptions if clean(text)],
        recommendations=kept_recommendations,
    )
    return cleaned, find_invented(insights, result, question)


def is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
