"""Regression: a thrown orchestration action force-fails the run instead of
leaving it stuck RUNNING forever (bug #4 hang).

When ``orchestrate_continue`` (or any run-advancing action) raises, the broker
has already acked the action so there is no redelivery, and shadow mode wires
no completion backstop — without the worker safety net the run hangs forever.
The worker must call ``engine.force_fail_run`` so the run reaches a terminal
state, and must do so without itself crashing the worker loop.
"""

from __future__ import annotations

import pytest

from app.layer2_application.interfaces.orchestration_queue_interface import (
    ContinueRunAction,
    StartRunAction,
)
from app.layer4_frameworks.orchestration_queue.worker import OrchestrationWorker


class _ThrowingEngine:
    """Engine whose run-advancing handlers raise; records force_fail_run."""

    def __init__(self) -> None:
        self.force_failed: list[tuple[str, str]] = []
        self.continued = 0

    async def orchestrate_continue(self, run_id, task_id, test_mode=None):
        raise RuntimeError("boom in successors")

    async def orchestrate_start(self, run_id):
        raise RuntimeError("boom at start")

    async def force_fail_run(self, run_id, error_message):
        self.force_failed.append((run_id, error_message))


class _SucceedingEngine(_ThrowingEngine):
    async def orchestrate_continue(self, run_id, task_id, test_mode=None):
        self.continued += 1  # no raise → success path


class _NoForceFailEngine:
    """Engine double predating force_fail_run — worker must not crash."""

    async def orchestrate_continue(self, run_id, task_id, test_mode=None):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_thrown_continue_force_fails_the_run() -> None:
    engine = _ThrowingEngine()
    worker = OrchestrationWorker(queue=object(), engine=engine)

    await worker._dispatch(ContinueRunAction(run_id="run-1", task_id="t-1"))

    assert engine.force_failed, "run was not force-failed — it would hang"
    run_id, msg = engine.force_failed[0]
    assert run_id == "run-1"
    # Message carries the action type ("continue") and the original error so
    # the FAILED run is diagnosable.
    assert "continue" in msg
    assert "boom in successors" in msg


@pytest.mark.asyncio
async def test_thrown_start_also_force_fails() -> None:
    engine = _ThrowingEngine()
    worker = OrchestrationWorker(queue=object(), engine=engine)

    await worker._dispatch(StartRunAction(run_id="run-2"))

    assert [r for r, _ in engine.force_failed] == ["run-2"]


@pytest.mark.asyncio
async def test_successful_action_does_not_force_fail() -> None:
    engine = _SucceedingEngine()
    worker = OrchestrationWorker(queue=object(), engine=engine)

    await worker._dispatch(ContinueRunAction(run_id="run-ok", task_id="t-ok"))

    assert engine.continued == 1
    assert engine.force_failed == [], "successful action must not force-fail"


@pytest.mark.asyncio
async def test_missing_force_fail_run_does_not_crash_worker() -> None:
    """Engines that predate ``force_fail_run`` (e.g. MagicMock doubles) must
    not break the worker loop — the safety net is best-effort."""
    worker = OrchestrationWorker(queue=object(), engine=_NoForceFailEngine())
    # Must not raise even though the engine cannot force-fail.
    await worker._dispatch(ContinueRunAction(run_id="run-3", task_id="t-3"))
