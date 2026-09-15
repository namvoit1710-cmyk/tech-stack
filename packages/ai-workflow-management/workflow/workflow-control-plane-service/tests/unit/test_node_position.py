"""Tests for node position (x, y) features."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
)
from app.layer2_application.features.get_workflow.use_cases.get_workflow_use_case import (
    GetWorkflowCommand,
    GetWorkflowUseCase,
)
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import (
    UpdateWorkflowCommand,
    UpdateWorkflowUseCase,
)
from app.layer4_frameworks.persistence.hana.hana_workflow_repo import HanaWorkflowRepository


# ────────────── Node Position (x, y) ──────────────


class TestNodePosition:
    def test_workflow_node_default_position_is_none(self):
        node = WorkflowNode(id="n1", node_type=NodeType.TASK, worker_type="w")
        assert node.x is None
        assert node.y is None

    def test_workflow_node_with_position(self):
        node = WorkflowNode(id="n1", node_type=NodeType.TASK, worker_type="w", x=100.5, y=200.0)
        assert node.x == 100.5
        assert node.y == 200.0

    @pytest.mark.asyncio
    async def test_create_workflow_preserves_position(self, logger, workflow_repo):
        uc = CreateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        cmd = CreateWorkflowCommand(
            name="With Position",
            nodes=[
                {"id": "n1", "name": "Start", "node_type": "TRIGGER", "x": 50.0, "y": 100.0},
                {"id": "n2", "name": "Task", "node_type": "TASK", "worker_type": "w1", "x": 200.0, "y": 300.0},
            ],
            edges=[{"source_node_id": "n1", "target_node_id": "n2"}],
        )
        result = await uc.execute(cmd)
        assert result.nodes[0]["x"] == 50.0
        assert result.nodes[0]["y"] == 100.0
        assert result.nodes[1]["x"] == 200.0
        assert result.nodes[1]["y"] == 300.0

    @pytest.mark.asyncio
    async def test_create_workflow_without_position_returns_none(self, logger, workflow_repo):
        uc = CreateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        cmd = CreateWorkflowCommand(
            name="No Position",
            nodes=[{"id": "n1", "name": "Start", "node_type": "TRIGGER"}],
        )
        result = await uc.execute(cmd)
        assert result.nodes[0]["x"] is None
        assert result.nodes[0]["y"] is None

    @pytest.mark.asyncio
    async def test_get_workflow_preserves_position(self, logger, workflow_repo):
        wf = Workflow(
            id="wf-pos",
            name="Positioned",
            nodes=[WorkflowNode(id="n1", node_type=NodeType.TASK, worker_type="w", x=10.0, y=20.0)],
        )
        workflow_repo.save(wf)
        uc = GetWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(GetWorkflowCommand(workflow_id="wf-pos"))
        assert result.nodes[0]["x"] == 10.0
        assert result.nodes[0]["y"] == 20.0

    @pytest.mark.asyncio
    async def test_update_workflow_preserves_position(self, logger, workflow_repo):
        wf = Workflow(id="wf-upd", name="Original", nodes=[], edges=[])
        workflow_repo.save(wf)
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        result = await uc.execute(UpdateWorkflowCommand(
            workflow_id="wf-upd",
            nodes=[{"id": "n1", "name": "Moved", "node_type": "TASK", "worker_type": "w", "x": 42.5, "y": 99.0}],
        ))
        assert result.nodes[0]["x"] == 42.5
        assert result.nodes[0]["y"] == 99.0


# ────────────── HANA Repo ──────────────


class TestHanaWorkflowRepoPosition:
    def _make_repo(self):
        db = MagicMock()
        return HanaWorkflowRepository(db)

    def test_row_to_entity_with_position(self):
        repo = self._make_repo()
        row = {
            "id": "wf-1",
            "workflow_id": "wf-1",
            "name": "Test",
            "version": "1",
            "description": "",
            "input_schema": "[]",
            "output_schema": "[]",
            "nodes": '[{"id": "n1", "name": "N1", "node_type": "TASK", "worker_type": "w", "input_binding": {}, "output_binding": {}, "config": {}, "x": 100.0, "y": 200.5}]',
            "edges": "[]",
            "status": "DRAFT",
            "created_at": "2025-01-01T00:00:00Z",
        }
        wf = repo._row_to_entity(row)
        assert wf.nodes[0].x == 100.0
        assert wf.nodes[0].y == 200.5

    def test_row_to_entity_without_position_backward_compat(self):
        repo = self._make_repo()
        row = {
            "id": "wf-2",
            "workflow_id": "wf-2",
            "name": "Old",
            "version": "1",
            "description": "",
            "input_schema": "[]",
            "output_schema": "[]",
            "nodes": '[{"id": "n1", "name": "N1", "node_type": "TASK", "worker_type": "w", "input_binding": {}, "output_binding": {}, "config": {}}]',
            "edges": "[]",
            "status": "DRAFT",
            "created_at": "2025-01-01T00:00:00Z",
        }
        wf = repo._row_to_entity(row)
        assert wf.nodes[0].x is None
        assert wf.nodes[0].y is None

    def test_node_to_dict_includes_position(self):
        repo = self._make_repo()
        node = WorkflowNode(id="n1", name="N1", node_type=NodeType.TASK, worker_type="w", x=1.0, y=2.0)

        d = repo._node_to_dict(node)
        assert d["x"] == 1.0
        assert d["y"] == 2.0
        assert d["node_type"] == "TASK"

    def test_edge_to_dict(self):
        repo = self._make_repo()
        edge = WorkflowEdge(id="e1", source_node_id="a", target_node_id="b", condition="ok")
        d = repo._edge_to_dict(edge)
        assert d["source_node_id"] == "a"
        assert d["target_node_id"] == "b"
        assert d["condition"] == "ok"
