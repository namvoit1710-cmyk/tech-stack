"""End-to-end: creating or updating a workflow pins spec_version on every node.

Uses the real create/update use cases wired via build_app_container(), with
a fake registry client injected by replacing the one constructed in
infra/bootstrap. Keeps the test runtime-free — no actual workers needed.
"""

from __future__ import annotations

import pytest

from bootstrap import build_app_container
from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
)
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import (
    PatchWorkflowCommand,
    UpdateWorkflowUseCase,
)


class _FakeRegistryClient:
    """Minimal stand-in exposing just the list_workers() method the pinner uses."""

    def __init__(self, workers):
        self._workers = workers

    def list_workers(self):
        return list(self._workers)


def _patch_registry(container, workers):
    """Replace the worker_registry_client inside the create/update use cases."""
    fake = _FakeRegistryClient(workers)
    create_uc: CreateWorkflowUseCase = container["create_workflow"]
    update_uc: UpdateWorkflowUseCase = container["update_workflow"]
    create_uc.worker_registry_client = fake
    update_uc.worker_registry_client = fake


class TestCreatePinsSpecVersion:
    @pytest.mark.asyncio
    async def test_builtin_node_pins_1_0_0(self):
        c = build_app_container()
        _patch_registry(c, [])
        uc: CreateWorkflowUseCase = c["create_workflow"]
        r = await uc.execute(CreateWorkflowCommand(
            name="t",
            nodes=[{"id": "n1", "name": "cond", "node_type": "CONDITION"}],
            edges=[],
        ))
        assert r.error == ""
        assert r.nodes[0]["spec_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_worker_node_pins_latest_registered_version(self):
        c = build_app_container()
        _patch_registry(c, [
            {"worker_type": "http", "spec_version": "2.0.0"},
            {"worker_type": "http", "spec_version": "1.0.0"},
        ])
        uc: CreateWorkflowUseCase = c["create_workflow"]
        r = await uc.execute(CreateWorkflowCommand(
            name="t",
            nodes=[{
                "id": "n1", "name": "http", "node_type": "TASK",
                "worker_type": "http",
            }],
            edges=[],
        ))
        assert r.error == ""
        assert r.nodes[0]["spec_version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_author_pinned_version_preserved(self):
        c = build_app_container()
        _patch_registry(c, [
            {"worker_type": "http", "spec_version": "9.9.9"},
        ])
        uc: CreateWorkflowUseCase = c["create_workflow"]
        r = await uc.execute(CreateWorkflowCommand(
            name="t",
            nodes=[{
                "id": "n1", "name": "http", "node_type": "TASK",
                "worker_type": "http",
                "spec_version": "1.0.0",  # author deliberately pinned old
            }],
            edges=[],
        ))
        assert r.error == ""
        # Pinner preserves the author's explicit value.
        assert r.nodes[0]["spec_version"] == "1.0.0"


class TestUpdatePinsSpecVersion:
    @pytest.mark.asyncio
    async def test_patch_pins_new_nodes(self):
        c = build_app_container()
        _patch_registry(c, [
            {"worker_type": "http", "spec_version": "3.1.0"},
        ])
        create_uc: CreateWorkflowUseCase = c["create_workflow"]
        update_uc: UpdateWorkflowUseCase = c["update_workflow"]

        created = await create_uc.execute(CreateWorkflowCommand(
            name="wf",
            nodes=[{"id": "n1", "name": "cond", "node_type": "CONDITION"}],
            edges=[],
        ))
        assert created.error == ""

        r = await update_uc.execute(PatchWorkflowCommand(
            workflow_id=created.id,
            nodes=[
                {"id": "n1", "name": "cond", "node_type": "CONDITION"},
                {"id": "n2", "name": "http", "node_type": "TASK",
                 "worker_type": "http"},
            ],
        ))
        assert r.error == ""
        spec_by_id = {n["id"]: n["spec_version"] for n in r.nodes}
        assert spec_by_id["n1"] == "1.0.0"
        assert spec_by_id["n2"] == "3.1.0"


class TestBusinessNodePinning:
    @pytest.mark.asyncio
    async def test_business_node_pins_to_definition_spec_version(self):
        from app.layer1_domain.entities.node_definition import NodeDefinition
        from app.layer1_domain.value_objects.node_type import NodeType

        c = build_app_container()
        _patch_registry(c, [])
        # Seed a business-node definition with a specific spec_version.
        c["_infra"]  # ensure built
        repo = c["create_workflow"].node_definition_repo
        assert repo is not None
        nd = NodeDefinition(
            id="biz-a", tenant_id="system", name="x",
            base_type=NodeType.TASK, base_worker_id="http",
            spec_version="5.5.5",
        )
        repo.save(nd)

        uc: CreateWorkflowUseCase = c["create_workflow"]
        r = await uc.execute(CreateWorkflowCommand(
            name="wf",
            nodes=[{
                "id": "n1", "name": "biz", "node_type": "TASK",
                "node_definition_id": "biz-a",
            }],
            edges=[],
        ))
        assert r.error == ""
        assert r.nodes[0]["spec_version"] == "5.5.5"
