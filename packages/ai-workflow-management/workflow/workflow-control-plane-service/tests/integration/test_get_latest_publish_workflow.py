"""Integration tests for GET /api/v1/workflows/publish/{workflow_id}."""

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer2_application.features.get_workflow.use_cases.get_workflow_use_case import GetWorkflowUseCase
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger, assert_ok, assert_error


def _seed_workflow(
    *,
    workflow_id: str,
    version: str,
    name: str,
    status: WorkflowStatus = WorkflowStatus.DRAFT,
    store_id: str | None = None,
) -> Workflow:
    return Workflow(
        id=store_id or f"{workflow_id}-v{version}",
        workflow_id=workflow_id,
        version=version,
        name=name,
        status=status,
        nodes=[
            WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n2", name="Task", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[WorkflowEdge(source_node_id="n1", target_node_id="n2")],
    )


@pytest.fixture
def repo():
    return MemoryWorkflowRepository()


@pytest.fixture
def client(repo):
    container = {
        "get_workflow": GetWorkflowUseCase(logger=DummyLogger(), workflow_repo=repo),
    }
    return TestClient(create_fastapi_app(container))


class TestGetLatestPublishWorkflowIntegration:
    def test_returns_latest_published_version(self, client, repo):
        wf_id = "wf-group-1"
        repo.save(_seed_workflow(workflow_id=wf_id, version="1", name="V1", status=WorkflowStatus.PUBLISHED))
        repo.save(_seed_workflow(workflow_id=wf_id, version="2", name="V2", status=WorkflowStatus.PUBLISHED))
        repo.save(_seed_workflow(workflow_id=wf_id, version="3", name="V3 Draft", status=WorkflowStatus.DRAFT))

        resp = client.get(f"/api/v1/workflows/publish/{wf_id}")

        data = assert_ok(resp)
        assert data["name"] == "V2"
        assert data["version"] == "2"
        assert data["status"] == "PUBLISHED"

    def test_falls_back_to_latest_draft_when_no_published_versions(self, client, repo):
        """HANA contract: latest PUBLISHED if one exists, else latest
        DRAFT — a draft-only workflow resolves to the draft, not 404."""
        wf_id = "wf-draft-only"
        repo.save(_seed_workflow(workflow_id=wf_id, version="1", name="Draft", status=WorkflowStatus.DRAFT))

        resp = client.get(f"/api/v1/workflows/publish/{wf_id}")

        data = assert_ok(resp)
        assert data["name"] == "Draft"
        assert data["status"] == "DRAFT"

    def test_returns_404_when_workflow_does_not_exist(self, client, repo):
        resp = client.get("/api/v1/workflows/publish/non-existent-id")

        assert resp.status_code == 404
        err = assert_error(resp)
        assert err["code"] == "NOT_FOUND"

    def test_returns_single_published_version(self, client, repo):
        wf_id = "wf-single"
        repo.save(_seed_workflow(workflow_id=wf_id, version="1", name="Only Published", status=WorkflowStatus.PUBLISHED))

        resp = client.get(f"/api/v1/workflows/publish/{wf_id}")

        data = assert_ok(resp)
        assert data["name"] == "Only Published"
        assert data["version"] == "1"

    def test_ignores_other_workflow_ids(self, client, repo):
        repo.save(_seed_workflow(workflow_id="wf-a", version="1", name="A-Public", status=WorkflowStatus.PUBLISHED))
        repo.save(_seed_workflow(workflow_id="wf-b", version="5", name="B-Public", status=WorkflowStatus.PUBLISHED))

        resp = client.get("/api/v1/workflows/publish/wf-a")

        data = assert_ok(resp)
        assert data["name"] == "A-Public"
        assert data["version"] == "1"

    def test_response_contains_nodes_and_edges(self, client, repo):
        wf_id = "wf-with-nodes"
        repo.save(_seed_workflow(workflow_id=wf_id, version="1", name="With Nodes", status=WorkflowStatus.PUBLISHED))

        resp = client.get(f"/api/v1/workflows/publish/{wf_id}")

        data = assert_ok(resp)
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        node_types = [n["node_type"] for n in data["nodes"]]
        assert "TRIGGER" in node_types
        assert "TASK" in node_types
