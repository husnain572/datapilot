import csv
import io
import os
import re
import time
from pathlib import Path

import httpx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

# Streamlit doesn't read .env by itself. Real environment variables take priority.
load_dotenv()
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
DATASETS_URL = f"{BACKEND_URL}/api/v1/datasets"
ANALYSES_URL = f"{BACKEND_URL}/api/v1/analyses"
LAB_URL = f"{BACKEND_URL}/api/v1/containment-tests"
SAMPLE_CSV = Path(__file__).parents[1] / "sample_data" / "sales.csv"
UPLOAD_TIMEOUT_SECONDS = 120
LAB_TIMEOUT_SECONDS = 150
POLL_SECONDS = 1.5
MAX_WAIT_SECONDS = 600
SERIES_COLOR = "#2a78d6"
DEMO_QUESTION = (
    "Analyze sales performance. Calculate total revenue, identify the top five products by revenue, "
    "compare monthly revenue, detect missing values, and recommend three data-backed actions."
)
CAVEAT = "These findings show patterns in this dataset. They don't prove what caused them."
VERIFICATION_LABELS = {
    "verified": "✅ Verified",
    "mismatch": "❌ Mismatch",
    "not_checked": "➖ Not independently checked",
}


def plain(text: object) -> str:
    """Stop Markdown in untrusted text (AI answers, file contents) from becoming links or images."""
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~$:])", r"\\\1", str(text))


def safe_csv(columns: list[str], rows: list[list]) -> str:
    """Build a CSV. Cells that a spreadsheet would run as formulas get a leading apostrophe."""

    def cell(value: object) -> object:
        return f"'{value}" if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([cell(column) for column in columns])
    writer.writerows([[cell(value) for value in row] for row in rows])
    return output.getvalue()


def backend_is_healthy() -> bool:
    """Ask the backend whether it is running. Any failure counts as 'no'."""
    try:
        return httpx.get(f"{BACKEND_URL}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def error_detail(response: httpx.Response, fallback: str) -> str:
    try:
        detail = response.json().get("detail", fallback)
    except ValueError:
        return fallback
    return detail if isinstance(detail, str) else fallback


def access_headers() -> dict[str, str]:
    return {"X-Access-Code": st.session_state.get("access_code", "")}


def upload_dataset(filename: str, content: bytes) -> None:
    """Send a file to the backend, and remember the result or show why it failed."""
    try:
        response = httpx.post(
            DATASETS_URL,
            files={"file": (filename, content, "text/csv")},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        st.session_state.upload_error = "Couldn't reach the backend. Please try again."
        return

    if response.status_code == 201:
        st.session_state.dataset = response.json()
        st.session_state.upload_error = None
        st.session_state.history = []
    else:
        st.session_state.upload_error = error_detail(response, "The upload failed.")


def remove_dataset() -> None:
    dataset_id = st.session_state.dataset["dataset_id"]
    try:
        httpx.delete(f"{DATASETS_URL}/{dataset_id}", timeout=10)
    except httpx.HTTPError:
        pass  # It expires by itself if the backend can't be reached right now.
    st.session_state.dataset = None
    st.session_state.history = []
    st.session_state.viewing = None


def load_preview(dataset_id: str) -> dict | None:
    try:
        response = httpx.get(f"{DATASETS_URL}/{dataset_id}/preview", timeout=30)
    except httpx.HTTPError:
        return None
    return response.json() if response.status_code == 200 else None


def show_landing() -> None:
    st.markdown(
        "Upload a CSV and ask questions in plain English. AI writes the analysis code, "
        "Python runs it in a locked-down sandbox, and DataPilot shows only numbers it has checked."
    )
    first, second, third = st.columns(3)
    first.markdown("**1. Upload**  \nWe check your file and profile it automatically.")
    second.markdown("**2. Ask**  \nAI plans the work and writes code. It doesn't supply the numbers.")
    third.markdown("**3. Verify**  \nCode runs with no network or secrets, then key figures are recalculated.")


def show_upload_section() -> None:
    uploaded = st.file_uploader("Upload a CSV file", type="csv")

    # Streamlit reruns this script on every click, so only send each file once.
    if uploaded is not None and uploaded.file_id != st.session_state.get("last_file_id"):
        st.session_state.last_file_id = uploaded.file_id
        with st.spinner("Checking your file..."):
            upload_dataset(uploaded.name, uploaded.getvalue())

    if st.button("Try the sample sales dataset"):
        with st.spinner("Loading the sample..."):
            upload_dataset(SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())

    # The page was drawn before the upload finished, so redraw it to show the dashboard.
    if st.session_state.get("dataset"):
        st.rerun()

    if st.session_state.get("upload_error"):
        st.error(st.session_state.upload_error)


def columns_table(columns: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Column": [c["name"] for c in columns],
            "Type": [c["kind"] for c in columns],
            "Missing": [c["missing_count"] for c in columns],
            "Missing %": [c["missing_percent"] for c in columns],
            "Unique values": [c["unique_count"] for c in columns],
        }
    )


def show_numeric_columns(columns: list[dict]) -> None:
    numeric = [c for c in columns if c["kind"] == "numeric"]
    if not numeric:
        st.caption("This dataset has no numeric columns.")
        return
    table = pd.DataFrame([{"Column": c["name"], **c["numeric"]} for c in numeric])
    st.dataframe(table, hide_index=True, width="stretch")


def show_category_columns(columns: list[dict]) -> None:
    categories = [c for c in columns if c.get("top_values")]
    if not categories:
        st.caption("This dataset has no category columns.")
        return
    for column in categories:
        st.markdown(f"**{plain(column['name'])}**: most common values")
        counts = pd.DataFrame(column["top_values"]).rename(columns={"value": "Value", "count": "Count"})
        st.dataframe(counts, hide_index=True, width="stretch")


def show_dataset(dataset: dict) -> None:
    profile = dataset["profile"]

    header, action = st.columns([4, 1])
    header.subheader(plain(dataset["filename"]))
    action.button("Remove dataset", on_click=remove_dataset)

    rows, columns, missing, duplicates = st.columns(4)
    rows.metric("Rows", f"{profile['row_count']:,}")
    columns.metric("Columns", f"{profile['column_count']:,}")
    missing.metric("Missing values", f"{profile['missing_cell_count']:,}")
    duplicates.metric("Duplicate rows", f"{profile['duplicate_row_count']:,}")

    with st.expander("Dataset details"):
        overview, numbers, categories, preview = st.tabs(
            ["Columns", "Number statistics", "Categories", "Preview"]
        )
        with overview:
            st.dataframe(columns_table(profile["columns"]), hide_index=True, width="stretch")
        with numbers:
            show_numeric_columns(profile["columns"])
        with categories:
            show_category_columns(profile["columns"])
        with preview:
            data = load_preview(dataset["dataset_id"])
            if data is None:
                st.warning("Couldn't load the preview.")
            else:
                st.caption(f"First {len(data['rows'])} of {data['total_rows']:,} rows")
                st.dataframe(pd.DataFrame(data["rows"], columns=data["columns"]), hide_index=True)


def start_analysis(dataset_id: str, question: str, parent_id: str | None) -> str | None:
    """Ask the backend to start. Returns the analysis ID, or shows why it couldn't."""
    body = {"dataset_id": dataset_id, "question": question, "parent_id": parent_id}
    try:
        response = httpx.post(ANALYSES_URL, json=body, headers=access_headers(), timeout=30)
    except httpx.HTTPError:
        st.error("Couldn't reach the backend. Please try again.")
        return None
    if response.status_code != 202:
        st.error(error_detail(response, "The analysis couldn't start."))
        return None
    return response.json()["analysis_id"]


def load_analysis(analysis_id: str) -> dict | None:
    try:
        response = httpx.get(f"{ANALYSES_URL}/{analysis_id}", timeout=15)
    except httpx.HTTPError:
        return None
    return response.json() if response.status_code == 200 else None


def wait_for_analysis(analysis_id: str) -> None:
    """Show live progress until the analysis finishes, then remember it."""
    with st.status("Starting...", expanded=True) as progress:
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        record = None
        while time.monotonic() < deadline:
            record = load_analysis(analysis_id)
            if record is None:
                progress.update(label="Waiting for the server...")
            else:
                progress.update(label=plain(record["stage"]))
                if record["status"] in ("completed", "failed", "refused"):
                    break
            time.sleep(POLL_SECONDS)
        finished = record is not None and record["status"] in ("completed", "failed", "refused")
        progress.update(label="Finished" if finished else "Still running", state="complete" if finished else "error")
    st.session_state.history.append({"analysis_id": analysis_id, "question": record["question"] if record else ""})
    st.session_state.viewing = analysis_id


def show_question_box(dataset: dict) -> None:
    st.subheader("Ask a question")
    if st.button("Use the sample question"):
        st.session_state.question = DEMO_QUESTION

    with st.form("ask"):
        question = st.text_area(
            "Your question",
            key="question",
            height=100,
            placeholder="For example: Which region has the highest revenue?",
        )
        history = st.session_state.get("history", [])
        follow_up = st.checkbox("Follow up on my last question", value=bool(history), disabled=not history)
        submitted = st.form_submit_button("Analyze", type="primary")

    if submitted:
        cleaned = question.strip()
        if len(cleaned) < 3:
            st.error("Please type a question first.")
            return
        parent_id = history[-1]["analysis_id"] if follow_up and history else None
        analysis_id = start_analysis(dataset["dataset_id"], cleaned, parent_id)
        if analysis_id:
            wait_for_analysis(analysis_id)
            st.rerun()


def verification_for(record: dict, name: str, target_type: str) -> dict | None:
    return next(
        (v for v in record["verifications"] if v["target"] == name and v["target_type"] == target_type),
        None,
    )


def verification_text(verification: dict | None) -> str:
    if verification is None:
        return VERIFICATION_LABELS["not_checked"]
    text = VERIFICATION_LABELS[verification["status"]]
    return f"{text}: {plain(verification['formula'])}" if verification.get("formula") else text


def format_number(value: float | int, unit: str | None) -> str:
    text = f"{value:,}" if isinstance(value, int) or float(value).is_integer() else f"{value:,.2f}"
    return f"{text} {unit}" if unit else text


def build_chart(chart: dict) -> go.Figure:
    """Draw from plain values. Nothing from the sandbox is treated as code or HTML."""
    x, y = chart["x"], chart["y"]
    labels = [str(value) for value in x]
    if chart["kind"] in ("bar", "pie"):
        # Parts of a whole compare best as ranked bars, so pies are drawn this way too.
        pairs = sorted(zip(labels, y), key=lambda pair: pair[1] if pair[1] is not None else 0)
        trace = go.Bar(
            x=[pair[1] for pair in pairs],
            y=[pair[0] for pair in pairs],
            orientation="h",
            marker={"color": SERIES_COLOR, "cornerradius": 4},
            hovertemplate="%{y}: %{x:,.2f}<extra></extra>",
        )
        x_title, y_title = chart["y_label"], chart["x_label"]
    elif chart["kind"] == "line":
        trace = go.Scatter(
            x=labels,
            y=y,
            mode="lines+markers",
            line={"color": SERIES_COLOR, "width": 2},
            marker={"size": 8, "color": SERIES_COLOR},
            hovertemplate="%{x}: %{y:,.2f}<extra></extra>",
        )
        x_title, y_title = chart["x_label"], chart["y_label"]
    else:
        trace = go.Scatter(
            x=x,
            y=y,
            mode="markers",
            marker={"size": 8, "color": SERIES_COLOR, "opacity": 0.75},
            hovertemplate="%{x}, %{y:,.2f}<extra></extra>",
        )
        x_title, y_title = chart["x_label"], chart["y_label"]

    figure = go.Figure(trace)
    figure.update_layout(
        title=chart.get("title") or chart["name"],
        xaxis_title=x_title,
        yaxis_title=y_title,
        margin={"l": 8, "r": 8, "t": 48, "b": 8},
        height=max(320, 28 * len(labels) + 120) if chart["kind"] in ("bar", "pie") else 360,
        showlegend=False,
    )
    figure.update_xaxes(automargin=True)
    figure.update_yaxes(automargin=True)
    return figure


def show_results(record: dict) -> None:
    result = record["result"]
    checks = record["verifications"]
    verified = sum(v["status"] == "verified" for v in checks)
    mismatched = sum(v["status"] == "mismatch" for v in checks)
    if checks:
        if mismatched:
            st.error(f"{mismatched} of {len(checks)} results failed independent recalculation.")
        else:
            st.success(f"{verified} of {len(checks)} results were independently recalculated and match.")

    if result["metrics"]:
        st.markdown("#### Key figures")
        for start in range(0, len(result["metrics"]), 3):
            row = st.columns(3)
            for column, metric in zip(row, result["metrics"][start : start + 3]):
                column.metric(plain(metric["name"]), format_number(metric["value"], metric.get("unit")))
                column.caption(verification_text(verification_for(record, metric["name"], "metric")))

    for chart in result["charts"]:
        st.plotly_chart(build_chart(chart), width="stretch", key=f"chart-{record['analysis_id']}-{chart['name']}")

    for index, table in enumerate(result["tables"]):
        st.markdown(f"#### {plain(table['name'])}")
        st.caption(verification_text(verification_for(record, table["name"], "table")))
        st.dataframe(pd.DataFrame(table["rows"], columns=table["columns"]), hide_index=True, width="stretch")
        if table["total_rows"] > len(table["rows"]):
            st.caption(f"Showing the first {len(table['rows'])} of {table['total_rows']:,} rows.")
        st.download_button(
            "Download this table (CSV)",
            safe_csv(table["columns"], table["rows"]),
            file_name=f"datapilot-table-{index + 1}.csv",
            mime="text/csv",
            key=f"table-{record['analysis_id']}-{index}",
        )


def show_insights(record: dict) -> None:
    insights = record.get("insights")
    if not insights:
        return
    if insights["observations"]:
        st.markdown("#### What the data shows")
        for text in insights["observations"]:
            st.markdown(f"- {plain(text)}")
    if insights["recommendations"]:
        st.markdown("#### Recommendations")
        for item in insights["recommendations"]:
            st.markdown(f"- **{plain(item['action'])}**  \nEvidence: {plain(item['evidence'])}")
    if insights["assumptions"]:
        st.markdown("#### Assumptions")
        for text in insights["assumptions"]:
            st.markdown(f"- {plain(text)}")
    if record["removed_claims"]:
        st.caption(
            f"{len(record['removed_claims'])} AI statement(s) were removed because their numbers "
            "couldn't be traced to the results."
        )
    st.info(CAVEAT)


def show_workings(record: dict) -> None:
    if record.get("plan"):
        with st.expander("Analysis plan"):
            st.markdown(plain(record["plan"]["goal"]))
            for number, step in enumerate(record["plan"]["steps"], 1):
                used = ", ".join(step["columns"])
                suffix = f" (columns: {plain(used)})" if used else ""
                st.markdown(f"{number}\\. {plain(step['description'])}{suffix}")

    if record.get("code"):
        with st.expander("Generated Python code"):
            st.code(record["code"], language="python")
            st.download_button(
                "Download code",
                record["code"],
                file_name="datapilot-analysis.py",
                mime="text/x-python",
                key=f"code-{record['analysis_id']}",
            )

    if record["attempts"]:
        with st.expander(f"Execution log ({len(record['attempts'])} attempt(s))"):
            for attempt in record["attempts"]:
                seconds = attempt.get("duration_seconds")
                timing = f", {seconds:.1f}s" if seconds is not None else ""
                st.markdown(f"**Attempt {attempt['attempt']}**: {plain(attempt['outcome'])}{timing}")
                if attempt.get("problem"):
                    st.text(attempt["problem"])
                if attempt.get("logs"):
                    st.code(attempt["logs"], language="text")


def show_analysis(record: dict) -> None:
    st.divider()
    st.subheader("Analysis")
    st.markdown(f"**Question:** {plain(record['question'])}")

    if record["status"] == "refused":
        st.warning(f"This dataset can't answer that question: {plain(record.get('message'))}")
    elif record["status"] == "failed":
        st.error(plain(record.get("message") or "The analysis failed."))
    elif record["status"] != "completed":
        st.info(f"Still working: {plain(record['stage'])}")

    if record["status"] == "completed":
        if record.get("message"):
            st.warning(plain(record["message"]))
        show_results(record)
        show_insights(record)
        try:
            report = httpx.get(f"{ANALYSES_URL}/{record['analysis_id']}/report", timeout=15)
        except httpx.HTTPError:
            report = None
        if report is not None and report.status_code == 200:
            st.download_button(
                "Download the full report (Markdown)",
                report.text,
                file_name="datapilot-report.md",
                mime="text/markdown",
                key=f"report-{record['analysis_id']}",
            )
    show_workings(record)


def show_containment_lab() -> None:
    with st.expander("Containment Lab: watch the sandbox stop real attacks"):
        st.markdown(
            "Runs fixed attack scripts in the real sandbox, on a tiny made-up dataset. "
            "This takes about a minute and uses no AI requests."
        )
        if st.button("Run the attacks"):
            with st.spinner("Attacking the sandbox..."):
                try:
                    response = httpx.post(LAB_URL, headers=access_headers(), timeout=LAB_TIMEOUT_SECONDS)
                except httpx.HTTPError:
                    st.error("The Containment Lab couldn't finish. Please try again.")
                    return
            if response.status_code == 200:
                st.session_state.lab_report = response.json()
            else:
                st.error(error_detail(response, "The Containment Lab couldn't run."))

        report = st.session_state.get("lab_report")
        if report:
            if report["all_contained"]:
                st.success(f"All {len(report['results'])} attacks were contained.")
            else:
                st.error("At least one attack wasn't contained.")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Attack": item["name"],
                            "What it tried": item["attack"],
                            "Result": "✅ Contained" if item["contained"] else "❌ Not contained",
                            "Evidence": item["evidence"],
                            "Seconds": round(item["duration_seconds"], 1),
                        }
                        for item in report["results"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def show_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Access")
        st.text_input(
            "Demo access code",
            type="password",
            key="access_code",
            help="Needed to run analyses, which keeps the free AI allowance from being used up.",
        )
        history = st.session_state.get("history", [])
        if history:
            st.markdown("### This session")
            for item in reversed(history):
                label = item["question"][:40] + ("..." if len(item["question"]) > 40 else "")
                if st.button(label, key=f"history-{item['analysis_id']}"):
                    st.session_state.viewing = item["analysis_id"]


st.set_page_config(page_title="DataPilot", page_icon="📊", layout="wide")
st.session_state.setdefault("history", [])
st.title("DataPilot")
st.caption("Your Data Analyst. Your Code. Verifiable Results.")

if backend_is_healthy():
    st.success("Backend connected.")
    show_sidebar()
    if st.session_state.get("dataset"):
        show_dataset(st.session_state.dataset)
        show_question_box(st.session_state.dataset)
        viewing = st.session_state.get("viewing")
        if viewing:
            shown = load_analysis(viewing)
            if shown:
                show_analysis(shown)
            else:
                st.warning("That analysis couldn't be loaded. It may have expired.")
    else:
        show_landing()
        show_upload_section()
    show_containment_lab()
else:
    st.error(f"Can't reach the backend at {BACKEND_URL}. Is it running?")
