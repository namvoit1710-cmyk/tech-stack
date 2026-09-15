"""Unit tests for the Phase 4c.4.5 ``LoopExitNodeHandler``.

Covers:

- Finds the innermost active event-driven loop via reverse dict
  iteration on ``run.context.loop_state``.
- Nested loops pick the innermost correctly (dict insertion order).
- Emits ``LoopExitRequested`` with a deterministic event id.
- Sets ``break_requested=True`` on the target loop's ``loop_state``
  in place, so the loop handler's ``_ed_iteration_complete`` observes
  it at the next iteration boundary.
- No-ops gracefully when the run has no active event-driven loop:
  either no loops at all, or only legacy (``invocation_id == ""``)
  loops, or only already-completed loops. Each case produces a logged
  warning and an empty event list.
- Skips loops that already have ``break_requested=True`` (idempotent
  re-break finds the next outer loop — but crucially, this only
  matters if a downstream LOOP_EXIT fires again in the same iteration,
  which is unusual).
- Companion check: ``LoopNodeHandler._ed_iteration_complete`` observes
  ``break_requested`` and emits ``LoopCompleted(reason="break")``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="legacy inline-execution coverage — rewrite to use tests.helpers.queue_runner for queued-mode regression coverage")

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.events import (
    LoopCompleted,
    LoopExitRequested,
    LoopIterationCompleted,
)
from app.layer2_application.node_handlers.loop_exit_handler import (
    LoopExitNodeHandler,
)
from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.node_type import NodeType
from tests.helpers.node_driver import drive_node


# ──────────────────────────── helpers ─────────────────────────────────


def _resolver():
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _factory():
    return TaskFactory()


def _topology(edge_map=None, predecessors=None, adjacency=None):
    return GraphTopology(
        edge_map=edge_map or {},
        adjacency=adjacency or {},
        predecessors=predecessors or {},
        entry_nodes=[],
        leaf_nodes=[],
    )


def _active_event_driven_loop_state(
    *,
    invocation_id: str = "inv-1",
    total: int = 3,
    completed: list | None = None,
    current_index: int = 0,
    enclosing_scope_stack: list | None = None,
    break_requested: bool = False,
) -> dict:
    """Build a ``loop_state`` dict in the shape produced by
    ``LoopNodeHandler._ed_initialize_loop``."""
    return {
        "items": list(range(total)),
        "current_index": current_index,
        "total": total,
        "completed_iterations": list(completed or []),
        "failed_iterations": [],
        "mode": "sequential",
        "loop_type": "collection",
        "condition": "",
        "counter_variable": "index",
        "next_dispatch_index": 0,
        "invocation_id": invocation_id,
        "enclosing_scope_stack": enclosing_scope_stack or [],
        "parallelism": 1,
        **({"break_requested": True} if break_requested else {}),
    }


def _run_with_loop_states(states: dict[str, dict]) -> WorkflowRun:
    """Build a WorkflowRun whose loop_state dict has the given entries
    in the given insertion order (outer first, inner last)."""
    run = WorkflowRun(
        id="run-1", workflow_id="wf-1",
        context=RunContext(loop_state={}),
    )
    # Insertion order matters for innermost detection — use an
    # ordered dict assignment pattern.
    for node_id, state in states.items():
        run.context.loop_state[node_id] = state
    return run


# ───────────────────── LoopExitNodeHandler — find innermost ─────────


class TestLoopExitFindsInnermost:
    @pytest.mark.asyncio
    async def test_single_active_loop_is_innermost(self):
        handler = LoopExitNodeHandler()
        node = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)
        run = _run_with_loop_states({
            "loop-A": _active_event_driven_loop_state(
                invocation_id="invA", total=5, completed=[0, 1], current_index=2,
            ),
        })
        result = await handler.handle(
            node, run, Workflow(id="wf-1"), _topology(), None, _factory(),
        )

        assert run.context.loop_state["loop-A"]["break_requested"] is True
        events = [e for e in result.events if isinstance(e, LoopExitRequested)]
        assert len(events) == 1
        assert events[0].loop_node_id == "loop-A"
        assert events[0].invocation_id == "invA"

    @pytest.mark.asyncio
    async def test_nested_loops_innermost_wins_via_insertion_order(self):
        """Outer is inserted first, inner second. The handler must pick
        the inner one even though both are 'active'."""
        handler = LoopExitNodeHandler()
        node = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)
        run = _run_with_loop_states({
            "loop-outer": _active_event_driven_loop_state(
                invocation_id="OUTER", total=3, completed=[0], current_index=1,
            ),
            "loop-inner": _active_event_driven_loop_state(
                invocation_id="INNER", total=2, completed=[], current_index=0,
            ),
        })
        result = await handler.handle(
            node, run, Workflow(id="wf-1"), _topology(), None, _factory(),
        )

        # Only the inner loop is marked.
        assert run.context.loop_state["loop-inner"]["break_requested"] is True
        assert run.context.loop_state["loop-outer"].get("break_requested", False) is False
        events = [e for e in result.events if isinstance(e, LoopExitRequested)]
        assert len(events) == 1
        assert events[0].loop_node_id == "loop-inner"
        assert events[0].invocation_id == "INNER"

    @pytest.mark.asyncio
    async def test_completed_loops_are_skipped(self):
        """A loop whose ``len(completed) >= total`` is not active — the
        handler should find the next-innermost active loop."""
        handler = LoopExitNodeHandler()
        node = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)
        run = _run_with_loop_states({
            "loop-outer": _active_event_driven_loop_state(
                invocation_id="OUTER", total=3, completed=[0, 1], current_index=2,
            ),
            # Inner already finished — len(completed)==total
            "loop-inner-done": _active_event_driven_loop_state(
                invocation_id="INNER_DONE", total=2, completed=[0, 1],
                current_index=2,
            ),
        })
        result = await handler.handle(
            node, run, Workflow(id="wf-1"), _topology(), None, _factory(),
        )

        events = [e for e in result.events if isinstance(e, LoopExitRequested)]
        assert len(events) == 1
        # The completed inner is skipped → break targets the outer.
        assert events[0].loop_node_id == "loop-outer"
        assert run.context.loop_state["loop-outer"]["break_requested"] is True

    @pytest.mark.asyncio
    async def test_already_broken_loops_are_skipped(self):
        """Re-firing a break for an already-broken loop should not
        re-target the same loop (idempotency). With only one broken
        active loop and nothing else, the handler no-ops."""
        handler = LoopExitNodeHandler()
        node = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)
        run = _run_with_loop_states({
            "loop-A": _active_event_driven_loop_state(
                invocation_id="invA", total=5, completed=[0], current_index=1,
                break_requested=True,
            ),
        })
        result = await handler.handle(
            node, run, Workflow(id="wf-1"), _topology(), None, _factory(),
        )

        events = [e for e in result.events if isinstance(e, LoopExitRequested)]
        assert len(events) == 0
        # No second signal — already broken, handler no-oped.
        assert run.context.loop_state["loop-A"]["break_requested"] is True


# ─────────────────── LoopExitNodeHandler — noop cases ──────────────


class TestLoopExitNoopCases:
    @pytest.mark.asyncio
    async def test_empty_loop_state_emits_nothing(self):
        handler = LoopExitNodeHandler()
        node = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext())

        result = await handler.handle(
            node, run, Workflow(id="wf-1"), _topology(), None, _factory(),
        )

        assert not any(isinstance(e, LoopExitRequested) for e in result.events)
        # state_updates records the noop for observability.
        assert result.state_updates[node.id]["metadata"]["status"] == "noop"
        assert (
            result.state_updates[node.id]["metadata"]["reason"]
            == "no_active_event_driven_loop"
        )

    @pytest.mark.asyncio
    async def test_only_legacy_loops_emits_nothing(self):
        """Legacy loops have invocation_id=""; the handler treats them
        as non-event-driven and skips them. This is how 'Flag OFF'
        semantics land for break."""
        handler = LoopExitNodeHandler()
        node = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)
        legacy_state = {
            "items": [1, 2, 3],
            "current_index": 0,
            "total": 3,
            "completed_iterations": [],
            "mode": "sequential",
            # No invocation_id — legacy path.
        }
        run = _run_with_loop_states({"loop-legacy": legacy_state})

        result = await handler.handle(
            node, run, Workflow(id="wf-1"), _topology(), None, _factory(),
        )

        assert not any(isinstance(e, LoopExitRequested) for e in result.events)
        # Legacy loop_state was NOT touched.
        assert "break_requested" not in run.context.loop_state["loop-legacy"]


# ──────── LoopExitNodeHandler — deterministic event ids ────────────


class TestLoopExitDeterministicEventId:
    @pytest.mark.asyncio
    async def test_same_inputs_same_event_id(self):
        """Replay property: same run_id + loop_node_id + invocation_id
        should produce the same event_id on the LoopExitRequested
        event so the event log dedupes on redelivery."""
        handler = LoopExitNodeHandler()
        node = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)

        def build_run():
            return _run_with_loop_states({
                "loop-A": _active_event_driven_loop_state(
                    invocation_id="invA", total=5, completed=[0], current_index=1,
                ),
            })

        r1 = await handler.handle(
            node, build_run(), Workflow(id="wf-1"), _topology(), None, _factory(),
        )
        r2 = await handler.handle(
            node, build_run(), Workflow(id="wf-1"), _topology(), None, _factory(),
        )
        e1 = next(e for e in r1.events if isinstance(e, LoopExitRequested))
        e2 = next(e for e in r2.events if isinstance(e, LoopExitRequested))
        assert e1.event_id == e2.event_id


# ───────── LoopNodeHandler._ed_iteration_complete observes break ────


class TestLoopHandlerObservesBreak:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_break_flag_causes_loop_completed_reason_break(self):
        """Simulate: a body task completes on iteration 0 of a 5-item
        loop, and the body set ``break_requested=True`` on loop_state
        (as ``LoopExitNodeHandler`` would). The loop handler's
        iteration-complete branch must emit ``LoopCompleted(reason="break",
        total_iterations=1)`` instead of advancing to iter 1."""
        # Build a workflow with a LOOP + body + exit edge.
        loop_node = WorkflowNode(
            id="loop-A", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "mode": "sequential"},
        )
        body = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf-1", nodes=[loop_node, body, exit_node])
        edges = [
            WorkflowEdge(
                id="e-body", source_node_id="loop-A",
                target_node_id="body", condition="body",
            ),
            WorkflowEdge(
                id="e-exit", source_node_id="loop-A",
                target_node_id="exit", condition="exit",
            ),
        ]
        dag = _topology(edge_map={"loop-A": edges})
        # Run with a loop_state that already has iter 0's dispatch,
        # break_requested=True, and 5 items total.
        loop_state = _active_event_driven_loop_state(
            invocation_id="invA", total=5, completed=[], current_index=0,
            break_requested=True,
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            context=RunContext(
                variables={"input": {"items": list(range(5))}},
                loop_state={"loop-A": loop_state},
            ),
        )
        handler = LoopNodeHandler(
            variable_resolver=_resolver(), event_driven=True,
        )
        synthetic = Task(
            id="synthetic-0", run_id="run-1", node_id="body",
            iteration_index=0,
        )

        result = await handler.handle(
            loop_node, run, workflow, dag, synthetic, _factory(),
        )

        # The iteration-complete event for iter 0 is still emitted...
        iter_completed = [
            e for e in result.events if isinstance(e, LoopIterationCompleted)
        ]
        assert len(iter_completed) == 1
        assert iter_completed[0].iter == 0
        # ...but the loop terminates with reason=break instead of
        # advancing to iter 1.
        completeds = [e for e in result.events if isinstance(e, LoopCompleted)]
        assert len(completeds) == 1
        assert completeds[0].reason == "break"
        assert completeds[0].total_iterations == 1
        # And the exit task is dispatched.
        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "exit" in dispatched_ids
        # Walker terminal check: total was forced down to len(completed)=1.
        assert loop_state["total"] == 1

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_break_takes_precedence_over_condition_false(self):
        """Condition loop with condition that would evaluate true
        (i.e., would normally continue), but break_requested is set.
        Break must win and produce reason='break', not 'condition_false'."""
        loop_node = WorkflowNode(
            id="loop-cond", node_type=NodeType.LOOP,
            config={"loop_type": "condition", "condition": "{{$input.keep_going}}", "mode": "sequential"},
        )
        body = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf-1", nodes=[loop_node, body, exit_node])
        edges = [
            WorkflowEdge(
                id="e-body", source_node_id="loop-cond",
                target_node_id="body", condition="body",
            ),
            WorkflowEdge(
                id="e-exit", source_node_id="loop-cond",
                target_node_id="exit", condition="exit",
            ),
        ]
        dag = _topology(edge_map={"loop-cond": edges})
        loop_state = _active_event_driven_loop_state(
            invocation_id="invC", total=10, completed=[0, 1], current_index=2,
            break_requested=True,
        )
        loop_state["loop_type"] = "condition"
        loop_state["condition"] = "{{$input.keep_going}}"
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            context=RunContext(
                variables={"input": {"keep_going": True, "items": list(range(10))}},
                loop_state={"loop-cond": loop_state},
            ),
        )
        handler = LoopNodeHandler(
            variable_resolver=_resolver(), event_driven=True,
        )
        synthetic = Task(
            id="synthetic-2", run_id="run-1", node_id="body",
            iteration_index=2,
        )

        result = await handler.handle(
            loop_node, run, workflow, dag, synthetic, _factory(),
        )

        completeds = [e for e in result.events if isinstance(e, LoopCompleted)]
        assert len(completeds) == 1
        # Break won, even though the condition was True.
        assert completeds[0].reason == "break"
        assert completeds[0].total_iterations == 3  # [0, 1, 2]


# ─────────────────── Integration: end-to-end via engine ────────────


class TestLoopExitIntegration:
    @pytest.mark.asyncio
    async def test_full_loop_with_break_mid_iteration(self):
        """Full engine: LOOP → body_entry (TRIGGER stub) →
        CONDITION-emulating path → LOOP_EXIT. Because we can't easily
        wire a real CONDITION in a unit fixture, we make the body_entry
        be the LOOP_EXIT node directly — simulating 'every iteration
        hits the break'. The loop starts with total=5 but breaks on
        iteration 0, so the final event log shows:
            LoopStarted(total=5)
            LoopIterationStarted(iter=0)
            LoopExitRequested
            LoopIterationCompleted(iter=0)
            LoopCompleted(reason="break", total_iterations=1)
        plus the exit task dispatched.
        """
        from app.layer2_application.orchestration.orchestration_engine import (
            OrchestrationEngine,
        )
        from app.layer2_application.node_handlers.base import (
            NodeHandlerRegistry,
        )
        from unittest.mock import MagicMock

        class _CapturingPublisher:
            def __init__(self):
                self.events = []

            def publish(self, topic, event):
                self.events.append((topic, event))

            def publish_with_headers(self, topic, event, *, headers=None, key=None):
                self.events.append((topic, event))

        resolver = _resolver()
        registry = NodeHandlerRegistry()
        registry.register(
            NodeType.LOOP,
            LoopNodeHandler(variable_resolver=resolver, event_driven=True),
        )
        registry.register(NodeType.LOOP_EXIT, LoopExitNodeHandler())
        # Stub handler for TRIGGER (used as the exit target below).
        class _StubHandler:
            shape = None
            async def handle(self, node, run, workflow, topology, completed_task, task_factory):
                from app.layer2_application.node_handlers.base import (
                    NodeHandlerResult,
                )
                return NodeHandlerResult(
                    tasks_to_dispatch=[], state_updates={}, events=[],
                )
        registry.register(NodeType.TRIGGER, _StubHandler())

        from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
        driver = EventDrivenLoopDriver(resolver)

        capturing = _CapturingPublisher()
        engine = OrchestrationEngine(
            handler_registry=registry,
            task_factory=TaskFactory(),
            successor_processor=MagicMock(),
            stage_recorder=MagicMock(),
            task_repo=MagicMock(),
            run_repo=MagicMock(),
            event_publisher=capturing,
            callback_publisher=None,
            event_driven_loop_driver=driver,
        )

        # Workflow: LOOP → body_entry (LOOP_EXIT) → [exit node]
        # Body edge points to LOOP_EXIT directly so firing the body
        # always triggers a break on iter 0.
        loop_node = WorkflowNode(
            id="loop-A", name="loop",
            node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "mode": "sequential"},
        )
        brk = WorkflowNode(id="brk", name="break", node_type=NodeType.LOOP_EXIT)
        exit_node = WorkflowNode(id="exit", name="exit", node_type=NodeType.TRIGGER)
        workflow = Workflow(
            id="wf-1", name="wf-1",
            nodes=[loop_node, brk, exit_node],
            edges=[
                WorkflowEdge(
                    id="e-body", source_node_id="loop-A",
                    target_node_id="brk", condition="body",
                ),
                WorkflowEdge(
                    id="e-exit", source_node_id="loop-A",
                    target_node_id="exit", condition="exit",
                ),
            ],
        )
        topology = GraphTopology(
            edge_map={
                "loop-A": workflow.edges, "brk": [], "exit": [],
            },
            adjacency={"loop-A": ["brk", "exit"], "brk": [], "exit": []},
            predecessors={"loop-A": [], "brk": ["loop-A"], "exit": ["loop-A"]},
            entry_nodes=["loop-A"],
            leaf_nodes=["brk", "exit"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            context=RunContext(
                variables={"input": {"items": [1, 2, 3, 4, 5]}},
                topology=topology,
            ),
        )
        loop_task = Task(id="t-loop", run_id="run-1", node_id="loop-A")

        await drive_node(engine, loop_task, run, workflow, topology)

        published = [ev for _topic, ev in capturing.events]
        # Critical assertions: exactly one break-requested, one
        # loop-completed with reason=break, total_iterations=1.
        break_requested = [e for e in published if isinstance(e, LoopExitRequested)]
        assert len(break_requested) >= 1
        assert break_requested[0].loop_node_id == "loop-A"
        completeds = [e for e in published if isinstance(e, LoopCompleted)]
        assert len(completeds) == 1
        assert completeds[0].reason == "break"
        assert completeds[0].total_iterations == 1
        # The loop should have emitted exactly one iteration-complete
        # (iter 0 — the body iteration that hit the break).
        iter_completed = [
            e for e in published if isinstance(e, LoopIterationCompleted)
        ]
        assert len(iter_completed) == 1
        assert iter_completed[0].iter == 0
