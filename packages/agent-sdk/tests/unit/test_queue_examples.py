from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES_DIR = _REPO_ROOT / "examples"


def _run_example(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_EXAMPLES_DIR / name), "--demo"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_kafka_consumer_demo_exits_zero():
    result = _run_example("kafka_consumer_example.py")

    assert result.returncode == 0, (
        "kafka_consumer_example.py --demo must exit 0.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_kafka_consumer_demo_outputs_queue_resume_markers():
    result = _run_example("kafka_consumer_example.py")

    assert result.returncode == 0, (
        "kafka_consumer_example.py --demo must exit 0 before output assertions.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    expected_markers = [
        "Scenario 3: queue-native delegation and resume",
        "Original delivery acknowledged",
        "Delegated request reply path: topic=agent.responses queue=default/agent.request",
        "Received correlated agent.response",
        "Resume delivery acknowledged",
        "Parent thread resumed: conv-delegate-003",
        "Correlation state cleaned up",
    ]

    for marker in expected_markers:
        assert (
            marker in result.stdout
        ), f"Missing demo marker: {marker}\nSTDOUT:\n{result.stdout}"


def test_queue_native_supervisor_demo_exits_zero():
    result = _run_example("queue_native_supervisor_example.py")

    assert result.returncode == 0, (
        "queue_native_supervisor_example.py --demo must exit 0.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_queue_native_supervisor_demo_outputs_queue_markers():
    result = _run_example("queue_native_supervisor_example.py")

    assert result.returncode == 0, (
        "queue_native_supervisor_example.py --demo must exit 0 before output assertions.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    expected_markers = [
        "Resolved queue route from registry: request_topic=planner.request.topic reply_topic=supervisor.request",
        "Published agent.request.agent",
        "Received agent.response",
        "Supervisor thread resumed: supervisor-thread-001",
        "Correlation state cleaned up",
        "Demo passed — queue-first supervisor flow verified end-to-end.",
    ]

    for marker in expected_markers:
        assert (
            marker in result.stdout
        ), f"Missing demo marker: {marker}\nSTDOUT:\n{result.stdout}"
