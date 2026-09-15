"""Phase 4 Sub 4.1 — TaskCreatorConsumer.

Closes Gate 3 Gap 1 — under EXECUTOR_MODE=executor the engine no
longer creates downstream Task entities, so a publisher-tee on
``NodeInput`` does it instead. These tests cover:

- Basic create: a NodeInput with empty task_id leads to a Task save +
  ``NodeDispatched`` emission.
- Replay safety: redelivered NodeInput → no double-save (idempotent
  via deterministic task_id), no second NodeDispatched.
- Skip when engine populated task_id (entry node case).
- Robustness: missing run / workflow / node logs and continues.
- Tee shape: only ``NodeInput`` events trigger creation; other topics
  pass through unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.events import (
    EventTopic,
    NodeInput,
    NodeOutputProduced,
    NodeDispatched,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer4_frameworks.executor import (
    TaskCreatorConsumer,
    TaskCreatorTeeingPublisher,
)


# ──────────────────────────── stubs ────────────────────────────


class _Repo:
    def __init__(self) -> None:
        self.items: dict[str, object] = {}

    def get_by_id(self, key):
        return self.items.get(key)

    def save(self, item):
        self.items[item.id] = item


class _CapturingPublisher:
    """Mirrors the production publisher's ``publish``/``publish_many``
    duck-typed interface."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def publish(self, topic, event) -> None:
        self.calls.append((str(topic), event))

    def publish_many(self, events) -> None:
        for entry in events:
            if isinstance(entry, tuple) and len(entry) == 2:
                self.publish(*entry)


def _make_workflow() -> Workflow:
    return Workflow(
        id="wf-1",
        name="Test",
        nodes=[
            WorkflowNode(
                id="n2", name="Two", node_type=NodeType.TASK, worker_type="w1",
            ),
        ],
        edges=[],
    )


def _make_run() -> WorkflowRun:
    return WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)


def _build_kit():
    workflow = _make_workflow()
    run = _make_run()

    workflow_repo = _Repo()
    workflow_repo.save(workflow)
    run_repo = _Repo()
    run_repo.save(run)
    task_repo = _Repo()
    publisher = _CapturingPublisher()

    consumer = TaskCreatorConsumer(
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        run_repo=run_repo,
        task_factory=TaskFactory(),
        publisher=publisher,
    )
    return consumer, task_repo, run_repo, workflow_repo, publisher


def _node_input(node_id="n2", run_id="run-1", task_id="", event_id="ev-1"):
    return NodeInput(
        event_id=event_id,
        run_id=run_id,
        workflow_id="wf-1",
        node_id=node_id,
        task_id=task_id,
        resolved_input={},
    )


# ─────────────────────── basic creation ───────────────────────


class TestBasicCreation:
    def test_node_input_creates_task_and_emits_dispatched(self):
        consumer, task_repo, _run_repo, _wf_repo, publisher = _build_kit()
        consumer.on_node_input(_node_input())

        # Task saved with deterministic id ``run-1-n2``.
        assert "run-1-n2" in task_repo.items
        task = task_repo.items["run-1-n2"]
        assert task.run_id == "run-1"
        assert task.node_id == "n2"
        assert task.worker_type == "w1"

        # ``NodeDispatched`` published.
        topics = [t for t, _ in publisher.calls]
        assert EventTopic.NODE_DISPATCHED in topics
        td = next(e for t, e in publisher.calls if t == EventTopic.NODE_DISPATCHED)
        assert isinstance(td, NodeDispatched)
        assert td.task_id == "run-1-n2"
        assert td.node_id == "n2"
        assert td.source == "task-creator"
        # Tracking-only: carries the (empty) source resolved_input, so the
        # task dispatchers must NOT forward it to the worker.
        assert td.dispatch_to_worker is False

        assert consumer.created_count == 1
        assert consumer.skipped_existing_count == 0


# ─────────────────────── replay safety ───────────────────────


class TestReplaySafety:
    def test_redelivery_is_idempotent(self):
        consumer, task_repo, *_ = _build_kit()
        evt = _node_input()
        consumer.on_node_input(evt)
        consumer.on_node_input(evt)
        # Single Task; counter shows the second pass short-circuited.
        assert len(task_repo.items) == 1
        assert consumer.created_count == 1
        assert consumer.skipped_existing_count == 1

    def test_no_double_task_dispatched_on_replay(self):
        consumer, _tr, _rr, _wr, publisher = _build_kit()
        evt = _node_input()
        consumer.on_node_input(evt)
        consumer.on_node_input(evt)
        td_count = sum(1 for t, _ in publisher.calls if t == EventTopic.NODE_DISPATCHED)
        assert td_count == 1


# ─────────────────────── skip-existing cases ───────────────────────


class TestSkipExisting:
    def test_skip_when_envelope_carries_task_id(self):
        """The engine emits the entry-node ``NodeInput`` itself with
        ``task_id`` populated; the consumer must not duplicate work."""
        consumer, task_repo, *_ = _build_kit()
        consumer.on_node_input(_node_input(task_id="already-exists"))
        assert task_repo.items == {}
        assert consumer.created_count == 0


# ─────────────────────── robustness ───────────────────────


class TestRobustness:
    def test_missing_run_skips_silently(self):
        consumer, task_repo, run_repo, *_ = _build_kit()
        consumer.on_node_input(_node_input(run_id="run-missing"))
        assert task_repo.items == {}
        assert consumer.created_count == 0

    def test_missing_workflow_skips_silently(self):
        consumer, task_repo, _rr, workflow_repo, _ = _build_kit()
        # Remove the workflow.
        workflow_repo.items.clear()
        consumer.on_node_input(_node_input())
        assert task_repo.items == {}

    def test_missing_node_skips_silently(self):
        consumer, task_repo, *_ = _build_kit()
        consumer.on_node_input(_node_input(node_id="n-missing"))
        assert task_repo.items == {}

    def test_publisher_failure_does_not_crash(self):
        consumer, task_repo, _rr, _wr, publisher = _build_kit()
        publisher.publish = MagicMock(side_effect=RuntimeError("publish boom"))
        # Must not propagate — task should still be saved before
        # the publisher raises.
        consumer.on_node_input(_node_input())
        assert "run-1-n2" in task_repo.items

    def test_handler_exception_swallowed_via_public_method(self):
        """``on_node_input`` is the public entry; it wraps internals
        in a try/except to keep the publish chain healthy."""
        consumer, *_ = _build_kit()
        consumer._task_repo = MagicMock(
            get_by_id=MagicMock(side_effect=RuntimeError("repo boom")),
        )
        # Should not raise.
        consumer.on_node_input(_node_input())


# ─────────────────────── tee shape ───────────────────────


class TestTeeingPublisher:
    def test_node_input_triggers_consumer(self):
        consumer, task_repo, *_ = _build_kit()
        inner = _CapturingPublisher()
        tee = TaskCreatorTeeingPublisher(inner=inner, consumer=consumer)
        # Re-bind consumer's publisher to the tee so NodeDispatched
        # also flows through it (mirrors bootstrap wiring).
        consumer.set_publisher(tee)

        tee.publish(EventTopic.NODE_INPUT, _node_input())
        # Inner saw the original ``NodeInput`` AND the consumer's
        # ``NodeDispatched`` re-publish.
        topics = [t for t, _ in inner.calls]
        assert EventTopic.NODE_INPUT in topics
        assert EventTopic.NODE_DISPATCHED in topics
        # Task created.
        assert "run-1-n2" in task_repo.items

    def test_non_node_input_passes_through(self):
        consumer, task_repo, *_ = _build_kit()
        inner = _CapturingPublisher()
        tee = TaskCreatorTeeingPublisher(inner=inner, consumer=consumer)
        # NodeOutputProduced should pass through without triggering
        # task creation.
        nop = NodeOutputProduced(
            run_id="run-1", workflow_id="wf-1", node_id="n2",
        )
        tee.publish(EventTopic.NODE_OUTPUT_PRODUCED, nop)
        # Inner saw it; consumer didn't fire.
        assert (EventTopic.NODE_OUTPUT_PRODUCED, nop) in inner.calls
        assert task_repo.items == {}

    def test_publish_many_handles_tuples(self):
        consumer, task_repo, *_ = _build_kit()
        inner = _CapturingPublisher()
        tee = TaskCreatorTeeingPublisher(inner=inner, consumer=consumer)
        consumer.set_publisher(tee)
        tee.publish_many([
            (EventTopic.NODE_INPUT, _node_input(node_id="n2")),
        ])
        assert "run-1-n2" in task_repo.items
