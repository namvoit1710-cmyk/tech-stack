"""ExecutorConsumer unit tests.

Covers:
- Mode gating: ``in_process`` is a no-op; ``executor`` invokes the
  handler and publishes authoritative events.
- Dedup: redelivered ``NodeInput`` for the same (run, node, attempt) is
  short-circuited.
- Authoritative publish: success → NodeOutputProduced + NodeCompleted;
  a raising handler → NodeFailed; handler events forward their own topic.
- Robustness: missing run / malformed envelope never crash the consumer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.events import NodeApprovalFlow
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer1_domain.value_objects import NodeType, RunStatus, TaskStatus
from app.layer4_frameworks.executor.executor_consumer import (
    ExecutorConsumer,
    ExecutorMode,
)
from app.layer4_frameworks.executor.executor_dedup import ExecutorDedup


# ──────────────────────────── stubs ────────────────────────────


class _StubHandler:
    def __init__(self, *, result: NodeHandlerResult | None = None, raise_exc: Exception | None = None) -> None:
        self._result = result or NodeHandlerResult(output_port="success")
        self._raise = raise_exc
        self.calls = 0

    async def handle(self, **kwargs) -> NodeHandlerResult:
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return self._result


class _StubRegistry:
    def __init__(self) -> None:
        self._h: dict[Any, _StubHandler] = {}

    def register(self, node_type, handler) -> None:
        self._h[node_type] = handler

    def get(self, node_type):
        if node_type not in self._h:
            raise KeyError(node_type)
        return self._h[node_type]


class _Repo:
    def __init__(self) -> None:
        self.items: dict[str, Any] = {}

    def get_by_id(self, key):
        return self.items.get(key)

    def save(self, item):
        self.items[item.id] = item


def _build_kit(
    *,
    mode: str = ExecutorMode.EXECUTOR.value,
    handler_result: NodeHandlerResult | None = None,
    handler_raises: Exception | None = None,
    register_node: bool = True,
):
    handler = _StubHandler(result=handler_result, raise_exc=handler_raises)
    registry = _StubRegistry()
    if register_node:
        registry.register(NodeType.TASK, handler)

    workflow = Workflow(
        id="wf-1",
        name="Test",
        nodes=[
            WorkflowNode(
                id="n1", name="One", node_type=NodeType.TASK, worker_type="w1",
            ),
        ],
        edges=[],
    )
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    task = Task(
        id="task-1", run_id="run-1", node_id="n1",
        status=TaskStatus.PENDING, worker_type="w1",
    )

    workflow_repo = _Repo()
    workflow_repo.save(workflow)
    run_repo = _Repo()
    run_repo.save(run)
    task_repo = _Repo()
    task_repo.save(task)

    publisher = MagicMock()
    consumer = ExecutorConsumer(
        handler_registry=registry,
        run_repo=run_repo,
        workflow_repo=workflow_repo,
        task_repo=task_repo,
        task_factory=MagicMock(),
        broker=None,  # direct-notify mode
        dedup=ExecutorDedup(),
        mode=mode,
        publisher=publisher,
    )
    return consumer, publisher, handler, workflow, run, task


def _envelope(node_id="n1", run_id="run-1", task_id="task-1", attempt=0):
    return {
        "run_id": run_id, "node_id": node_id,
        "task_id": task_id, "attempt": attempt,
        "event_type": "NodeInput",
    }


def _topics(publisher) -> list:
    return [args[0] for args, _ in publisher.publish.call_args_list]


# ─────────────────────── mode gating ───────────────────────


@pytest.mark.asyncio
class TestModeGating:
    async def test_in_process_does_not_start(self):
        consumer, _pub, handler, *_ = _build_kit(
            mode=ExecutorMode.IN_PROCESS.value,
        )
        consumer.start()
        consumer.notify(_envelope())
        # No async loop captured, no invocation.
        await consumer.drain_once()
        assert handler.calls == 0
        assert consumer.invocations == 0

    async def test_executor_invokes_handler(self):
        consumer, _pub, handler, *_ = _build_kit(
            mode=ExecutorMode.EXECUTOR.value,
        )
        consumer.start()
        consumer.notify(_envelope())
        await consumer.drain_once()
        assert handler.calls == 1
        assert consumer.invocations == 1

    async def test_executor_mode_publishes_authoritative_events(self):
        """Gate 3 (Phase 3) — under ``executor`` mode the consumer
        publishes ``NodeOutputProduced`` + ``NodeCompleted`` after a
        successful handler invocation."""
        consumer, publisher, handler, *_ = _build_kit(
            mode=ExecutorMode.EXECUTOR.value,
        )
        consumer.start()
        consumer.notify(_envelope())
        await consumer.drain_once()
        assert handler.calls == 1
        # Two publishes: NodeOutputProduced + NodeCompleted.
        topics = _topics(publisher)
        assert "node.output.produced" in str(topics).lower()
        assert "node.completed" in str(topics).lower()

    async def test_executor_mode_publishes_task_failed_on_handler_raise(self):
        """A raising handler maps to ``NodeFailed`` rather than
        ``NodeOutputProduced``; the executor never emits a successful
        completion for a failed invocation."""
        consumer, publisher, _h, *_ = _build_kit(
            mode=ExecutorMode.EXECUTOR.value,
            handler_raises=RuntimeError("boom"),
        )
        consumer.start()
        consumer.notify(_envelope())
        await consumer.drain_once()
        topics = _topics(publisher)
        assert any("node.failed" in str(t).lower() for t in topics)
        assert not any("node.output.produced" in str(t).lower() for t in topics)

    async def test_executor_mode_publishes_approval_flow_topic_from_handler_events(self):
        result = NodeHandlerResult(
            output_port="success",
            events=[
                NodeApprovalFlow(
                    run_id="run-1",
                    workflow_id="wf-1",
                    node_id="n1",
                    change_request_id="cr-1",
                    output_port="success",
                )
            ],
        )
        consumer, publisher, handler, *_ = _build_kit(
            mode=ExecutorMode.EXECUTOR.value,
            handler_result=result,
        )
        consumer.start()
        consumer.notify(_envelope())
        await consumer.drain_once()
        assert handler.calls == 1
        topics = _topics(publisher)
        assert "node.approval_flow.result" in topics


# ─────────────────────── dedup ───────────────────────


@pytest.mark.asyncio
class TestDedup:
    async def test_redelivery_short_circuits(self):
        consumer, _pub, handler, *_ = _build_kit()
        consumer.start()
        consumer.notify(_envelope())
        consumer.notify(_envelope())  # same key
        await consumer.drain_once()
        assert handler.calls == 1

    async def test_different_attempt_invokes_again(self):
        consumer, _pub, handler, *_ = _build_kit()
        consumer.start()
        consumer.notify(_envelope(attempt=0))
        consumer.notify(_envelope(attempt=1))
        await consumer.drain_once()
        assert handler.calls == 2


# ─────────────────────── error robustness ───────────────────────


@pytest.mark.asyncio
class TestRobustness:
    async def test_missing_run_does_not_invoke_handler(self):
        consumer, publisher, handler, *_ = _build_kit()
        consumer.start()
        consumer.notify(_envelope(run_id="run-missing"))
        await consumer.drain_once()
        assert handler.calls == 0
        # A missing run never produces an authoritative success.
        topics = _topics(publisher)
        assert not any("node.output.produced" in str(t).lower() for t in topics)

    async def test_malformed_envelope_dropped(self):
        consumer, _pub, handler, *_ = _build_kit()
        consumer.start()
        consumer.notify({"event_type": "NodeInput"})  # no run_id/node_id
        await consumer.drain_once()
        assert handler.calls == 0
