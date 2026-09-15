"""Unit tests for OrchestrationEngine's event-buffering contract.

These tests guard against the regression where two duplicate definitions
of ``publish_event`` / ``publish_events`` shadowed the EventCollector
shims and published events immediately. That bypass made
``input.requested`` reach the wire before the ``node.dispatched`` events
that should precede it.

Properties verified:

1. ``publish_event`` and ``publish_events`` buffer rather than publish.
2. ``drain_events`` actually delivers buffered events to the inner
   publisher.
3. The error/early-return paths in ``orchestrate_start`` flush before
   returning, so ``run.failed`` always reaches the wire.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.events import RunStatusChanged, NodeDispatched
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus


class _CapturingPublisher:
    """Records every (topic, event) tuple instead of forwarding anywhere."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event) -> None:
        self.events.append((topic, event))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


def _wire_async_repo_mocks(repo):
    """Make a MagicMock-based repo cooperate with the engine's
    ``await repo.a<method>(...)`` calls (Task #19). MagicMock
    auto-creates ``aget_by_id``/``asave``/``alist_by_run`` as plain
    callables; awaiting them raises ``TypeError``. Replace with
    AsyncMocks that delegate to the sync return values."""
    if not isinstance(repo, MagicMock):
        return repo
    repo.aget_by_id = AsyncMock(side_effect=lambda *a, **kw: repo.get_by_id(*a, **kw))
    repo.asave = AsyncMock(side_effect=lambda *a, **kw: repo.save(*a, **kw))
    repo.alist_by_run = AsyncMock(
        side_effect=lambda *a, **kw: repo.list_by_run(*a, **kw),
    )
    return repo


def _engine(event_publisher=None, workflow_repo=None, run_repo=None):
    return OrchestrationEngine(
        handler_registry=MagicMock(),
        task_factory=TaskFactory(),
        successor_processor=MagicMock(),
        stage_recorder=MagicMock(),
        task_repo=_wire_async_repo_mocks(MagicMock()),
        run_repo=_wire_async_repo_mocks(run_repo or MagicMock()),
        event_publisher=event_publisher,
        workflow_repo=_wire_async_repo_mocks(workflow_repo or MagicMock()),
    )


# ---------------------------------------------------------------------------
# 1. publish_event / publish_events buffer (do not fire immediately)
# ---------------------------------------------------------------------------


class TestPublishRoutesThroughCollector:
    """Event-emission contract for OrchestrationEngine.

    Current design (see orchestration_engine.py ~lines 470-506, 880-891):

    * ``emit`` (singular) BUFFERS through the per-async-task
      EventCollector and only reaches the wire on ``drain_events``.
      This is the ordering-sensitive path — verified by
      ``test_drain_preserves_priority_ordering`` below.
    * ``publish_event`` / ``publish_events`` publish IMMEDIATELY via
      ``_publish_now`` so an event can never strand at a call site that
      lacks a drain boundary.

    The "input.requested must not overtake node.dispatched" ordering this
    file used to enforce by making ``publish_event`` buffer is now enforced
    structurally in ``_exec_uniform``: the per-mode runner emits
    ``node.dispatched`` and ``drain_events()`` flushes it BEFORE the handler's
    tail events (e.g. ``input.requested``) are published via
    ``_publish_now_many``. End-to-end ordering is covered by
    tests/integration/test_child_workflow_input_event_order.py.
    """

    @pytest.mark.asyncio
    async def test_publish_event_publishes_immediately(self):
        publisher = _CapturingPublisher()
        engine = _engine(event_publisher=publisher)

        engine.publish_event(
            "node.dispatched",
            NodeDispatched(task_id="t1", run_id="r1", workflow_id="w1", node_id="n1"),
        )
        # publish_event is the immediate (non-buffering) alias: the event
        # reaches the publisher without a drain boundary.
        assert publisher.topics() == ["node.dispatched"]

    @pytest.mark.asyncio
    async def test_publish_events_publishes_immediately(self):
        publisher = _CapturingPublisher()
        engine = _engine(event_publisher=publisher)

        engine.publish_events([
            NodeDispatched(task_id="t1", run_id="r1", workflow_id="w1", node_id="n1"),
        ])
        # Immediate, and the topic is derived from the event (NodeDispatched
        # → "node.dispatched") via topic_for_event.
        assert publisher.topics() == ["node.dispatched"]

    @pytest.mark.asyncio
    async def test_buffer_event_singular_does_not_fire_until_drain(self):
        """The ordering-sensitive path: ``emit`` (singular) holds the
        event in the collector until ``drain_events``."""
        publisher = _CapturingPublisher()
        engine = _engine(event_publisher=publisher)

        engine.emit(
            "node.dispatched",
            NodeDispatched(task_id="t1", run_id="r1", workflow_id="w1", node_id="n1"),
        )
        assert publisher.events == [], (
            "emit must hold the event in the EventCollector until drain"
        )

        await engine.drain_events()
        assert publisher.topics() == ["node.dispatched"]

    @pytest.mark.asyncio
    async def test_drain_preserves_priority_ordering(self):
        """The whole point of routing through the collector: drain order
        is (priority, insertion_seq), not call-site order.
        """
        publisher = _CapturingPublisher()
        engine = _engine(event_publisher=publisher)

        # Insertion order: TERMINAL, NORMAL, IMMEDIATE — drain must reverse it.
        engine.emit("run.status", RunStatusChanged(run_id="r", status="completed"))
        engine.emit("node.completed", "normal")
        engine.emit("node.dispatched", "immediate")

        await engine.drain_events()
        assert publisher.topics() == [
            "node.dispatched",   # IMMEDIATE first
            "node.completed",    # NORMAL
            "run.status",        # TERMINAL last
        ]

    @pytest.mark.asyncio
    async def test_drain_calls_publisher_inside_running_loop(self):
        """Regression: ``drain_events`` must invoke the publisher INSIDE the
        current async task — not via ``asyncio.to_thread`` — because the
        realtime notifier chain (``PushGatewayNotifier`` / Socket.IO)
        calls ``asyncio.get_running_loop()`` to schedule async sends.

        A previous change wrapped the publish call in ``asyncio.to_thread``;
        inside that worker thread ``get_running_loop()`` raises
        ``RuntimeError`` and the notifier silently dropped every event,
        causing "no events reach the push gateway in production".
        """
        import asyncio
        captured_loops: list = []
        publish_errors: list = []

        class _LoopRequiringPublisher:
            """Mirrors PushGatewayNotifier.notify's loop requirement."""
            def publish(self, topic, event):
                try:
                    captured_loops.append(asyncio.get_running_loop())
                except RuntimeError as exc:
                    publish_errors.append(exc)

        engine = _engine(event_publisher=_LoopRequiringPublisher())
        engine.publish_event(
            "node.dispatched",
            NodeDispatched(task_id="t1", run_id="r1", workflow_id="w1", node_id="n1"),
        )

        loop_before = asyncio.get_running_loop()
        await engine.drain_events()

        assert publish_errors == [], (
            f"publisher.publish saw RuntimeError(s) from get_running_loop(): "
            f"{publish_errors}. drain_events is running publish in a worker "
            f"thread (asyncio.to_thread). Call it directly so the realtime "
            f"notifier chain has access to the loop."
        )
        assert captured_loops == [loop_before], (
            f"publisher should run in the same loop as the caller; got "
            f"{captured_loops} vs {loop_before}"
        )


# ---------------------------------------------------------------------------
# 2. orchestrate_start error paths must flush before returning
# ---------------------------------------------------------------------------


def _minimal_workflow():
    return Workflow(
        id="wf-1",
        nodes=[WorkflowNode(id="trigger", name="Trigger", node_type=NodeType.TRIGGER)],
        edges=[],
    )


def _running_run(workflow_id="wf-1", run_id="run-1"):
    topology = GraphTopology(
        adjacency={"trigger": []},
        predecessors={"trigger": []},
        edge_map={"trigger": []},
        entry_nodes=["trigger"],
        leaf_nodes=["trigger"],
    )
    return WorkflowRun(
        id=run_id,
        workflow_id=workflow_id,
        status=RunStatus.RUNNING,
        context=RunContext(topology=topology),
    )


class TestOrchestrateStartFlushesOnFailure:
    """Once publish_event is buffered, the run.failed early-return paths
    in orchestrate_start MUST flush — otherwise that final event is
    stranded in the collector and the frontend never learns the run died.
    """

    @pytest.mark.asyncio
    async def test_exception_in_handle_entry_nodes_publishes_run_failed(self):
        from tests.conftest import _get_shared_snapshot_repo
        from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot
        workflow = _minimal_workflow()
        run = _running_run()

        # Phase B+C: the engine reads workflow via a snapshot repo.
        # Capture into the shared test snapshot repo (the conftest
        # init shim wires that into every ``OrchestrationEngine``).
        run.workflow_snapshot_id = capture_snapshot(workflow, _get_shared_snapshot_repo())

        workflow_repo = MagicMock()
        workflow_repo.get_by_id.return_value = workflow
        run_repo = MagicMock()
        run_repo.get_by_id.return_value = run

        publisher = _CapturingPublisher()
        engine = _engine(
            event_publisher=publisher,
            workflow_repo=workflow_repo,
            run_repo=run_repo,
        )

        async def _boom(*args, **kwargs):
            raise RuntimeError("handler exploded")

        # Force the except branch by making handle_entry_nodes blow up.
        engine.handle_entry_nodes = _boom  # type: ignore[assignment]

        await engine.orchestrate_start(run.id)

        assert run.status == RunStatus.FAILED
        assert "run.status" in publisher.topics(), (
            f"run.status should reach the wire when orchestrate_start's "
            f"except block fires; got {publisher.topics()}. Missing "
            f"drain_events() after the publish_event would strand this event."
        )
        run_failed = next(e for t, e in publisher.events if t == "run.status")
        assert isinstance(run_failed, RunStatusChanged)
        assert run_failed.status == "failed"
        assert "handler exploded" in run_failed.error
