"""A parallel loop dispatches N body tasks. They must be N tasks, not one.

``_dispatch_iteration_body`` builds each body task with
``task_factory.create_task(body_entry, run)``, and ``task_id_for`` is
``{run}-{node}`` (plus ``-v{n}`` for a cycle revisit) — no iteration term. In
``sequential`` mode the iterations are separated in time so the reuse is
invisible. In ``parallel`` mode ``initialize_loop`` builds ``min(total,
parallelism)`` of them in ONE pass, so they collide:

* ``_run_loop_body`` calls ``task_repo.save`` on each — one row, last write wins,
  and the row's ``scope_stack`` ends up on the LAST iteration;
* it enqueues N ``ExecuteNodeAction`` all naming that one ``task_id``;
* ``_execute_node_inner`` guards on ``task.status in _NON_DISPATCHABLE_STATUSES``
  — per TASK, not per node — so the first delivery runs it and the rest are
  dropped as duplicates.

One iteration executes. ``completed_iterations`` gets one entry against
``total=N``, ``len(completed) >= total`` is never true, and the run hangs
RUNNING forever with the LOOP node stuck READY.

Measured on tenant-1 (2026-08-10, run a522ee2c, after the items-pointer fix so
the body could run at all): ``total=10 mode=parallel parallelism=5``, five
``LoopIterationStarted`` for iters 0-4, then a single body execution reporting
**iter=4** — the last of the five, i.e. the one that won the upsert. Iterations
0-3 never ran. The loop then advanced 5..9 one at a time (those go through
``_dispatch_next_iteration_body``, one task per call, so they never collide) and
stopped at 6 of 10.

``_execute_node_inner``'s own comment states the invariant this violates:
"Legitimate re-runs (loop bodies, cycle revisits) always get a fresh -v{n} task
id (see TaskFactory.task_id_for)". Loop bodies do not.

The data sizes below climb deliberately: the collision needs at least two
concurrent dispatches to appear at all, and the id scheme has to stay
collision-free at the scale ``max_iterations`` actually permits (10 000).
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.events import LoopCompleted
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import (
    GraphTopology, RunContext, WorkflowRun,
)
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy, TraversalResolverStrategy, VariableResolver,
)


def _driver():
    return EventDrivenLoopDriver(
        VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])
    )


def _loop(items, mode="parallel", parallelism=5, **cfg):
    config = {"collection": "{{$input.items}}", "mode": mode,
              "parallelism": parallelism, "max_iterations": 100000}
    config.update(cfg)
    loop_node = WorkflowNode(id="loop", name="loop", node_type=NodeType.LOOP,
                             config=config)
    body = WorkflowNode(id="body", name="body", node_type=NodeType.TRIGGER)
    exit_node = WorkflowNode(id="exit", name="exit", node_type=NodeType.TRIGGER)
    workflow = Workflow(
        id="wf", name="wf", nodes=[loop_node, body, exit_node],
        edges=[
            WorkflowEdge(id="e-body", source_node_id="loop",
                         target_node_id="body", condition="body"),
            WorkflowEdge(id="e-exit", source_node_id="loop",
                         target_node_id="exit", condition="exit"),
        ],
    )
    dag = GraphTopology(
        edge_map={"loop": workflow.edges, "body": [], "exit": []},
        adjacency={"loop": ["body", "exit"], "body": [], "exit": []},
        predecessors={"loop": [], "body": ["loop"], "exit": ["loop"]},
        entry_nodes=["loop"], leaf_nodes=["body", "exit"],
    )
    run = WorkflowRun(id="run-1", workflow_id="wf",
                      context=RunContext(variables={"input": {"items": items}},
                                         topology=dag))
    return loop_node, workflow, dag, run


async def _initial_batch(total, parallelism, mode="parallel"):
    """The tasks ``initialize_loop`` hands to ``_run_loop_body`` in one pass."""
    items = [{"n": i} for i in range(total)]
    loop_node, workflow, dag, run = _loop(items, mode=mode, parallelism=parallelism)
    result = await _driver().initialize_loop(
        loop_node, run, workflow, dag, None, TaskFactory(),
    )
    return result.tasks_to_dispatch


# ───────────────── the collision, at climbing sizes ──────────────────


class TestOneTaskPerIteration:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [
        (1, 1),           # degenerate: nothing to collide
        (2, 2),           # the smallest size the bug can appear at
        (5, 5),
        (10, 5),          # the shape measured live on tenant-1
        (100, 10),
        (1000, 50),
        (10000, 100),     # max_iterations territory
    ])
    async def test_every_dispatched_body_has_its_own_id(self, total, parallelism):
        tasks = await _initial_batch(total, parallelism)
        expected = min(total, parallelism)
        assert len(tasks) == expected, "wrong number of body tasks dispatched"
        ids = [t.id for t in tasks]
        assert len(set(ids)) == expected, (
            f"{expected} iterations collapsed onto {len(set(ids))} task id(s) — "
            f"task_repo.save upserts them onto one row and only one runs"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [(2, 2), (10, 5), (1000, 50)])
    async def test_the_idempotency_key_is_distinct_too(self, total, parallelism):
        """It is derived from the id at construction, so a duplicated id
        duplicates the key — and the key is what ``attempt_id`` folds into the
        NodeOutputProduced correlation, so two iterations would dedupe to one
        event even if both somehow ran."""
        tasks = await _initial_batch(total, parallelism)
        keys = [t.idempotency_key for t in tasks]
        assert len(set(keys)) == len(tasks)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [(2, 2), (5, 5), (10, 5)])
    async def test_each_task_keeps_its_OWN_iteration(self, total, parallelism):
        """The scope is what ``$loop.item`` and ``iter_progress`` are keyed on.
        With one shared row the surviving scope was the last writer's — which is
        how a 5-wide batch reported ``iter=4`` and nothing else."""
        tasks = await _initial_batch(total, parallelism)
        iters = sorted(t.scope_stack[-1].iter for t in tasks)
        assert iters == list(range(min(total, parallelism)))

    @pytest.mark.asyncio
    async def test_the_id_still_names_the_run_and_node(self):
        """Whatever the scheme, the id stays derivable and greppable — it is the
        causation key for every event the body produces."""
        tasks = await _initial_batch(5, 5)
        for t in tasks:
            assert t.id.startswith("run-1-body")
            assert t.run_id == "run-1" and t.node_id == "body"


class TestSequentialIsUnaffected:
    """Sequential mode dispatches one body per pass, so it never collided. Its
    ids must still differ per iteration — the same row being overwritten each
    time is why a sequential loop's body history is a single task row — but the
    change must not alter its dispatch COUNT."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total", [1, 2, 10, 1000])
    async def test_one_body_task_per_pass(self, total):
        tasks = await _initial_batch(total, parallelism=1, mode="sequential")
        assert len(tasks) == 1

    @pytest.mark.asyncio
    async def test_successive_iterations_get_successive_ids(self):
        """``iteration_complete`` builds the next body task the same way, so the
        ids must advance with the iteration rather than repeat."""
        items = [{"n": i} for i in range(4)]
        loop_node, workflow, dag, run = _loop(items, mode="sequential",
                                              parallelism=1)
        driver, factory = _driver(), TaskFactory()
        first = await driver.initialize_loop(loop_node, run, workflow, dag,
                                             None, factory)
        run.context.loop_state.update(first.loop_state_updates)
        body0 = first.tasks_to_dispatch[0]

        state = run.context.loop_state["loop"]
        completed = first.tasks_to_dispatch[0]
        completed.status = __import__(
            "app.layer1_domain.value_objects.task_status", fromlist=["TaskStatus"],
        ).TaskStatus.COMPLETED
        second = await driver.iteration_complete(
            loop_node, run, workflow, dag, completed, factory, state,
        )
        body1 = [t for t in second.tasks_to_dispatch if t.node_id == "body"]
        assert body1, "iteration 1 dispatched no body"
        assert body1[0].id != body0.id


class TestTheWholeLoopDrains:
    """The first batch is where the ids collide, but the property that matters
    is about the WHOLE loop: every iteration dispatched exactly once, and the
    loop reaching ``total`` so it can finish.

    So this drives the driver end to end — ``initialize_loop`` then one
    ``iteration_complete`` per completion, exactly as
    ``LoopIterationDriver.orchestrate_iteration`` does — and watches the two
    things the hang was made of: iterations that never got dispatched, and
    ``completed_iterations`` never reaching ``total``.

    Sizes climb past the first batch on purpose. ``initialize_loop`` dispatches
    ``min(total, parallelism)``; everything after that comes from
    ``iteration_complete``'s ``next_dispatch_index`` path, which the batch-only
    tests never exercise.
    """

    async def _drain(self, total, parallelism):
        items = [{"n": i} for i in range(total)]
        loop_node, workflow, dag, run = _loop(items, parallelism=parallelism)
        driver, factory = _driver(), TaskFactory()

        result = await driver.initialize_loop(
            loop_node, run, workflow, dag, None, factory,
        )
        run.context.loop_state.update(result.loop_state_updates)
        state = run.context.loop_state["loop"]

        dispatched = [t for t in result.tasks_to_dispatch if t.node_id == "body"]
        pending = [t.scope_stack[-1].iter for t in dispatched]
        completed_events = []
        guard = 0

        while pending and guard < total * 4:
            guard += 1
            iter_i = pending.pop(0)
            synthetic = Task(
                id=f"synthetic-{iter_i}", run_id=run.id, node_id="body",
                status=TaskStatus.COMPLETED,
                scope_stack=[LoopScope(
                    loop_node_id="loop",
                    invocation_id=state.get("invocation_id", ""),
                    iter=iter_i, total=total)],
            )
            step = await driver.iteration_complete(
                loop_node, run, workflow, dag, synthetic, factory, state,
            )
            completed_events.extend(
                e for e in step.events if isinstance(e, LoopCompleted)
            )
            for t in step.tasks_to_dispatch:
                if t.node_id != "body":
                    continue
                dispatched.append(t)
                pending.append(t.scope_stack[-1].iter)

        return dispatched, state, completed_events

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [
        (2, 2), (5, 5), (10, 5), (50, 10), (200, 25),
    ])
    async def test_every_iteration_is_dispatched_exactly_once(
        self, total, parallelism,
    ):
        """GUARD, not proof: this passes with and without the id fix. At driver
        level each call mints a distinct Task OBJECT; the collapse only happens
        once they are persisted BY ID, which is what
        ``test_loop_body_reaches_the_queue.py`` keys its store on. What this
        pins is the advancement arithmetic — ``next_dispatch_index`` skipping or
        repeating an iteration would show up here and nowhere else."""
        dispatched, _, _ = await self._drain(total, parallelism)
        iters = sorted(t.scope_stack[-1].iter for t in dispatched)
        assert iters == list(range(total)), (
            f"dispatched {len(iters)} of {total} iterations"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [
        (2, 2), (5, 5), (10, 5), (50, 10), (200, 25),
    ])
    async def test_no_two_body_tasks_ever_share_an_id(self, total, parallelism):
        """Across the whole loop, not just the first batch — the advancement
        path mints its tasks through the same helper."""
        dispatched, _, _ = await self._drain(total, parallelism)
        ids = [t.id for t in dispatched]
        assert len(set(ids)) == len(ids) == total

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [(2, 2), (10, 5), (50, 10)])
    async def test_the_loop_reaches_total_and_completes(self, total, parallelism):
        """The hang itself: ``len(completed_iterations) >= total`` is the only
        condition that emits ``LoopCompleted`` and drives the LOOP node's
        tracking task terminal. Six of ten is a run that never quiesces.

        Also a GUARD rather than proof — see the note above. It passes without
        the id fix because this harness feeds every iteration back by hand,
        which is precisely what the shared task id prevented in production."""
        _, state, completed = await self._drain(total, parallelism)
        assert len(state["completed_iterations"]) == total
        assert completed, "LoopCompleted never emitted — the run would hang"
        assert completed[-1].total_iterations == total
        assert completed[-1].reason == "all_done"


class TestTheIdsAreStable:
    """Deterministic ids are load-bearing: ``EventId.derive`` uses the task id as
    the causation, and a redelivered ExecuteNodeAction must resolve to the same
    row. Building the same batch twice must give the same ids."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [(5, 5), (100, 10)])
    async def test_rebuilding_the_batch_reproduces_the_ids(self, total, parallelism):
        first = [t.id for t in await _initial_batch(total, parallelism)]
        second = [t.id for t in await _initial_batch(total, parallelism)]
        assert first == second

    @pytest.mark.asyncio
    async def test_a_NESTED_loop_does_not_reuse_ids_across_outer_iterations(self):
        """Why the id carries ``invocation_id`` and not only ``i``.

        An inner loop runs iterations 0..n AGAIN on every outer iteration, so
        ``{run}-{node}-i0`` would name outer-iter-0's first inner body and
        outer-iter-1's first inner body — the same collision one level up,
        separated in time instead of in flight. ``derive_invocation_id`` folds
        the enclosing scopes in, so the two invocations differ.

        Mutation-checked: dropping ``invocation_id`` from the id leaves every
        other test in this file green and fails only this one."""
        outer_a = LoopScope(loop_node_id="outer", invocation_id="inv-outer",
                            iter=0, total=2)
        outer_b = LoopScope(loop_node_id="outer", invocation_id="inv-outer",
                            iter=1, total=2)

        async def batch(outer_scope):
            items = [{"n": i} for i in range(3)]
            loop_node, workflow, dag, run = _loop(items, parallelism=3)
            enclosing_task = Task(id="t-outer", run_id="run-1", node_id="outer",
                                  status=TaskStatus.COMPLETED,
                                  scope_stack=[outer_scope])
            result = await _driver().initialize_loop(
                loop_node, run, workflow, dag, enclosing_task, TaskFactory(),
            )
            return {t.id for t in result.tasks_to_dispatch}

        first, second = await batch(outer_a), await batch(outer_b)
        assert len(first) == 3 and len(second) == 3
        assert not (first & second), (
            "outer iteration 1 reused outer iteration 0's body task ids"
        )
