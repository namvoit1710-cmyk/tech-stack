"""4-layer nested child-workflow integration tests.

Builds a Russian-doll structure where each layer has CONDITION + HUMAN_ACTION
+ WORKFLOW (to call the next layer):

    L1 (root parent):
        TRIGGER → HA1 → COND1
                          ├─ true  → WORKFLOW(wf-l2)
                          └─ false → leaf (l1-rejected)
    L2:
        HA2 → COND2
                ├─ true  → WORKFLOW(wf-l3)
                └─ false → leaf (l2-rejected)
    L3:
        HA3 → COND3
                ├─ true  → WORKFLOW(wf-l4)
                └─ false → leaf (l3-rejected)
    L4 (deepest):
        HA4 → COND4
                ├─ true  → leaf (l4-done)
                └─ false → leaf (l4-skipped)

Each layer's CONDITION reads ``{{$<layer>-ha._action}} == "confirm"``,
so submitting the layer's HUMAN_ACTION with ``_action: confirm`` descends
to the next layer; submitting ``_action: reject`` takes the false branch
to the leaf and prevents the child workflow from spawning.

Tests cover:
1. ``test_all_confirm_descends_through_all_four_layers`` — user confirms at
   every layer; child runs cascade L1→L2→L3→L4; every run reaches
   COMPLETED.
2. ``test_reject_at_l2_short_circuits_l3_and_l4`` — reject at L2; L3 and L4
   never spawn; L2 + L1 still reach COMPLETED via the false-branch leaf.

Uses ``bootstrap.build_app_container()`` for full production-shaped wiring
(handler registry, snapshot repo, dispatcher, projector, queue) and a
``_drain_queue`` helper that drives the orchestration worker synchronously.
"""

from __future__ import annotations

import asyncio
import pytest

import bootstrap
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskCommand,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _drain_queue(container, max_idle_rounds: int = 3) -> None:
    """Dequeue + dispatch every action until the queue is empty for
    ``max_idle_rounds`` consecutive cooperative yields.

    Mirrors ``OrchestrationWorker.run_forever`` but returns once the
    queue settles. Necessary because the test process doesn't start the
    real worker loop.
    """
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


def _find_pending_ha_task(container, run_id: str, node_id: str):
    """Return the single PENDING HUMAN_ACTION task for ``(run_id, node_id)``."""
    task_repo = container["_infra"].repos.task
    tasks = task_repo.list_by_run(run_id)
    pending = [
        t for t in tasks
        if t.node_id == node_id and t.status == TaskStatus.PENDING
    ]
    assert len(pending) == 1, (
        f"Expected exactly one PENDING task for node={node_id} in run={run_id}; "
        f"got tasks={[(t.node_id, t.status.value) for t in tasks]}"
    )
    return pending[0]


def _child_run_id_for(container, parent_run_id: str, parent_node_id: str) -> str:
    """Return the child run id created by a WORKFLOW node, asserting it
    actually got spawned (the most common failure shape)."""
    run_repo = container["_infra"].repos.run
    parent_run = run_repo.get_by_id(parent_run_id)
    child_run_id = parent_run.context.child_runs.get(parent_node_id)
    assert child_run_id, (
        f"No child_runs entry for {parent_node_id!r} on parent {parent_run_id}; "
        f"child_runs={parent_run.context.child_runs}"
    )
    return child_run_id


# ---------------------------------------------------------------------------
# Workflow fixtures — one per layer
# ---------------------------------------------------------------------------

def _wf_l4() -> Workflow:
    """L4 (deepest): HA → COND → leaf either way. No further WORKFLOW node."""
    return Workflow(
        id="wf-l4", name="L4",
        nodes=[
            WorkflowNode(
                id="l4-ha", name="L4-HA",
                node_type=NodeType.HUMAN_ACTION,
                config={"instruction": "L4 approve?", "input_schema": []},
            ),
            WorkflowNode(
                id="l4-cond", name="L4-COND",
                node_type=NodeType.CONDITION,
                config={"expression": '{{$l4-ha._action}} == "confirm"'},
            ),
            WorkflowNode(id="l4-done", name="L4-DONE", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="l4-skipped", name="L4-SKIPPED", node_type=NodeType.TRIGGER, config={}),
        ],
        edges=[
            WorkflowEdge(id="l4-e1", source_node_id="l4-ha", target_node_id="l4-cond"),
            WorkflowEdge(
                id="l4-e2", source_node_id="l4-cond",
                target_node_id="l4-done", source_port_id="true_branch",
            ),
            WorkflowEdge(
                id="l4-e3", source_node_id="l4-cond",
                target_node_id="l4-skipped", source_port_id="false_branch",
            ),
        ],
    )


def _wf_l3() -> Workflow:
    """L3: HA → COND → (true: WORKFLOW(L4); false: leaf)."""
    return Workflow(
        id="wf-l3", name="L3",
        nodes=[
            WorkflowNode(
                id="l3-ha", name="L3-HA",
                node_type=NodeType.HUMAN_ACTION,
                config={"instruction": "L3 approve?", "input_schema": []},
            ),
            WorkflowNode(
                id="l3-cond", name="L3-COND",
                node_type=NodeType.CONDITION,
                config={"expression": '{{$l3-ha._action}} == "confirm"'},
            ),
            WorkflowNode(
                id="l3-wf-l4", name="L3-CallL4",
                node_type=NodeType.WORKFLOW,
                config={"workflow_id": "wf-l4", "input_mapping": {}, "on_failure": "fail"},
            ),
            WorkflowNode(id="l3-rejected", name="L3-REJECTED", node_type=NodeType.TRIGGER, config={}),
        ],
        edges=[
            WorkflowEdge(id="l3-e1", source_node_id="l3-ha", target_node_id="l3-cond"),
            WorkflowEdge(
                id="l3-e2", source_node_id="l3-cond",
                target_node_id="l3-wf-l4", source_port_id="true_branch",
            ),
            WorkflowEdge(
                id="l3-e3", source_node_id="l3-cond",
                target_node_id="l3-rejected", source_port_id="false_branch",
            ),
        ],
    )


def _wf_l2() -> Workflow:
    """L2: HA → COND → (true: WORKFLOW(L3); false: leaf)."""
    return Workflow(
        id="wf-l2", name="L2",
        nodes=[
            WorkflowNode(
                id="l2-ha", name="L2-HA",
                node_type=NodeType.HUMAN_ACTION,
                config={"instruction": "L2 approve?", "input_schema": []},
            ),
            WorkflowNode(
                id="l2-cond", name="L2-COND",
                node_type=NodeType.CONDITION,
                config={"expression": '{{$l2-ha._action}} == "confirm"'},
            ),
            WorkflowNode(
                id="l2-wf-l3", name="L2-CallL3",
                node_type=NodeType.WORKFLOW,
                config={"workflow_id": "wf-l3", "input_mapping": {}, "on_failure": "fail"},
            ),
            WorkflowNode(id="l2-rejected", name="L2-REJECTED", node_type=NodeType.TRIGGER, config={}),
        ],
        edges=[
            WorkflowEdge(id="l2-e1", source_node_id="l2-ha", target_node_id="l2-cond"),
            WorkflowEdge(
                id="l2-e2", source_node_id="l2-cond",
                target_node_id="l2-wf-l3", source_port_id="true_branch",
            ),
            WorkflowEdge(
                id="l2-e3", source_node_id="l2-cond",
                target_node_id="l2-rejected", source_port_id="false_branch",
            ),
        ],
    )


def _wf_l1() -> Workflow:
    """L1 (root parent): TRIGGER → HA → COND → (true: WORKFLOW(L2); false: leaf)."""
    return Workflow(
        id="wf-l1", name="L1",
        nodes=[
            WorkflowNode(id="l1-trig", name="L1-START", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="l1-ha", name="L1-HA",
                node_type=NodeType.HUMAN_ACTION,
                config={"instruction": "L1 approve?", "input_schema": []},
            ),
            WorkflowNode(
                id="l1-cond", name="L1-COND",
                node_type=NodeType.CONDITION,
                config={"expression": '{{$l1-ha._action}} == "confirm"'},
            ),
            WorkflowNode(
                id="l1-wf-l2", name="L1-CallL2",
                node_type=NodeType.WORKFLOW,
                config={"workflow_id": "wf-l2", "input_mapping": {}, "on_failure": "fail"},
            ),
            WorkflowNode(id="l1-rejected", name="L1-REJECTED", node_type=NodeType.TRIGGER, config={}),
        ],
        edges=[
            WorkflowEdge(id="l1-e0", source_node_id="l1-trig", target_node_id="l1-ha"),
            WorkflowEdge(id="l1-e1", source_node_id="l1-ha", target_node_id="l1-cond"),
            WorkflowEdge(
                id="l1-e2", source_node_id="l1-cond",
                target_node_id="l1-wf-l2", source_port_id="true_branch",
            ),
            WorkflowEdge(
                id="l1-e3", source_node_id="l1-cond",
                target_node_id="l1-rejected", source_port_id="false_branch",
            ),
        ],
    )


def _seed_all_workflows(container) -> None:
    repo = container["_infra"].repos.workflow
    for wf in (_wf_l1(), _wf_l2(), _wf_l3(), _wf_l4()):
        repo.save(wf)


# ---------------------------------------------------------------------------
# Test fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def container():
    """Full app container with default INFRA_MODE=mock wiring."""
    return bootstrap.build_app_container()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_confirm_descends_through_all_four_layers(container):
    """User confirms at every layer.

    Expected DAG progression:
        start L1 → L1-HA PENDING
        confirm L1 → COND1 true → spawn L2 → L2-HA PENDING
        confirm L2 → COND2 true → spawn L3 → L3-HA PENDING
        confirm L3 → COND3 true → spawn L4 → L4-HA PENDING
        confirm L4 → COND4 true → L4-DONE → L4 COMPLETED
                  → L3 advances → L3 COMPLETED
                  → L2 advances → L2 COMPLETED
                  → L1 advances → L1 COMPLETED
    """
    _seed_all_workflows(container)
    start_run = container["start_run"]
    resume_pending = container["resume_pending_task"]
    run_repo = container["_infra"].repos.run

    # 1. Start L1 — drains to L1-HA PENDING.
    result = await start_run.execute(StartRunCommand(workflow_id="wf-l1", input_data={}))
    assert not result.error, f"start_run failed: {result.error}"
    l1_run_id = result.id
    await _drain_queue(container)

    l1_run = run_repo.get_by_id(l1_run_id)
    assert l1_run.status == RunStatus.PENDING, (
        f"L1 should be PENDING on L1-HA; got {l1_run.status}"
    )
    assert l1_run.context.child_runs == {}, "No child should be spawned yet"

    # 2. Confirm L1-HA → spawns L2.
    l1_ha_task = _find_pending_ha_task(container, l1_run_id, "l1-ha")
    await resume_pending.execute(ResumePendingTaskCommand(
        run_id=l1_run_id, task_id=l1_ha_task.id,
        updated_input={"_action": "confirm"},
    ))
    await _drain_queue(container)

    l2_run_id = _child_run_id_for(container, l1_run_id, "l1-wf-l2")
    l2_run = run_repo.get_by_id(l2_run_id)
    assert l2_run.status == RunStatus.PENDING, (
        f"L2 should be PENDING on L2-HA; got {l2_run.status}"
    )

    # 3. Confirm L2-HA → spawns L3.
    l2_ha_task = _find_pending_ha_task(container, l2_run_id, "l2-ha")
    await resume_pending.execute(ResumePendingTaskCommand(
        run_id=l2_run_id, task_id=l2_ha_task.id,
        updated_input={"_action": "confirm"},
    ))
    await _drain_queue(container)

    l3_run_id = _child_run_id_for(container, l2_run_id, "l2-wf-l3")
    l3_run = run_repo.get_by_id(l3_run_id)
    assert l3_run.status == RunStatus.PENDING

    # 4. Confirm L3-HA → spawns L4.
    l3_ha_task = _find_pending_ha_task(container, l3_run_id, "l3-ha")
    await resume_pending.execute(ResumePendingTaskCommand(
        run_id=l3_run_id, task_id=l3_ha_task.id,
        updated_input={"_action": "confirm"},
    ))
    await _drain_queue(container)

    l4_run_id = _child_run_id_for(container, l3_run_id, "l3-wf-l4")
    l4_run = run_repo.get_by_id(l4_run_id)
    assert l4_run.status == RunStatus.PENDING

    # 5. Confirm L4-HA → cascades completion all the way back to L1.
    l4_ha_task = _find_pending_ha_task(container, l4_run_id, "l4-ha")
    await resume_pending.execute(ResumePendingTaskCommand(
        run_id=l4_run_id, task_id=l4_ha_task.id,
        updated_input={"_action": "confirm"},
    ))
    await _drain_queue(container)

    # Every layer must reach COMPLETED.
    for label, run_id in (
        ("L4", l4_run_id), ("L3", l3_run_id),
        ("L2", l2_run_id), ("L1", l1_run_id),
    ):
        run = run_repo.get_by_id(run_id)
        assert run.status == RunStatus.COMPLETED, (
            f"{label} run {run_id} should be COMPLETED; got {run.status}; "
            f"completed_nodes={run.context.completed_nodes}; "
            f"skipped_nodes={run.context.skipped_nodes}"
        )

    # Confirm branch was taken at every layer. Under edge-based dispatch
    # the cascade-skip BFS was retired (see fanout.py Phase 4b.2): untaken
    # branches are simply not dispatched — their targets stay neither
    # completed nor skipped. The invariant we can assert is:
    #   * the taken target IS in completed_nodes
    #   * the untaken target is NOT in completed_nodes
    #   * last_taken_edge_ports on the CONDITION node records the chosen edge
    def _layer_took_confirm(run_id: str, cond_node: str, taken_target: str, untaken_target: str, taken_edge: str) -> None:
        run = run_repo.get_by_id(run_id)
        assert taken_target in run.context.completed_nodes, (
            f"{taken_target} should be completed; got {run.context.completed_nodes}"
        )
        assert untaken_target not in run.context.completed_nodes, (
            f"{untaken_target} must NOT be completed; got {run.context.completed_nodes}"
        )
        chosen = run.context.last_taken_edge_ports.get(cond_node, {})
        assert taken_edge in chosen, (
            f"{cond_node} should have taken edge {taken_edge}; got {chosen}"
        )

    _layer_took_confirm(l4_run_id, "l4-cond", "l4-done", "l4-skipped", "l4-e2")
    _layer_took_confirm(l3_run_id, "l3-cond", "l3-wf-l4", "l3-rejected", "l3-e2")
    _layer_took_confirm(l2_run_id, "l2-cond", "l2-wf-l3", "l2-rejected", "l2-e2")
    _layer_took_confirm(l1_run_id, "l1-cond", "l1-wf-l2", "l1-rejected", "l1-e2")


@pytest.mark.asyncio
async def test_reject_at_l2_short_circuits_l3_and_l4(container):
    """Reject at L2 → L3 and L4 never spawn; L2 + L1 still reach COMPLETED."""
    _seed_all_workflows(container)
    start_run = container["start_run"]
    resume_pending = container["resume_pending_task"]
    run_repo = container["_infra"].repos.run

    # Start + L1 confirm → spawn L2.
    result = await start_run.execute(StartRunCommand(workflow_id="wf-l1", input_data={}))
    l1_run_id = result.id
    await _drain_queue(container)
    l1_ha_task = _find_pending_ha_task(container, l1_run_id, "l1-ha")
    await resume_pending.execute(ResumePendingTaskCommand(
        run_id=l1_run_id, task_id=l1_ha_task.id,
        updated_input={"_action": "confirm"},
    ))
    await _drain_queue(container)

    l2_run_id = _child_run_id_for(container, l1_run_id, "l1-wf-l2")

    # L2 rejects → false branch leaf, no L3 child.
    l2_ha_task = _find_pending_ha_task(container, l2_run_id, "l2-ha")
    await resume_pending.execute(ResumePendingTaskCommand(
        run_id=l2_run_id, task_id=l2_ha_task.id,
        updated_input={"_action": "reject"},
    ))
    await _drain_queue(container)

    l2_run = run_repo.get_by_id(l2_run_id)
    assert l2_run.status == RunStatus.COMPLETED, (
        f"L2 should COMPLETE on reject; got {l2_run.status}; "
        f"completed={l2_run.context.completed_nodes}; skipped={l2_run.context.skipped_nodes}"
    )
    assert l2_run.context.child_runs == {}, (
        f"L2 must NOT have spawned a child on reject; got {l2_run.context.child_runs}"
    )
    assert "l2-rejected" in l2_run.context.completed_nodes, (
        f"L2's rejected-branch leaf must complete; completed={l2_run.context.completed_nodes}"
    )
    # Under edge-based dispatch, the untaken WORKFLOW target isn't added
    # to ``skipped_nodes`` — it simply isn't dispatched. Assert it stayed
    # out of completed_nodes and that the CONDITION recorded the
    # false-branch edge as taken.
    assert "l2-wf-l3" not in l2_run.context.completed_nodes, (
        f"L2's WORKFLOW node must NOT have run on reject; completed={l2_run.context.completed_nodes}"
    )
    assert l2_run.context.last_taken_edge_ports.get("l2-cond", {}).get("l2-e3") == "false_branch", (
        f"L2-COND should record l2-e3 as the taken edge; got {l2_run.context.last_taken_edge_ports}"
    )

    # L1's WORKFLOW node completes → L1 completes.
    l1_run = run_repo.get_by_id(l1_run_id)
    assert l1_run.status == RunStatus.COMPLETED, (
        f"L1 should COMPLETE once its child L2 finishes; got {l1_run.status}; "
        f"completed={l1_run.context.completed_nodes}; skipped={l1_run.context.skipped_nodes}"
    )
    assert "l1-wf-l2" in l1_run.context.completed_nodes
    assert "l1-rejected" not in l1_run.context.completed_nodes
    assert l1_run.context.last_taken_edge_ports.get("l1-cond", {}).get("l1-e2") == "true_branch"
