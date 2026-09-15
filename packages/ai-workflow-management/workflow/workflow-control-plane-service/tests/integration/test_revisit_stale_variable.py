"""Revisited nodes must resolve the CURRENT visit's upstream output.

Regression (SA-846 cycles): the view→aggregate hydration uses
``setdefault`` (deliberate — aggregate-owned keys like ``input`` stay
authoritative), so a node's aggregate ``variables`` entry written on its
FIRST visit was never overwritten on a revisit. A loop CONDITION around a
sub-workflow therefore always resolved iteration 1's child output —
``again == again`` evaluated true forever, spawning a fresh pending child
on every user resume and never completing the parent.

Fix: every revisit visit-counter bump also discards the re-run node's
stale aggregate variable entry (+ name alias), so the next hydration
refills it from the authoritative projector view.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
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
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


def _workflows() -> tuple[Workflow, Workflow]:
    """Parent loop around a sub-workflow that pends on INPUT each visit.

    parent: TRIGGER → w(WORKFLOW: child) → COND({{$w.output.c-input.go}} == again)
                         ▲__________true___________|
    child:  INPUT(c-input: {go})
    """
    child = Workflow(
        id="loop-child", name="loop-child",
        nodes=[
            WorkflowNode(
                id="c-input", name="ci", node_type=NodeType.INPUT,
                config={
                    "instruction": "go?",
                    "input_schema": [{"key": "go", "type": "string"}],
                },
            ),
        ],
        edges=[],
    )
    parent = Workflow(
        id="loop-parent", name="loop-parent",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="w", name="w", node_type=NodeType.WORKFLOW,
                         config={"workflow_id": "loop-child"}),
            WorkflowNode(id="cond", name="cond", node_type=NodeType.CONDITION,
                         config={"expression": "{{$w.output.c-input.go}} == again"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trig", target_node_id="w"),
            WorkflowEdge(id="e2", source_node_id="w", target_node_id="cond"),
            WorkflowEdge(id="e3", source_node_id="cond", target_node_id="w",
                         source_port_id="true_branch"),
        ],
    )
    return parent, child


def _child_runs(container, parent_id):
    rr = container["_infra"].repos.run
    store = getattr(rr, "_runs", None) or getattr(rr, "_store", None) or {}
    return sorted(
        (r for r in store.values() if getattr(r, "parent_run_id", None) == parent_id),
        key=lambda r: r.started_at or "",
    )


async def _resume_pending_child(container, parent_id, value):
    rr = container["_infra"].repos.run
    tr = container["_infra"].repos.task
    pending_children = [
        r for r in _child_runs(container, parent_id)
        if rr.get_by_id(r.id).status == RunStatus.PENDING
    ]
    assert pending_children, "expected a pending child run"
    child = pending_children[-1]
    pending = [
        t for t in tr.list_by_run(child.id)
        if t.node_id == "c-input" and t.status == TaskStatus.PENDING
    ]
    assert pending, f"child {child.id} should pend on c-input"
    await container["resume_pending_task"].execute(ResumePendingTaskCommand(
        run_id=child.id, task_id=pending[0].id, updated_input={"go": value},
    ))
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


@pytest.mark.asyncio
async def test_loop_condition_sees_fresh_child_output_on_revisit(container):
    parent, child = _workflows()
    repo = container["_infra"].repos.workflow
    repo.save(child)
    repo.save(parent)

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="loop-parent", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    parent_id = result.id
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()

    # Iteration 1: child pends; "again" loops back into w (revisit).
    await _resume_pending_child(container, parent_id, "again")
    kids = _child_runs(container, parent_id)
    assert len(kids) == 2, (
        f"loop-back should spawn exactly the second child, got "
        f"{[(k.id, k.status.value) for k in kids]}"
    )

    # Iteration 2: "stop" must exit the loop — the revisited CONDITION
    # must resolve child-2's output, not iteration 1's stale "again".
    await _resume_pending_child(container, parent_id, "stop")

    rr = container["_infra"].repos.run
    parent_run = rr.get_by_id(parent_id)
    kids = _child_runs(container, parent_id)
    assert len(kids) == 2, (
        "stale revisit resolution: the loop condition saw iteration 1's "
        f"output again and spawned a phantom third child: "
        f"{[(k.id, rr.get_by_id(k.id).status.value) for k in kids]}"
    )
    assert parent_run.status == RunStatus.COMPLETED, (
        f"parent should complete after go=stop, got {parent_run.status}"
    )

    # The revisited CONDITION's recorded trace proves the fresh resolution.
    view = container["run_context_view_store"].get(parent_id)
    cond_ref = (view.variables or {}).get("cond")
    materialized = await engine._data_store_service.materialize(cond_ref)
    assert materialized["output"]["resolved"] == "stop == again", (
        f"revisited CONDITION resolved stale input: {materialized['output']}"
    )
    assert materialized["output"]["result"] is False

    # Scan guard: no pending-node task anywhere was CANCELLED by the revisit.
    tr = container["_infra"].repos.task
    for k in kids:
        cancelled = [
            t for t in tr.list_by_run(k.id) if t.status == TaskStatus.CANCELLED
        ]
        assert not cancelled, f"revisit cancelled child tasks: {cancelled}"


def _install_copy_on_reload(run_repo) -> None:
    """Make the run repo return a fresh copy on every read — simulating the
    HANA repo's serialize-on-save / deserialize-on-read semantics that the
    in-memory mock skips (it returns the same object reference).

    The SA-846 TASK-path bug only surfaces under copy-on-read: ``complete_task``
    pops the stale per-node variable on the in-memory ``run`` it loaded, but
    the persisted run is reloaded fresh inside ``save_run_with_cas_retry`` — so
    without the fix the pop is lost and the downstream CONDITION re-reads
    iteration 1's output. With the mock's same-object repo the pop is never
    lost, so the bug is invisible. Production code is written to be correct
    under copy-on-read (it must be, for HANA), so this wrapper is faithful.
    """
    import copy as _copy

    orig_aget = run_repo.aget_by_id
    orig_get = run_repo.get_by_id

    async def _aget(rid):
        r = await orig_aget(rid)
        return _copy.deepcopy(r) if r is not None else None

    def _get(rid):
        r = orig_get(rid)
        return _copy.deepcopy(r) if r is not None else None

    run_repo.aget_by_id = _aget
    run_repo.get_by_id = _get


@pytest.mark.asyncio
async def test_loop_condition_sees_fresh_task_output_on_revisit(container):
    """SA-846 (TASK path): a remote-worker TASK node re-run in a CONDITION
    cycle. The revisited CONDITION must resolve the CURRENT visit's worker
    output, not iteration 1's.

    Regression for the user-reported loop that never exits because the
    condition kept reading the FIRST HTTP response. The existing tests above
    only re-run in-process nodes (WORKFLOW/INPUT/COMPUTE), whose output is
    produced inside their own ``_exec_uniform``; a TASK node's output arrives
    later via ``complete_task``, whose SA-846 aggregate-pop ran on a transient
    run the use case discarded on reload — so the stale entry survived. This
    test installs copy-on-reload (see ``_install_copy_on_reload``) so the
    prod-only mechanism is exercised; without the fix it loops forever.

    Topology mirrors the reported workflow (minus the incidental DATA_EDIT):
        TRIGGER -> vd(TASK: http_request) -> COND(vd.validate_status != False)
                     ^________________ false_branch _______|
    COND true (validate_status != "False") takes no wired edge -> run completes.
    """
    _install_copy_on_reload(container["_infra"].repos.run)
    parent = Workflow(
        id="task-loop", name="task-loop",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="vd", name="vd", node_type=NodeType.TASK,
                         worker_type="http_request", config={}),
            WorkflowNode(id="cond", name="cond", node_type=NodeType.CONDITION,
                         config={"data": [{
                             "key": "{{$vd.response_body.agent_data.validate_status}}",
                             "operations": "!=", "value": "False"}]}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trig", target_node_id="vd"),
            WorkflowEdge(id="e2", source_node_id="vd", target_node_id="cond",
                         source_port_id="success"),
            WorkflowEdge(id="e3", source_node_id="cond", target_node_id="vd",
                         source_port_id="false_branch"),
        ],
    )
    container["_infra"].repos.workflow.save(parent)

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="task-loop", input_data={})
    )
    assert not result.error, f"start_run failed: {result.error}"
    run_id = result.id
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    runner = QueueRunner(engine, queue)
    await runner.run_until_idle()
    tr = container["_infra"].repos.task

    async def complete_vd(validate_status: str) -> None:
        running = [t for t in tr.list_by_run(run_id)
                   if t.node_id == "vd" and t.status != TaskStatus.COMPLETED]
        assert len(running) == 1, (
            "expected exactly one running vd task, got "
            f"{[(t.id, t.status.value) for t in tr.list_by_run(run_id) if t.node_id == 'vd']}"
        )
        await container["complete_task"].execute(CompleteTaskCommand(
            run_id=run_id, task_id=running[0].id, status="COMPLETED",
            output_metadata={
                "status_code": 200, "is_success": True,
                "response_body": {"agent_data": {"validate_status": validate_status}},
            },
        ))
        await runner.run_until_idle()

    # Iteration 1: validation fails -> false_branch loops back into vd.
    await complete_vd("False")
    # Iteration 2: validation passes -> the revisited CONDITION must see
    # "True", take no false edge, and the run completes.
    await complete_vd("True")

    rr = container["_infra"].repos.run
    run = rr.get_by_id(run_id)
    vd_tasks = [t for t in tr.list_by_run(run_id) if t.node_id == "vd"]
    assert len(vd_tasks) == 2, (
        "stale revisit: the loop condition re-read iteration 1's validate_status "
        f"and dispatched vd a third time: {[(t.id, t.status.value) for t in vd_tasks]}"
    )
    assert run.status == RunStatus.COMPLETED, (
        f"loop should exit after validate_status=True, got {run.status}"
    )

    # The revisited CONDITION's trace proves it resolved iteration 2's output.
    view = container["run_context_view_store"].get(run_id)
    cond_ref = (view.variables or {}).get("cond")
    materialized = await engine._data_store_service.materialize(cond_ref)
    assert materialized["output"]["resolved"] == "True != False", (
        f"revisited CONDITION resolved stale input: {materialized['output']}"
    )
    assert materialized["output"]["result"] is True


def _iteration_workflows() -> tuple[Workflow, Workflow]:
    """Parent loop feeding fresh per-iteration data into the sub-workflow.

    parent: TRIGGER → p-in(INPUT val,go) → w(child, val ⇐ $p-in) → COND(go==again) ⤴ p-in
    child:  TRIGGER → c-echo(COMPUTE {{$input.val}} + 0)
    """
    child = Workflow(
        id="iter-child", name="iter-child",
        nodes=[
            WorkflowNode(id="c-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="c-echo", name="ce", node_type=NodeType.COMPUTE,
                         config={"expression": "{{$input.val}} + 0"}),
        ],
        edges=[WorkflowEdge(id="ce1", source_node_id="c-trig", target_node_id="c-echo")],
    )
    parent = Workflow(
        id="iter-parent", name="iter-parent",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="p-in", name="pi", node_type=NodeType.INPUT,
                         config={"instruction": "v?", "input_schema": [
                             {"key": "val", "type": "number"},
                             {"key": "go", "type": "string"}]}),
            WorkflowNode(id="w", name="w", node_type=NodeType.WORKFLOW,
                         config={"workflow_id": "iter-child",
                                 "input_mapping": {"val": "{{$p-in.output.val}}"}}),
            WorkflowNode(id="cond", name="cd", node_type=NodeType.CONDITION,
                         config={"expression": "{{$p-in.output.go}} == again"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trig", target_node_id="p-in"),
            WorkflowEdge(id="e2", source_node_id="p-in", target_node_id="w"),
            WorkflowEdge(id="e3", source_node_id="w", target_node_id="cond"),
            WorkflowEdge(id="e4", source_node_id="cond", target_node_id="p-in",
                         source_port_id="true_branch"),
        ],
    )
    return parent, child


@pytest.mark.asyncio
async def test_each_iterations_child_receives_that_iterations_data(container):
    """Iteration N's sub-workflow must receive iteration N's data — not
    iteration 1's (the user-reported symptom of the stale hydration bug)."""
    parent, child = _iteration_workflows()
    repo = container["_infra"].repos.workflow
    repo.save(child)
    repo.save(parent)

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="iter-parent", input_data={})
    )
    assert not result.error, result.error
    parent_id = result.id
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    runner = QueueRunner(engine, queue)
    await runner.run_until_idle()
    tr = container["_infra"].repos.task

    async def submit(val, go):
        pend = [t for t in tr.list_by_run(parent_id)
                if t.node_id == "p-in" and t.status == TaskStatus.PENDING]
        assert len(pend) == 1, "expected exactly one pending p-in task"
        await container["resume_pending_task"].execute(ResumePendingTaskCommand(
            run_id=parent_id, task_id=pend[0].id,
            updated_input={"val": val, "go": go},
        ))
        await runner.run_until_idle()

    await submit(111, "again")
    await submit(222, "again")
    await submit(333, "stop")

    rr = container["_infra"].repos.run
    assert rr.get_by_id(parent_id).status == RunStatus.COMPLETED
    kids = _child_runs(container, parent_id)
    assert len(kids) == 3
    expected = [111, 222, 333]
    for i, k in enumerate(kids):
        view = container["run_context_view_store"].get(k.id)
        echo = await engine._data_store_service.materialize(view.variables["c-echo"])
        assert echo["output"]["result"] == expected[i], (
            f"iteration {i + 1}'s child resolved stale data: {echo['output']}"
        )


@pytest.mark.asyncio
async def test_intra_child_loop_nodes_resolve_each_iterations_data(container):
    """A loop INSIDE the sub-workflow: revisited nodes resolve the current
    iteration's INPUT submission, not the first one."""
    child = Workflow(
        id="inloop-child", name="inloop-child",
        nodes=[
            WorkflowNode(id="c-trig", name="ct", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="c-in", name="ci", node_type=NodeType.INPUT,
                         config={"instruction": "v?", "input_schema": [
                             {"key": "val", "type": "number"},
                             {"key": "go", "type": "string"}]}),
            WorkflowNode(id="c-echo", name="ce", node_type=NodeType.COMPUTE,
                         config={"expression": "{{$c-in.output.val}} * 10"}),
            WorkflowNode(id="c-cond", name="cc", node_type=NodeType.CONDITION,
                         config={"expression": "{{$c-in.output.go}} == again"}),
        ],
        edges=[
            WorkflowEdge(id="f1", source_node_id="c-trig", target_node_id="c-in"),
            WorkflowEdge(id="f2", source_node_id="c-in", target_node_id="c-echo"),
            WorkflowEdge(id="f3", source_node_id="c-echo", target_node_id="c-cond"),
            WorkflowEdge(id="f4", source_node_id="c-cond", target_node_id="c-in",
                         source_port_id="true_branch"),
        ],
    )
    parent = Workflow(
        id="inloop-parent", name="inloop-parent",
        nodes=[
            WorkflowNode(id="trig", name="t", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(id="w", name="w", node_type=NodeType.WORKFLOW,
                         config={"workflow_id": "inloop-child"}),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="trig", target_node_id="w")],
    )
    repo = container["_infra"].repos.workflow
    repo.save(child)
    repo.save(parent)

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="inloop-parent", input_data={})
    )
    assert not result.error, result.error
    parent_id = result.id
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    runner = QueueRunner(engine, queue)
    await runner.run_until_idle()
    child_id = _child_runs(container, parent_id)[0].id
    tr = container["_infra"].repos.task

    async def submit_and_echo(val, go):
        pend = [t for t in tr.list_by_run(child_id)
                if t.node_id == "c-in" and t.status == TaskStatus.PENDING]
        assert len(pend) == 1, "expected exactly one pending c-in task"
        await container["resume_pending_task"].execute(ResumePendingTaskCommand(
            run_id=child_id, task_id=pend[0].id,
            updated_input={"val": val, "go": go},
        ))
        await runner.run_until_idle()
        view = container["run_context_view_store"].get(child_id)
        echo = await engine._data_store_service.materialize(view.variables["c-echo"])
        return echo["output"]["result"]

    assert await submit_and_echo(7, "again") == 70
    assert await submit_and_echo(8, "again") == 80
    assert await submit_and_echo(9, "stop") == 90

    rr = container["_infra"].repos.run
    assert rr.get_by_id(child_id).status == RunStatus.COMPLETED
    assert rr.get_by_id(parent_id).status == RunStatus.COMPLETED
