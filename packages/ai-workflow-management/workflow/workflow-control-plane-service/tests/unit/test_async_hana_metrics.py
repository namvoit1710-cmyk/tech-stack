"""Observability metrics emitted by the async-HANA layer (Task #15).

Three metric sources:

1. ``HanaConnectionManager`` emits pool wait/timeout + executor depth
2. ``save_run_with_cas_retry`` emits per-attempt + exhausted counters
3. No-op contract: every emit is wrapped in try/except so monitor
   failures never break request flow

Uses a recording stub for ``IMonitor`` so we observe exactly which
metrics fired without depending on a real Prometheus registry.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from app.layer2_application import run_save_retry
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)
from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)


class _RecordingMonitor:
    """Captures every metric call so tests can assert on them."""

    def __init__(self) -> None:
        self.counters: list[tuple[str, dict | None]] = []
        self.histograms: list[tuple[str, float, dict | None]] = []
        self.gauges: list[tuple[str, float]] = []

    def increment(self, metric: str, tags=None):
        self.counters.append((metric, dict(tags) if tags else None))

    def histogram(self, metric: str, value: float, tags=None):
        self.histograms.append((metric, value, dict(tags) if tags else None))

    def track(self, metric: str, value: float):
        self.gauges.append((metric, value))


# ─────────────────────── pool acquire metrics ─────────────────────


class TestHanaPoolAcquireMetrics:
    """``_acquire`` emits ``hana_pool_acquire_wait_seconds`` (histogram)
    on every call and ``hana_pool_acquire_timeout_total`` (counter)
    on the 30 s timeout path."""

    def _make_manager(self) -> HanaConnectionManager:
        # Bypass the connect-on-init flow — we only need _acquire's
        # observability semantics, not a real connection.
        mgr = HanaConnectionManager.__new__(HanaConnectionManager)
        mgr._pool = MagicMock()
        mgr._executor = None
        mgr._monitor = None
        return mgr

    def test_acquire_emits_zero_wait_on_fast_path(self):
        mgr = self._make_manager()
        monitor = _RecordingMonitor()
        mgr.set_monitor(monitor)
        # Fast path: pool returns a connection immediately
        conn = MagicMock()
        mgr._pool.get_nowait = MagicMock(return_value=conn)
        mgr._is_stale = MagicMock(return_value=False)

        result = mgr._acquire()

        assert result is conn
        assert len(monitor.histograms) == 1
        name, value, _ = monitor.histograms[0]
        assert name == "hana_pool_acquire_wait_seconds"
        assert value == 0.0

    def test_acquire_emits_timeout_counter_on_pool_exhaustion(self):
        from queue import Empty
        mgr = self._make_manager()
        monitor = _RecordingMonitor()
        mgr.set_monitor(monitor)
        # Empty pool, no headroom to create — forces the
        # ``self._pool.get(timeout=30)`` path to time out.
        mgr._pool.get_nowait = MagicMock(side_effect=Empty)
        mgr._pool.get = MagicMock(side_effect=Empty)
        mgr._lock = MagicMock()
        mgr._lock.__enter__ = MagicMock(return_value=None)
        mgr._lock.__exit__ = MagicMock(return_value=False)
        mgr._created = 100
        mgr._pool_size = 5
        mgr._max_overflow = 10

        with pytest.raises(Empty):
            mgr._acquire()

        # Exactly one timeout counter + one wait histogram observation.
        assert ("hana_pool_acquire_timeout_total", None) in monitor.counters
        assert any(
            name == "hana_pool_acquire_wait_seconds"
            for name, _, _ in monitor.histograms
        )

    def test_emit_is_safe_when_monitor_unset(self):
        mgr = self._make_manager()
        # No set_monitor call — emits must not raise.
        mgr._emit_acquire_wait(0.5)
        mgr._emit_acquire_timeout()
        mgr._sample_executor_depth()


class TestExecutorDepthGauge:
    """``arun`` samples ``hana_executor_queue_depth`` every call."""

    @pytest.mark.asyncio
    async def test_arun_emits_depth_gauge(self):
        mgr = HanaConnectionManager.__new__(HanaConnectionManager)
        mgr._monitor = None
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="t-")
        mgr._executor = executor
        monitor = _RecordingMonitor()
        mgr.set_monitor(monitor)
        try:
            await mgr.arun(lambda: 42)
        finally:
            executor.shutdown(wait=False)

        assert any(name == "hana_executor_queue_depth" for name, _ in monitor.gauges)

    @pytest.mark.asyncio
    async def test_arun_no_emit_when_executor_none(self):
        mgr = HanaConnectionManager.__new__(HanaConnectionManager)
        mgr._monitor = None
        mgr._executor = None
        monitor = _RecordingMonitor()
        mgr.set_monitor(monitor)

        await mgr.arun(lambda: 1)
        # No executor → no depth gauge emitted (the default loop
        # executor isn't ours to sample).
        assert not any(
            name == "hana_executor_queue_depth" for name, _ in monitor.gauges
        )


# ─────────────────────── CAS retry metrics ─────────────────────────


class _StubRunRepo:
    """Run repo stub with configurable conflict-on-attempt behavior."""

    def __init__(self, run, *, conflict_on_attempts=()) -> None:
        self._run = run
        self._conflict_on = set(conflict_on_attempts)
        self.save_calls = 0

    async def aget_by_id(self, run_id):
        return self._run if run_id == self._run.id else None

    async def asave(self, run):
        self.save_calls += 1
        if self.save_calls in self._conflict_on:
            raise OptimisticConcurrencyError(
                run.id, expected=run.version, actual=run.version + 1,
            )


class TestCasRetryMetrics:
    """``save_run_with_cas_retry`` emits one
    ``cas_retry_total{use_case=X}`` per conflict (incl. the final one
    that exhausts), and one ``cas_retry_exhausted_total{use_case=X}``
    when all attempts fail."""

    def setup_method(self):
        # Reset module-level monitor between tests
        run_save_retry.set_monitor(None)

    def teardown_method(self):
        run_save_retry.set_monitor(None)

    @pytest.mark.asyncio
    async def test_one_retry_emits_one_counter_no_exhausted(self):
        from app.layer1_domain.entities.workflow_run import WorkflowRun
        from app.layer1_domain.value_objects.run_status import RunStatus
        run = WorkflowRun(
            id="r-1", workflow_id="wf-1", status=RunStatus.RUNNING, version=1,
        )
        # Conflict on attempt 1, success on attempt 2.
        repo = _StubRunRepo(run, conflict_on_attempts=(1,))
        monitor = _RecordingMonitor()
        run_save_retry.set_monitor(monitor)

        await run_save_retry.save_run_with_cas_retry(
            repo, "r-1", lambda r: None, use_case="my_use_case",
        )

        retry_counters = [
            tags for name, tags in monitor.counters
            if name == "cas_retry_total"
        ]
        assert len(retry_counters) == 1
        assert retry_counters[0] == {"use_case": "my_use_case"}
        exhausted = [
            tags for name, tags in monitor.counters
            if name == "cas_retry_exhausted_total"
        ]
        assert exhausted == []

    @pytest.mark.asyncio
    async def test_all_attempts_fail_emits_exhausted(self):
        from app.layer1_domain.entities.workflow_run import WorkflowRun
        from app.layer1_domain.value_objects.run_status import RunStatus
        run = WorkflowRun(
            id="r-1", workflow_id="wf-1", status=RunStatus.RUNNING, version=1,
        )
        repo = _StubRunRepo(run, conflict_on_attempts=(1, 2, 3))
        monitor = _RecordingMonitor()
        run_save_retry.set_monitor(monitor)

        with pytest.raises(OptimisticConcurrencyError):
            await run_save_retry.save_run_with_cas_retry(
                repo, "r-1", lambda r: None,
                max_attempts=3, base_delay=0.0,
                use_case="hot_path",
            )

        retry_counters = [
            tags for name, tags in monitor.counters
            if name == "cas_retry_total"
        ]
        # One increment per attempt (3 total).
        assert len(retry_counters) == 3
        assert all(t == {"use_case": "hot_path"} for t in retry_counters)
        # Exactly one exhausted increment.
        exhausted = [
            tags for name, tags in monitor.counters
            if name == "cas_retry_exhausted_total"
        ]
        assert exhausted == [{"use_case": "hot_path"}]

    @pytest.mark.asyncio
    async def test_no_monitor_is_safe(self):
        """No emit happens when set_monitor was never called."""
        from app.layer1_domain.entities.workflow_run import WorkflowRun
        from app.layer1_domain.value_objects.run_status import RunStatus
        run = WorkflowRun(
            id="r-1", workflow_id="wf-1", status=RunStatus.RUNNING, version=1,
        )
        repo = _StubRunRepo(run, conflict_on_attempts=(1,))
        # Explicitly leave monitor unset.

        await run_save_retry.save_run_with_cas_retry(
            repo, "r-1", lambda r: None,
        )
        # Test passes if no exception was raised by the emit path.
