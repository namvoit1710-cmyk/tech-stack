"""tools/normalize_node_config against real repositories (Module 7).

The transform table itself is unit-tested per row
(tests/unit/tools/test_normalize_node_config.py); these tests cover what
units can't: the runner functions against real repo instances (listing,
saving, snapshot iteration via run paging), end-to-end idempotency, the
dry-run write guarantee — and the money test, migrate-then-RUN: a
legacy-shaped workflow must execute correctly through the clean-break
handlers after one apply pass.

Seeding trap (by design): legacy fixtures are constructed as entities
with explicit legacy ``config=``/``input_binding=`` keys and written
via the repo — never via POST, which would re-split parameters and
destroy the legacy shape.
"""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.entities.workflow_snapshot import WorkflowSnapshot
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)
from bootstrap import build_app_container
from tests.helpers.queue_runner import QueueRunner
from tools.normalize_node_config import Report, run_snapshots, run_workflows


def _legacy_everything_workflow(wf_id="wf-legacy-all") -> Workflow:
    return Workflow(id=wf_id, name="legacy-all", nodes=[
        WorkflowNode(id="m1", workflow_id="m1", node_type=NodeType.MERGE,
                     config={"wait_strategy": "wait_n", "n_required": 2}),
        WorkflowNode(id="l1", workflow_id="l1", node_type=NodeType.LOOP,
                     config={"loop_type": "collection",
                             "items_expression": "{{$input.items}}"}),
        WorkflowNode(id="w1", workflow_id="w1", node_type=NodeType.WORKFLOW,
                     config={"workflow_id": "child",
                             "input_binding": [
                                 {"param_name": "a", "param_value": "{{$x.y}}"}]}),
        WorkflowNode(id="t1", workflow_id="t1", node_type=NodeType.TRIGGER,
                     config={"trigger_type": "webhook"},
                     input_binding={"webhook_path": "/h/x"}),
        WorkflowNode(id="c1", workflow_id="c1", node_type=NodeType.CONDITION,
                     input_binding={"data": [
                         {"key": "{{$a.b}}", "operations": "==", "value": 1}]}),
        WorkflowNode(id="p1", workflow_id="p1", node_type=NodeType.PARALLEL,
                     config={"max_concurrency": 4}),
        WorkflowNode(id="i1", workflow_id="i1", node_type=NodeType.INPUT,
                     config={"input_schema": '[{"key": "x"}]'}),
        WorkflowNode(id="task1", workflow_id="task1", node_type=NodeType.TASK,
                     worker_type="w",
                     config={"_node_internals": {"locked_defaults": {"m": 1}}}),
    ])


def _assert_canonical(workflow: Workflow) -> None:
    m1 = workflow.get_node("m1")
    assert m1.config == {"merge_strategy": "n_of_m", "wait_count": 2}
    assert workflow.get_node("l1").config["collection"] == "{{$input.items}}"
    w1 = workflow.get_node("w1")
    assert w1.config["input_mapping"] == {"a": "{{$x.y}}"}
    assert "input_binding" not in w1.config
    t1 = workflow.get_node("t1")
    assert t1.config["webhook_path"] == "/h/x"
    assert t1.input_binding == {}
    c1 = workflow.get_node("c1")
    assert c1.config["data"] and c1.input_binding == {}
    assert "max_concurrency" not in workflow.get_node("p1").config
    assert workflow.get_node("i1").config["input_schema"] == [{"key": "x"}]
    task1 = workflow.get_node("task1")
    assert task1.definition_snapshot == {"locked_defaults": {"m": 1}}
    assert "_node_internals" not in task1.config


# ── F1: apply + idempotency + dry-run write guarantee, real repos ──


def test_apply_normalizes_repos_idempotently():
    container = build_app_container()
    repos = container["_infra"].repos
    repos.workflow.save(_legacy_everything_workflow())

    # Legacy run snapshot, seeded raw (the conftest auto-capture shim only
    # fires for runs WITHOUT a snapshot id, so this stays legacy-shaped).
    legacy_definition = _legacy_everything_workflow("wf-legacy-snap").to_snapshot_dict()
    snapshot = WorkflowSnapshot(id="snap-legacy", workflow_id="wf-legacy-snap",
                                definition=legacy_definition)
    repos.workflow_snapshot.save(snapshot)
    run = WorkflowRun(id="r-legacy", workflow_id="wf-legacy-snap",
                      workflow_snapshot_id="snap-legacy",
                      status=RunStatus.RUNNING, context=RunContext())
    repos.run.save(run)

    # dry-run: report only, repos untouched
    dry = Report()
    run_workflows(repos.workflow, dry, apply=False)
    run_snapshots(repos.run, repos.workflow_snapshot, dry, apply=False)
    assert dry.workflows_changed == 1 and dry.snapshots_changed == 1
    still_legacy = repos.workflow.get_by_id("wf-legacy-all").get_node("m1")
    assert "wait_strategy" in still_legacy.config, "dry-run must not write"

    # apply
    report = Report()
    run_workflows(repos.workflow, report, apply=True)
    run_snapshots(repos.run, repos.workflow_snapshot, report, apply=True)
    assert report.workflows_changed == 1
    assert report.snapshots_changed == 1
    assert report.transforms["merge_wait_strategy_renamed"] >= 2  # wf + snapshot

    _assert_canonical(repos.workflow.get_by_id("wf-legacy-all"))
    migrated_snapshot = repos.workflow_snapshot.get_by_id("snap-legacy")
    _assert_canonical(Workflow.from_snapshot_dict(migrated_snapshot.definition))

    # idempotency against the same real repos
    again = Report()
    run_workflows(repos.workflow, again, apply=True)
    run_snapshots(repos.run, repos.workflow_snapshot, again, apply=True)
    assert again.workflows_changed == 0
    assert again.snapshots_changed == 0
    assert again.nodes_changed == 0


# ── F2: migrate-then-run — legacy data × clean-break handlers ──


@pytest.mark.asyncio
async def test_migrated_legacy_merge_workflow_executes():
    container = build_app_container()
    repos = container["_infra"].repos

    def compute(node_id):
        return WorkflowNode(id=node_id, workflow_id=node_id,
                            node_type=NodeType.COMPUTE,
                            config={"expression": "1"})

    legacy = Workflow(id="wf-legacy-run", name="legacy-run", nodes=[
        WorkflowNode(id="t1", workflow_id="t1", node_type=NodeType.TRIGGER,
                     config={"trigger_type": "manual"}),
        compute("b1"), compute("b2"), compute("b3"),
        # Legacy vocabulary + legacy keys — unreadable by the Phase 3
        # handlers until migrated.
        WorkflowNode(id="merge", workflow_id="merge", node_type=NodeType.MERGE,
                     config={"wait_strategy": "wait_n", "n_required": 2}),
        compute("after"),
    ], edges=[
        WorkflowEdge(source_node_id="t1", target_node_id="b1"),
        WorkflowEdge(source_node_id="t1", target_node_id="b2"),
        WorkflowEdge(source_node_id="t1", target_node_id="b3"),
        WorkflowEdge(source_node_id="b1", target_node_id="merge"),
        WorkflowEdge(source_node_id="b2", target_node_id="merge"),
        WorkflowEdge(source_node_id="b3", target_node_id="merge"),
        WorkflowEdge(source_node_id="merge", target_node_id="after"),
    ])
    repos.workflow.save(legacy)

    report = Report()
    run_workflows(repos.workflow, report, apply=True)
    assert report.workflows_changed == 1

    result = await container["start_run"].execute(
        StartRunCommand(workflow_id="wf-legacy-run", input_data={}),
    )
    assert not result.error, result.error
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()

    run = repos.run.get_by_id(result.id)
    assert "after" in run.context.completed_nodes, (
        "migrated n_of_m quorum must fire through the clean-break handler"
    )
    assert run.status == RunStatus.COMPLETED
