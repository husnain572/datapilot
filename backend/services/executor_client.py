"""Sends code to the executor service. The backend itself never touches Docker."""

from typing import Literal

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

# The executor enforces the real time limit. This only covers waiting for it to answer.
REQUEST_TIMEOUT_SECONDS = 120


class ExecutorError(Exception):
    """The sandbox service couldn't be used. The message is safe to show to users."""


class ExecutionResult(BaseModel):
    status: Literal["completed", "timeout", "out_of_memory", "output_too_large", "crashed"]
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str


class ExecutorClient:
    def __init__(self, base_url: str, token: SecretStr, transport: httpx.BaseTransport | None = None) -> None:
        self._token = token
        self._http = httpx.Client(base_url=base_url, timeout=REQUEST_TIMEOUT_SECONDS, transport=transport)

    def __repr__(self) -> str:
        return "ExecutorClient()"

    def close(self) -> None:
        self._http.close()

    def run(self, code: str, csv_text: str, timeout_seconds: float | None = None) -> ExecutionResult:
        form = {"code": code}
        if timeout_seconds is not None:
            form["timeout_seconds"] = str(timeout_seconds)
        try:
            response = self._http.post(
                "/run",
                data=form,
                files={"dataset": ("data.csv", csv_text.encode("utf-8"), "text/csv")},
                headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
            )
        except httpx.HTTPError as error:
            raise ExecutorError("The secure sandbox isn't reachable right now.") from error

        if response.status_code == 429:
            raise ExecutorError("All secure sandboxes are busy. Please try again in a moment.")
        if response.status_code != 200:
            raise ExecutorError(f"The secure sandbox refused the request (HTTP {response.status_code}).")
        try:
            return ExecutionResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise ExecutorError("The secure sandbox sent an unreadable reply.") from error
