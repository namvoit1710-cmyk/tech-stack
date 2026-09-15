"""Integration test for GET /runs/{run_id}/nodes/{node_id}/output.

Verifies the node-output panel contract end-to-end through FastAPI:
the endpoint reads NodeOutputProduced envelopes from the event log,
pairs them with Task records, and returns FE-ready Scope/VariablePath.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok

WORKFLOW_ID = "wf-no-1"
RUN_ID = "run-no-1"


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _seed(container, node):
    uc = container["get_node_output"]
    uc.workflow_repo.save(Workflow(
        id=WORKFLOW_ID, name="Node Output Test", nodes=[node], edges=[],
    ))
    uc.run_repo.save(WorkflowRun(
        id=RUN_ID, workflow_id=WORKFLOW_ID, status=RunStatus.RUNNING,
    ))


def _emit(container, node_id, value, *, output_port="", taken_edge_ports=None):
    container["event_log_repo"].append(EventLogEnvelope(
        event_id=f"evt-{node_id}", run_id=RUN_ID, topic="node.output.produced",
        event_type="NodeOutputProduced", timestamp="2026-05-19T05:00:00+00:00",
        payload={
            "node_id": node_id,
            "state_updates": {node_id: value},
            "output_port": output_port,
            "taken_edge_ports": taken_edge_ports or {},
        },
    ))


@pytest.mark.asyncio
async def test_node_output_task_iteration(client, container):
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed(container, node)
    container["get_node_output"].task_repo.save(Task(
        run_id=RUN_ID, node_id="task-1", status=TaskStatus.COMPLETED,
    ))
    _emit(container, "task-1", {"score": 7}, output_port="success")

    resp = client.get(f"/api/v1/runs/{RUN_ID}/nodes/task-1/output")
    data = assert_ok(resp)

    assert data["node_id"] == "task-1"
    assert data["node_type"] == "TASK"
    assert data["node_summary"] is None
    assert len(data["iterations"]) == 1
    it = data["iterations"][0]
    assert it["status"] == "COMPLETED"
    assert it["output_port"] == "success"
    assert it["task_id"]
    assert it["scopes"][0]["scope_type"] == "node"
    paths = {p["path"]: p for p in it["scopes"][0]["paths"]}
    assert paths["score"]["sample"] == 7
    assert paths["score"]["display_path"] == "{{$task-1.score}}"


@pytest.mark.asyncio
async def test_node_output_condition_taken_edges(client, container):
    node = WorkflowNode(id="cond-1", name="Condition", node_type=NodeType.CONDITION)
    _seed(container, node)
    _emit(container, "cond-1", {"output": {}, "metadata": {"type": "condition"}},
          taken_edge_ports={"edge-ac": "true_branch"})

    resp = client.get(f"/api/v1/runs/{RUN_ID}/nodes/cond-1/output")
    data = assert_ok(resp)

    it = data["iterations"][0]
    assert it["task_id"] is None
    assert it["taken_edge_ports"] == {"edge-ac": "true_branch"}


@pytest.mark.asyncio
async def test_node_output_run_not_found(client):
    resp = client.get("/api/v1/runs/no-such-run/nodes/x/output")
    data = assert_ok(resp)
    assert data["error_code"] == "RUN_NOT_FOUND"
    assert data["iterations"] == []
