"""Smoke check that the new lightweight status endpoints are registered.

The repo's TestClient-based integration tests are currently broken due to
a starlette/httpx version skew, so this test inspects the FastAPI router
directly and exercises the use cases through the auto-discovered DI
container — covering wiring without going through HTTP.
"""

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus, TaskStatus
from app.layer2_application.features import FEATURE_REGISTRY, load_features
from app.layer2_application.features.list_run_children.use_cases.list_run_children_use_case import (
    ListRunChildrenCommand,
)
from app.layer2_application.features.list_run_node_statuses.use_cases.list_run_node_statuses_use_case import (
    ListRunNodeStatusesCommand,
)
from app.layer3_adapters.controllers.restful.v1.run_controller import router as run_router
from tests.conftest import DummyLogger


def test_new_status_routes_are_registered():
    paths = {(route.path, tuple(sorted(route.methods))) for route in run_router.routes}
    assert ("/{run_id}/nodes/status", ("GET",)) in paths
    assert ("/{run_id}/children", ("GET",)) in paths


def test_existing_routes_still_present():
    paths = {(route.path, tuple(sorted(route.methods))) for route in run_router.routes}
    # Resume endpoint must remain unchanged
    assert ("/{run_id}/resume", ("POST",)) in paths
    # Heavy execution endpoint must coexist with the new lightweight endpoint
    assert ("/{run_id}/nodes/{node_id}/execution", ("GET",)) in paths


def test_features_auto_register_in_registry():
    load_features()
    assert "list_run_node_statuses" in FEATURE_REGISTRY
    assert "list_run_children" in FEATURE_REGISTRY


class TestEndToEndThroughUseCases:
    """Wire-style test: build minimal repos, run both use cases together."""

    @pytest.fixture
    def parent_workflow(self):
        return Workflow(
            id="wf-parent",
            name="Parent",
            nodes=[
                WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(id="task-a", name="A", node_type=NodeType.TASK, worker_type="w1"),
                WorkflowNode(id="subflow", name="Sub", node_type=NodeType.WORKFLOW),
            ],
            edges=[
                WorkflowEdge(source_node_id="trigger", target_node_id="task-a"),
                WorkflowEdge(source_node_id="task-a", target_node_id="subflow"),
            ],
        )

    @pytest.fixture
    def child_workflow(self):
        return Workflow(
            id="wf-child",
            name="Child",
            nodes=[WorkflowNode(id="c1", name="ChildNode", node_type=NodeType.TASK, worker_type="w2")],
            edges=[],
        )

    async def test_drill_into_child_run_returns_child_node_statuses(
        self, workflow_repo, run_repo, task_repo, parent_workflow, child_workflow,
    ):
        from app.layer2_application.features.list_run_node_statuses.use_cases.list_run_node_statuses_use_case import (
            ListRunNodeStatusesUseCase,
        )
        from app.layer2_application.features.list_run_children.use_cases.list_run_children_use_case import (
            ListRunChildrenUseCase,
        )

        workflow_repo.save(parent_workflow)
        workflow_repo.save(child_workflow)
        run_repo.save(WorkflowRun(
            id="parent-run", workflow_id="wf-parent", status=RunStatus.RUNNING,
            context=RunContext(child_runs={"subflow": "child-run"}),
        ))
        run_repo.save(WorkflowRun(
            id="child-run", workflow_id="wf-child", status=RunStatus.RUNNING,
            parent_run_id="parent-run", parent_node_id="subflow", depth_level=1,
        ))
        task_repo.save(Task(id="t1", run_id="parent-run", node_id="task-a", status=TaskStatus.COMPLETED))
        task_repo.save(Task(id="t2", run_id="child-run", node_id="c1", status=TaskStatus.RUNNING))

        statuses = ListRunNodeStatusesUseCase(
            logger=DummyLogger(), run_repo=run_repo, task_repo=task_repo, workflow_repo=workflow_repo,
        )
        children = ListRunChildrenUseCase(logger=DummyLogger(), run_repo=run_repo)

        # Step 1: frontend fetches parent's per-node status.
        parent = await statuses.execute(ListRunNodeStatusesCommand(run_id="parent-run"))
        sub = next(n for n in parent.nodes if n["node_id"] == "subflow")
        assert sub["child_run_id"] == "child-run"

        # Step 2: frontend lists children to confirm.
        kids = await children.execute(ListRunChildrenCommand(run_id="parent-run"))
        assert len(kids.children) == 1
        assert kids.children[0]["child_run_id"] == "child-run"
        assert kids.children[0]["status"] == "RUNNING"

        # Step 3: frontend recursively re-uses the same endpoint with the child run id.
        child = await statuses.execute(ListRunNodeStatusesCommand(run_id=sub["child_run_id"]))
        assert child.run_id == "child-run"
        assert child.workflow_id == "wf-child"
        assert len(child.nodes) == 1
        assert child.nodes[0]["node_id"] == "c1"
        assert child.nodes[0]["status"] == "RUNNING"
