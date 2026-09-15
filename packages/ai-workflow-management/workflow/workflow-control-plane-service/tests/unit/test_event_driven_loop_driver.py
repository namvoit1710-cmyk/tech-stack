"""Unit tests for the Phase 5.2.1 ``EventDrivenLoopDriver``.

This file exercises the driver's **run_loop** entry point — the
engine-facing API that owns full iteration orchestration. The
driver's ``initialize_loop`` / ``iteration_complete`` methods
(which the handler delegates to) are already covered by the
Phase 4c.4.3 handler tests in ``test_loop_handler_event_driven.py``
and the Phase 4c.4.5 break tests in ``test_loop_exit_handler.py``.
Those tests continue passing because the handler's event-driven
methods are now thin delegation shims that forward to the driver.

This file adds tests that specifically target ``run_loop``:

- End-to-end sequential loop with strict event-order assertions
  (proving the Phase 5.2.1 ordering fix works).
- End-to-end parallel loop.
- Empty collection short-circuits correctly.
- Break mid-iteration via ``LoopExitNodeHandler``.
- Condition-false early exit.

The driver is invoked directly with injected callables for
``enqueue_or_dispatch`` and ``publish_events`` — no real engine
involved — so these tests stay unit-scoped. A separate integration
test in ``tests/integration/test_event_driven_loop.py`` proves the
full engine wiring.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.events import (
    DomainEvent,
    LoopCompleted,
    LoopIterationCompleted,
    LoopIterationStarted,
    LoopStarted,
)
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus


# ──────────────────────────── helpers ─────────────────────────────────


def _resolver():
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _factory():
    return TaskFactory()


def _driver():
    return EventDrivenLoopDriver(_resolver())


def _simple_loop_workflow(items: list, mode: str = "sequential", **config):
    """LOOP → body (TRIGGER-typed stub) → exit (TRIGGER-typed stub).

    TRIGGER is used as the body/exit node type so the dummy dispatch
    closure below can recognise and immediately-complete them without
    needing a real handler registry.
    """
    loop_cfg: dict = {"collection": "{{$input.items}}", "mode": mode}
    loop_cfg.update(config)
    loop_node = WorkflowNode(id="loop", name="loop", node_type=NodeType.LOOP, config=loop_cfg)
    body = WorkflowNode(id="body", name="body", node_type=NodeType.TRIGGER)
    exit_node = WorkflowNode(id="exit", name="exit", node_type=NodeType.TRIGGER)
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[loop_node, body, exit_node],
        edges=[
            WorkflowEdge(id="e-body", source_node_id="loop", target_node_id="body", condition="body"),
            WorkflowEdge(id="e-exit", source_node_id="loop", target_node_id="exit", condition="exit"),
        ],
    )
    dag = GraphTopology(
        edge_map={"loop": workflow.edges, "body": [], "exit": []},
        adjacency={"loop": ["body", "exit"], "body": [], "exit": []},
        predecessors={"loop": [], "body": ["loop"], "exit": ["loop"]},
        entry_nodes=["loop"],
        leaf_nodes=["body", "exit"],
    )
    run = WorkflowRun(
        id="run-1", workflow_id="wf",
        context=RunContext(
            variables={"input": {"items": items}},
            topology=dag,
        ),
    )
    return loop_node, workflow, dag, run


class _DispatchRecorder:
    """Mock enqueue_or_dispatch callable that records every dispatched
    task and auto-marks body/exit nodes as completed so the driver's
    walker-style loop can advance.

    A real engine would recursively walk the dispatched task's handler
    chain. Here we simulate that side-effect: when a body task is
    dispatched, we add it to ``run.context.completed_nodes`` so the
    next iteration can proceed. Nothing about this mock depends on
    the driver internals — it's the minimum observable contract.
    """

    def __init__(self):
        self.dispatched: list[Task] = []

    async def __call__(self, task, run, workflow, topology):
        self.dispatched.append(task)
        run.context.completed_nodes.add(task.node_id)


class _PublishRecorder:
    """Mock publish_events callable that records every event batch
    in the order ``run_loop`` emits them. Flat ``events`` list for
    order assertions; ``batches`` preserved for callers that want to
    see the drain grouping."""

    def __init__(self):
        self.events: list[DomainEvent] = []
        self.batches: list[list[DomainEvent]] = []

    def __call__(self, events):
        self.batches.append(list(events))
        self.events.extend(events)


# RunLoop inline-mode tests removed — coverage now via tests/integration/test_queued_event_driven_loop.py
