"""Phase 4 Sub 4.2 — RunStartConsumer.

Closes Gate 3 Gaps 2 + 3 — under EXECUTOR_MODE=executor the engine
no longer drives entry-node dispatch inline; this consumer fires off
``RunStarted`` and ``ChildRunStarted`` events to emit ``NodeInput``
per entry node. Tests cover:

- RunStarted with N entry nodes → N NodeInput events emitted
- ChildRunStarted → entry-node NodeInputs scoped to the child run
- Replay safety: redelivered RunStarted → deterministic event_ids
  collapse downstream NodeInputs at the log level
- Robustness: missing run/workflow/empty entry_nodes/missing publisher
- Tee shape: only RunStarted/ChildRunStarted trigger; other topics
  pass through
- Engine gating: under EXECUTOR_MODE=executor the engine's inline
  ``handle_entry_nodes`` is skipped
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.events import (
    ChildRunStarted,
    EventTopic,
    NodeInput,
    NodeOutputProduced,
    RunStatusChanged,
)
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer4_frameworks.executor import (
    RunStartConsumer,
    RunStartTeeingPublisher,
)


# ──────────────────────────── stubs ────────────────────────────


class _Repo:
    def __init__(self) -> None:
        self.items: dict[str, Any] = {}

    def get_by_id(self, key):
        return self.items.get(key)

    def save(self, item):
        self.items[item.id] = item


class _CapturingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def publish(self, topic, event) -> None:
        self.calls.append((str(topic), event))

    def publish_many(self, events) -> None:
        for entry in events:
            if isinstance(entry, tuple) and len(entry) == 2:
                self.publish(*entry)


def _make_workflow(node_ids: list[str]) -> Workflow:
    return Workflow(
        id="wf-1",
        name="Test",
        nodes=[
            WorkflowNode(id=nid, name=nid, node_type=NodeType.TASK, worker_type="w1")
            for nid in node_ids
        ],
        edges=[],
    )


def _make_run(run_id="run-1", entries=("n1",), workflow_id="wf-1") -> WorkflowRun:
    run = WorkflowRun(id=run_id, workflow_id=workflow_id, status=RunStatus.RUNNING)
    run.context.topology = GraphTopology(entry_nodes=list(entries))
    return run


def _build_kit(*, entries=("n1", "n2")):
    workflow = _make_workflow(list(entries))
    run = _make_run(entries=entries)
    workflow_repo = _Repo()
    workflow_repo.save(workflow)
    run_repo = _Repo()
    run_repo.save(run)
    publisher = _CapturingPublisher()
    consumer = RunStartConsumer(
        run_repo=run_repo, workflow_repo=workflow_repo, publisher=publisher,
    )
    return consumer, run_repo, workflow_repo, publisher


# ─────────────────────── basic dispatch ───────────────────────


class TestRunStarted:
    def test_emits_node_input_per_entry_node(self):
        consumer, _rr, _wr, publisher = _build_kit(entries=("n1", "n2"))
        consumer.on_run_started(RunStatusChanged(status="started", 
            event_id="rs-1", run_id="run-1", workflow_id="wf-1",
        ))

        topics = [t for t, _ in publisher.calls]
        ni_events = [e for t, e in publisher.calls if t == EventTopic.NODE_INPUT]
        assert len(ni_events) == 2
        node_ids = sorted(e.node_id for e in ni_events)
        assert node_ids == ["n1", "n2"]
        # task_id is intentionally empty — TaskCreatorConsumer fills it.
        assert all(e.task_id == "" for e in ni_events)
        assert all(e.run_id == "run-1" for e in ni_events)
        assert consumer.dispatched_count == 2

    def test_replay_safe_event_ids(self):
        """Redelivered RunStarted produces NodeInput envelopes with
        identical event_ids → log dedupes downstream."""
        consumer, *_, publisher = _build_kit(entries=("n1",))
        evt = RunStatusChanged(status="started", event_id="rs-1", run_id="run-1", workflow_id="wf-1")
        consumer.on_run_started(evt)
        consumer.on_run_started(evt)
        ni_events = [e for t, e in publisher.calls if t == EventTopic.NODE_INPUT]
        assert len(ni_events) == 2
        assert ni_events[0].event_id == ni_events[1].event_id


class TestChildRunStarted:
    def test_emits_node_input_for_child_entries(self):
        consumer, run_repo, workflow_repo, publisher = _build_kit()
        # Add child workflow + child run.
        child_wf = _make_workflow(["c1", "c2"])
        child_wf.id = "wf-child"
        workflow_repo.save(child_wf)
        child_run = _make_run(
            run_id="child-1", entries=("c1", "c2"),
            workflow_id="wf-child",
        )
        run_repo.save(child_run)

        consumer.on_child_run_started(ChildRunStarted(
            event_id="crs-1",
            parent_run_id="run-1", parent_node_id="n1",
            child_run_id="child-1", child_workflow_id="wf-child",
        ))
        ni_events = [e for t, e in publisher.calls if t == EventTopic.NODE_INPUT]
        assert len(ni_events) == 2
        assert {e.run_id for e in ni_events} == {"child-1"}
        assert {e.node_id for e in ni_events} == {"c1", "c2"}
        assert consumer.dispatched_child_count == 2


# ─────────────────────── robustness ───────────────────────


class TestRobustness:
    def test_missing_run_skips(self):
        consumer, *_, publisher = _build_kit()
        consumer.on_run_started(RunStatusChanged(status="started", run_id="run-missing"))
        assert publisher.calls == []

    def test_missing_workflow_skips(self):
        consumer, _rr, workflow_repo, publisher = _build_kit()
        workflow_repo.items.clear()
        consumer.on_run_started(RunStatusChanged(status="started", run_id="run-1"))
        assert publisher.calls == []

    def test_empty_entry_nodes_skips(self):
        consumer, run_repo, workflow_repo, publisher = _build_kit()
        # Replace the run's topology with an empty entry list.
        run = run_repo.items["run-1"]
        run.context.topology = GraphTopology(entry_nodes=[])
        consumer.on_run_started(RunStatusChanged(status="started", run_id="run-1"))
        assert publisher.calls == []

    def test_missing_node_in_workflow_skipped(self):
        """Entry list references a node not in the workflow — skipped
        with a warning, other entries still dispatched."""
        consumer, run_repo, workflow_repo, publisher = _build_kit()
        run = run_repo.items["run-1"]
        run.context.topology = GraphTopology(entry_nodes=["n1", "n-missing", "n2"])
        consumer.on_run_started(RunStatusChanged(status="started", run_id="run-1"))
        ni_events = [e for t, e in publisher.calls if t == EventTopic.NODE_INPUT]
        # Only the two real entry nodes dispatched.
        assert {e.node_id for e in ni_events} == {"n1", "n2"}

    def test_publisher_unset_does_not_crash(self):
        consumer, *_ = _build_kit()
        consumer._publisher = None
        # Must not raise.
        consumer.on_run_started(RunStatusChanged(status="started", run_id="run-1"))
        assert consumer.dispatched_count == 0

    def test_consumer_swallows_exceptions(self):
        consumer, *_ = _build_kit()
        consumer._run_repo = MagicMock(
            get_by_id=MagicMock(side_effect=RuntimeError("boom")),
        )
        # Public method must not propagate.
        consumer.on_run_started(RunStatusChanged(status="started", run_id="run-1"))


# ─────────────────────── tee shape ───────────────────────


class TestTeeingPublisher:
    def test_run_started_triggers_consumer(self):
        consumer, *_ = _build_kit()
        inner = _CapturingPublisher()
        tee = RunStartTeeingPublisher(inner=inner, consumer=consumer)
        consumer.set_publisher(tee)

        tee.publish(EventTopic.RUN_STATUS_CHANGED, RunStatusChanged(status="started", 
            event_id="rs-1", run_id="run-1", workflow_id="wf-1",
        ))
        # Inner saw RunStarted plus the NodeInputs the consumer
        # re-published.
        topics = [t for t, _ in inner.calls]
        assert EventTopic.RUN_STATUS_CHANGED in topics
        assert topics.count(EventTopic.NODE_INPUT) == 2

    def test_node_output_produced_passes_through(self):
        consumer, *_ = _build_kit()
        inner = _CapturingPublisher()
        tee = RunStartTeeingPublisher(inner=inner, consumer=consumer)
        nop = NodeOutputProduced(run_id="run-1", workflow_id="wf-1", node_id="n1")
        tee.publish(EventTopic.NODE_OUTPUT_PRODUCED, nop)
        # Only inner's pass-through; no NodeInput emissions.
        topics = [t for t, _ in inner.calls]
        assert EventTopic.NODE_INPUT not in topics


# ─────────────────────── engine gating ───────────────────────


class TestEngineGating:
    """The engine's ``_is_executor_mode`` gate decides whether
    ``handle_entry_nodes`` runs inline. These tests assert the predicate
    matches the executor mode without booting a full engine."""

    def test_in_process_mode_keeps_inline_walk(self):
        from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
        engine = OrchestrationEngine.__new__(OrchestrationEngine)
        engine._executor_mode = "in_process"
        assert engine._is_executor_mode() is False

    def test_executor_mode_skips_inline_walk(self):
        from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
        engine = OrchestrationEngine.__new__(OrchestrationEngine)
        engine._executor_mode = "executor"
        assert engine._is_executor_mode() is True
