import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import UpdateWorkflowUseCase
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger, assert_error, assert_ok


def _seed_workflow(*, id: str, workflow_id: str, version: str, name: str) -> Workflow:
    return Workflow(
        id=id,
        workflow_id=workflow_id,
        version=version,
        name=name,
        description="desc",
        nodes=[
            WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n2", name="Task", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[WorkflowEdge(source_node_id="n1", target_node_id="n2")],
        metadata={},
        schema_version="1.0.0",
        action="",
        instruction="",
        agents=[],
    )


def _payload(*, version: str, name: str, is_confirm: bool = False) -> dict:
    return {
        "name": name,
        "version": version,
        "description": "desc",
        "input_schema": [],
        "output_schema": [],
        "nodes": [
            {"id": "n1", "name": "Start", "node_type": "TRIGGER"},
            {"id": "n2", "name": "Task", "node_type": "TASK", "worker_type": "w1"},
        ],
        "edges": [{"source_node_id": "n1", "target_node_id": "n2"}],
        "metadata": {},
        "schema_version": "1.0.0",
        "action": "",
        "instruction": "",
        "agents": [],
        "is_confirm": is_confirm,
    }


@pytest.fixture
def repo():
    return MemoryWorkflowRepository()


@pytest.fixture
def client(repo):
    container = {
        "update_workflow": UpdateWorkflowUseCase(logger=DummyLogger(), workflow_repo=repo),
    }
    return TestClient(create_fastapi_app(container))


def test_put_equal_version_updates_in_place(client, repo):
    repo.save(_seed_workflow(id="wf-1", workflow_id="wf-main", version='0', name="Original"))

    response = client.put("/api/v1/workflows/wf-1", json=_payload(version='0', name="Updated"))

    data = assert_ok(response)
    # When version equals latest AND there are changes, update with new version (UUID-based)
    assert data["version"] != '0'  # Version changed
    assert data["id"] == "wf-1"  # Same ID (update in place)
    assert len(repo.list()) == 1  # Updated in place, no new record


def test_put_equal_version_no_changes_updates_in_place(client, repo):
    repo.save(_seed_workflow(id="wf-1", workflow_id="wf-main", version='0', name="Original"))

    # Send same data - no changes
    response = client.put("/api/v1/workflows/wf-1", json=_payload(version='0', name="Original"))

    data = assert_ok(response)
    # When version equals latest AND no changes, update in place
    assert data["id"] == "wf-1"
    assert data["version"] == '0'
    assert len(repo.list()) == 1  # No new version created


def test_put_stale_unconfirmed_returns_409(client, repo):
    # Setup: Save base version (v0) and current/latest version (v1) with workflow_id matching path param
    # so get_by_id_version can find the base workflow for comparison
    repo.save(_seed_workflow(id="wf-base", workflow_id="wf-1", version='0', name="Base"))
    repo.save(_seed_workflow(id="wf-1", workflow_id="wf-1", version='1', name="Latest"))

    # User tries to update with stale version='0' but workflow is now at version='1'
    response = client.put("/api/v1/workflows/wf-1", json=_payload(version='0', name="Stale Edit"))

    err = assert_error(response, "WORKFLOW_VERSION_CONFLICT")
    assert response.status_code == 409
    assert err["details"]["latest_version"] == '1'


def test_put_stale_confirmed_creates_new_version(client, repo):
    # Setup: Save base version (v0) and current/latest version (v1) with workflow_id matching path param
    repo.save(_seed_workflow(id="wf-base", workflow_id="wf-1", version='0', name="Base"))
    repo.save(_seed_workflow(id="wf-1", workflow_id="wf-1", version='1', name="Latest"))

    response = client.put(
        "/api/v1/workflows/wf-1",
        json=_payload(version='0', name="Confirmed Edit", is_confirm=True),
    )

    data = assert_ok(response)
    # When confirmed, update with new version (UUID-based), updated in place
    assert data["version"] not in {'0', '1'}  # New version created
    assert data["id"] == "wf-1"  # Same ID (update in place)
    assert len(repo.list()) == 2  # Base + updated (latest updated in place)