"""Regression: test_node's surgical reseed must re-emit NodeCompleted for
surviving upstream nodes WITHOUT throwing.

``_reseed_projector`` calls ``emit_node_completed(None, run, node)`` (task=None)
to restore the terminal status of preserved upstream nodes after clearing the
event log. ``emit_node_completed`` used to dereference ``task.id`` unguarded,
so the call threw ``AttributeError`` — swallowed by its own try/except — and the
upstream node silently reverted to PENDING in the view. This guards the
None-safe path (mirroring ``emit_node_cancelled``).
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects import NodeType
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok
from tests.helpers.queue_runner import QueueRunner


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


@pytest.mark.asyncio
async def test_reseed_preserves_upstream_completed_status(client, container, caplog):
    wf = Workflow(
        id="wf-reseed-probe", name="reseed-probe",
        nodes=[
            WorkflowNode(id="A", name="A", node_type=NodeType.COMPUTE,
                         config={"expression": "1 + 1"}),
            WorkflowNode(id="B", name="B", node_type=NodeType.COMPUTE,
                         config={"expression": "2 + 2"}),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="A", target_node_id="B")],
    )
    container["get_node_schema"].workflow_repo.save(wf)

    # 1) Test upstream A → it lands in the aggregate's completed_nodes.
    assert_ok(client.post("/api/v1/workflows/wf-reseed-probe/nodes/A/test", json={}))
    await _drain(container)
    run_id = container["get_node_schema"].workflow_repo.get_by_id(
        "wf-reseed-probe"
    ).test_run_id

    # 2) Test downstream B → reuse branch runs _reseed_projector, which loops
    #    over completed_nodes={A} and re-emits NodeCompleted with task=None.
    with caplog.at_level(logging.ERROR):
        assert_ok(client.post("/api/v1/workflows/wf-reseed-probe/nodes/B/test", json={}))
        await _drain(container)

    reemit_errors = [r.getMessage() for r in caplog.records
                     if "Failed to emit NodeCompleted" in r.getMessage()]
    assert not reemit_errors, (
        f"emit_node_completed threw on task=None during reseed: {reemit_errors}"
    )

    # A's COMPLETED status must survive the reseed (the projector folds the
    # re-emitted NodeCompleted back into the set).
    view = container["run_context_view_store"].get(run_id)
    assert "A" in view.completed_nodes, (
        f"upstream A lost its COMPLETED status across the reseed: "
        f"{view.completed_nodes!r}"
    )
