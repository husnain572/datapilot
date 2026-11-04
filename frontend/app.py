"""DataPilot's screens. Start with: uv run streamlit run frontend/app.py"""

import os

import httpx
import streamlit as st
from dotenv import load_dotenv

# Streamlit doesn't read .env by itself. Real environment variables take priority.
load_dotenv()
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")


def backend_is_healthy() -> bool:
    """Ask the backend whether it is running. Any failure counts as 'no'."""
    try:
        return httpx.get(f"{BACKEND_URL}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


st.set_page_config(page_title="DataPilot", page_icon="📊", layout="wide")
st.title("DataPilot")
st.caption("Your Data Analyst. Your Code. Verifiable Results.")

if backend_is_healthy():
    st.success("Backend connected.")
else:
    st.error(f"Can't reach the backend at {BACKEND_URL}. Is it running?")

st.info("Dataset upload arrives in Phase 2.")
