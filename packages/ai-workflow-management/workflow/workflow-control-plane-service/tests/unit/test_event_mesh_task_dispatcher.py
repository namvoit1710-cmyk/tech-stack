"""Tests for EventMeshTaskDispatcher and WorkerResultsConsumer."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.layer4_frameworks.messaging.event_mesh_task_dispatcher import EventMeshTaskDispatcher
from app.layer4_frameworks.messaging.worker_results_consumer import WorkerResultsConsumer
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import CompleteTaskCommand
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import FailTaskCommand


# ── helpers ──────────────────────────────────────────────────────────────


@dataclass
class _DispatchEvent:
    task_id: str = "task-1"
    run_id: str = "run-1"
    node_id: str = "node-1"
    worker_type: str = "http_request"
    input: dict = field(default_factory=lambda: {"a": 1})
    node_config: dict = field(default_factory=lambda: {"c": 2})
    idempotency_key: str = "idem-1"


class _FakeBroker:
    def __init__(self):
        self.published = []

    def publish_to_topic(self, topic, message, key=None):
        self.published.append({"topic": topic, "message": message, "key": key})


class _FakePublisher:
    def __init__(self):
        self.calls = []

    def publish(self, topic, event):
        self.calls.append((topic, event))


class _RecordingUseCase:
    def __init__(self):
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)


# ── EventMeshTaskDispatcher ────────────────────────────────────────────────


def test_dispatch_event_publishes_to_tasks_topic():
    broker = _FakeBroker()
    fallback = _FakePublisher()
    dispatcher = EventMeshTaskDispatcher(fallback, broker)

    dispatcher.publish("task.dispatched", _DispatchEvent())

    # Always delegates to the wrapped publisher
    assert len(fallback.calls) == 1
    assert fallback.calls[0][0] == "task.dispatched"
    # And publishes the dispatch payload to workflow.tasks keyed by run_id
    assert len(broker.published) == 1
    pub = broker.published[0]
    assert pub["topic"] == "workflow.tasks"
    assert pub["key"] == "run-1"
    assert pub["message"] == {
        "task_id": "task-1",
        "run_id": "run-1",
        "node_id": "node-1",
        "worker_type": "http_request",
        "spec_version": "",  # SA-1490 Phase 2: carried on the payload (unpinned here)
        "input": {"a": 1},
        "node_config": {"c": 2},
        "idempotency_key": "idem-1",
    }


def test_non_dispatch_topic_only_delegates():
    broker = _FakeBroker()
    fallback = _FakePublisher()
    dispatcher = EventMeshTaskDispatcher(fallback, broker)

    dispatcher.publish("run.started", {"foo": "bar"})

    assert len(fallback.calls) == 1
    assert broker.published == []


def test_dispatch_without_worker_type_not_published():
    broker = _FakeBroker()
    dispatcher = EventMeshTaskDispatcher(_FakePublisher(), broker)

    dispatcher.publish("task.dispatched", _DispatchEvent(worker_type=""))

    assert broker.published == []


# ── SA-1526 incident parity (first live event_mesh run hung in RUNNING) ────


def _live_node_dispatched(**overrides) -> dict:
    """The FLAT dict shape EnrichingEventPublisher forwards (observed live —
    no ``payload`` envelope)."""
    event = {
        "task_id": "run-1-node-1",
        "run_id": "run-1",
        "node_id": "node-1",
        "worker_type": "http-request-worker",
        "spec_version": "1.0.0",
        "input": {"url": "https://example.com"},
        "node_config": {"method": "GET"},
        "idempotency_key": "idem-1",
    }
    event.update(overrides)
    return event


def test_node_dispatched_topic_triggers_dispatch():
    """Incident bug #1: the engine emits ``node.dispatched`` — the old topic
    set (task.dispatched only) never matched, so no task ever reached
    workflow.tasks and runs hung in RUNNING."""
    broker = _FakeBroker()
    dispatcher = EventMeshTaskDispatcher(_FakePublisher(), broker)
    dispatcher.publish("node.dispatched", _live_node_dispatched())
    assert len(broker.published) == 1
    assert broker.published[0]["topic"] == "workflow.tasks"
    assert broker.published[0]["key"] == "run-1"
    assert broker.published[0]["message"]["spec_version"] == "1.0.0"  # SA-1490


def test_flat_dict_payload_extracted():
    """Incident bug #2: flat dicts previously extracted via
    ``event['payload']`` → {} → worker_type falsy → silently skipped."""
    broker = _FakeBroker()
    dispatcher = EventMeshTaskDispatcher(_FakePublisher(), broker)
    dispatcher.publish("node.dispatched", _live_node_dispatched())
    msg = broker.published[0]["message"]
    assert msg["input"] == {"url": "https://example.com"}
    assert msg["node_config"] == {"method": "GET"}


def test_payload_envelope_still_tolerated():
    broker = _FakeBroker()
    dispatcher = EventMeshTaskDispatcher(_FakePublisher(), broker)
    dispatcher.publish("node.dispatched", {"payload": _live_node_dispatched()})
    assert broker.published[0]["message"]["run_id"] == "run-1"


def test_lifecycle_only_dispatch_not_forwarded():
    """Incident bug #3a: ``dispatch_to_worker=False`` must reach the projector
    but never the worker (parity with Grpc/Http dispatchers)."""
    broker = _FakeBroker()
    fallback = _FakePublisher()
    dispatcher = EventMeshTaskDispatcher(fallback, broker)
    dispatcher.publish("node.dispatched", _live_node_dispatched(dispatch_to_worker=False))
    assert broker.published == []
    assert len(fallback.calls) == 1


def test_builtin_worker_type_not_forwarded():
    """Incident bug #3b: ``builtin:*`` nodes run inline — forwarding would
    phantom-complete the task."""
    broker = _FakeBroker()
    dispatcher = EventMeshTaskDispatcher(_FakePublisher(), broker)
    dispatcher.publish("node.dispatched", _live_node_dispatched(worker_type="builtin:merge"))
    assert broker.published == []


def test_broker_failure_is_non_fatal():
    class _BoomBroker:
        def publish_to_topic(self, *a, **k):
            raise RuntimeError("mesh down")

    dispatcher = EventMeshTaskDispatcher(_FakePublisher(), _BoomBroker())
    dispatcher.publish("node.dispatched", _live_node_dispatched())  # must not raise


# ── WorkerResultsConsumer ──────────────────────────────────────────────────


async def test_handle_completed_invokes_complete_task():
    complete = _RecordingUseCase()
    fail = _RecordingUseCase()
    consumer = WorkerResultsConsumer({"complete_task": complete, "fail_task": fail}, _FakeBroker())

    await consumer._handle({
        "message_type": "task.completed",
        "correlation_id": "run-1",
        "payload": {
            "run_id": "run-1",
            "task_id": "task-1",
            "output_reference": "csv://ref",
            "output_metadata": {"rows": 3},
        },
    })

    assert len(complete.commands) == 1
    cmd = complete.commands[0]
    assert isinstance(cmd, CompleteTaskCommand)
    assert (cmd.run_id, cmd.task_id, cmd.output_reference, cmd.output_metadata) == (
        "run-1", "task-1", "csv://ref", {"rows": 3},
    )
    assert fail.commands == []


async def test_handle_failed_invokes_fail_task_with_outputs_fallback():
    complete = _RecordingUseCase()
    fail = _RecordingUseCase()
    consumer = WorkerResultsConsumer({"complete_task": complete, "fail_task": fail}, _FakeBroker())

    # No explicit 'outputs' — falls back to output_metadata
    await consumer._handle({
        "message_type": "task.failed",
        "payload": {
            "run_id": "run-1",
            "task_id": "task-1",
            "error": {"code": "WORKER_ERROR", "message": "boom"},
            "output_metadata": {"partial": True},
        },
    })

    assert len(fail.commands) == 1
    cmd = fail.commands[0]
    assert isinstance(cmd, FailTaskCommand)
    assert cmd.run_id == "run-1"
    assert cmd.task_id == "task-1"
    assert cmd.error == {"code": "WORKER_ERROR", "message": "boom"}
    assert cmd.outputs == {"partial": True}
    assert complete.commands == []


async def test_handle_missing_ids_is_ignored():
    complete = _RecordingUseCase()
    fail = _RecordingUseCase()
    consumer = WorkerResultsConsumer({"complete_task": complete, "fail_task": fail}, _FakeBroker())

    await consumer._handle({"message_type": "task.completed", "payload": {"task_id": "task-1"}})

    assert complete.commands == []
    assert fail.commands == []


class _BoomUseCase:
    async def execute(self, command):
        raise RuntimeError("transient db error")


def test_on_message_propagates_handler_exception_for_redelivery():
    """``_on_message`` must NOT swallow handler exceptions — they propagate to
    the broker poll loop, which then skips the ack/commit so the message is
    redelivered (at-least-once). Runs the handler on a background loop, mirroring
    the broker poll thread that hands work to the main loop via
    ``run_coroutine_threadsafe``."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        consumer = WorkerResultsConsumer(
            {"complete_task": _BoomUseCase(), "fail_task": _BoomUseCase()}, _FakeBroker(),
        )
        consumer._loop = loop  # what start() captures from the running loop
        msg = {
            "message_type": "task.completed",
            "payload": {"run_id": "r", "task_id": "t",
                        "output_reference": "", "output_metadata": {}},
        }
        with pytest.raises(RuntimeError, match="transient db error"):
            consumer._on_message(msg)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()
