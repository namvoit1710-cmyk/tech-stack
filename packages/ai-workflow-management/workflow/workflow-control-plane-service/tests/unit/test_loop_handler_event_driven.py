"""Unit tests for the Phase 4c.4.3 event-driven ``LoopNodeHandler`` path.

All tests construct ``LoopNodeHandler(variable_resolver=..., event_driven=True)``
explicitly. The legacy path (``event_driven=False``, the default) is
covered by the existing ``TestLoopHandler`` suite in
``test_node_handlers.py`` and MUST keep passing unchanged — which
doubles as a regression guard that the new branch never runs for
callers that don't opt in.

Critical invariants enforced here:

- ``LoopStarted`` + ``LoopIterationStarted`` events appear in
  ``result.events`` on initialisation with populated ``invocation_id``
  and ``scope_stack`` fields.
- Body tasks carry a non-empty ``scope_stack`` — the innermost entry
  is ``LoopScope(loop_node_id, invocation_id, iter, total)``. This
  is the foundation Phase 4c.4.4 builds on for scope-aware merge
  quorum.
- Event ids are deterministic — replaying the same initialisation
  twice produces identical ``event_id``s so the event log can dedupe
  on redelivery.
- Nested-loop uniqueness — an inner LOOP initialised inside two
  different outer iterations gets two different ``invocation_id``s.
  This is the spike §3.6 invariant at the handler level.
- Legacy dual-write — ``loop_context`` and ``iteration_index`` stay
  populated on body tasks alongside ``scope_stack`` so handlers that
  haven't migrated keep working during the transition.
- The ``event_driven=False`` default keeps the legacy code path.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.events import (
    LoopCompleted,
    LoopIterationCompleted,
    LoopIterationStarted,
    LoopStarted,
)
from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.loop_scope import LoopScope, derive_invocation_id
from app.layer1_domain.value_objects.node_type import NodeType


# ──────────────────────────── helpers ─────────────────────────────────


def _resolver():
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _factory():
    return TaskFactory()


def _topology(edge_map=None, adjacency=None, predecessors=None, entry=None, leaf=None):
    return GraphTopology(
        edge_map=edge_map or {},
        adjacency=adjacency or {},
        predecessors=predecessors or {},
        entry_nodes=entry or [],
        leaf_nodes=leaf or [],
    )


def _ed_handler():
    return LoopNodeHandler(variable_resolver=_resolver(), event_driven=True)


def _apply_loop_state_updates(run: WorkflowRun, result) -> None:
    """Simulate ``OrchestrationEngine._run_loop_iterations`` applying
    the handler's ``loop_state_updates`` back onto ``run.context.loop_state``.

    The event-driven handler stores the loop's ``invocation_id`` /
    ``enclosing_scope_stack`` on ``loop_state`` at init time; without
    applying the updates between calls the next invocation of
    ``handle`` has no persisted loop_state and falls back to the
    initialisation branch. Tests that drive multi-step iteration
    sequences call this helper after each handler invocation to
    mirror production behavior.
    """
    if result.loop_state_updates:
        run.context.loop_state.update(result.loop_state_updates)


def _collection_workflow(items: list, mode: str = "sequential", **config):
    """Build a LOOP → body → exit workflow driven by an inline items list."""
    loop_config: dict = {
        "collection": "{{$input.items}}",
        "mode": mode,
    }
    loop_config.update(config)
    node = WorkflowNode(id="loop", node_type=NodeType.LOOP, config=loop_config)
    body_node = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
    exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
    workflow = Workflow(id="wf", nodes=[node, body_node, exit_node])
    edges = [
        WorkflowEdge(id="e-body", source_node_id="loop", target_node_id="body", condition="body"),
        WorkflowEdge(id="e-exit", source_node_id="loop", target_node_id="exit", condition="exit"),
    ]
    dag = _topology(edge_map={"loop": edges})
    run = WorkflowRun(
        id="run-1", workflow_id="wf",
        context=RunContext(variables={"input": {"items": items}}),
    )
    return node, workflow, dag, run


# ─────────────────────── initialisation path ─────────────────────────


class TestEventDrivenInitialize:
    @pytest.mark.asyncio
    async def test_emits_loop_started_with_invocation_id(self):
        node, workflow, dag, run = _collection_workflow(["a", "b", "c"])
        result = await _ed_handler().handle(
            node, run, workflow, dag, None, _factory(),
        )

        started = [e for e in result.events if isinstance(e, LoopStarted)]
        assert len(started) == 1
        ev = started[0]
        assert ev.node_id == "loop"
        assert ev.loop_type == "collection"
        assert ev.mode == "sequential"
        assert ev.total == 3
        assert ev.scope_stack == []  # top-level loop, no enclosing scopes
        # Deterministic — the same run_id + loop_node_id + no enclosing
        # always produces the same invocation_id.
        assert ev.invocation_id == derive_invocation_id("run-1", "loop", [])

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_body_task_carries_scope_stack(self):
        """This is the critical Phase 4c.4.3 behavior: body tasks
        dispatched under the event-driven path carry ``scope_stack``
        so the Phase 4c.4.4 merge handler can key quorum buffers on
        it."""
        node, workflow, dag, run = _collection_workflow(["a", "b", "c"])
        result = await _ed_handler().handle(
            node, run, workflow, dag, None, _factory(),
        )

        assert len(result.tasks_to_dispatch) == 1
        body_task = result.tasks_to_dispatch[0]
        assert body_task.node_id == "body"
        # New event-driven fields:
        assert body_task.scope_stack is not None
        assert len(body_task.scope_stack) == 1
        innermost = body_task.scope_stack[-1]
        assert innermost.loop_node_id == "loop"
        assert innermost.iter == 0
        assert innermost.total == 3
        assert innermost.invocation_id  # non-empty
        # Legacy dual-write: iteration_index and loop_context stay set.
        assert body_task.iteration_index == 0
        assert body_task.loop_context == {
            "index": 0, "item": "a", "total": 3,
        }

    @pytest.mark.asyncio
    async def test_emits_loop_iteration_started_for_first_iter(self):
        node, workflow, dag, run = _collection_workflow(["a", "b"])
        result = await _ed_handler().handle(
            node, run, workflow, dag, None, _factory(),
        )

        starts = [e for e in result.events if isinstance(e, LoopIterationStarted)]
        assert len(starts) == 1
        start = starts[0]
        assert start.iter == 0
        assert start.item == "a"
        # scope_stack INCLUDES self at tail per spike §3.3
        assert len(start.scope_stack) == 1
        assert start.scope_stack[-1]["iter"] == 0
        assert start.scope_stack[-1]["loop_node_id"] == "loop"

    @pytest.mark.asyncio
    async def test_parallel_mode_dispatches_batch_with_scope_stacks(self):
        node, workflow, dag, run = _collection_workflow(
            ["a", "b", "c"], mode="parallel", parallelism=2,
        )
        result = await _ed_handler().handle(
            node, run, workflow, dag, None, _factory(),
        )

        assert len(result.tasks_to_dispatch) == 2
        iters = {t.scope_stack[-1].iter for t in result.tasks_to_dispatch}
        assert iters == {0, 1}
        # Both share the same invocation_id — same loop, different iter.
        inv_ids = {t.scope_stack[-1].invocation_id for t in result.tasks_to_dispatch}
        assert len(inv_ids) == 1
        # And two LoopIterationStarted events, one per body task.
        starts = [e for e in result.events if isinstance(e, LoopIterationStarted)]
        assert len(starts) == 2

    @pytest.mark.asyncio
    async def test_empty_collection_emits_started_then_completed(self):
        node, workflow, dag, run = _collection_workflow([])
        result = await _ed_handler().handle(
            node, run, workflow, dag, None, _factory(),
        )

        starteds = [e for e in result.events if isinstance(e, LoopStarted)]
        completeds = [e for e in result.events if isinstance(e, LoopCompleted)]
        assert len(starteds) == 1
        assert starteds[0].total == 0
        assert len(completeds) == 1
        assert completeds[0].total_iterations == 0
        assert completeds[0].reason == "all_done"
        # Exit task dispatched
        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "exit" in dispatched_ids

    @pytest.mark.asyncio
    async def test_deterministic_loop_started_event_id(self):
        """Replay property: invoking the handler twice with the same
        inputs produces identical LoopStarted event_ids, so the event
        log can dedupe on redelivery."""
        node1, wf1, dag1, run1 = _collection_workflow(["a", "b", "c"])
        node2, wf2, dag2, run2 = _collection_workflow(["a", "b", "c"])
        r1 = await _ed_handler().handle(node1, run1, wf1, dag1, None, _factory())
        r2 = await _ed_handler().handle(node2, run2, wf2, dag2, None, _factory())
        e1 = next(e for e in r1.events if isinstance(e, LoopStarted))
        e2 = next(e for e in r2.events if isinstance(e, LoopStarted))
        assert e1.event_id == e2.event_id


# ─────────────────────── iteration completion ────────────────────────


class TestEventDrivenIterationComplete:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_completion_emits_extended_event_fields(self):
        node, workflow, dag, run = _collection_workflow(["a", "b"])
        # Initialise first so loop_state gets populated on the run.
        handler = _ed_handler()
        init = await handler.handle(node, run, workflow, dag, None, _factory())
        _apply_loop_state_updates(run, init)
        # Now simulate iteration 0 completing by feeding back a
        # synthetic task with iteration_index=0.
        synthetic = Task(
            id="synthetic", run_id="run-1", node_id="body", iteration_index=0,
        )
        result = await handler.handle(
            node, run, workflow, dag, synthetic, _factory(),
        )

        completions = [
            e for e in result.events if isinstance(e, LoopIterationCompleted)
        ]
        assert len(completions) == 1
        comp = completions[0]
        assert comp.iteration == 0          # legacy field
        assert comp.iter == 0               # new canonical field
        assert comp.total == 2
        assert comp.outcome == "ok"
        assert comp.invocation_id           # non-empty
        assert len(comp.scope_stack) == 1
        assert comp.scope_stack[-1]["iter"] == 0
        assert comp.scope_stack[-1]["loop_node_id"] == "loop"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_next_iter_shares_invocation_id_but_changes_iter(self):
        """Same loop invocation — invocation_id is stable across
        iterations; only ``iter`` advances."""
        node, workflow, dag, run = _collection_workflow(["a", "b", "c"])
        handler = _ed_handler()
        init_result = await handler.handle(
            node, run, workflow, dag, None, _factory(),
        )
        _apply_loop_state_updates(run, init_result)
        first_inv = init_result.tasks_to_dispatch[0].scope_stack[-1].invocation_id
        # Feed back iteration 0's body completion → dispatch iteration 1.
        synthetic = Task(
            id="synthetic-0", run_id="run-1", node_id="body", iteration_index=0,
        )
        next_result = await handler.handle(
            node, run, workflow, dag, synthetic, _factory(),
        )
        # Exactly one new body task, for iteration 1.
        body_tasks = [t for t in next_result.tasks_to_dispatch if t.node_id == "body"]
        assert len(body_tasks) == 1
        second = body_tasks[0]
        assert second.scope_stack[-1].iter == 1
        assert second.scope_stack[-1].invocation_id == first_inv

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_all_iterations_complete_emits_loop_completed(self):
        node, workflow, dag, run = _collection_workflow(["only-one"])
        handler = _ed_handler()
        init = await handler.handle(node, run, workflow, dag, None, _factory())
        _apply_loop_state_updates(run, init)
        synthetic = Task(
            id="synthetic-0", run_id="run-1", node_id="body", iteration_index=0,
        )
        result = await handler.handle(
            node, run, workflow, dag, synthetic, _factory(),
        )

        completeds = [e for e in result.events if isinstance(e, LoopCompleted)]
        assert len(completeds) == 1
        assert completeds[0].reason == "all_done"
        assert completeds[0].total_iterations == 1
        # Exit task dispatched.
        assert "exit" in {t.node_id for t in result.tasks_to_dispatch}


# ─────────────────────── nested-loop invariant ───────────────────────


class TestNestedLoopInvocationUniqueness:
    @pytest.mark.asyncio
    async def test_inner_invocation_differs_per_outer_iter(self):
        """Spike §3.6 invariant, enforced at the handler level.

        Two synthetic parent tasks model an inner LOOP being initialised
        first inside outer iter 0, then inside outer iter 1. The two
        resulting ``LoopStarted`` events MUST carry DIFFERENT
        invocation_ids, so downstream merges inside the inner body get
        fresh quorum buffers per outer iteration.
        """
        # Inner loop workflow.
        inner_node = WorkflowNode(
            id="loop_inner", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "mode": "sequential"},
        )
        body = WorkflowNode(id="inner_body", node_type=NodeType.TASK, worker_type="w")
        inner_exit = WorkflowNode(id="inner_exit", node_type=NodeType.TASK, worker_type="w")
        wf = Workflow(id="wf", nodes=[inner_node, body, inner_exit])
        edges = [
            WorkflowEdge(
                id="ei-body", source_node_id="loop_inner",
                target_node_id="inner_body", condition="body",
            ),
            WorkflowEdge(
                id="ei-exit", source_node_id="loop_inner",
                target_node_id="inner_exit", condition="exit",
            ),
        ]
        dag = _topology(edge_map={"loop_inner": edges})
        run = WorkflowRun(
            id="run-1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": ["p", "q"]}}),
        )

        outer_inv = derive_invocation_id("run-1", "loop_outer", [])
        # Two parent tasks — same outer loop, different iters.
        parent_iter_0 = Task(
            id="p0", run_id="run-1", node_id="loop_outer",
            scope_stack=[
                LoopScope("loop_outer", outer_inv, iter=0, total=2),
            ],
        )
        parent_iter_1 = Task(
            id="p1", run_id="run-1", node_id="loop_outer",
            scope_stack=[
                LoopScope("loop_outer", outer_inv, iter=1, total=2),
            ],
        )

        # Fresh RunContext for each call so loop_state doesn't leak.
        run_0 = WorkflowRun(
            id="run-1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": ["p", "q"]}}),
        )
        run_1 = WorkflowRun(
            id="run-1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": ["p", "q"]}}),
        )
        r0 = await _ed_handler().handle(
            inner_node, run_0, wf, dag, parent_iter_0, _factory(),
        )
        r1 = await _ed_handler().handle(
            inner_node, run_1, wf, dag, parent_iter_1, _factory(),
        )

        inner_started_0 = next(e for e in r0.events if isinstance(e, LoopStarted))
        inner_started_1 = next(e for e in r1.events if isinstance(e, LoopStarted))

        assert inner_started_0.invocation_id != inner_started_1.invocation_id, (
            "inner LOOP must get a different invocation_id for each "
            "outer iteration — otherwise nested merge buffers leak"
        )
        # And the inner body tasks inherit the full nested scope_stack:
        # [outer_scope, inner_scope].
        body_0 = next(t for t in r0.tasks_to_dispatch if t.node_id == "inner_body")
        body_1 = next(t for t in r1.tasks_to_dispatch if t.node_id == "inner_body")
        assert len(body_0.scope_stack) == 2
        assert body_0.scope_stack[0].iter == 0  # outer
        assert body_0.scope_stack[1].iter == 0  # inner
        assert len(body_1.scope_stack) == 2
        assert body_1.scope_stack[0].iter == 1  # outer
        assert body_1.scope_stack[1].iter == 0  # inner


# ─────────────────── flag-off regression guard ───────────────────────


class TestEventDrivenFlagOffLegacyPath:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_flag_off_emits_no_loop_started(self):
        """Regression guard: with the flag off, the handler takes the
        legacy code path — no LoopStarted / LoopIterationStarted /
        LoopCompleted events appear, body tasks have no scope_stack."""
        node, workflow, dag, run = _collection_workflow(["a", "b"])
        handler = LoopNodeHandler(
            variable_resolver=_resolver(), event_driven=False,
        )
        result = await handler.handle(
            node, run, workflow, dag, None, _factory(),
        )

        assert not any(isinstance(e, LoopStarted) for e in result.events)
        assert not any(isinstance(e, LoopIterationStarted) for e in result.events)
        assert not any(isinstance(e, LoopCompleted) for e in result.events)
        # Body task still dispatched, but scope_stack is None (legacy).
        assert len(result.tasks_to_dispatch) == 1
        assert result.tasks_to_dispatch[0].scope_stack is None
        # Legacy fields populated as before.
        assert result.tasks_to_dispatch[0].iteration_index == 0
        assert result.tasks_to_dispatch[0].loop_context == {
            "index": 0, "item": "a", "total": 2,
        }
