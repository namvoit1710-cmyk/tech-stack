"""Regression: an unlocked run-aggregate writer must not land inside a locked
orchestration section.

Measured on tenant-1 2026-08-25. A four-way ``worker:code`` fan-out into one
MERGE failed 10 of 26 runs with::

    OptimisticConcurrencyError: Run 'x': expected version 7, found 8

``found`` was ``expected + 1`` in every single case — not two writers colliding
at random, but exactly ONE write landing between an orchestration action's load
and its save.

Every ``orchestrate_*`` action holds ``locked_run(run_id)`` and does
``aget_by_id -> mutate -> asave(run)``, with the save at the END of the section.
``CompleteTaskUseCase`` — the worker callback — writes the same aggregate and
took no lock at all, so a sibling branch's completion could land in the middle
of another branch's section and invalidate its in-memory ``run``.

MERGE is NOT the cause; it is only what makes the lost write fatal. Measured,
same worker, same tenant:

    4 branches -> MERGE          26 runs, 10 hard failures
    4 branches, no merge          8 runs,  4 conflicted (run still COMPLETED)
    2 branches, no merge          8 runs,  2 conflicted
    1 branch (serial)             8 runs,  0
    4 BUILTIN branches           10 runs,  0

The builtin fan-out runs inline UNDER the lock and produces no completion
callback. That contrast is what identifies the writer.

Why the existing suite could not catch it: every integration driver dispatches
one queued action at a time (see the ordering note in
``test_merge_strategy_fanout.py``) and no test has ever run a completion
concurrently with an orchestration action. ``MemoryRunRepository`` also returns
the SAME object it stored, so an in-place version bump propagates back and the
conflict cannot surface — the deep-copy patch below is what makes the memory
repo behave like HANA, the same trick ``test_child_workflow_cas_version.py``
needed.
"""

from __future__ import annotations

import asyncio
import copy
import types
import uuid

import pytest

import bootstrap
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer2_application.run_save_retry import save_run_with_cas_retry


def _make_run_repo_non_aliasing(container) -> None:
    """Reads return fresh copies, mirroring HANA's deserialize-per-read.
    Without this the memory repo hands back the stored object and the stale
    version can never be observed."""
    repo = container["_infra"].repos.run

    def _deepcopy_get(self, id):
        run = self._store.get(id)
        return copy.deepcopy(run) if run is not None else None

    repo.get_by_id = types.MethodType(_deepcopy_get, repo)


def _seed_run(container) -> str:
    run_id = str(uuid.uuid4())
    run = WorkflowRun(
        id=run_id,
        workflow_id="wf-irrelevant",
        status=RunStatus.RUNNING,
        context=RunContext(),
    )
    container["_infra"].repos.run.save(run)
    return run_id


@pytest.fixture
def container():
    return bootstrap.build_app_container()


@pytest.mark.asyncio
async def test_cas_writer_cannot_land_inside_a_locked_orchestration_section(container):
    """The invariant, stated directly: while an orchestration action holds a
    run's lock, no other coroutine may bump that run's stored version.

    Both halves of the assertion matter. The save must survive — and the
    callback's write must still be there afterwards, so that "make the
    conflict go away" cannot be satisfied by dropping the second write.
    """
    _make_run_repo_non_aliasing(container)
    engine = container["_orchestration_worker"]._engine
    run_repo = container["_infra"].repos.run
    run_id = _seed_run(container)

    inside_section = asyncio.Event()
    conflict: list[BaseException] = []

    async def orchestration_side() -> None:
        # Exactly the shape of orchestrate_continue / _execute_node_inner:
        # take the lock, load, do work that yields, save the loaded object.
        async with engine.locked_run(run_id):
            run = await run_repo.aget_by_id(run_id)
            inside_section.set()
            # Every await in the real section is an opening for another task.
            # 50ms stands in for the broker dispatch and file-service calls
            # that make the real window hundreds of milliseconds wide.
            await asyncio.sleep(0.05)
            run.context.variables["from_orchestration"] = 1
            try:
                await run_repo.asave(run)
            except BaseException as exc:  # noqa: BLE001
                conflict.append(exc)
                raise

    async def completion_side() -> None:
        # CompleteTaskUseCase's only run-aggregate write.
        await inside_section.wait()

        def _apply(fresh) -> None:
            fresh.context.variables["from_callback"] = 1

        await save_run_with_cas_retry(
            run_repo, run_id, _apply, use_case="test_completion",
        )

    await asyncio.wait_for(
        asyncio.gather(orchestration_side(), completion_side()), timeout=10,
    )

    assert not conflict, (
        "an unlocked writer bumped the version inside the orchestration "
        f"section: {conflict[0]!r}"
    )
    stored = run_repo.get_by_id(run_id)
    assert stored.context.variables.get("from_orchestration") == 1
    assert stored.context.variables.get("from_callback") == 1, (
        "the callback's write was lost — serialising the writers must not mean "
        "discarding one of them"
    )


@pytest.mark.asyncio
async def test_a_nested_write_on_a_second_run_never_waits_for_its_lock(container):
    """Serialising the writers must not introduce a downward acquisition.

    ``locked_run``'s ordering rule is strictly child -> parent: the child's
    lock may be held while the parent's is taken, never the reverse. A CAS
    write can be reached from inside another run's section —
    ``ChildWorkflowCoordinator.start`` runs ``check_run_completion(child_run)``
    under the PARENT's lock, and that bottoms out in ``_finalize`` ->
    ``save_run_with_cas_retry`` on the CHILD. If the helper took the child's
    lock there, it would deadlock against the legitimate upward path.

    Staged below exactly:

        task 1   holds B, then wants A     (the upward path: complete_parent)
        task 2   holds A, then writes B    (the downward one: coordinator.start)

    Both finish, or neither does.
    """
    _make_run_repo_non_aliasing(container)
    engine = container["_orchestration_worker"]._engine
    run_repo = container["_infra"].repos.run
    run_a = _seed_run(container)
    run_b = _seed_run(container)

    holds_b = asyncio.Event()
    a_taken = asyncio.Event()

    async def upward() -> None:
        async with engine.locked_run(run_b):
            holds_b.set()
            await a_taken.wait()
            # Blocks until `downward` lets go of A. Fine — as long as
            # `downward` is not itself blocked waiting on B.
            async with engine.locked_run(run_a):
                pass

    async def downward() -> None:
        await holds_b.wait()
        async with engine.locked_run(run_a):
            a_taken.set()
            await asyncio.sleep(0)

            def _apply(fresh) -> None:
                fresh.context.variables["nested"] = 1

            await save_run_with_cas_retry(
                run_repo, run_b, _apply, use_case="test_nested",
            )

    await asyncio.wait_for(asyncio.gather(upward(), downward()), timeout=5)
    assert run_repo.get_by_id(run_b).context.variables.get("nested") == 1


# ---------------------------------------------------------------------------
# End to end: the shape that actually failed on tenant-1.
# ---------------------------------------------------------------------------

def _make_run_repo_production_like(container) -> None:
    """Non-aliasing reads AND a yield point on every async repo call.

    ``MemoryRunRepository.aget_by_id`` / ``asave`` await nothing, so a test
    coroutine runs to completion before any other gets the loop — no
    interleaving, no race, whatever the code does. In the service both calls go
    through ``run_in_executor`` (``ASYNC_HANA_ENABLED``) and therefore yield.
    These wrappers restore that property; they are the difference between a
    test that can see this class of bug and one that cannot.
    """
    repo = container["_infra"].repos.run

    def _deepcopy_get(self, id):
        run = self._store.get(id)
        return copy.deepcopy(run) if run is not None else None

    async def _yielding_aget(self, id):
        await asyncio.sleep(0)
        return self.get_by_id(id)

    async def _yielding_asave(self, run):
        await asyncio.sleep(0)
        self.save(run)

    repo.get_by_id = types.MethodType(_deepcopy_get, repo)
    repo.aget_by_id = types.MethodType(_yielding_aget, repo)
    repo.asave = types.MethodType(_yielding_asave, repo)


def _wf_fanout_merge(branches: int = 4) -> Workflow:
    """TRIGGER -> N worker TASKs -> MERGE(wait_all). The tenant-1 repro,
    minus the code worker."""
    nodes = [WorkflowNode(id="trig", name="Start", node_type=NodeType.TRIGGER, config={})]
    edges = []
    for i in range(1, branches + 1):
        nodes.append(WorkflowNode(
            id=f"b{i}", name=f"Branch {i}",
            node_type=NodeType.TASK, worker_type="http-worker", config={},
        ))
        edges.append(WorkflowEdge(
            id=f"e-t{i}", source_node_id="trig", target_node_id=f"b{i}",
        ))
        edges.append(WorkflowEdge(
            id=f"e-b{i}", source_node_id=f"b{i}", target_node_id="join",
            source_port_id="success",
        ))
    nodes.append(WorkflowNode(
        id="join", name="Join", node_type=NodeType.MERGE,
        config={"merge_strategy": "wait_all"},
    ))
    return Workflow(id="wf-fanout", name="Fanout", nodes=nodes, edges=edges)


async def _drain_until_idle(container, rounds: int = 3) -> None:
    queue = container["_orchestration_queue"]
    worker = container["_orchestration_worker"]
    idle = 0
    for _ in range(4000):
        if queue._queue.empty():
            idle += 1
            if idle >= rounds:
                return
            await asyncio.sleep(0)
            continue
        idle = 0
        await worker._dispatch(await queue.dequeue())
    raise AssertionError("orchestration queue did not settle")


async def _drain_forever(container, stop: asyncio.Event) -> None:
    """One orchestration worker coroutine. The service runs
    ``ORCHESTRATION_WORKER_COUNT`` of these (default 4) on one event loop."""
    queue = container["_orchestration_queue"]
    worker = container["_orchestration_worker"]
    while not stop.is_set():
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=0.02)
        except (asyncio.TimeoutError, TimeoutError):
            continue
        await worker._dispatch(action)


@pytest.mark.asyncio
async def test_parallel_branch_completions_do_not_fail_the_run(container):
    """Four worker callbacks land while the orchestration workers are running.

    This is the tenant-1 shape: four ``worker:code`` branches into one MERGE,
    38% of runs dead. It fails here for the same reason it failed there — not
    because MERGE is wrong, but because the completions were writing the run
    aggregate outside the lock the orchestration actions hold.
    """
    _make_run_repo_production_like(container)
    container["_infra"].repos.workflow.save(_wf_fanout_merge(4))
    run_repo = container["_infra"].repos.run
    task_repo = container["_infra"].repos.task

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="wf-fanout", input_data={}),
    )
    assert not result.error, result.error
    run_id = result.id

    await _drain_until_idle(container)

    branch_tasks = [
        t for t in task_repo.list_by_run(run_id) if t.node_id.startswith("b")
    ]
    assert len(branch_tasks) == 4, (
        f"expected 4 dispatched branch tasks, got "
        f"{[(t.node_id, t.status) for t in task_repo.list_by_run(run_id)]}"
    )

    stop = asyncio.Event()
    drains = [asyncio.create_task(_drain_forever(container, stop)) for _ in range(4)]
    try:
        await asyncio.wait_for(
            asyncio.gather(*[
                container["complete_task"].execute(CompleteTaskCommand(
                    run_id=run_id, task_id=t.id, status="COMPLETED",
                    output_reference=f"ref-{t.node_id}",
                    output_metadata={"branch": t.node_id},
                ))
                for t in branch_tasks
            ]),
            timeout=30,
        )
        # Let the four workers finish everything the completions enqueued.
        for _ in range(200):
            if container["_orchestration_queue"]._queue.empty():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
    finally:
        stop.set()
        await asyncio.gather(*drains, return_exceptions=True)

    run = run_repo.get_by_id(run_id)
    assert run.status is not RunStatus.FAILED, (
        f"parallel completions failed the run: {run.error!r}"
    )
    assert not run.error, f"run carries an error: {run.error!r}"
    # Every branch's output has to be in the aggregate — a fix that serialises
    # the writers must not drop one of them.
    for t in branch_tasks:
        assert t.node_id in run.context.variables, (
            f"branch {t.node_id} output missing from the run context; "
            f"present={sorted(run.context.variables)}"
        )
