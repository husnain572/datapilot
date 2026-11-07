import base64
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
# The frontend never reads .env, so the backend's AI keys can't reach the Streamlit process.
# On the server, its only setting comes from frontend.env (see scripts/deploy_server.sh).
BACKEND_URL = os.environ.get("BACKEND_URL") or "http://127.0.0.1:8000"
DATASETS_URL = f"{BACKEND_URL}/api/v1/datasets"
ANALYSES_URL = f"{BACKEND_URL}/api/v1/analyses"
LAB_URL = f"{BACKEND_URL}/api/v1/containment-tests"
USAGE_URL = f"{BACKEND_URL}/api/v1/usage"
EXAMPLE_ID = "00000000-0000-4000-8000-00000000da7a"
UPLOAD_TYPES = ["csv", "xlsx", "json", "parquet", "tsv", "txt"]
SAMPLE_CSV = Path(__file__).parents[1] / "sample_data" / "sales.csv"
ASSETS = Path(__file__).with_name("assets")
STYLE_FILE = Path(__file__).with_name("style.css")
UPLOAD_TIMEOUT_SECONDS = 120
LAB_TIMEOUT_SECONDS = 150
POLL_SECONDS = 1.5
MAX_WAIT_SECONDS = 600
SERIES_COLOR = "#2563eb"
AREA_FILL = "rgba(37, 99, 235, 0.10)"
GRID_COLOR = "#eef1f6"
INK = "#26324b"
DEMO_QUESTION = (
    "Analyze sales performance. Calculate total revenue, identify the top five products by revenue, "
    "compare monthly revenue, detect missing values, and recommend three data-backed actions."
)
CAVEAT = "These findings show patterns in this dataset. They don't prove what caused them."
UNSAFE_HTML_CHARACTERS = set("&<>\"'\\`*_{}[]()#+-.!|~$:")
VERIFICATION_LABELS = {
    "verified": "✅ Verified",
    "mismatch": "❌ Mismatch",
    "not_checked": "➖ Not independently checked",
}
# Progress steps, and the start of the backend's stage message for each.
STEPS = [
    ("Plan and write code", "Planning"),
    ("Run in sandbox", "Running"),
    ("Verify results", "Validating"),
    ("Explain findings", "Explaining"),
]
# The main menu, in the order people use it.
PAGES = [
    ("Upload data", ":material/upload_file:"),
    ("Dashboard", ":material/space_dashboard:"),
    ("Ask AI", ":material/auto_awesome:"),
]
# Shown as a small link at the bottom of the sidebar, not in the main menu.
SECURITY_PAGE = "Security"


# --- Safe text and small building blocks ---


def plain(text: object) -> str:
    """Stop Markdown in untrusted text (AI answers, file contents) from becoming links or images."""
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~$:])", r"\\\1", str(text))


def safe_html(text: object) -> str:
    """Show untrusted text literally inside our HTML, with no tags, links, or images."""
    return "".join(f"&#{ord(char)};" if char in UNSAFE_HTML_CHARACTERS else char for char in str(text))


def show_html(content: str) -> None:
    # Only ever given our own markup, with outside text passed through safe_html.
    st.markdown(content, unsafe_allow_html=True)


def card(title: str, body: str = "", label: str = "") -> str:
    """Build a card. Callers pass text through safe_html if it isn't our own."""
    tag = f'<div class="dp-card-label">{label}</div>' if label else ""
    return f'<div class="dp-card">{tag}<div class="dp-card-title">{title}</div><div class="dp-card-body">{body}</div></div>'


def section(title: str) -> None:
    show_html(f'<div class="dp-section">{title}</div>')


def card_title(title: str, note: str = "") -> None:
    extra = f'<div class="dp-card-note">{note}</div>' if note else ""
    show_html(f'<div class="dp-card-heading">{title}</div>{extra}')


def image_data(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def safe_csv(columns: list[str], rows: list[list]) -> str:
    """Build a CSV. Cells that a spreadsheet would run as formulas get a leading apostrophe."""

    def cell(value: object) -> object:
        return f"'{value}" if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([cell(column) for column in columns])
    writer.writerows([[cell(value) for value in row] for row in rows])
    return output.getvalue()


def format_number(value: float | int, unit: str | None = None) -> str:
    text = f"{value:,}" if isinstance(value, int) or float(value).is_integer() else f"{value:,.2f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text} {unit}" if unit else text


# --- Talking to the backend ---


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


def visitor_headers() -> dict[str, str]:
    """Tell the backend who's asking, so each visitor gets a fair share of the free AI allowance.
    Nginx passes the visitor's network address in X-Real-IP."""
    try:
        headers = st.context.headers
        address = headers.get("X-Real-Ip") or headers.get("X-Forwarded-For", "").split(",")[0].strip()
        address = address or st.context.ip_address or ""
    except Exception:  # noqa: BLE001 - there are no request details in some test runs.
        address = ""
    return {"X-Visitor-Id": address[:200] or "unknown"}


def get_json(url: str, timeout: float = 15) -> dict | None:
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def analyses_left_today() -> int | None:
    data = get_json(USAGE_URL, timeout=5)
    left = data.get("analyses_left_today") if data else None
    return left if isinstance(left, int) else None


def load_analysis(analysis_id: str) -> dict | None:
    return get_json(f"{ANALYSES_URL}/{analysis_id}")


def load_preview(dataset_id: str) -> dict | None:
    return get_json(f"{DATASETS_URL}/{dataset_id}/preview", timeout=30)


def load_dashboard(dataset_id: str) -> dict | None:
    data = get_json(f"{DATASETS_URL}/{dataset_id}/charts", timeout=60)
    return data if data and "missing" in data else None


def upload_dataset(filename: str, content: bytes) -> bool:
    """Send a file to the backend. Returns True on success, or remembers why it failed."""
    try:
        response = httpx.post(
            DATASETS_URL,
            files={"file": (filename, content, "application/octet-stream")},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        st.session_state.upload_error = "Couldn't reach the backend. Please try again."
        return False

    if response.status_code == 201:
        st.session_state.dataset = response.json()
        st.session_state.upload_error = None
        st.session_state.history = []
        st.session_state.viewing = None
        st.session_state.page = "Dashboard"
        return True
    st.session_state.upload_error = error_detail(response, "The upload failed.")
    return False


def remove_dataset() -> None:
    dataset_id = st.session_state.dataset["dataset_id"]
    try:
        httpx.delete(f"{DATASETS_URL}/{dataset_id}", timeout=10)
    except httpx.HTTPError:
        pass  # It expires by itself if the backend can't be reached right now.
    st.session_state.dataset = None
    st.session_state.history = []
    st.session_state.viewing = None


def start_analysis(dataset_id: str, question: str, parent_id: str | None) -> str | None:
    """Ask the backend to start. Returns the analysis ID, or shows why it couldn't."""
    body = {"dataset_id": dataset_id, "question": question, "parent_id": parent_id}
    try:
        response = httpx.post(ANALYSES_URL, json=body, headers=visitor_headers(), timeout=30)
    except httpx.HTTPError:
        st.error("Couldn't reach the backend. Please try again.")
        return None
    if response.status_code != 202:
        st.error(error_detail(response, "The analysis couldn't start."))
        return None
    return response.json()["analysis_id"]


# --- Navigation ---


def current_page() -> str:
    page = st.session_state.get("page")
    if page in [name for name, _ in PAGES] + [SECURITY_PAGE]:
        return page
    return "Ask AI" if st.session_state.get("dataset") else "Upload data"


def go_to(page: str) -> None:
    st.session_state.page = page


def open_example() -> None:
    """A saved, real analysis of the sample data. It appears instantly and uses no AI requests."""
    st.session_state.viewing = EXAMPLE_ID
    st.session_state.page = "Ask AI"


def set_question(question: str) -> None:
    st.session_state.question = question


def example_button(key: str) -> None:
    st.button(
        "See an example analysis",
        key=key,
        icon=":material/visibility:",
        on_click=open_example,
        help="A real, verified analysis of the sample data, saved earlier. It uses no AI requests.",
    )


def show_sidebar() -> None:
    page = current_page()
    with st.sidebar:
        show_html(
            f'<div class="dp-logo"><img src="{image_data(ASSETS / "logo.png")}" alt="">'
            '<div><div class="dp-logo-name">DataPilot</div><div class="dp-logo-tag">Verifiable AI analyst</div></div></div>'
        )
        show_html('<div class="dp-nav-label">Menu</div>')
        for name, icon in PAGES:
            st.button(
                name,
                icon=icon,
                key=f"nav-{name}",
                type="primary" if name == page else "secondary",
                on_click=go_to,
                args=(name,),
                width="stretch",
            )

        history = st.session_state.get("history", [])
        if history:
            show_html('<div class="dp-nav-label">This session</div>')
            for item in reversed(history):
                label = item["question"][:38] + ("..." if len(item["question"]) > 38 else "")
                if st.button(plain(label), key=f"history-{item['analysis_id']}", width="stretch"):
                    st.session_state.viewing = item["analysis_id"]
                    st.session_state.page = "Ask AI"

        show_html('<div class="dp-side-spacer"></div>')
        st.button(
            "Security",
            icon=":material/verified_user:",
            key="nav-security",
            type="primary" if page == SECURITY_PAGE else "tertiary",
            on_click=go_to,
            args=(SECURITY_PAGE,),
            width="stretch",
            help="See how DataPilot keeps generated code locked away from your data and our servers.",
        )


def page_header(title: str, subtitle: str, label: str = "") -> None:
    dataset = st.session_state.get("dataset")
    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        badge = f'<div class="dp-badge">{label}</div>' if label else ""
        show_html(f'{badge}<div class="dp-page-title">{title}</div><div class="dp-page-subtitle">{subtitle}</div>')
    with right:
        if dataset:
            show_html(
                f'<div class="dp-file-chip"><span class="dp-file-icon">📄</span>'
                f'<span>{safe_html(dataset["filename"])}</span>'
                f'<span class="dp-file-meta">{dataset["profile"]["row_count"]:,} rows</span></div>'
            )
            if dataset.get("source_format", "CSV") not in ("CSV", "TSV", "Text"):
                note = dataset.get("source_note")
                st.caption(f"Converted from {plain(dataset['source_format'])}" + (f": {plain(note)}" if note else ""))


# --- Charts ---


def style_figure(figure: go.Figure, height: int, title: str | None = None) -> go.Figure:
    figure.update_layout(
        title=title,
        height=height,
        margin={"l": 8, "r": 12, "t": 40 if title else 8, "b": 8},
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": INK, "size": 12},
        title_font={"size": 15, "color": "#111c33"},
        showlegend=False,
        hoverlabel={"bgcolor": "#111c33", "font_color": "#ffffff", "bordercolor": "#111c33"},
        bargap=0.35,
    )
    figure.update_xaxes(automargin=True, gridcolor=GRID_COLOR, zeroline=False, showline=False)
    figure.update_yaxes(automargin=True, gridcolor=GRID_COLOR, zeroline=False, showline=False)
    return figure


def area_chart(labels: list, values: list, y_title: str, height: int = 320) -> go.Figure:
    trace = go.Scatter(
        x=labels,
        y=values,
        mode="lines+markers",
        line={"color": SERIES_COLOR, "width": 2, "shape": "spline", "smoothing": 0.6},
        marker={"size": 8, "color": "#ffffff", "line": {"color": SERIES_COLOR, "width": 2}},
        fill="tozeroy",
        fillcolor=AREA_FILL,
        hovertemplate="%{x}<br><b>%{y:,.2f}</b><extra></extra>",
    )
    figure = style_figure(go.Figure(trace), height)
    figure.update_yaxes(title=y_title)
    return figure


def ranked_bars(labels: list, values: list, x_title: str, height: int | None = None) -> go.Figure:
    """Horizontal bars, largest at the top, in one color."""
    pairs = sorted(zip([str(label) for label in labels], values), key=lambda pair: pair[1] if pair[1] is not None else 0)
    trace = go.Bar(
        x=[pair[1] for pair in pairs],
        y=[pair[0] for pair in pairs],
        orientation="h",
        marker={"color": SERIES_COLOR, "cornerradius": 4},
        hovertemplate="%{y}: <b>%{x:,.2f}</b><extra></extra>",
    )
    figure = style_figure(go.Figure(trace), height or max(260, 32 * len(pairs) + 70))
    figure.update_xaxes(title=x_title)
    return figure


def column_bars(labels: list, values: list, y_title: str, height: int = 300) -> go.Figure:
    trace = go.Bar(
        x=[str(label) for label in labels],
        y=values,
        marker={"color": SERIES_COLOR, "cornerradius": 4},
        hovertemplate="%{x}: <b>%{y:,}</b><extra></extra>",
    )
    figure = style_figure(go.Figure(trace), height)
    figure.update_yaxes(title=y_title)
    return figure


def build_chart(chart: dict) -> go.Figure:
    """Draw a chart the sandbox described as plain values. Nothing from it is treated as code."""
    title = chart.get("title") or chart["name"]
    if chart["kind"] in ("bar", "pie"):
        # Parts of a whole compare best as ranked bars, so pies are drawn this way too.
        figure = ranked_bars(chart["x"], chart["y"], chart["y_label"])
    elif chart["kind"] == "line":
        figure = area_chart([str(value) for value in chart["x"]], chart["y"], chart["y_label"])
        figure.update_xaxes(title=chart["x_label"])
    else:
        trace = go.Scatter(
            x=chart["x"],
            y=chart["y"],
            mode="markers",
            marker={"size": 8, "color": SERIES_COLOR, "opacity": 0.75},
            hovertemplate="%{x}, %{y:,.2f}<extra></extra>",
        )
        figure = style_figure(go.Figure(trace), 340)
        figure.update_xaxes(title=chart["x_label"])
        figure.update_yaxes(title=chart["y_label"])
    figure.update_layout(title=title, margin={"t": 44})
    return figure


def plot(figure: go.Figure, key: str) -> None:
    st.plotly_chart(figure, width="stretch", key=key, config={"displaylogo": False})


# --- Upload page ---


def show_upload_page() -> None:
    show_html(
        '<div class="dp-upload-hero"><div class="dp-badge">Data Upload Center</div>'
        '<div class="dp-upload-title">Upload Your Data</div>'
        '<div class="dp-page-subtitle">Upload a data file for AI analysis. Your data is checked, profiled, and analyzed '
        "with code that runs in a locked-down sandbox. Files are deleted after 24 hours.</div></div>"
    )

    with st.container(border=True):
        card_title("Choose a data source")
        local, sample, example = st.columns(3)
        with local:
            show_html(
                '<div class="dp-source dp-source-active"><div class="dp-source-icon">⬆</div>'
                '<div class="dp-source-name">Local file</div><div class="dp-source-text">Use the upload box below</div></div>'
            )
        with sample:
            show_html(
                '<div class="dp-source"><div class="dp-source-icon">📊</div><div class="dp-source-name">Sample dataset</div>'
                '<div class="dp-source-text">1,010 made-up sales records</div></div>'
            )
            sample_clicked = st.button("Try the sample sales dataset", width="stretch")
        with example:
            show_html(
                '<div class="dp-source"><div class="dp-source-icon">✨</div><div class="dp-source-name">Example analysis</div>'
                '<div class="dp-source-text">A finished, verified result</div></div>'
            )
            example_button("example-upload")

    uploaded = st.file_uploader(
        "Drag and drop a file here",
        type=UPLOAD_TYPES,
        help="CSV, Excel (.xlsx, first sheet), JSON, Parquet, TSV, or TXT. Up to 25 MB.",
    )
    st.caption("Supported: CSV, Excel (.xlsx), JSON, Parquet, TSV, and TXT, up to 25 MB. Don't upload sensitive data.")

    uploaded_now = False
    # Streamlit reruns this script on every click, so only send each file once.
    if uploaded is not None and uploaded.file_id != st.session_state.get("last_file_id"):
        st.session_state.last_file_id = uploaded.file_id
        with st.spinner("Checking and profiling your file..."):
            uploaded_now = upload_dataset(uploaded.name, uploaded.getvalue())
    if sample_clicked:
        with st.spinner("Loading the sample..."):
            uploaded_now = upload_dataset(SAMPLE_CSV.name, SAMPLE_CSV.read_bytes())

    if uploaded_now:
        st.rerun()  # Show the new dataset's dashboard.
    if st.session_state.get("upload_error"):
        st.error(st.session_state.upload_error)
    elif st.session_state.get("dataset"):
        st.info("Uploading a new file replaces the current dataset.")


# --- Dashboard page ---


def show_empty_state(message: str) -> None:
    with st.container(border=True):
        card_title("No dataset yet", message)
        first, second, _ = st.columns([1, 1, 2])
        first.button("Upload a file", on_click=go_to, args=("Upload data",), type="primary", key="empty-upload")
        with second:
            example_button("example-empty")


def show_kpis(dataset: dict) -> None:
    profile = dataset["profile"]
    rows, columns, missing, duplicates = st.columns(4)
    rows.metric("Rows", f"{profile['row_count']:,}")
    columns.metric("Columns", f"{profile['column_count']:,}")
    missing.metric("Missing values", f"{profile['missing_cell_count']:,}")
    duplicates.metric("Duplicate rows", f"{profile['duplicate_row_count']:,}")


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


def show_trend_card(charts: dict, dataset_id: str) -> None:
    with st.container(border=True):
        title, picker = st.columns([3, 2], vertical_alignment="center")
        with title:
            card_title("Trend over time", f"By month, from {safe_html(charts['date_column'])}")
        measure = picker.selectbox("Measure", list(charts["trend"]), key="trend-measure", label_visibility="collapsed")
        series = charts["trend"][measure]
        plot(area_chart(series["labels"], series["values"], measure, height=330), f"trend-{dataset_id}")


def show_category_card(charts: dict, dataset_id: str) -> None:
    with st.container(border=True):
        card_title("Top categories", "The 10 largest groups")
        column_pick, measure_pick = st.columns(2)
        column = column_pick.selectbox("Category", list(charts["categories"]), key="category-column")
        groups = charts["categories"][column]
        measure = measure_pick.selectbox("Measure", list(groups), key="category-measure")
        series = groups[measure]
        plot(ranked_bars(series["labels"], series["values"], measure, height=360), f"categories-{dataset_id}")


def show_distribution_card(charts: dict, dataset_id: str) -> None:
    with st.container(border=True):
        title, picker = st.columns([3, 2], vertical_alignment="center")
        with title:
            card_title("Value distribution", "How many rows fall in each range")
        column = picker.selectbox("Column", list(charts["histograms"]), key="histogram-column", label_visibility="collapsed")
        histogram = charts["histograms"][column]
        plot(column_bars(histogram["labels"], histogram["counts"], "Rows", height=360), f"histogram-{dataset_id}")


def show_missing_card(charts: dict, dataset_id: str) -> None:
    with st.container(border=True):
        card_title("Missing values", "Blank cells in each column")
        missing = charts["missing"]
        if not any(missing["values"]):
            show_html('<div class="dp-good">✓ No missing values</div>')
        else:
            plot(ranked_bars(missing["labels"], missing["values"], "Blank cells", height=330), f"missing-{dataset_id}")


def show_dashboard_page() -> None:
    page_header("Dataset overview", "Charts calculated directly from your data. No AI is used on this page.", "Dashboard")
    dataset = st.session_state.get("dataset")
    if not dataset:
        show_empty_state("Upload a file, or try the sample dataset, to see its dashboard.")
        return

    show_kpis(dataset)
    _, ask, remove = st.columns([4, 1, 1])
    ask.button("Ask AI", icon=":material/auto_awesome:", on_click=go_to, args=("Ask AI",), type="primary", width="stretch")
    remove.button("Remove dataset", on_click=remove_dataset, width="stretch")

    charts = load_dashboard(dataset["dataset_id"])
    if charts is None:
        st.warning("The charts couldn't be loaded. Please refresh the page.")
    else:
        dataset_id = dataset["dataset_id"]
        if charts["trend"]:
            wide, narrow = st.columns([2, 1])
            with wide:
                show_trend_card(charts, dataset_id)
            with narrow:
                show_missing_card(charts, dataset_id)
        else:
            show_missing_card(charts, dataset_id)
        left, right = st.columns(2)
        if charts["categories"]:
            with left:
                show_category_card(charts, dataset_id)
        if charts["histograms"]:
            with right:
                show_distribution_card(charts, dataset_id)

    with st.container(border=True):
        card_title("Data preview and columns")
        preview, columns = st.tabs(["Preview", "Columns"])
        with preview:
            data = load_preview(dataset["dataset_id"])
            if data is None:
                st.warning("Couldn't load the preview.")
            else:
                st.caption(f"First {len(data['rows'])} of {data['total_rows']:,} rows")
                st.dataframe(pd.DataFrame(data["rows"], columns=data["columns"]), hide_index=True, width="stretch")
        with columns:
            st.dataframe(columns_table(dataset["profile"]["columns"]), hide_index=True, width="stretch")


# --- Ask AI page ---


def suggested_questions(profile: dict) -> list[str]:
    """Questions that fit this dataset's real columns."""
    columns = profile["columns"]
    numeric = [c["name"] for c in columns if c["kind"] == "numeric"]
    categories = [c["name"] for c in columns if c["kind"] == "categorical"]
    dated = any(c["kind"] == "datetime" for c in columns)
    questions = []
    if numeric and categories:
        questions.append(f"Which {categories[0]} has the highest total {numeric[0]}?")
    if numeric and dated:
        questions.append(f"How does total {numeric[0]} change by month?")
    if numeric:
        questions.append(f"Summarize {numeric[0]}: total, average, minimum, and maximum.")
    questions.append("Check data quality: missing values and duplicate rows by column.")
    return questions[:4]


def steps_html(stage: str, finished: bool = False) -> str:
    current = next((index for index, (_, prefix) in enumerate(STEPS) if stage.startswith(prefix)), None)
    items = []
    for index, (label, _) in enumerate(STEPS):
        if finished or (current is not None and index < current):
            items.append(f'<div class="dp-step dp-step-done">✓ {label}</div>')
        elif index == current:
            items.append(f'<div class="dp-step dp-step-active">● {label}</div>')
        else:
            items.append(f'<div class="dp-step">{label}</div>')
    return f'<div class="dp-steps">{"".join(items)}</div><div class="dp-step-note">{safe_html(stage)}</div>'


def wait_for_analysis(analysis_id: str) -> None:
    """Show live progress until the analysis finishes, then remember it."""
    tracker = st.empty()
    tracker.markdown(steps_html("Waiting to start"), unsafe_allow_html=True)
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    record = None
    while time.monotonic() < deadline:
        record = load_analysis(analysis_id)
        if record is not None:
            done = record["status"] == "completed"
            tracker.markdown(steps_html(record["stage"], finished=done), unsafe_allow_html=True)
            if record["status"] in ("completed", "failed", "refused"):
                break
        time.sleep(POLL_SECONDS)
    st.session_state.history.append({"analysis_id": analysis_id, "question": record["question"] if record else ""})
    st.session_state.viewing = analysis_id


def show_question_box(dataset: dict) -> None:
    ask, suggestions = st.columns([3, 2])
    with ask, st.container(border=True):
        card_title("Ask AI ✨", "AI writes the code. Python in the sandbox calculates every number.")
        with st.form("ask", border=False):
            question = st.text_area(
                "Your question",
                key="question",
                height=110,
                placeholder="For example: Which region has the highest revenue?",
            )
            history = st.session_state.get("history", [])
            follow_up = st.checkbox("Follow up on my last question", value=bool(history), disabled=not history)
            submitted = st.form_submit_button("Analyze", type="primary", icon=":material/send:")

    with suggestions, st.container(border=True):
        card_title("You may ask")
        st.button("Use the sample question", key="suggest-sample", on_click=set_question, args=(DEMO_QUESTION,), width="stretch")
        for index, text in enumerate(suggested_questions(dataset["profile"])):
            st.button(plain(text), key=f"suggest-{index}", on_click=set_question, args=(text,), width="stretch")
        if analyses_left_today() == 0:
            st.info("Today's analysis limit has been reached. It resets at midnight UTC. You can still see the example.")
        example_button("example-question")

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


def verification_badge(verification: dict | None) -> str:
    status = verification["status"] if verification else "not_checked"
    text = VERIFICATION_LABELS[status]
    if verification and verification.get("formula"):
        text += f": {safe_html(verification['formula'])}"
    return f'<div class="dp-pill dp-pill-{status}">{text}</div>'


def number_formats(table: dict) -> dict:
    """Show numeric columns with thousands separators. The data itself isn't changed."""
    formats = {}
    for index, column in enumerate(table["columns"]):
        values = [row[index] for row in table["rows"] if row[index] is not None]
        if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            formats[column] = st.column_config.NumberColumn(format="localized")
    return formats


def show_summary(record: dict) -> None:
    checks = record["verifications"]
    verified = sum(v["status"] == "verified" for v in checks)
    mismatched = sum(v["status"] == "mismatch" for v in checks)
    if not checks:
        return
    if mismatched:
        st.error(f"{mismatched} of {len(checks)} results failed independent recalculation.")
    else:
        st.success(f"{verified} of {len(checks)} results were independently recalculated and match.")


def show_key_figures(record: dict) -> None:
    metrics = record["result"]["metrics"]
    if not metrics:
        return
    for start in range(0, len(metrics), 4):
        row = st.columns(4)
        for column, metric in zip(row, metrics[start : start + 4]):
            column.metric(plain(metric["name"]), format_number(metric["value"], metric.get("unit")))
            with column:
                show_html(verification_badge(verification_for(record, metric["name"], "metric")))


def show_result_charts(record: dict) -> None:
    charts = record["result"]["charts"]
    if not charts:
        return
    section("Charts")
    for start in range(0, len(charts), 2):
        row = st.columns(2)
        for column, chart in zip(row, charts[start : start + 2]):
            with column, st.container(border=True):
                plot(build_chart(chart), f"chart-{record['analysis_id']}-{chart['name']}")


def show_tables(record: dict) -> None:
    tables = record["result"]["tables"]
    if not tables:
        st.caption("This analysis has no tables.")
    for index, table in enumerate(tables):
        section(safe_html(table["name"]))
        show_html(verification_badge(verification_for(record, table["name"], "table")))
        st.dataframe(
            pd.DataFrame(table["rows"], columns=table["columns"]),
            hide_index=True,
            width="stretch",
            column_config=number_formats(table),
        )
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
        st.caption("No written explanation is available for this analysis.")
        return
    observations, recommendations = st.columns([1, 1])
    if insights["observations"]:
        with observations:
            section("What the data shows")
            items = "".join(f"<li>{safe_html(text)}</li>" for text in insights["observations"])
            show_html(f'<div class="dp-card"><ul class="dp-list">{items}</ul></div>')
    if insights["recommendations"]:
        with recommendations:
            section("Recommendations")
            cards = "".join(
                card(
                    safe_html(item["action"]),
                    f'<div class="dp-evidence">Evidence: {safe_html(item["evidence"])}</div>',
                    f"Action {number}",
                )
                for number, item in enumerate(insights["recommendations"], 1)
            )
            show_html(f'<div class="dp-stack">{cards}</div>')
    if insights["assumptions"]:
        section("Assumptions")
        items = "".join(f"<li>{safe_html(text)}</li>" for text in insights["assumptions"])
        show_html(f'<div class="dp-card"><ul class="dp-list">{items}</ul></div>')
    if record["removed_claims"]:
        st.caption(
            f"{len(record['removed_claims'])} AI statement(s) were removed because their numbers "
            "couldn't be traced to the results."
        )
    st.info(CAVEAT)


def show_plan(record: dict) -> None:
    st.markdown(plain(record["plan"]["goal"]))
    for number, step in enumerate(record["plan"]["steps"], 1):
        used = ", ".join(step["columns"])
        suffix = f" (columns: {plain(used)})" if used else ""
        st.markdown(f"{number}\\. {plain(step['description'])}{suffix}")


def show_code(record: dict) -> None:
    st.code(record["code"], language="python")
    st.download_button(
        "Download code",
        record["code"],
        file_name="datapilot-analysis.py",
        mime="text/x-python",
        key=f"code-{record['analysis_id']}",
    )


def show_logs(record: dict) -> None:
    for attempt in record["attempts"]:
        seconds = attempt.get("duration_seconds")
        timing = f", {seconds:.1f}s" if seconds is not None else ""
        st.markdown(f"**Attempt {attempt['attempt']}**: {plain(attempt['outcome'])}{timing}")
        if attempt.get("problem"):
            st.text(attempt["problem"])
        if attempt.get("logs"):
            st.code(attempt["logs"], language="text")


def show_report_button(record: dict) -> None:
    try:
        report = httpx.get(f"{ANALYSES_URL}/{record['analysis_id']}/report", timeout=15)
    except httpx.HTTPError:
        return
    if report.status_code == 200:
        st.download_button(
            "Download the full report (Markdown)",
            report.text,
            file_name="datapilot-report.md",
            mime="text/markdown",
            key=f"report-{record['analysis_id']}",
            type="primary",
            icon=":material/download:",
        )


def show_analysis(record: dict) -> None:
    st.divider()
    if str(record["analysis_id"]) == EXAMPLE_ID:
        st.info("This is a saved example: a real, verified analysis of the sample sales dataset.")
    show_html(f'<div class="dp-question"><span>Question</span>{safe_html(record["question"])}</div>')

    if record["status"] == "refused":
        st.warning(f"This dataset can't answer that question: {plain(record.get('message'))}")
    elif record["status"] == "failed":
        st.error(plain(record.get("message") or "The analysis failed."))
    elif record["status"] != "completed":
        st.info(f"Still working: {plain(record['stage'])}")

    completed = record["status"] == "completed"
    if completed:
        if record.get("message"):
            st.warning(plain(record["message"]))
        show_html(steps_html("Finished", finished=True))
        show_summary(record)
        show_key_figures(record)
        show_result_charts(record)
        show_insights(record)

    views = []
    if completed:
        views.append(("Tables", show_tables))
    if record.get("plan"):
        views.append(("Analysis plan", show_plan))
    if record.get("code"):
        views.append(("Generated Python code", show_code))
    if record["attempts"]:
        views.append((f"Execution log ({len(record['attempts'])} attempt(s))", show_logs))
    if views:
        for tab, (_, show) in zip(st.tabs([label for label, _ in views]), views):
            with tab:
                show(record)
    if completed:
        show_report_button(record)


def show_ask_page() -> None:
    page_header("Ask AI", "Ask questions in plain English, and get verified answers with charts.", "AI analyst")
    dataset = st.session_state.get("dataset")
    if dataset:
        show_question_box(dataset)
    elif st.session_state.get("viewing") != EXAMPLE_ID:
        show_empty_state("Upload a file to ask questions about it, or open the example analysis.")

    viewing = st.session_state.get("viewing")
    if viewing:
        shown = load_analysis(viewing)
        if shown:
            show_analysis(shown)
        else:
            st.warning("That analysis couldn't be loaded. It may have expired.")


# --- Containment Lab page ---


def show_lab_page() -> None:
    page_header(
        "How your data is protected",
        "AI-written code never runs on our servers directly. Each analysis runs in a sealed, throwaway container "
        "with no internet, no secrets, and strict limits. Run real attacks against it to see for yourself. "
        "It takes about a minute.",
        "Security",
    )
    show_html(
        '<div class="dp-grid">'
        + card("No internet", "Code can't send your data anywhere.", "Sealed")
        + card("No secrets", "API keys and passwords never enter the container.", "Isolated")
        + card("Strict limits", "Time, memory, and processes are capped, then the container is destroyed.", "Disposable")
        + "</div>"
    )
    if st.button("Run the attacks", type="primary", icon=":material/bolt:"):
        with st.spinner("Attacking the sandbox..."):
            try:
                response = httpx.post(LAB_URL, headers=visitor_headers(), timeout=LAB_TIMEOUT_SECONDS)
            except httpx.HTTPError:
                st.error("The Containment Lab couldn't finish. Please try again.")
                return
        if response.status_code == 200:
            st.session_state.lab_report = response.json()
        else:
            st.error(error_detail(response, "The Containment Lab couldn't run."))

    report = st.session_state.get("lab_report")
    if not report:
        return
    if report["all_contained"]:
        st.success(f"All {len(report['results'])} attacks were contained.")
    else:
        st.error("At least one attack wasn't contained.")
    cards = []
    for item in report["results"]:
        result = (
            '<div class="dp-attack-result dp-contained">✓ Contained</div>'
            if item["contained"]
            else '<div class="dp-attack-result dp-breached">✗ Not contained</div>'
        )
        body = (
            f"{safe_html(item['attack'])}{result}"
            f'<div class="dp-evidence">{safe_html(item["evidence"])} ({item["duration_seconds"]:.1f}s)</div>'
        )
        cards.append(card(safe_html(item["name"]), body))
    show_html(f'<div class="dp-grid">{"".join(cards)}</div>')


# --- Page ---

st.set_page_config(
    page_title="DataPilot",
    page_icon=str(ASSETS / "favicon.png"),
    layout="wide",
    initial_sidebar_state="expanded",
)
show_html(f"<style>{STYLE_FILE.read_text(encoding='utf-8')}</style>")
st.session_state.setdefault("history", [])

show_sidebar()

if backend_is_healthy():
    page = current_page()
    if page == "Dashboard":
        show_dashboard_page()
    elif page == "Upload data":
        show_upload_page()
    elif page == "Ask AI":
        show_ask_page()
    else:
        show_lab_page()
else:
    st.error("DataPilot is temporarily unavailable. Please try again in a minute.")
