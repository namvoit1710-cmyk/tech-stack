"""WORKFLOW node exposes every sub-workflow node's output.

A WORKFLOW node's output must surface all of its child run's node outputs so
the parent can address them with a single ``.output`` hop:

    {{$<wf-node>.output.<child-node-id>.<field>}}

Regression: previously each child node sat under a redundant second
``.output`` (``...<child-node-id>.output.<field>``), so the natural
single-hop expression failed to resolve.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.resolve.variable_resolver import (
    set_current_run_id,
    reset_current_run_id,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskCommand,
)
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand,
)
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


def _child_workflow() -> Workflow:
    """TRIGGER → COMPUTE(21 + 21 → {"result": 42})."""
    return Workflow(
        id="child-wf", name="child",
        nodes=[
            WorkflowNode(id="child-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="child-compute", name="cc", node_type=NodeType.COMPUTE,
                config={"expression": "21 + 21"},
            ),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="child-trig", target_node_id="child-compute")],
    )


def _parent_workflow() -> Workflow:
    """TRIGGER → WORKFLOW(child-wf)."""
    return Workflow(
        id="parent-wf", name="parent",
        nodes=[
            WorkflowNode(id="p-trig", name="pt", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="wf-node", name="wn", node_type=NodeType.WORKFLOW,
                config={"workflow_id": "child-wf", "input_mapping": {}, "on_failure": "fail"},
            ),
        ],
        edges=[WorkflowEdge(id="pe1", source_node_id="p-trig", target_node_id="wf-node")],
    )


@pytest.mark.asyncio
async def test_workflow_node_exposes_all_child_node_outputs(container):
    repo = container["_infra"].repos.workflow
    repo.save(_child_workflow())
    repo.save(_parent_workflow())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-wf", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id
    await _drain(container)

    parent_view = container["run_context_view_store"].get(parent_run_id)
    assert parent_view is not None
    variables = dict(parent_view.variables)

    resolver = container["_orchestration_worker"]._engine._variable_resolver
    tok = set_current_run_id(parent_run_id)
    try:
        # Single ``.output`` hop reaches each child node's output content.
        compute_result = await resolver.resolve(
            "{{$wf-node.output.child-compute.result}}", variables,
        )
        assert compute_result == 42

        trigger_type = await resolver.resolve(
            "{{$wf-node.output.child-trig.type}}", variables,
        )
        assert trigger_type == "manual"

        # The aggregate ``.output`` is keyed by child node id, each value the
        # node's output content directly — no nested ``output`` wrapper.
        # COMPUTE output also carries its evaluation trace (expression /
        # resolved) alongside the result.
        full = await resolver.resolve("{{$wf-node.output}}", variables)
        assert full["child-compute"]["result"] == 42
        assert full["child-compute"]["resolved"] == "21 + 21"
        assert "output" not in full["child-compute"]
    finally:
        reset_current_run_id(tok)


@pytest.mark.asyncio
async def test_child_relay_refuses_oversized_output(monkeypatch):
    """SA-1942 — the child-workflow output relay is size-guarded. With a 1-byte
    cap, the child COMPUTE output (a file-backed value) can't be materialized
    into the parent inline, so the parent run FAILS with the OOM-guard error
    instead of OOMing the pod. The default 64 MB cap (other tests) leaves the
    envelope-unwrap unchanged."""
    from app.layer4_frameworks.config.app_config import settings as _settings
    from app.layer1_domain.value_objects.run_status import RunStatus

    monkeypatch.setattr(_settings, "RESOLVE_MATERIALIZE_MAX_BYTES", 1)
    container = build_app_container()

    repo = container["_infra"].repos.workflow
    repo.save(_child_workflow())
    repo.save(_parent_workflow())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-wf", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id
    await _drain(container)

    parent_run = container["_infra"].repos.run.get_by_id(parent_run_id)
    assert parent_run.status == RunStatus.FAILED, (
        f"expected the guarded relay to fail the run, got {parent_run.status}"
    )
    # Non-tautological: prove the failure came from the RELAY guard specifically
    # (not some other cap trip under the global 1-byte setting) — the WORKFLOW
    # node's tracking task must carry the child-relay OOM-guard reason.
    wf_task = next(
        t for t in container["_infra"].repos.task.list_by_run(parent_run_id)
        if t.node_id == "wf-node" and t.status == TaskStatus.FAILED
    )
    assert "could not be relayed" in (wf_task.error or "")
    assert "OOM guard" in (wf_task.error or "")


def _task_child_workflow() -> Workflow:
    """TRIGGER → TASK(http_request). The sub-workflow's work is a worker call
    whose output the parent must be able to read."""
    return Workflow(
        id="task-child-wf", name="task-child",
        nodes=[
            WorkflowNode(id="tc-trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="tc-http", name="Fetch", node_type=NodeType.TASK,
                worker_type="http_request",
                input_binding={"method": "GET", "url": "https://example.com"},
            ),
        ],
        edges=[WorkflowEdge(id="tce1", source_node_id="tc-trig", target_node_id="tc-http")],
    )


def _parent_calls_task_child() -> Workflow:
    return Workflow(
        id="parent-task-wf", name="parent-task",
        nodes=[
            WorkflowNode(id="p-trig", name="pt", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="wf-node", name="wn", node_type=NodeType.WORKFLOW,
                         config={"workflow_id": "task-child-wf", "input_mapping": {}, "on_failure": "fail"}),
        ],
        edges=[WorkflowEdge(id="pe1", source_node_id="p-trig", target_node_id="wf-node")],
    )


@pytest.mark.asyncio
async def test_workflow_node_exposes_child_task_worker_output(container):
    """Regression: a child TASK (worker) node's output must surface under the
    WORKFLOW node so ``$<wf-node>.output.<task>.response_body...`` resolves.

    A worker node's variable entry stores ``output`` as a thin
    ``{"uri": <ref>}`` file pointer and spreads its real fields
    (response_body, status_code, …) at the entry's TOP level
    (CompleteTaskUseCase). Lifting ``output`` dropped them, leaving only the
    uri under the parent WORKFLOW node — so the natural
    ``$<wf-node>.output.<task>.response_body`` failed to resolve.
    """
    repo = container["_infra"].repos.workflow
    repo.save(_task_child_workflow())
    repo.save(_parent_calls_task_child())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-task-wf", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id
    await _drain(container)

    run_repo = container["_infra"].repos.run
    task_repo = container["_infra"].repos.task
    child_run_id = run_repo.get_by_id(parent_run_id).context.child_runs.get("wf-node")
    assert child_run_id, "child run was not spawned"

    # Simulate the worker completing the child TASK with a JSON response body —
    # this builds the {"output": {"uri": ...}, "metadata": ..., <fields>} envelope.
    tc_tasks = [
        t for t in task_repo.list_by_run(child_run_id)
        if t.node_id == "tc-http" and t.status != TaskStatus.COMPLETED
    ]
    assert tc_tasks, "child TASK was not dispatched"
    await container["complete_task"].execute(CompleteTaskCommand(
        run_id=child_run_id, task_id=tc_tasks[-1].id, status="COMPLETED",
        output_reference="file-ref-1",
        output_metadata={
            "status_code": 201,
            "is_success": True,
            "response_body": {"data": {"id": "tmpl-xyz"}},
        },
    ))
    await _drain(container)

    parent_view = container["run_context_view_store"].get(parent_run_id)
    variables = dict(parent_view.variables)
    resolver = container["_orchestration_worker"]._engine._variable_resolver
    tok = set_current_run_id(parent_run_id)
    try:
        # The worker's response_body is reachable with a single .output hop.
        tid = await resolver.resolve(
            "{{$wf-node.output.tc-http.response_body.data.id}}", variables,
        )
        assert tid == "tmpl-xyz", f"expected tmpl-xyz, got {tid!r}"
        # And scalar worker fields too.
        assert await resolver.resolve(
            "{{$wf-node.output.tc-http.status_code}}", variables,
        ) == 201
    finally:
        reset_current_run_id(tok)


def _condition_human_action_child() -> Workflow:
    """CONDITION(entry) → [true] → HUMAN_ACTION → [confirm] → CONDITION (leaf)
                                                → [reject]  → CONDITION (leaf).

    Mirrors the user-reported sub-workflow shape: all CONDITION / HUMAN_ACTION
    nodes. CONDITION nodes carry no output payload (only routing metadata);
    HUMAN_ACTION carries the submitted ``_action``.
    """
    return Workflow(
        id="sub-wf", name="sub",
        nodes=[
            WorkflowNode(id="cond1", name="Condition", node_type=NodeType.CONDITION,
                         config={"expression": '{{$input.value}} == 23'}),
            WorkflowNode(id="ha", name="Human Action", node_type=NodeType.HUMAN_ACTION,
                         config={"instruction": "approve?", "input_schema": []}),
            WorkflowNode(id="cond2", name="Condition", node_type=NodeType.CONDITION, config={}),
            WorkflowNode(id="cond3", name="Condition", node_type=NodeType.CONDITION, config={}),
        ],
        edges=[
            WorkflowEdge(id="se1", source_node_id="cond1", target_node_id="ha", source_port_id="true_branch"),
            WorkflowEdge(id="se2", source_node_id="ha", target_node_id="cond2", source_port_id="confirm"),
            WorkflowEdge(id="se3", source_node_id="ha", target_node_id="cond3", source_port_id="reject"),
        ],
    )


def _parent_calls_sub() -> Workflow:
    return Workflow(
        id="parent-wf", name="parent",
        nodes=[
            WorkflowNode(id="p-trig", name="pt", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="wf-node", name="wn", node_type=NodeType.WORKFLOW,
                         config={"workflow_id": "sub-wf", "input_mapping": {}, "on_failure": "fail"}),
        ],
        edges=[WorkflowEdge(id="pe1", source_node_id="p-trig", target_node_id="wf-node")],
    )


@pytest.mark.asyncio
async def test_workflow_node_exposes_condition_and_human_action_outputs(container):
    """Sub-workflow of CONDITION + HUMAN_ACTION nodes: every executed node is
    surfaced under the WORKFLOW node's ``.output`` — not just the child input.
    """
    repo = container["_infra"].repos.workflow
    repo.save(_condition_human_action_child())
    repo.save(_parent_calls_sub())

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="parent-wf", input_data={"value": 23})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_run_id = result.id
    await _drain(container)

    run_repo = container["_infra"].repos.run
    task_repo = container["_infra"].repos.task
    child_run_id = run_repo.get_by_id(parent_run_id).context.child_runs.get("wf-node")
    assert child_run_id, "child run was not spawned"

    # The sub-workflow pauses at the HUMAN_ACTION — confirm it.
    ha_tasks = [
        t for t in task_repo.list_by_run(child_run_id)
        if t.node_id == "ha" and t.status == TaskStatus.PENDING
    ]
    assert len(ha_tasks) == 1, "expected one pending HUMAN_ACTION task"
    await container["resume_pending_task"].execute(ResumePendingTaskCommand(
        run_id=child_run_id, task_id=ha_tasks[0].id,
        updated_input={"_action": "confirm"},
    ))
    await _drain(container)

    parent_view = container["run_context_view_store"].get(parent_run_id)
    variables = dict(parent_view.variables)

    resolver = container["_orchestration_worker"]._engine._variable_resolver
    tok = set_current_run_id(parent_run_id)
    try:
        full = await resolver.resolve("{{$wf-node.output}}", variables)
        # Every executed node is present — not just ``input``. The untaken
        # reject branch (cond3) never ran, so it is absent.
        assert set(full.keys()) >= {"input", "cond1", "ha", "cond2"}, (
            f"missing sub-workflow node outputs; got keys: {sorted(full.keys())}"
        )

        # Child input is reachable.
        assert await resolver.resolve("{{$wf-node.output.input.value}}", variables) == 23
        # HUMAN_ACTION submission is reachable with a single .output hop.
        assert await resolver.resolve("{{$wf-node.output.ha._action}}", variables) == "confirm"
        # CONDITION nodes now carry an evaluation trace in their output
        # (result / branch / expression / resolved), so the lift exposes
        # that — not the metadata-only entry of the empty-output era.
        assert await resolver.resolve(
            "{{$wf-node.output.cond1.branch}}", variables,
        ) == "then"
        assert await resolver.resolve(
            "{{$wf-node.output.cond1.resolved}}", variables,
        ) == "23 == 23"
    finally:
        reset_current_run_id(tok)
