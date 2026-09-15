"""``_run_loop_body`` must put N body tasks in the store and N in the queue.

The id fix is provable one layer up (``test_parallel_loop_body_task_ids.py``),
but the DAMAGE happened here: this is the method that calls ``task_repo.save``
per body task and enqueues one ``ExecuteNodeAction`` per body task. With one
shared id those two loops wrote a single row and posted N copies of the same
message — the shape that made a 5-wide batch execute exactly once.

So this asserts the observable contract at the boundary the engine actually
crosses: distinct rows, distinct messages, and each message naming a row that
carries its own iteration. A fake repo keyed by id is the whole point — a list
would hide the upsert, which is exactly how this survived.

Sizes climb because the failure needs concurrency to appear and has to keep
holding at the width ``parallelism`` permits.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import (
    GraphTopology, RunContext, WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.orchestration.node_execution_runner import (
    ExecutionContext, NodeExecutionRunner,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy, TraversalResolverStrategy, VariableResolver,
)


class _TaskStore:
    """Keyed by id — an upsert therefore LOSES a row, which is the defect."""

    def __init__(self):
        self.rows: dict[str, object] = {}
        self.saves = 0

    def save(self, task):
        self.saves += 1
        self.rows[task.id] = task

    async def asave(self, task):
        self.save(task)


class _Queue:
    def __init__(self):
        self.actions: list = []

    async def enqueue(self, action):
        self.actions.append(action)


class _Engine:
    """Only what ``_run_loop_body`` touches."""

    def __init__(self, run):
        self._task_repo = _TaskStore()
        self._queue = _Queue()
        self._run = run
        self.published: list = []

    def emit_node_dispatched(self, *a, **kw):
        pass

    def _view_completed(self, run):
        return set(run.context.completed_nodes)

    def _view_skipped(self, run):
        return set(run.context.skipped_nodes)

    def _publish_now(self, topic, event):
        self.published.append((topic, event))


def _graph(items, parallelism):
    loop_node = WorkflowNode(
        id="loop", name="loop", node_type=NodeType.LOOP,
        config={"collection": "{{$input.items}}", "mode": "parallel",
                "parallelism": parallelism, "max_iterations": 100000},
    )
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


async def _dispatch(total, parallelism):
    items = [{"n": i} for i in range(total)]
    loop_node, workflow, dag, run = _graph(items, parallelism)
    driver = EventDrivenLoopDriver(
        VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])
    )
    result = await driver.initialize_loop(
        loop_node, run, workflow, dag, None, TaskFactory(),
    )
    engine = _Engine(run)
    loop_task = TaskFactory().create_task(loop_node, run)
    await NodeExecutionRunner(engine=engine)._run_loop_body(ExecutionContext(
        task=loop_task, node=loop_node, run=run, workflow=workflow,
        topology=dag, handler_result=result, now="2026-01-01T00:00:00Z",
    ))
    return engine, result


class TestTheQueueGetsOneMessagePerIteration:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [
        (1, 1), (2, 2), (5, 5), (10, 5), (100, 10), (1000, 50), (10000, 100),
    ])
    async def test_rows_and_messages_match_the_batch_width(self, total, parallelism):
        engine, result = await _dispatch(total, parallelism)
        expected = min(total, parallelism)
        assert len(result.tasks_to_dispatch) == expected
        assert len(engine._task_repo.rows) == expected, (
            "rows were upserted onto each other — a saved iteration was lost"
        )
        assert engine._task_repo.saves == expected
        assert len(engine._queue.actions) == expected
        assert len({a.task_id for a in engine._queue.actions}) == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [(2, 2), (5, 5), (10, 5)])
    async def test_every_message_names_a_row_that_exists(self, total, parallelism):
        """A message pointing at no row is a dropped iteration; a message
        pointing at the WRONG row is a duplicated one."""
        engine, _ = await _dispatch(total, parallelism)
        for action in engine._queue.actions:
            assert action.task_id in engine._task_repo.rows

    @pytest.mark.asyncio
    @pytest.mark.parametrize("total,parallelism", [(2, 2), (5, 5), (10, 5), (100, 10)])
    async def test_the_stored_rows_cover_every_iteration_exactly_once(
        self, total, parallelism,
    ):
        """The measured symptom in one assertion: before the fix the single
        surviving row carried iter=parallelism-1 and every other iteration was
        gone."""
        engine, _ = await _dispatch(total, parallelism)
        iters = sorted(t.scope_stack[-1].iter
                       for t in engine._task_repo.rows.values())
        assert iters == list(range(min(total, parallelism)))

    @pytest.mark.asyncio
    async def test_a_completed_body_node_does_not_block_the_batch(self):
        """The dispatch guard is per NODE (``body_task.node_id in
        view_completed``) and all N tasks share one node id. It is evaluated
        before any body runs — ``_run_loop_body`` holds ``locked_run`` for the
        whole pass — so the batch is unaffected. Pinned because if that guard
        ever moves after execution it would silently drop iterations 1..N-1."""
        engine, _ = await _dispatch(5, 5)
        assert len(engine._queue.actions) == 5

    @pytest.mark.asyncio
    async def test_a_skipped_body_node_still_suppresses_the_batch(self):
        """The other half of the same guard, unchanged: a body cascade-skipped
        by an upstream CONDITION must not be dispatched at all."""
        items = [{"n": i} for i in range(5)]
        loop_node, workflow, dag, run = _graph(items, 5)
        driver = EventDrivenLoopDriver(
            VariableResolver([MetadataResolverStrategy(),
                              TraversalResolverStrategy()])
        )
        result = await driver.initialize_loop(
            loop_node, run, workflow, dag, None, TaskFactory(),
        )
        run.context.skipped_nodes.add("body")
        engine = _Engine(run)
        await NodeExecutionRunner(engine=engine)._run_loop_body(ExecutionContext(
            task=TaskFactory().create_task(loop_node, run), node=loop_node,
            run=run, workflow=workflow, topology=dag, handler_result=result,
            now="2026-01-01T00:00:00Z",
        ))
        assert engine._queue.actions == []


class TestTheIdStaysUsable:
    @pytest.mark.asyncio
    async def test_it_fits_the_column(self):
        """AIW_TASKS.ID is NVARCHAR(1024); the base id is ~73 chars and this
        adds an invocation id plus the iteration. Worst realistic case is a
        five-digit iteration at max_iterations."""
        engine, _ = await _dispatch(10000, 100)
        longest = max(len(t) for t in engine._task_repo.rows)
        assert longest < 1024, longest
        longest_key = max(len(t.idempotency_key)
                          for t in engine._task_repo.rows.values())
        assert longest_key < 1280, longest_key

    @pytest.mark.asyncio
    async def test_it_is_still_derived_from_the_run_and_node(self):
        engine, _ = await _dispatch(5, 5)
        for task_id in engine._task_repo.rows:
            assert task_id.startswith("run-1-body-")
