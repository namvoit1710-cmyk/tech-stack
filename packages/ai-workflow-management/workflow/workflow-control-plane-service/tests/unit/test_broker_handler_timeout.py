"""Every thread→loop bridge must be bounded, and the bound must be real.

Three of the four bridges that hand a broker message to the event loop wait for
the coroutine so the ack/commit reflects its outcome. That is correct. What was
not correct is that the wait had no bound in practice:

* ``EventMeshConsumer`` waits with ``handler_timeout_s``, which
  ``EventMeshConfig`` declares as ``float | None = None`` and **nothing ever
  set** → ``fut.result(timeout=None)`` → wait forever.
* ``KafkaBrokerClient`` takes ``handler_timeout_s: float | None = None`` and
  the construction site **never passed it** — directly beneath a comment
  reading "bounded by ``handler_timeout_s``".
* ``WorkerResultsConsumer`` called a bare ``future.result()``. It also
  registers a *sync* callback, so the broker's own guard never applied: the
  broker takes its ``else: handler(payload)`` branch.

One wedged handler therefore pinned a poll thread for the life of the pod. For
``workflow.results`` that means every ``task.completed`` stops arriving and
every run in the tenant stalls — with nothing logged and no metric moving.

These tests assert the wiring, not just the existence of a parameter, because
a parameter nobody passes is exactly the bug.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError

import pytest

from app.layer4_frameworks.messaging.worker_results_consumer import (
    WorkerResultsConsumer,
)


class _Monitor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def increment(self, name: str, tags: dict | None = None) -> None:
        self.calls.append((name, tags or {}))


class TestTheWaitIsBounded:
    def test_a_wedged_handler_does_not_pin_the_poll_thread(self):
        """The whole point: the thread comes back."""
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            monitor = _Monitor()
            container = {"monitor": monitor, "complete_task": _WedgedUseCase()}
            consumer = WorkerResultsConsumer(
                container, broker=None, handler_timeout_s=0.05,
            )
            consumer._loop = loop

            message = {
                "message_type": "task.completed",
                "payload": {"run_id": "r1", "task_id": "t1"},
            }

            # The poll thread is THIS thread. If the bound is missing, this
            # call never returns and the test hangs rather than fails — which
            # is exactly what the pod did.
            with pytest.raises(FuturesTimeoutError):
                consumer._on_message(message)

            assert ("worker_result_handler_timeout", {"outcome": "timeout"}) \
                in monitor.calls, "a silent timeout is the bug wearing a hat"
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()

    def test_the_exception_propagates_so_the_broker_redelivers(self):
        """Swallowing the timeout would ack a completion that never happened,
        which loses the result and hangs the run permanently — strictly worse
        than the wedge it replaced."""
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            consumer = WorkerResultsConsumer(
                {"complete_task": _WedgedUseCase()}, broker=None,
                handler_timeout_s=0.05,
            )
            consumer._loop = loop
            with pytest.raises(FuturesTimeoutError):
                consumer._on_message({
                    "message_type": "task.completed",
                    "payload": {"run_id": "r1", "task_id": "t1"},
                })
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()

    def test_a_healthy_handler_is_untouched(self):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            uc = _RecordingUseCase()
            consumer = WorkerResultsConsumer(
                {"complete_task": uc}, broker=None, handler_timeout_s=30.0,
            )
            consumer._loop = loop
            consumer._on_message({
                "message_type": "task.completed",
                "payload": {"run_id": "r1", "task_id": "t1"},
            })
            assert len(uc.commands) == 1
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()


class TestTheBoundIsActuallyWired:
    """A default on a parameter proves nothing if no call site passes it."""

    def test_settings_expose_the_timeout(self):
        from app.layer4_frameworks.config.app_config import Settings
        assert Settings().BROKER_HANDLER_TIMEOUT_S > 0

    def test_every_construction_site_passes_it(self):
        """Reads the wiring module's source. Blunt, but it is the only thing
        that catches 'the parameter exists and nobody passes it' — the exact
        shape of the original defect."""
        import inspect
        import app.layer4_frameworks as framework

        source = inspect.getsource(framework)
        # KafkaBrokerClient + primary EventMeshConfig + secondary EventMeshConfig
        assert source.count("handler_timeout_s=float(") == 3, (
            "a broker client is being constructed without a handler timeout; "
            "its poll thread will block forever on a wedged handler"
        )

    def test_bootstrap_passes_it_to_the_results_consumer(self):
        import inspect
        import bootstrap

        source = inspect.getsource(bootstrap)
        marker = source.split("WorkerResultsConsumer(", 1)[1][:300]
        assert "handler_timeout_s" in marker


class _WedgedUseCase:
    async def execute(self, _command):
        await asyncio.sleep(3600)


class _RecordingUseCase:
    def __init__(self) -> None:
        self.commands: list = []

    async def execute(self, command):
        self.commands.append(command)
