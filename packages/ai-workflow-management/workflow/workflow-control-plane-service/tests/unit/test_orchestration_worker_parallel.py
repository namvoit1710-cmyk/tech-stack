"""Multi-worker parallel execution — ORCHESTRATION_WORKER_COUNT > 1.

Validates two invariants of running N ``OrchestrationWorker`` coroutines
against a shared ``MemoryOrchestrationQueue``:

1. Different runs proceed CONCURRENTLY — total wall-clock for two
   ~T-second runs is closer to T than to 2*T when N >= 2.
2. Same-run actions SERIALISE via the engine's per-run ``asyncio.Lock``
   — two actions for the same run never overlap, even with N workers.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.layer2_application.interfaces.orchestration_queue_interface import (
    StartRunAction,
)
from app.layer2_application.orchestration_queue.memory_queue import (
    MemoryOrchestrationQueue,
)
from app.layer4_frameworks.orchestration_queue.worker import OrchestrationWorker


class _FakeEngine:
    """Minimal engine stub that mimics OrchestrationEngine's locking contract.

    Exposes ``run_lock(run_id)`` returning per-run ``asyncio.Lock`` and an
    ``orchestrate_start`` coroutine that records overlap windows so the
    test can assert concurrency / serialisation properties.
    """

    def __init__(self, work_seconds: float) -> None:
        self._work = work_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        # In-flight window per run_id: list of (start_ts, end_ts) tuples
        self.windows: dict[str, list[tuple[float, float]]] = {}

    def run_lock(self, run_id: str) -> asyncio.Lock:
        if run_id not in self._locks:
            self._locks[run_id] = asyncio.Lock()
        return self._locks[run_id]

    async def orchestrate_start(self, run_id: str) -> None:
        async with self.run_lock(run_id):
            start = time.monotonic()
            # Simulate I/O-bound handler work (yields the loop)
            await asyncio.sleep(self._work)
            end = time.monotonic()
            self.windows.setdefault(run_id, []).append((start, end))


def _windows_overlap(
    a: tuple[float, float], b: tuple[float, float],
) -> bool:
    """Return True if [a_start, a_end] and [b_start, b_end] overlap."""
    return a[0] < b[1] and b[0] < a[1]


async def _drive(workers: list[OrchestrationWorker], duration: float) -> None:
    """Start N workers, run for ``duration`` seconds, then cancel."""
    tasks = [asyncio.create_task(w.run_forever()) for w in workers]
    await asyncio.sleep(duration)
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_two_workers_run_two_runs_in_parallel() -> None:
    """Two runs each with ~0.2s of handler work complete in ~0.2s total
    (not 0.4s) when 2 workers drain the queue."""
    work = 0.2
    engine = _FakeEngine(work_seconds=work)
    queue = MemoryOrchestrationQueue()

    workers = [
        OrchestrationWorker(queue=queue, engine=engine)
        for _ in range(2)
    ]

    await queue.enqueue(StartRunAction(run_id="run-A"))
    await queue.enqueue(StartRunAction(run_id="run-B"))

    await _drive(workers, duration=work + 0.15)

    # Both runs processed exactly once
    assert engine.windows.get("run-A"), "run-A was never processed"
    assert engine.windows.get("run-B"), "run-B was never processed"
    assert len(engine.windows["run-A"]) == 1
    assert len(engine.windows["run-B"]) == 1

    # Windows overlap — proves true concurrency, not interleaved serial
    win_a = engine.windows["run-A"][0]
    win_b = engine.windows["run-B"][0]
    assert _windows_overlap(win_a, win_b), (
        f"run-A {win_a} and run-B {win_b} did not overlap — "
        f"workers ran serially"
    )

    # Wall-clock budget: measure the actual processing span (earliest start
    # to latest end across both runs). Each handler takes ``work`` seconds;
    # if serial the span would be >= 2*work, concurrent → close to ``work``.
    # Measuring the recorded windows rather than total drive time excludes
    # the fixed teardown sleep in ``_drive`` (work + 0.15s) and cancellation
    # overhead, which would otherwise dominate and make this assertion flaky.
    span = max(win_a[1], win_b[1]) - min(win_a[0], win_b[0])
    assert span < 1.8 * work, (
        f"processing span={span:.3f}s suggests serial execution "
        f"(expected ~{work}s)"
    )


@pytest.mark.asyncio
async def test_same_run_actions_serialise_across_workers() -> None:
    """Two ``StartRunAction``s for the SAME run_id never overlap, even
    when 4 workers drain the queue."""
    work = 0.1
    engine = _FakeEngine(work_seconds=work)
    queue = MemoryOrchestrationQueue()

    workers = [
        OrchestrationWorker(queue=queue, engine=engine)
        for _ in range(4)
    ]

    # Enqueue 3 actions for the same run
    for _ in range(3):
        await queue.enqueue(StartRunAction(run_id="run-X"))

    await _drive(workers, duration=3 * work + 0.2)

    windows = engine.windows.get("run-X", [])
    assert len(windows) == 3, (
        f"Expected 3 invocations for run-X, got {len(windows)}"
    )

    # Sort by start time, then assert no overlapping pair
    windows.sort(key=lambda w: w[0])
    for prev, curr in zip(windows, windows[1:]):
        assert not _windows_overlap(prev, curr), (
            f"Same-run actions overlapped: {prev} and {curr} — "
            f"per-run lock is not protecting against concurrent workers"
        )


def test_default_orchestration_worker_count_is_4_for_1gb_pod(monkeypatch) -> None:
    """``ORCHESTRATION_WORKER_COUNT`` default is 4, sized for a 1 GB pod.

    Construct a fresh ``Settings`` instance instead of reading the cached
    module-level singleton — the singleton was bound at import time and
    may reflect whichever env var was set when this test process started.
    """
    monkeypatch.delenv("ORCHESTRATION_WORKER_COUNT", raising=False)
    from app.layer4_frameworks.config.app_config import Settings
    fresh = Settings()
    assert fresh.ORCHESTRATION_WORKER_COUNT == 4, (
        "Default ORCHESTRATION_WORKER_COUNT changed; update the memory math"
    )


def test_orchestration_worker_count_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATION_WORKER_COUNT", "8")
    from app.layer4_frameworks.config.app_config import Settings
    assert Settings().ORCHESTRATION_WORKER_COUNT == 8


@pytest.mark.asyncio
async def test_one_worker_preserves_serial_behaviour() -> None:
    """``ORCHESTRATION_WORKER_COUNT=1`` (explicit, single-pod / low-memory
    deployment) processes actions strictly serially — windows never
    overlap regardless of run_id. This is the back-compat path; the
    shipped default for 1 GB pods is 4."""
    work = 0.1
    engine = _FakeEngine(work_seconds=work)
    queue = MemoryOrchestrationQueue()

    workers = [OrchestrationWorker(queue=queue, engine=engine)]

    await queue.enqueue(StartRunAction(run_id="run-A"))
    await queue.enqueue(StartRunAction(run_id="run-B"))

    start = time.monotonic()
    await _drive(workers, duration=2 * work + 0.15)
    elapsed = time.monotonic() - start

    win_a = engine.windows["run-A"][0]
    win_b = engine.windows["run-B"][0]
    assert not _windows_overlap(win_a, win_b), (
        "With 1 worker, different-run windows should NOT overlap"
    )
    # Should take roughly 2*work, not less
    assert elapsed >= 2 * work, (
        f"elapsed={elapsed:.3f}s — expected >= {2*work}s for serial"
    )
