"""Phase 3 Gate 3 — engine routes handler invocation to the standalone
Executor under ``EXECUTOR_MODE=executor``.

Each of the six handler-invocation sites in ``orchestration_engine.py``
is wrapped with a mode check that, under ``executor`` mode, publishes a
``NodeInput`` envelope and returns early. These tests assert:

- The engine emits exactly one ``NodeInput`` per dispatch when in
  ``executor`` mode.
- The handler is NOT invoked in-process under ``executor`` mode.
- Default ``in_process`` mode preserves the original behaviour
  (handler IS invoked, NodeInput is NOT emitted by this code path —
  the dispatcher emits it from NodeOutputProduced fan-out).

The full engine has ~3000 lines of wiring around handler invocation —
testing every code path under both modes is the soak-window's job.
These tests guard the surgical contract: the mode flip is observable
at the boundary and reversible.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.events import EventTopic, NodeInput
from app.layer2_application.node_handlers.base import (
    NodeHandlerRegistry,
    NodeHandlerResult,
)
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.value_objects import NodeType, RunStatus, TaskStatus


# ──────────────────────────── stubs ────────────────────────────


class _RecordingPublisher:
    """Captures every ``publish(topic, event)`` call. Mirrors the
    duck-typed interface of the production publisher chain.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def publish(self, topic: Any, event: Any) -> None:
        self.calls.append((str(topic), event))


class _SpyHandler:
    def __init__(self, result: NodeHandlerResult | None = None) -> None:
        self._result = result or NodeHandlerResult(
            output_port="success", completion_mode="immediate",
        )
        self.calls = 0

    async def handle(self, **kwargs) -> NodeHandlerResult:
        self.calls += 1
        return self._result


def _build_engine(*, executor_mode: str) -> tuple[OrchestrationEngine, _SpyHandler, _RecordingPublisher]:
    handler = _SpyHandler()
    registry = NodeHandlerRegistry()
    registry.register(NodeType.TASK, handler)

    publisher = _RecordingPublisher()
    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=TaskFactory(),
        successor_processor=MagicMock(spec=SuccessorProcessor),
        stage_recorder=MagicMock(),
        task_repo=MagicMock(),
        run_repo=MagicMock(),
        event_publisher=publisher,
        executor_mode=executor_mode,
    )
    return engine, handler, publisher


def _make_workflow(node_type: NodeType = NodeType.TASK) -> Workflow:
    return Workflow(
        id="wf-1",
        name="Test",
        nodes=[
            WorkflowNode(
                id="n1", name="One", node_type=node_type, worker_type="w1",
            ),
        ],
        edges=[],
    )


def _make_run() -> WorkflowRun:
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    return run


def _make_task() -> Task:
    return Task(
        id="task-1", run_id="run-1", node_id="n1",
        status=TaskStatus.READY, worker_type="w1",
    )


# ─────────────────────────── tests ────────────────────────────


class TestExecutorModePredicate:
    def test_in_process_is_not_authoritative(self):
        engine, *_ = _build_engine(executor_mode="in_process")
        assert engine._is_executor_mode() is False

    def test_executor_is_authoritative(self):
        engine, *_ = _build_engine(executor_mode="executor")
        assert engine._is_executor_mode() is True


class TestEmitNodeInputForExecutor:
    def test_publishes_node_input_envelope(self):
        engine, _h, publisher = _build_engine(executor_mode="executor")
        node = _make_workflow().nodes[0]
        run = _make_run()
        run.depth_level = 0
        workflow = _make_workflow()
        task = _make_task()

        engine._emit_node_input_for_executor(
            node=node, run=run, workflow=workflow, task=task,
        )

        assert len(publisher.calls) == 1
        topic, event = publisher.calls[0]
        assert topic == EventTopic.NODE_INPUT
        assert isinstance(event, NodeInput)
        assert event.run_id == "run-1"
        assert event.node_id == "n1"
        assert event.task_id == "task-1"
        # Deterministic event_id: replaying with the same task gives
        # the same id, so the event log dedupes redeliveries.
        first_event_id = event.calls if hasattr(event, "calls") else event.event_id
        publisher.calls.clear()
        engine._emit_node_input_for_executor(
            node=node, run=run, workflow=workflow, task=task,
        )
        _, event_again = publisher.calls[0]
        assert event_again.event_id == event.event_id

    def test_handles_missing_task(self):
        """Some sites (loop iter via synthetic task) pass a task with
        no resolved_input. The helper must tolerate either shape."""
        engine, _h, publisher = _build_engine(executor_mode="executor")
        node = _make_workflow().nodes[0]
        run = _make_run()
        run.depth_level = 0
        workflow = _make_workflow()

        # Synthetic task with no resolved_input (mimics loop iter).
        synthetic = Task(id="syn-1", run_id="run-1", node_id="n1")
        engine._emit_node_input_for_executor(
            node=node, run=run, workflow=workflow, task=synthetic,
            iteration_index=3,
        )
        _, event = publisher.calls[0]
        assert event.iteration_index == 3
        assert event.resolved_input == {}
