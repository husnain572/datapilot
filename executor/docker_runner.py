"""Starts one locked-down container for one piece of code, then destroys it.

Every security setting lives in `sandbox_command`, with no way for a caller to change
it. Callers provide only the code, the dataset, and a shorter time limit if they want.
"""

import logging
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

logger = logging.getLogger(__name__)

SANDBOX_LABEL = "datapilot.sandbox=1"
RunStatus = Literal["completed", "timeout", "out_of_memory", "output_too_large", "crashed"]


@dataclass(frozen=True)
class SandboxLimits:
    image: str = "datapilot-sandbox:1"
    runtime: str | None = None  # For example "runsc" for gVisor.
    timeout_seconds: float = 30
    memory: str = "768m"
    cpus: str = "1.0"
    pids: int = 64
    tmp_size: str = "64m"
    max_file_bytes: int = 32 * 1024 * 1024
    max_open_files: int = 256
    max_output_bytes: int = 8 * 1024 * 1024
    max_log_bytes: int = 64 * 1024


@dataclass
class RunResult:
    status: RunStatus
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str


def sandbox_command(name: str, input_dir: Path, limits: SandboxLimits) -> list[str]:
    """Build the `docker run` command. Each line below is one security control."""
    command = [
        "docker", "run",
        "--name", name,
        "--label", SANDBOX_LABEL,
        "--pull", "never",  # Only ever run our own locally built image.
        "--network", "none",  # No internet, so nothing can be sent out.
        "--read-only",  # The container's own files can't be changed.
        "--user", "10001:10001",  # Not root.
        "--cap-drop", "ALL",  # No special Linux privileges.
        "--security-opt", "no-new-privileges",  # No gaining privileges later.
        "--ipc", "none",  # No shared memory with anything else.
        "--memory", limits.memory,
        "--memory-swap", limits.memory,  # Equal to memory, which means no swap.
        "--cpus", limits.cpus,
        "--pids-limit", str(limits.pids),  # Stops fork bombs.
        "--ulimit", f"fsize={limits.max_file_bytes}",
        "--ulimit", f"nofile={limits.max_open_files}:{limits.max_open_files}",
        "--ulimit", "core=0",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={limits.tmp_size}",  # Small scratch space in memory.
        "--mount", f"type=bind,source={input_dir},target=/input,readonly",
        "--log-driver", "none",  # Keep output from being stored on the server's disk.
        "--init",  # Cleans up stray processes.
        "--hostname", "sandbox",
    ]
    if limits.runtime:
        command += ["--runtime", limits.runtime]
    command.append(limits.image)
    return command


def _read_capped(stream: IO[bytes], limit: int, on_overflow: threading.Event | None, sink: bytearray) -> None:
    """Keep up to `limit` bytes. Always keep reading, so the container can't get stuck."""
    for chunk in iter(lambda: stream.read(65536), b""):
        room = limit - len(sink)
        if room > 0:
            sink.extend(chunk[:room])
        if len(chunk) > room and on_overflow is not None:
            on_overflow.set()  # Keep draining: a full pipe would jam `docker kill`.


def _docker(*args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


def run_in_sandbox(
    code: str, dataset: bytes, limits: SandboxLimits, work_root: Path, timeout_seconds: float | None = None
) -> RunResult:
    """Run the code in a new container. The container and its files never outlive this call."""
    name = f"datapilot-sandbox-{uuid.uuid4().hex}"
    timeout = min(timeout_seconds or limits.timeout_seconds, limits.timeout_seconds)
    work_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="run-", dir=work_root))
    started = time.monotonic()

    try:
        work_dir.chmod(0o755)  # The sandbox's user must be able to read, not write.
        for filename, content in (("analysis.py", code.encode("utf-8")), ("data.csv", dataset)):
            path = work_dir / filename
            path.write_bytes(content)
            path.chmod(0o444)

        process = subprocess.Popen(
            sandbox_command(name, work_dir, limits),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = bytearray(), bytearray()
        overflow = threading.Event()
        readers = [
            threading.Thread(target=_read_capped, args=(process.stdout, limits.max_output_bytes, overflow, stdout), daemon=True),
            threading.Thread(target=_read_capped, args=(process.stderr, limits.max_log_bytes, None, stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        status: RunStatus | None = None
        deadline = started + timeout
        while process.poll() is None:
            if overflow.is_set():
                status = "output_too_large"
                break
            if time.monotonic() >= deadline:
                status = "timeout"
                break
            time.sleep(0.05)

        if status is not None:
            _kill_container(name)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
        for reader in readers:
            reader.join(timeout=2)

        exit_code = process.returncode
        if status is None:
            status = "completed" if exit_code == 0 else _classify_failure(name)
        duration = round(time.monotonic() - started, 3)
        logger.info("Sandbox run finished: status=%s exit_code=%s duration=%.1fs", status, exit_code, duration)
        return RunResult(
            status=status,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
    finally:
        _remove_container(name)
        shutil.rmtree(work_dir, ignore_errors=True)


def _classify_failure(name: str) -> RunStatus:
    result = _docker("inspect", "--format", "{{.State.OOMKilled}}", name)
    return "out_of_memory" if result.stdout.strip() == "true" else "crashed"


def _kill_container(name: str) -> None:
    try:
        _docker("kill", name)
    except subprocess.TimeoutExpired:
        logger.error("Timed out killing sandbox container %s", name)


def _remove_container(name: str) -> None:
    try:
        _docker("rm", "--force", name)
    except subprocess.TimeoutExpired:
        logger.error("Timed out removing sandbox container %s", name)


def remove_leftover_containers() -> int:
    """Clean up containers left by a crash. Finds them only by our own label."""
    found = _docker("ps", "--all", "--quiet", "--filter", f"label={SANDBOX_LABEL}")
    ids = found.stdout.split()
    if ids:
        _docker("rm", "--force", *ids)
    return len(ids)
