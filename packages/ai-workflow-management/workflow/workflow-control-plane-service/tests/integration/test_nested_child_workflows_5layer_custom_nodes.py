"""5-layer nested sub-workflows with custom (NodeDefinition) nodes.

The most-complex deterministic nesting scenario: a Russian-doll chain of FIVE
workflows, each level invoking the next via a WORKFLOW node, and each level
carrying a *rich* node mix:

    Ln (n = 1..4):
        (l1-trig →) l{n}-ha (HUMAN_ACTION)
          → l{n}-cond (CONDITION  '{{$l{n}-ha._action}} == "confirm"')
              true_branch  → l{n}-biz  (custom TASK, node_definition_id)
              false_branch → l{n}-rej  (OUTPUT leaf)
          l{n}-biz → l{n}-compute (COMPUTE)
          l{n}-compute → l{n}-switch (SWITCH on the HA action)
              port "go"      → l{n}-wf (WORKFLOW → wf-l{n+1})
              port "default" → l{n}-side (OUTPUT, never taken on the spine)
          l{n}-wf → l{n}-out (OUTPUT)

    L5 (deepest): same minus the WORKFLOW hop — l5-switch "go" → l5-out.

Custom nodes are real ``NodeDefinition`` business templates: their
``definition_snapshot`` is stamped with the production
``NodeDefinitionResolver`` (exactly as ``CreateWorkflowUseCase`` does at save
time), so dispatch applies ``locked_defaults`` + ``input_mapping`` and
completion applies ``output_mapping``. L5 uses an *inheritance* definition
(parent → child) to exercise the resolver's chain merge.

What the tests prove end-to-end:
  1. 5-deep WORKFLOW nesting cascades to COMPLETED (depth ≤ max_depth=10).
  2. Custom nodes apply locked_defaults (``method`` wins) + input_mapping at
     dispatch and output_mapping at completion.
  3. A value produced by the *deepest* custom node ("L5-CUSTOM-OK") propagates
     back up all five levels into the root run's ``output_data`` via the
     OUTPUT-node child→parent contract (``{{$<wf-node>.output.<field>}}``).
  4. CONDITION / SWITCH routing + COMPUTE evaluation run inside every level.

Uses ``bootstrap.build_app_container()`` (default INFRA_MODE=mock) and a
``_drain_queue`` helper that drives the orchestration worker synchronously,
mirroring ``test_nested_child_workflows_4layer.py``.
"""

from __future__ import annotations

import asyncio
import pytest

import bootstrap
from app.layer1_domain.entities.node_definition import NodeDefinition
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
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand,
)
from app.layer2_application.definition.node_definition_resolver import (
    NodeDefinitionResolver,
)
from app.layer2_application.resolve.variable_resolver import (
    VariableResolutionError,
    set_current_run_id,
    reset_current_run_id,
)


# ---------------------------------------------------------------------------
# Harness helpers (mirrors test_nested_child_workflows_4layer.py)
# ---------------------------------------------------------------------------

async def _drain_queue(container, max_idle_rounds: int = 3) -> None:
    """Dequeue + dispatch every action until the queue stays empty for
    ``max_idle_rounds`` consecutive cooperative yields."""
    queue = container["_orchestration_queue"]
    worker = container["_orchestration_worker"]
    asyncio_queue = queue._queue
    idle = 0
    for _ in range(4000):
        if asyncio_queue.empty():
            idle += 1
            if idle >= max_idle_rounds:
                return
            await asyncio.sleep(0)
            continue
        idle = 0
        action = await asyncio.wait_for(queue.dequeue(), timeout=2.0)
        await worker._dispatch(action)
    raise AssertionError("Orchestration queue did not settle within 4000 iterations")


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


def _find_open_task(container, run_id: str, node_id: str):
    """Return the latest not-yet-terminal task for ``(run_id, node_id)`` — a
    dispatched custom TASK awaiting a worker callback."""
    task_repo = container["_infra"].repos.task
    open_tasks = [
        t for t in task_repo.list_by_run(run_id)
        if t.node_id == node_id
        and t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED)
    ]
    assert open_tasks, (
        f"No open task for node={node_id} in run={run_id}; "
        f"tasks={[(t.node_id, t.status.value) for t in task_repo.list_by_run(run_id)]}"
    )
    return open_tasks[-1]


async def _resolve_in_run(container, run_id: str, expr: str):
    """Resolve a ``{{...}}`` expression against a run's (file-backed) variables,
    the way production reads OUTPUT-node values. Returns None if unresolvable."""
    view = container["run_context_view_store"].get(run_id)
    variables = dict(view.variables) if view is not None else {}
    resolver = container["_orchestration_worker"]._engine._variable_resolver
    tok = set_current_run_id(run_id)
    try:
        return await resolver.resolve(expr, variables)
    except VariableResolutionError:
        return None
    finally:
        reset_current_run_id(tok)


def _child_run_id_for(container, parent_run_id: str, parent_node_id: str) -> str:
    """Return the child run id created by a WORKFLOW node, asserting it spawned."""
    run_repo = container["_infra"].repos.run
    parent_run = run_repo.get_by_id(parent_run_id)
    child_run_id = parent_run.context.child_runs.get(parent_node_id)
    assert child_run_id, (
        f"No child_runs entry for {parent_node_id!r} on parent {parent_run_id}; "
        f"child_runs={parent_run.context.child_runs}"
    )
    return child_run_id


# ---------------------------------------------------------------------------
# Custom node definitions (real NodeDefinition templates)
# ---------------------------------------------------------------------------

def _base_definition() -> NodeDefinition:
    """Business template wrapping a worker: locks ``method``, maps a payload in
    from the node's own input, and reshapes the worker output on completion."""
    return NodeDefinition(
        id="nd-business", tenant_id="t1", name="Business Step",
        base_type=NodeType.TASK, base_worker_id="biz-worker",
        data_mode="json", edit_mode="none",
        locked_defaults={"method": "POST"},
        input_mapping={"payload": "{{$custom.note}}"},
        output_mapping={
            "summary": "{{$_output.status_code}}",
            "tag": "{{$_output.tag}}",
        },
        input_schema=[{"key": "note", "outputType": "string"}],
        output_schema=[
            {"key": "summary", "outputType": "number"},
            {"key": "tag", "outputType": "string"},
        ],
    )


def _l5_inheritance_definition() -> NodeDefinition:
    """Child of nd-business that overrides only ``method`` — exercises the
    resolver's parent-chain merge (locked_defaults: child wins; base_worker_id,
    input/output_mapping, schemas inherited from the parent)."""
    return NodeDefinition(
        id="nd-business-l5", tenant_id="t1", name="Business Step (L5)",
        base_type=NodeType.TASK,
        parent_definition_id="nd-business",
        locked_defaults={"method": "PATCH"},
    )


def _register_definitions(container) -> None:
    repo = container["_infra"].repos.node_definition
    repo.save(_base_definition())
    repo.save(_l5_inheritance_definition())


def _enrich(workflow: Workflow, container) -> None:
    """Stamp ``definition_snapshot`` on every node that references a
    NodeDefinition, using the production resolver — the exact artifact
    ``CreateWorkflowUseCase._enrich_nodes_with_definitions`` writes at save time."""
    resolver = NodeDefinitionResolver()
    nd_repo = container["_infra"].repos.node_definition
    for node in workflow.nodes:
        if not node.node_definition_id:
            continue
        r = resolver.resolve(node.node_definition_id, nd_repo)
        node.definition_snapshot = {
            "input_schema": r.input_schema,
            "output_schema": r.output_schema,
            "data_mode": r.data_mode,
            "edit_mode": r.edit_mode,
            "locked_defaults": r.locked_defaults,
            "input_mapping": r.input_mapping,
            "output_mapping": r.output_mapping,
            "base_worker_id": r.base_worker_id,
        }
        if r.base_worker_id and not node.worker_type:
            node.worker_type = r.base_worker_id


# ---------------------------------------------------------------------------
# Per-level node builders
# ---------------------------------------------------------------------------

def _ha(n: int) -> WorkflowNode:
    return WorkflowNode(
        id=f"l{n}-ha", name=f"L{n}-HA",
        node_type=NodeType.HUMAN_ACTION,
        config={"instruction": f"L{n} approve?", "input_schema": []},
    )


def _cond(n: int) -> WorkflowNode:
    return WorkflowNode(
        id=f"l{n}-cond", name=f"L{n}-COND",
        node_type=NodeType.CONDITION,
        config={"expression": f'{{{{$l{n}-ha._action}}}} == "confirm"'},
    )


def _biz(n: int) -> WorkflowNode:
    """Custom TASK node. L5 uses the inheritance definition (method → PATCH)."""
    defn_id = "nd-business-l5" if n == 5 else "nd-business"
    return WorkflowNode(
        id=f"l{n}-biz", name=f"L{n}-BIZ",
        node_type=NodeType.TASK,
        node_definition_id=defn_id,
        parameters={"note": f"level-{n}-note"},
    )


def _compute(n: int) -> WorkflowNode:
    return WorkflowNode(
        id=f"l{n}-compute", name=f"L{n}-COMPUTE",
        node_type=NodeType.COMPUTE,
        config={
            "expression": f"{{{{$l{n}-biz.summary}}}} + {n}",
            "result_key": f"l{n}_total",
        },
    )


def _switch(n: int) -> WorkflowNode:
    return WorkflowNode(
        id=f"l{n}-switch", name=f"L{n}-SWITCH",
        node_type=NodeType.SWITCH,
        config={"data": [{
            "key": f"{{{{$l{n}-ha._action}}}}",
            "operations": "==",
            "value": "confirm",
            "branch": "go",
        }]},
    )


def _out(n: int, value_for_deepest: str) -> WorkflowNode:
    return WorkflowNode(
        id=f"l{n}-out", name=f"L{n}-OUT",
        node_type=NodeType.OUTPUT,
        config={"output_schema": [
            {"key": "deepest", "type": "string", "value": value_for_deepest},
            {"key": f"level{n}", "type": "number",
             "value": f"{{{{$l{n}-compute.output.result}}}}"},
        ]},
    )


def _leaf_output(node_id: str, marker: str) -> WorkflowNode:
    """A leaf OUTPUT (reject / unused-default branch). It exposes ``deepest`` as
    well as ``status`` so a parent level's OUTPUT can resolve
    ``{{$<wf-node>.output.deepest}}`` even when a deeper level took this leaf
    (the engine's OUTPUT-field resolution is strict — a missing path fails the
    node)."""
    return WorkflowNode(
        id=node_id, name=node_id.upper(),
        node_type=NodeType.OUTPUT,
        config={"output_schema": [
            {"key": "deepest", "type": "string", "value": marker},
            {"key": "status", "type": "string", "value": marker},
        ]},
    )


def _level_workflow(n: int) -> Workflow:
    """Build the workflow for level ``n``.

    Levels 1..4 nest into ``wf-l{n+1}`` via a WORKFLOW node; level 5 is the
    leaf and its OUTPUT seeds ``deepest`` from its own custom node's tag.
    """
    is_deepest = n == 5
    nodes: list[WorkflowNode] = []
    edges: list[WorkflowEdge] = []

    if n == 1:
        nodes.append(WorkflowNode(id="l1-trig", name="L1-START",
                                  node_type=NodeType.TRIGGER, config={}))
        edges.append(WorkflowEdge(id="l1-e-trig",
                                  source_node_id="l1-trig", target_node_id="l1-ha"))

    nodes += [_ha(n), _cond(n), _biz(n), _compute(n), _switch(n)]
    nodes.append(_leaf_output(f"l{n}-rej", "rejected"))

    # ha → cond → (biz | rej) → compute → switch
    edges += [
        WorkflowEdge(id=f"l{n}-e-hacond",
                     source_node_id=f"l{n}-ha", target_node_id=f"l{n}-cond"),
        WorkflowEdge(id=f"l{n}-e-true", source_node_id=f"l{n}-cond",
                     target_node_id=f"l{n}-biz", source_port_id="true_branch"),
        WorkflowEdge(id=f"l{n}-e-false", source_node_id=f"l{n}-cond",
                     target_node_id=f"l{n}-rej", source_port_id="false_branch"),
        WorkflowEdge(id=f"l{n}-e-bizcompute",
                     source_node_id=f"l{n}-biz", target_node_id=f"l{n}-compute"),
        WorkflowEdge(id=f"l{n}-e-computeswitch",
                     source_node_id=f"l{n}-compute", target_node_id=f"l{n}-switch"),
    ]

    # SWITCH default branch → an untaken side leaf at every level.
    nodes.append(_leaf_output(f"l{n}-side", "side"))
    edges.append(WorkflowEdge(id=f"l{n}-e-switchdef", source_node_id=f"l{n}-switch",
                              target_node_id=f"l{n}-side", source_port_id="default"))

    if is_deepest:
        # SWITCH "go" → OUTPUT; deepest seeded from THIS level's custom node tag.
        nodes.append(_out(n, value_for_deepest=f"{{{{$l{n}-biz.tag}}}}"))
        edges.append(WorkflowEdge(id=f"l{n}-e-switchout", source_node_id=f"l{n}-switch",
                                  target_node_id=f"l{n}-out", source_port_id="go"))
    else:
        # SWITCH "go" → WORKFLOW(next) → OUTPUT; deepest forwarded from child.
        nodes.append(WorkflowNode(
            id=f"l{n}-wf", name=f"L{n}-CALL-L{n+1}",
            node_type=NodeType.WORKFLOW,
            config={"workflow_id": f"wf-l{n+1}", "input_mapping": {}, "on_failure": "fail"},
        ))
        nodes.append(_out(n, value_for_deepest=f"{{{{$l{n}-wf.output.deepest}}}}"))
        edges += [
            WorkflowEdge(id=f"l{n}-e-switchwf", source_node_id=f"l{n}-switch",
                         target_node_id=f"l{n}-wf", source_port_id="go"),
            WorkflowEdge(id=f"l{n}-e-wfout",
                         source_node_id=f"l{n}-wf", target_node_id=f"l{n}-out"),
        ]

    return Workflow(id=f"wf-l{n}", name=f"L{n}", nodes=nodes, edges=edges)


def _seed_all_workflows(container) -> None:
    repo = container["_infra"].repos.workflow
    for n in range(1, 6):
        wf = _level_workflow(n)
        _enrich(wf, container)
        repo.save(wf)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def container():
    c = bootstrap.build_app_container()
    _register_definitions(c)
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_five_level_chain_with_custom_nodes_cascades_and_propagates(container):
    """Confirm at every layer: the chain descends L1→L5, each level's custom
    node applies its definition, all five runs COMPLETE, and the deepest custom
    node's tag propagates back to the root run's output_data."""
    _seed_all_workflows(container)
    start_run = container["start_run"]
    resume_pending = container["resume_pending_task"]
    complete_task = container["complete_task"]
    run_repo = container["_infra"].repos.run

    # Start L1 → drains to L1-HA PENDING.
    result = await start_run.execute(StartRunCommand(workflow_id="wf-l1", input_data={}))
    assert not result.error, f"start_run failed: {result.error}"
    await _drain_queue(container)

    run_ids = [result.id]
    biz_tasks: dict[int, object] = {}

    for n in range(1, 6):
        run_id = run_ids[n - 1]
        run = run_repo.get_by_id(run_id)
        assert run.status == RunStatus.PENDING, (
            f"L{n} should be PENDING on l{n}-ha; got {run.status}"
        )

        # Confirm the HUMAN_ACTION → CONDITION true → custom TASK dispatched.
        ha_task = _find_pending_ha_task(container, run_id, f"l{n}-ha")
        await resume_pending.execute(ResumePendingTaskCommand(
            run_id=run_id, task_id=ha_task.id,
            updated_input={"_action": "confirm"},
        ))
        await _drain_queue(container)

        # The custom TASK is dispatched and awaiting a worker callback.
        biz_task = _find_open_task(container, run_id, f"l{n}-biz")
        biz_tasks[n] = biz_task

        # locked_defaults + input_mapping applied at dispatch.
        resolved = biz_task.resolved_input or {}
        expected_method = "PATCH" if n == 5 else "POST"
        assert resolved.get("method") == expected_method, (
            f"L{n} custom node should lock method={expected_method}; got {resolved}"
        )
        assert resolved.get("payload") == f"level-{n}-note", (
            f"L{n} input_mapping should map payload from $custom.note; got {resolved}"
        )

        # Worker completes; output_mapping reshapes the result.
        complete = await complete_task.execute(CompleteTaskCommand(
            run_id=run_id, task_id=biz_task.id, status="COMPLETED",
            output_reference="",
            output_metadata={"status_code": 200 + n, "tag": f"L{n}-CUSTOM-OK"},
        ))
        assert complete.completed, f"L{n} biz completion failed: {complete.error}"
        await _drain_queue(container)

        if n < 5:
            child_run_id = _child_run_id_for(container, run_id, f"l{n}-wf")
            run_ids.append(child_run_id)

    assert len(run_ids) == 5, f"expected 5 nested runs; got {run_ids}"

    # 1. Every level reached COMPLETED.
    for n, run_id in enumerate(run_ids, start=1):
        run = run_repo.get_by_id(run_id)
        assert run.status == RunStatus.COMPLETED, (
            f"L{n} run {run_id} should be COMPLETED; got {run.status}; "
            f"completed_nodes={run.context.completed_nodes}; "
            f"skipped_nodes={run.context.skipped_nodes}"
        )

    # 2. Routing: each level took the CONDITION true edge and the SWITCH "go" edge.
    for n, run_id in enumerate(run_ids, start=1):
        run = run_repo.get_by_id(run_id)
        cond_edges = run.context.last_taken_edge_ports.get(f"l{n}-cond", {})
        assert "true_branch" in cond_edges.values(), (
            f"L{n}-COND should record the true_branch edge; got {cond_edges}"
        )
        switch_edges = run.context.last_taken_edge_ports.get(f"l{n}-switch", {})
        assert "go" in switch_edges.values(), (
            f"L{n}-SWITCH should record the 'go' edge; got {switch_edges}"
        )
        assert f"l{n}-biz" in run.context.completed_nodes, (
            f"L{n} custom node should have completed; got {run.context.completed_nodes}"
        )
        assert f"l{n}-rej" not in run.context.completed_nodes
        assert f"l{n}-side" not in run.context.completed_nodes

    # 3. Propagation: the deepest custom node's tag reached the root OUTPUT node
    #    through all five OUTPUT/WORKFLOW hops. The root run's output_data is
    #    keyed by the OUTPUT node id with file-backed fields, so resolve it the
    #    way production reads it.
    root_run = run_repo.get_by_id(run_ids[0])
    assert "l1-out" in (root_run.output_data or {}), (
        f"root run output_data should carry the OUTPUT node; got {root_run.output_data}"
    )
    deepest = await _resolve_in_run(container, run_ids[0], "{{$l1-out.output.deepest}}")
    assert deepest == "L5-CUSTOM-OK", (
        f"deepest custom-node value must propagate 5 levels to root output; "
        f"got {deepest!r} (output_data={root_run.output_data})"
    )
    level1 = await _resolve_in_run(container, run_ids[0], "{{$l1-out.output.level1}}")
    assert level1 is not None, "root OUTPUT should carry level1 from the COMPUTE node"


@pytest.mark.asyncio
async def test_reject_at_l3_short_circuits_l4_and_l5(container):
    """Reject at L3 → L4 and L5 never spawn; L3/L2/L1 still COMPLETE via the
    reject leaf, and no deepest value reaches the root."""
    _seed_all_workflows(container)
    start_run = container["start_run"]
    resume_pending = container["resume_pending_task"]
    complete_task = container["complete_task"]
    run_repo = container["_infra"].repos.run

    result = await start_run.execute(StartRunCommand(workflow_id="wf-l1", input_data={}))
    assert not result.error, f"start_run failed: {result.error}"
    await _drain_queue(container)

    run_ids = [result.id]
    for n in (1, 2):  # confirm L1 and L2 → descend to L3
        run_id = run_ids[n - 1]
        ha_task = _find_pending_ha_task(container, run_id, f"l{n}-ha")
        await resume_pending.execute(ResumePendingTaskCommand(
            run_id=run_id, task_id=ha_task.id, updated_input={"_action": "confirm"},
        ))
        await _drain_queue(container)
        biz_task = _find_open_task(container, run_id, f"l{n}-biz")
        await complete_task.execute(CompleteTaskCommand(
            run_id=run_id, task_id=biz_task.id, status="COMPLETED",
            output_reference="",
            output_metadata={"status_code": 200 + n, "tag": f"L{n}-CUSTOM-OK"},
        ))
        await _drain_queue(container)
        run_ids.append(_child_run_id_for(container, run_id, f"l{n}-wf"))

    l3_run_id = run_ids[2]

    # Reject at L3 → false branch leaf, no custom node, no L4 child.
    l3_ha = _find_pending_ha_task(container, l3_run_id, "l3-ha")
    await resume_pending.execute(ResumePendingTaskCommand(
        run_id=l3_run_id, task_id=l3_ha.id, updated_input={"_action": "reject"},
    ))
    await _drain_queue(container)

    l3_run = run_repo.get_by_id(l3_run_id)
    assert l3_run.status == RunStatus.COMPLETED, (
        f"L3 should COMPLETE on reject; got {l3_run.status}; "
        f"completed={l3_run.context.completed_nodes}"
    )
    assert l3_run.context.child_runs == {}, (
        f"L3 must NOT spawn a child on reject; got {l3_run.context.child_runs}"
    )
    assert "l3-rej" in l3_run.context.completed_nodes
    assert "l3-biz" not in l3_run.context.completed_nodes
    assert "l3-wf" not in l3_run.context.completed_nodes

    # L2 + L1 still complete once the child finishes.
    for n in (2, 1):
        run = run_repo.get_by_id(run_ids[n - 1])
        assert run.status == RunStatus.COMPLETED, (
            f"L{n} should COMPLETE once its child finishes; got {run.status}"
        )

    # No deepest value propagated (L4/L5 never ran).
    deepest = await _resolve_in_run(container, run_ids[0], "{{$l1-out.output.deepest}}")
    assert deepest != "L5-CUSTOM-OK", (
        f"deepest must NOT be the L5 marker on the reject path; got {deepest!r}"
    )
