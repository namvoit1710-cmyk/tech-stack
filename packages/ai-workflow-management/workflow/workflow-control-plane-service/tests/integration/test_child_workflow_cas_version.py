"""Regression test: triggering a sub-workflow must not fail the parent run
with an optimistic-CAS version conflict.

Bug: ``ChildWorkflowCoordinator.start`` persists the parent run via
``save_run_with_cas_retry`` (bumping the stored ``version``) but discarded
the helper's return value, so the engine's in-memory ``run`` kept the stale
version. The trailing ``asave(run)`` in ``_execute_node_inner`` then hit
``OptimisticConcurrencyError(expected=N, actual=N+1)`` and the WORKFLOW node
was marked FAILED — "expected version 22, found 23".

Why a normal mock-mode test can't catch it: ``MemoryRunRepository.get_by_id``
returns the *same object reference* it stored, so the in-place version bump
propagates back automatically and the conflict never surfaces. Production
(HANA) deserializes a fresh object per read, so it does. This test patches the
run repo to deep-copy on read, faithfully reproducing the non-aliasing
behavior, then drives a WORKFLOW node through the real queued execution path
(``orchestrate_execute_node``).
"""

from __future__ import annotations

import asyncio
import copy
import types

import pytest

import bootstrap
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _drain_queue(container, max_idle_rounds: int = 3) -> None:
    """Dequeue + dispatch every action until the queue settles."""
    queue = container["_orchestration_queue"]
    worker = container["_orchestration_worker"]
    asyncio_queue = queue._queue
    idle = 0
    for _ in range(2000):
        if asyncio_queue.empty():
            idle += 1
            if idle >= max_idle_rounds:
                return
            await asyncio.sleep(0)
            continue
        idle = 0
        action = await asyncio.wait_for(queue.dequeue(), timeout=2.0)
        await worker._dispatch(action)
    raise AssertionError("Orchestration queue did not settle within 2000 iterations")


def _make_run_repo_non_aliasing(container) -> None:
    """Patch the container's run repo so reads return fresh copies, mirroring
    the HANA repo's deserialize-per-read semantics. Every component in the
    container references this same instance, so patching its ``get_by_id``
    (which ``aget_by_id`` delegates to) is enough to make all readers
    non-aliasing.
    """
    repo = container["_infra"].repos.run

    def _deepcopy_get(self, id):
        run = self._store.get(id)
        return copy.deepcopy(run) if run is not None else None

    repo.get_by_id = types.MethodType(_deepcopy_get, repo)


def _wf_child() -> Workflow:
    """Child workflow: a single HUMAN_ACTION entry node so the child pauses
    (PENDING) and never completes during the drain — keeps this test scoped
    to the *start* path, not child-completion."""
    return Workflow(
        id="wf-child", name="Child",
        nodes=[
            WorkflowNode(
                id="c-ha", name="Child-HA",
                node_type=NodeType.HUMAN_ACTION,
                config={"instruction": "approve?", "input_schema": []},
            ),
        ],
        edges=[],
    )


def _wf_parent() -> Workflow:
    """Parent workflow: TRIGGER → WORKFLOW(wf-child)."""
    return Workflow(
        id="wf-parent", name="Parent",
        nodes=[
            WorkflowNode(id="p-trig", name="Start", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="p-wf", name="CallChild",
                node_type=NodeType.WORKFLOW,
                config={"workflow_id": "wf-child", "input_mapping": {}, "on_failure": "fail"},
            ),
        ],
        edges=[
            WorkflowEdge(id="p-e1", source_node_id="p-trig", target_node_id="p-wf"),
        ],
    )


@pytest.fixture
def container():
    """Full app container with default INFRA_MODE=mock wiring."""
    return bootstrap.build_app_container()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_sub_workflow_does_not_fail_parent_on_version(container):
    """A WORKFLOW node spawning a child must leave the parent run healthy —
    no spurious FAILED from a stale-version double-save."""
    _make_run_repo_non_aliasing(container)
    wf_repo = container["_infra"].repos.workflow
    wf_repo.save(_wf_parent())
    wf_repo.save(_wf_child())

    run_repo = container["_infra"].repos.run
    start_run = container["start_run"]

    result = await start_run.execute(
        StartRunCommand(workflow_id="wf-parent", input_data={}),
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id

    # Drives p-trig → p-wf through orchestrate_execute_node → the child-run
    # start path. Before the fix this raises OptimisticConcurrencyError on the
    # parent and marks the run FAILED.
    await _drain_queue(container)

    parent_run = run_repo.get_by_id(parent_run_id)
    assert parent_run.status != RunStatus.FAILED, (
        f"Parent run was failed during sub-workflow trigger: {parent_run.error!r}"
    )
    assert not parent_run.error, f"Parent run carries an error: {parent_run.error!r}"

    # The child was actually registered and spawned.
    child_run_id = parent_run.context.child_runs.get("p-wf")
    assert child_run_id, (
        f"WORKFLOW node did not register a child run; "
        f"child_runs={parent_run.context.child_runs}"
    )
    assert run_repo.get_by_id(child_run_id) is not None, "Child run was not persisted"
