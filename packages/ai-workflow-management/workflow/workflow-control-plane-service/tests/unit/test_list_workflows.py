"""Tests for the list_workflows feature with contains filtering."""

import pytest

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer2_application.features.list_workflows.use_cases.list_workflows_use_case import (
    ListWorkflowsUseCase,
    ListWorkflowsCommand,
)


@pytest.fixture
def list_workflows_uc(logger, workflow_repo):
    return ListWorkflowsUseCase(logger=logger, workflow_repo=workflow_repo)


def _make_workflow(name="Test Workflow", description="", status=WorkflowStatus.DRAFT, **kwargs):
    return Workflow(id=kwargs.get("id", ""), name=name, description=description, status=status)


class TestListWorkflowsContains:

    async def test_contains_name(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", name="Approval Flow"))
        workflow_repo.save(_make_workflow(id="w2", name="Invoice Process"))
        workflow_repo.save(_make_workflow(id="w3", name="Order Approval"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(contains={"name": "approval"})
        )
        assert result.total == 2
        names = {item["name"] for item in result.items}
        assert names == {"Approval Flow", "Order Approval"}

    async def test_contains_description(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", name="WF1", description="Handles invoice processing"))
        workflow_repo.save(_make_workflow(id="w2", name="WF2", description="Order management"))
        workflow_repo.save(_make_workflow(id="w3", name="WF3", description="Invoice approval"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(contains={"description": "invoice"})
        )
        assert result.total == 2

    async def test_contains_case_insensitive(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", name="APPROVAL Flow"))
        workflow_repo.save(_make_workflow(id="w2", name="approval process"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(contains={"name": "Approval"})
        )
        assert result.total == 2

    async def test_contains_no_match(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", name="Invoice Process"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(contains={"name": "nonexistent"})
        )
        assert result.total == 0
        assert result.items == []

    async def test_contains_combined_with_status(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", name="Approval Draft", status=WorkflowStatus.DRAFT))
        workflow_repo.save(_make_workflow(id="w2", name="Approval Valid", status=WorkflowStatus.VALID))
        workflow_repo.save(_make_workflow(id="w3", name="Other Draft", status=WorkflowStatus.DRAFT))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(status="DRAFT", contains={"name": "approval"})
        )
        assert result.total == 1
        assert result.items[0]["name"] == "Approval Draft"

    async def test_contains_multiple_fields(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", name="Approval Flow", description="For invoices"))
        workflow_repo.save(_make_workflow(id="w2", name="Approval Process", description="For orders"))
        workflow_repo.save(_make_workflow(id="w3", name="Other Flow", description="For invoices"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(contains={"name": "approval", "description": "invoice"})
        )
        assert result.total == 1
        assert result.items[0]["name"] == "Approval Flow"

    async def test_contains_with_pagination(self, list_workflows_uc, workflow_repo):
        for i in range(5):
            workflow_repo.save(_make_workflow(id=f"w{i}", name=f"Approval {i}"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(contains={"name": "approval"}, skip=0, top=2)
        )
        assert result.total == 5
        assert len(result.items) == 2


class TestListWorkflowsComparisonOperators:

    async def test_eq_filter_by_routing_path(self, list_workflows_uc, workflow_repo):
        from app.layer1_domain.entities.workflow import Workflow
        workflow_repo.save(Workflow(id="w1", name="A", routing_path="/orders"))
        workflow_repo.save(Workflow(id="w2", name="B", routing_path="/invoices"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(eq={"routing_path": "/orders"})
        )
        assert result.total == 1
        assert result.items[0]["id"] == "w1"

    async def test_eq_filter_main_flow_true(self, list_workflows_uc, workflow_repo):
        from app.layer1_domain.entities.workflow import Workflow
        workflow_repo.save(Workflow(id="w1", name="A", main_flow=True))
        workflow_repo.save(Workflow(id="w2", name="B", main_flow=False))
        workflow_repo.save(Workflow(id="w3", name="C", main_flow=True))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(eq={"main_flow": "true"})
        )
        assert result.total == 2
        assert {item["id"] for item in result.items} == {"w1", "w3"}

    async def test_eq_filter_unknown_attr_ignored(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1"))
        workflow_repo.save(_make_workflow(id="w2"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(eq={"foo": "bar"})
        )
        assert result.total == 2

    async def test_ne_filter_status(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", status=WorkflowStatus.DRAFT))
        workflow_repo.save(_make_workflow(id="w2", status=WorkflowStatus.VALID))
        workflow_repo.save(_make_workflow(id="w3", status=WorkflowStatus.DRAFT))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(ne={"status": "DRAFT"})
        )
        assert result.total == 1
        assert result.items[0]["id"] == "w2"

    async def test_gt_lt_filter_created_at(self, list_workflows_uc, workflow_repo):
        from app.layer1_domain.entities.workflow import Workflow
        workflow_repo.save(Workflow(id="w1", name="A", created_at="2026-01-01T00:00:00"))
        workflow_repo.save(Workflow(id="w2", name="B", created_at="2026-06-15T00:00:00"))
        workflow_repo.save(Workflow(id="w3", name="C", created_at="2026-12-31T00:00:00"))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(
                gt={"created_at": "2026-03-01T00:00:00"},
                lt={"created_at": "2026-09-01T00:00:00"},
            )
        )
        assert result.total == 1
        assert result.items[0]["id"] == "w2"

    async def test_legacy_status_arg_still_works(self, list_workflows_uc, workflow_repo):
        workflow_repo.save(_make_workflow(id="w1", status=WorkflowStatus.DRAFT))
        workflow_repo.save(_make_workflow(id="w2", status=WorkflowStatus.VALID))

        result = await list_workflows_uc.execute(
            ListWorkflowsCommand(status="DRAFT")
        )
        assert result.total == 1
        assert result.items[0]["id"] == "w1"
