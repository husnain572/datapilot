"""The Containment Lab: fixed attack scripts run in the real sandbox, to show they're contained.

The scripts are written here, never by users or the AI. They use a tiny made-up dataset, not
anyone's uploaded data. Each one is judged by what actually happened in its run.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from backend.schemas.analysis import ContainmentReport, ContainmentResult
from backend.services.executor_client import ExecutionResult, ExecutorClient

LAB_CSV = "item,amount\nA,1\nB,2\n"
RUN_TIMEOUT_SECONDS = 6


def _metrics(run: ExecutionResult) -> dict[str, float]:
    try:
        document = json.loads(run.stdout)
        return {metric["name"]: metric["value"] for metric in document.get("metrics", [])}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}


def _judge_files(run: ExecutionResult) -> tuple[bool, str]:
    found = _metrics(run)
    contained = found.get("blocked") == 3 and found.get("rows_left") == 2
    return contained, f"{found.get('blocked', 0):.0f} of 3 destructive actions were blocked, and the data is intact."


def _judge_loop(run: ExecutionResult) -> tuple[bool, str]:
    return run.status == "timeout", f"Stopped from outside after {run.duration_seconds:.1f} seconds."


def _judge_fork(run: ExecutionResult) -> tuple[bool, str]:
    count = _metrics(run).get("processes")
    if count is None:
        return run.status in ("timeout", "crashed"), f"The run ended with status '{run.status}'."
    return count < 64, f"Stopped at {count:.0f} processes, where the limit is 64."


def _judge_memory(run: ExecutionResult) -> tuple[bool, str]:
    if run.status == "out_of_memory":
        return True, "Killed when it reached the 768 MB memory limit."
    try:
        error_type = json.loads(run.stdout).get("error_type")
    except ValueError:
        error_type = None
    return error_type == "MemoryError" or run.status == "timeout", f"The run ended with status '{run.status}'."


def _judge_network(run: ExecutionResult) -> tuple[bool, str]:
    found = _metrics(run)
    contained = found.get("connections") == 0 and found.get("interfaces") == 0
    return contained, f"{found.get('connections', 0):.0f} of 3 connections succeeded, with no network interfaces."


def _judge_secrets(run: ExecutionResult) -> tuple[bool, str]:
    found = _metrics(run)
    contained = found.get("env_matches") == 0 and found.get("paths_found") == 0
    return contained, f"Found {found.get('env_matches', 0):.0f} secrets in its environment and {found.get('paths_found', 0):.0f} sensitive paths."


def _judge_disk(run: ExecutionResult) -> tuple[bool, str]:
    written = _metrics(run).get("written_mb")
    if written is None:
        return False, f"The run ended with status '{run.status}'."
    return written <= 64, f"Stopped after {written:.0f} MB, where the limit is 64 MB."


@dataclass(frozen=True)
class Attack:
    name: str
    description: str
    code: str
    judge: Callable[[ExecutionResult], tuple[bool, str]]


ATTACKS = [
    Attack(
        "Delete everything",
        "Tries rm -rf on system files and deletes the dataset",
        """
import os, shutil
blocked = 0
for action in (lambda: shutil.rmtree('/usr/lib'), lambda: os.remove('/input/data.csv'), lambda: open('/etc/passwd', 'a').write('x')):
    try:
        action()
    except OSError:
        blocked += 1
emit_metric('blocked', blocked)
emit_metric('rows_left', len(pd.read_csv('/input/data.csv')))
""",
        _judge_files,
    ),
    Attack("Infinite loop", "Tries to run forever", "while True:\n    pass\n", _judge_loop),
    Attack(
        "Fork bomb",
        "Tries to create endless processes",
        """
import os, time
count = 0
try:
    while count < 500:
        if os.fork() == 0:
            time.sleep(30)
            os._exit(0)
        count += 1
except OSError:
    pass
emit_metric('processes', count)
""",
        _judge_fork,
    ),
    Attack(
        "Memory bomb",
        "Tries to use all the server's memory",
        "chunks = []\nwhile True:\n    chunks.append(bytearray(b'x' * (50 * 1024 * 1024)))\n",
        _judge_memory,
    ),
    Attack(
        "Fill the disk",
        "Tries to write endlessly to temporary storage",
        """
written = 0
try:
    with open('/tmp/fill', 'wb') as file:
        for _ in range(300):
            file.write(b'x' * 1024 * 1024)
            file.flush()
            written += 1
except OSError:
    pass
emit_metric('written_mb', written)
""",
        _judge_disk,
    ),
    Attack(
        "Send data out",
        "Tries to reach the internet and the cloud metadata service",
        """
import socket
connections = 0
for target in [('1.1.1.1', 53), ('8.8.8.8', 443), ('169.254.169.254', 80)]:
    try:
        socket.create_connection(target, timeout=1)
        connections += 1
    except OSError:
        pass
emit_metric('connections', connections)
emit_metric('interfaces', len([i for i in socket.if_nameindex() if i[1] != 'lo']))
""",
        _judge_network,
    ),
    Attack(
        "Steal secrets",
        "Searches for API keys, tokens, and the Docker socket",
        """
import os
words = ('AI_API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'ACCESS_CODE')
emit_metric('env_matches', sum(any(w in k.upper() for w in words) for k in os.environ))
paths = ['/var/run/docker.sock', '/run/docker.sock', '/home/deploy/datapilot/.env', '/root/.ssh', '/etc/datapilot']
emit_metric('paths_found', sum(os.path.exists(p) for p in paths))
""",
        _judge_secrets,
    ),
]


def run_containment_lab(executor: ExecutorClient) -> ContainmentReport:
    results = []
    for attack in ATTACKS:
        run = executor.run(attack.code, LAB_CSV, timeout_seconds=RUN_TIMEOUT_SECONDS)
        contained, evidence = attack.judge(run)
        results.append(
            ContainmentResult(
                name=attack.name,
                attack=attack.description,
                contained=contained,
                evidence=evidence,
                duration_seconds=run.duration_seconds,
            )
        )
    return ContainmentReport(all_contained=all(result.contained for result in results), results=results)
