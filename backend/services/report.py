"""Builds the downloadable Markdown report using only validated results."""

from backend.schemas.analysis import AnalysisRecord, SandboxTable, Verification

STATUS_LABELS = {
    "verified": "Verified ✓",
    "mismatch": "Mismatch ✗",
    "not_checked": "Not independently checked",
}
CAVEAT = "These findings show patterns in this dataset. They don't prove what caused them."


def _escape(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _table_markdown(table: SandboxTable) -> list[str]:
    lines = [
        "| " + " | ".join(_escape(column) for column in table.columns) + " |",
        "|" + " --- |" * len(table.columns),
    ]
    lines += ["| " + " | ".join(_escape(cell) for cell in row) + " |" for row in table.rows]
    if table.total_rows > len(table.rows):
        lines += ["", f"Showing the first {len(table.rows)} of {table.total_rows} rows."]
    return lines


def _status(record: AnalysisRecord, name: str, target_type: str) -> Verification | None:
    return next((v for v in record.verifications if v.target == name and v.target_type == target_type), None)


def _status_text(verification: Verification | None) -> str:
    if verification is None:
        return STATUS_LABELS["not_checked"]
    text = STATUS_LABELS[verification.status]
    return f"{text} ({verification.formula})" if verification.formula else text


def build_report(record: AnalysisRecord) -> str:
    lines = ["# DataPilot analysis report", "", f"**Question:** {_escape(record.question)}", ""]
    lines += [f"**Generated:** {record.updated_at:%Y-%m-%d %H:%M} UTC", ""]

    if record.plan:
        lines += ["## Analysis plan", "", _escape(record.plan.goal), ""]
        lines += [f"{i}. {_escape(step.description)}" for i, step in enumerate(record.plan.steps, 1)]
        lines.append("")

    if record.result:
        if record.result.metrics:
            lines += ["## Key figures", "", "| Metric | Value | Verification |", "| --- | --- | --- |"]
            for metric in record.result.metrics:
                value = f"{metric.value:,}" + (f" {metric.unit}" if metric.unit else "")
                status = _status_text(_status(record, metric.name, "metric"))
                lines.append(f"| {_escape(metric.name)} | {_escape(value)} | {_escape(status)} |")
            lines.append("")
        for table in record.result.tables:
            lines += [f"## {_escape(table.name)}", "", f"*{_escape(_status_text(_status(record, table.name, 'table')))}*", ""]
            lines += _table_markdown(table) + [""]

    if record.insights:
        if record.insights.observations:
            lines += ["## Observations", ""] + [f"- {_escape(text)}" for text in record.insights.observations] + [""]
        if record.insights.assumptions:
            lines += ["## Assumptions", ""] + [f"- {_escape(text)}" for text in record.insights.assumptions] + [""]
        if record.insights.recommendations:
            lines += ["## Recommendations", ""]
            lines += [f"- **{_escape(r.action)}** Evidence: {_escape(r.evidence)}" for r in record.insights.recommendations]
            lines.append("")
    if record.removed_claims:
        lines += [f"*{len(record.removed_claims)} statement(s) were removed because their numbers couldn't be traced to results.*", ""]

    lines += [f"> {CAVEAT}", ""]
    if record.code:
        fence = "````"
        lines += ["## Code that produced these results", "", f"{fence}python", record.code, fence, ""]
    return "\n".join(lines)
