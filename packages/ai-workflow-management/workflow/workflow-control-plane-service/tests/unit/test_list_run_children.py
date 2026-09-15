"""Tests for ListRunChildrenUseCase."""

import pytest

from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects import RunStatus
from app.layer2_application.features.list_run_children.use_cases.list_run_children_use_case import (
    ListRunChildrenCommand,
    ListRunChildrenUseCase,
)
from tests.conftest import DummyLogger


@pytest.fixture
def usecase(run_repo):
    return ListRunChildrenUseCase(logger=DummyLogger(), run_repo=run_repo)


class TestListRunChildren:
    async def test_run_not_found(self, usecase):
        result = await usecase.execute(ListRunChildrenCommand(run_id="missing"))
        assert result.error == "Run not found"
        assert result.error_code == "RUN_NOT_FOUND"

    async def test_run_with_no_children(self, usecase, run_repo):
        run_repo.save(WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING))
        result = await usecase.execute(ListRunChildrenCommand(run_id="r1"))
        assert result.error is None
        assert result.run_id == "r1"
        assert result.children == []

    async def test_lists_children_with_status_and_workflow_id(self, usecase, run_repo):
        run_repo.save(WorkflowRun(
            id="parent", workflow_id="wf-parent", status=RunStatus.RUNNING,
            context=RunContext(child_runs={
                "subflow-node-1": "child-1",
                "subflow-node-2": "child-2",
            }),
        ))
        run_repo.save(WorkflowRun(
            id="child-1", workflow_id="wf-c1", status=RunStatus.COMPLETED,
            started_at="2025-01-01T00:00:00Z", completed_at="2025-01-01T00:01:00Z",
        ))
        run_repo.save(WorkflowRun(
            id="child-2", workflow_id="wf-c2", status=RunStatus.FAILED,
            started_at="2025-01-01T00:02:00Z", error="boom",
        ))

        result = await usecase.execute(ListRunChildrenCommand(run_id="parent"))

        assert len(result.children) == 2
        by_node = {c["parent_node_id"]: c for c in result.children}
        c1 = by_node["subflow-node-1"]
        assert c1["child_run_id"] == "child-1"
        assert c1["workflow_id"] == "wf-c1"
        assert c1["status"] == "COMPLETED"
        assert c1["completed_at"] == "2025-01-01T00:01:00Z"
        c2 = by_node["subflow-node-2"]
        assert c2["status"] == "FAILED"
        assert c2["error"] == "boom"
        assert c2["completed_at"] is None

    async def test_missing_child_record_yields_pending_placeholder(self, usecase, run_repo):
        run_repo.save(WorkflowRun(
            id="parent", workflow_id="wf-parent", status=RunStatus.RUNNING,
            context=RunContext(child_runs={"subflow": "ghost-id"}),
        ))
        result = await usecase.execute(ListRunChildrenCommand(run_id="parent"))
        assert len(result.children) == 1
        c = result.children[0]
        assert c["child_run_id"] == "ghost-id"
        assert c["status"] == "PENDING"
        assert c["workflow_id"] == ""
