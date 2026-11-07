"""Attacks a real Docker sandbox. These run only where Docker and the image exist (the server).

Run with: DATAPILOT_RUN_SANDBOX_TESTS=1 pytest tests/sandbox
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from executor.docker_runner import SandboxLimits, run_in_sandbox, sandbox_command

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATAPILOT_RUN_SANDBOX_TESTS"),
    reason="Needs Docker, so this runs on the server only.",
)

CSV = b"order_date,product,quantity,unit_price\n2025-01-15,Pen,2,1.5\n2025-02-20,Book,3,10\n2025-02-21,Pen,,1.5\n"
LIMITS = SandboxLimits(timeout_seconds=15)


def run(code: str, tmp_path: Path, limits: SandboxLimits = LIMITS):
    return run_in_sandbox(code, CSV, limits, tmp_path)


def run_ok(code: str, tmp_path: Path) -> dict:
    result = run(code, tmp_path)
    assert result.status == "completed", result.stderr
    document = json.loads(result.stdout)
    assert document["status"] == "ok", document
    return document


def metric(document: dict, name: str):
    return next(m["value"] for m in document["metrics"] if m["name"] == name)


# --- Normal use ---


def test_calculates_revenue_from_the_real_data(tmp_path):
    document = run_ok(
        "emit_metric('revenue', (df.quantity * df.unit_price).sum())\n"
        "print('hello from the log')",
        tmp_path,
    )

    assert metric(document, "revenue") == 33.0  # 2*1.5 + 3*10, worked out by hand.


def test_prints_go_to_logs_and_cannot_corrupt_results(tmp_path):
    result = run("print('{not json')\nemit_metric('a', 1)", tmp_path)

    assert json.loads(result.stdout)["metrics"][0]["value"] == 1
    assert "{not json" in result.stderr


def test_reports_tables_and_charts_as_plain_data(tmp_path):
    document = run_ok(
        "t = df.groupby('product', as_index=False)['unit_price'].sum()\n"
        "emit_table('prices', t)\n"
        "emit_chart('c', 'bar', t, x='product', y='unit_price', title='Prices')",
        tmp_path,
    )

    assert document["tables"][0]["columns"] == ["product", "unit_price"]
    assert document["charts"][0]["x"] == ["Book", "Pen"]
    assert document["charts"][0]["y"] == [10.0, 3.0]


def test_code_errors_are_reported_with_the_line_number(tmp_path):
    document = json.loads(run("x = 1\nraise ValueError('boom')", tmp_path).stdout)

    assert document == {"status": "error", "error_type": "ValueError", "message": "boom", "line": 2}


def test_sys_exit_cannot_skip_the_report(tmp_path):
    document = json.loads(run("import sys\nsys.exit(0)", tmp_path).stdout)

    assert document["status"] == "error"
    assert document["error_type"] == "SystemExit"


# --- Identity and privileges ---


def test_runs_as_a_non_root_user_with_no_capabilities(tmp_path):
    document = run_ok(
        "import os\n"
        "emit_metric('uid', os.getuid())\n"
        "status = open('/proc/self/status').read()\n"
        "effective = [l for l in status.splitlines() if l.startswith('CapEff')][0].split()[1]\n"
        "emit_metric('caps', int(effective, 16))\n"
        "emit_metric('no_new_privs', int([l for l in status.splitlines() if l.startswith('NoNewPrivs')][0].split()[1]))",
        tmp_path,
    )

    assert metric(document, "uid") == 10001
    assert metric(document, "caps") == 0
    assert metric(document, "no_new_privs") == 1


# --- Filesystem ---

ATTEMPT_WRITES = """
import os, shutil
blocked = 0
def attempt(action):
    global blocked
    try:
        action()
    except OSError:
        blocked += 1
attempt(lambda: os.remove('/input/data.csv'))
attempt(lambda: open('/input/data.csv', 'a').write('tampered'))
attempt(lambda: open('/input/new.txt', 'w').write('x'))
attempt(lambda: open('/sandbox/runner.py', 'a').write('x'))
attempt(lambda: open('/etc/passwd', 'a').write('x'))
attempt(lambda: open('/usr/evil', 'w').write('x'))
attempt(lambda: shutil.rmtree('/usr/lib'))
emit_metric('blocked', blocked)
emit_metric('rows', len(__import__('pandas').read_csv('/input/data.csv')))
"""


def test_cannot_change_any_files_including_rm_rf(tmp_path):
    document = run_ok(ATTEMPT_WRITES, tmp_path)

    assert metric(document, "blocked") == 7
    assert metric(document, "rows") == 3


def test_temporary_space_is_limited_and_not_executable(tmp_path):
    document = run_ok(
        "import os, stat, subprocess\n"
        "written = 0\n"
        "try:\n"
        "    with open('/tmp/fill', 'wb') as f:\n"
        "        for _ in range(200):\n"
        "            f.write(b'x' * 1024 * 1024); f.flush(); written += 1\n"
        "except OSError:\n"
        "    pass\n"
        "os.remove('/tmp/fill')\n"
        "emit_metric('written_mb', written)\n"
        "open('/tmp/s.sh', 'w').write('#!/bin/sh\\necho hi\\n')\n"
        "os.chmod('/tmp/s.sh', 0o755)\n"
        "try:\n"
        "    subprocess.run(['/tmp/s.sh'], check=True)\n"
        "    emit_metric('ran', 1)\n"
        "except OSError:\n"
        "    emit_metric('ran', 0)\n",
        tmp_path,
    )

    assert metric(document, "written_mb") <= 64
    assert metric(document, "ran") == 0


# --- Network ---


def test_has_no_network(tmp_path):
    document = run_ok(
        "import socket\n"
        "results = 0\n"
        "for host, port in [('1.1.1.1', 53), ('8.8.8.8', 443), ('169.254.169.254', 80), ('172.17.0.1', 8100)]:\n"
        "    try:\n"
        "        socket.create_connection((host, port), timeout=2)\n"
        "        results += 1\n"
        "    except OSError:\n"
        "        pass\n"
        "emit_metric('connections', results)\n"
        "try:\n"
        "    socket.gethostbyname('example.com')\n"
        "    emit_metric('dns', 1)\n"
        "except OSError:\n"
        "    emit_metric('dns', 0)\n"
        "emit_metric('interfaces', len([i for i in socket.if_nameindex() if i[1] != 'lo']))",
        tmp_path,
    )

    assert metric(document, "connections") == 0
    assert metric(document, "dns") == 0
    assert metric(document, "interfaces") == 0


# --- Secrets ---


def test_no_secrets_or_docker_socket_inside(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "must-not-appear-in-the-sandbox")
    monkeypatch.setenv("EXECUTOR_TOKEN", "must-not-appear-either")
    document = run_ok(
        "import os\n"
        "text = ' '.join(f'{k}={v}' for k, v in os.environ.items())\n"
        "emit_metric('leaks', sum(word in text for word in ['must-not', 'AI_API_KEY', 'EXECUTOR_TOKEN', 'TOKEN', 'SECRET']))\n"
        "paths = ['/var/run/docker.sock', '/run/docker.sock', '/home/deploy', '/root/.ssh', '/etc/datapilot']\n"
        "emit_metric('visible', sum(os.path.exists(p) for p in paths))\n",
        tmp_path,
    )

    assert metric(document, "leaks") == 0
    assert metric(document, "visible") == 0


# --- Resource exhaustion ---


def test_infinite_loop_is_stopped_at_the_time_limit(tmp_path):
    result = run_in_sandbox("while True:\n    pass", CSV, LIMITS, tmp_path, timeout_seconds=3)

    assert result.status == "timeout"
    assert 3 <= result.duration_seconds < 25


def test_memory_bomb_is_contained(tmp_path):
    result = run("chunks = []\nwhile True:\n    chunks.append(bytearray(50 * 1024 * 1024))", tmp_path)

    assert result.status in ("out_of_memory", "completed")
    if result.status == "completed":
        assert json.loads(result.stdout)["error_type"] == "MemoryError"


def test_fork_bomb_is_contained(tmp_path):
    result = run(
        "import os, time\n"
        "count = 0\n"
        "try:\n"
        "    while count < 1000:\n"
        "        if os.fork() == 0:\n"
        "            time.sleep(60)\n"
        "            os._exit(0)\n"
        "        count += 1\n"
        "except OSError:\n"
        "    pass\n"
        "emit_metric('processes', count)",
        tmp_path,
    )

    assert result.status in ("completed", "timeout", "crashed")
    if result.status == "completed":
        assert json.loads(result.stdout)["metrics"][0]["value"] < 64


def test_huge_output_is_cut_off(tmp_path):
    result = run(
        "import os\n"
        "for fd in range(3, 10):\n"
        "    try:\n"
        "        while True:\n"
        "            os.write(fd, b'x' * 1024 * 1024)\n"
        "    except OSError:\n"
        "        pass",
        tmp_path,
    )

    assert result.status == "output_too_large"
    assert len(result.stdout) <= LIMITS.max_output_bytes
    assert result.duration_seconds < 20


def test_huge_logs_are_capped(tmp_path):
    result = run("for _ in range(2000):\n    print('x' * 1024 * 100)", tmp_path)

    assert len(result.stderr) <= LIMITS.max_log_bytes


# --- Lifecycle ---


def containers() -> list[str]:
    output = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", "label=datapilot.sandbox=1"],
        capture_output=True,
        text=True,
        check=True,
    )
    return output.stdout.split()


def test_nothing_is_left_behind_even_after_a_timeout(tmp_path):
    work_root = tmp_path / "runs"
    run_in_sandbox("emit_metric('a', 1)", CSV, LIMITS, work_root)
    run_in_sandbox("while True:\n    pass", CSV, LIMITS, work_root, timeout_seconds=2)

    assert containers() == []
    assert list(work_root.iterdir()) == []


def test_files_do_not_survive_between_runs(tmp_path):
    run_ok("open('/tmp/left-behind', 'w').write('x')\nemit_metric('a', 1)", tmp_path)

    document = run_ok("import os\nemit_metric('exists', int(os.path.exists('/tmp/left-behind')))", tmp_path)

    assert metric(document, "exists") == 0


def test_callers_cannot_extend_the_time_limit(tmp_path):
    limits = SandboxLimits(timeout_seconds=2)

    result = run_in_sandbox("while True:\n    pass", CSV, limits, tmp_path, timeout_seconds=100)

    assert result.status == "timeout"
    assert result.duration_seconds < 15


def test_command_has_every_security_setting():
    command = " ".join(sandbox_command("n", Path("/x"), SandboxLimits()))

    for setting in [
        "--network none",
        "--read-only",
        "--user 10001:10001",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 64",
        "--memory 768m",
        "--memory-swap 768m",
        "--cpus 1.0",
        "readonly",
    ]:
        assert setting in command
    assert "docker.sock" not in command
    assert "--privileged" not in command
    assert " -e " not in command
    assert "--env" not in command


def test_original_data_is_read_only_but_copies_are_editable(tmp_path):
    document = run_ok(
        "data = df.assign(revenue=df.quantity * df.unit_price)\n"
        "data['x'] = 1\n"
        "try:\n"
        "    df['revenue'] = 1\n"
        "    emit_metric('blocked', 0)\n"
        "except TypeError:\n"
        "    emit_metric('blocked', 1)\n"
        "emit_metric('missing', int(df.isna().sum().sum()))",
        tmp_path,
    )

    assert metric(document, "blocked") == 1
    assert metric(document, "missing") == 1  # Only the real blank, not the new column's.
