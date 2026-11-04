import os
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Streamlit doesn't read .env by itself. Real environment variables take priority.
load_dotenv()
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
DATASETS_URL = f"{BACKEND_URL}/api/v1/datasets"
SAMPLE_CSV = Path(__file__).parents[1] / "sample_data" / "sales.csv"
UPLOAD_TIMEOUT_SECONDS = 120


def backend_is_healthy() -> bool:
    """Ask the backend whether it is running. Any failure counts as 'no'."""
    try:
        return httpx.get(f"{BACKEND_URL}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


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
    else:
        st.session_state.upload_error = response.json().get("detail", "The upload failed.")


def remove_dataset() -> None:
    dataset_id = st.session_state.dataset["dataset_id"]
    try:
        httpx.delete(f"{DATASETS_URL}/{dataset_id}", timeout=10)
    except httpx.HTTPError:
        pass  # It expires by itself if the backend can't be reached right now.
    st.session_state.dataset = None


def load_preview(dataset_id: str) -> dict | None:
    try:
        response = httpx.get(f"{DATASETS_URL}/{dataset_id}/preview", timeout=30)
    except httpx.HTTPError:
        return None
    return response.json() if response.status_code == 200 else None


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
        st.markdown(f"**{column['name']}**: most common values")
        counts = pd.DataFrame(column["top_values"]).rename(columns={"value": "Value", "count": "Count"})
        st.dataframe(counts, hide_index=True, width="stretch")


def show_dataset(dataset: dict) -> None:
    profile = dataset["profile"]

    header, action = st.columns([4, 1])
    header.subheader(dataset["filename"])
    action.button("Remove dataset", on_click=remove_dataset)

    rows, columns, missing, duplicates = st.columns(4)
    rows.metric("Rows", f"{profile['row_count']:,}")
    columns.metric("Columns", f"{profile['column_count']:,}")
    missing.metric("Missing values", f"{profile['missing_cell_count']:,}")
    duplicates.metric("Duplicate rows", f"{profile['duplicate_row_count']:,}")

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


st.set_page_config(page_title="DataPilot", page_icon="📊", layout="wide")
st.title("DataPilot")
st.caption("Your Data Analyst. Your Code. Verifiable Results.")

if backend_is_healthy():
    st.success("Backend connected.")
    if st.session_state.get("dataset"):
        show_dataset(st.session_state.dataset)
    else:
        show_upload_section()
else:
    st.error(f"Can't reach the backend at {BACKEND_URL}. Is it running?")
