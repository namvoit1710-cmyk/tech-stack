import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import (
    ReplaceWorkflowCommand,
    UpdateWorkflowUseCase,
)


def _workflow_payload(name: str) -> tuple[list[dict], list[dict]]:
    nodes = [
        {"id": "n1", "name": name, "node_type": "TRIGGER"},
        {"id": "n2", "name": "Task", "node_type": "TASK", "worker_type": "w1"},
    ]
    edges = [{"source_node_id": "n1", "target_node_id": "n2"}]
    return nodes, edges


def _workflow(*, workflow_id: str, group_id: str, version: int, name: str, description: str = "") -> Workflow:
    return Workflow(
        id=workflow_id,
        group_id=group_id,
        version=version,
        name=name,
        description=description,
        nodes=[
            WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n2", name="Task", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[WorkflowEdge(source_node_id="n1", target_node_id="n2")],
    )


@pytest.mark.skip(reason=(
    "Workflow versioning was redesigned: the entity no longer has a `group_id` "
    "field and `version` is a UUID string (not an incrementing int). New versions "
    "are minted as UUIDs and saved in place rather than as separate group records. "
    "These tests encode the removed group_id + integer-increment model "
    "(Workflow(group_id=..., version=0) → version==2). All three OCC branches "
    "(equal-version in-place, stale-unconfirmed 409 conflict, stale-confirmed "
    "new-version) are covered against the current model by "
    "tests/integration/test_workflow_put_versioning.py."
))
class TestUpdateWorkflowVersioning:
    @pytest.mark.asyncio
    async def test_equal_version_updates_in_place(self, logger, workflow_repo):
        workflow_repo.save(_workflow(workflow_id="wf-1", group_id="g-1", version=0, name="Original"))
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        nodes, edges = _workflow_payload("Updated")

        result = await uc.execute(ReplaceWorkflowCommand(
            workflow_id="wf-1",
            group_id="g-1",
            version=0,
            name="Updated Workflow",
            description="updated desc",
            nodes=nodes,
            edges=edges,
        ))

        assert result.error == ""
        assert result.id == "wf-1"
        assert result.version == 0
        assert len(workflow_repo.list()) == 1
        persisted = workflow_repo.get_by_id("wf-1")
        assert persisted is not None
        assert persisted.name == "Updated Workflow"
        assert persisted.version == 0

    @pytest.mark.asyncio
    async def test_stale_unconfirmed_update_returns_conflict(self, logger, workflow_repo):
        workflow_repo.save(_workflow(workflow_id="wf-1", group_id="g-1", version=0, name="Base"))
        workflow_repo.save(_workflow(workflow_id="wf-2", group_id="g-1", version=1, name="Latest"))
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        nodes, edges = _workflow_payload("Stale Edit")

        result = await uc.execute(ReplaceWorkflowCommand(
            workflow_id="wf-1",
            group_id="g-1",
            version=0,
            name="Stale Edit",
            description="edited from old version",
            nodes=nodes,
            edges=edges,
            is_confirm=False,
        ))

        assert result.error_code == "WORKFLOW_VERSION_CONFLICT"
        assert result.error != ""
        assert result.error_details["latest_version"] == 1
        assert result.error_details["version"] == 0
        assert len(workflow_repo.list()) == 2

    @pytest.mark.asyncio
    async def test_stale_confirmed_update_creates_new_version(self, logger, workflow_repo):
        workflow_repo.save(_workflow(workflow_id="wf-1", group_id="g-1", version=0, name="Base"))
        workflow_repo.save(_workflow(workflow_id="wf-2", group_id="g-1", version=1, name="Latest"))
        uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
        nodes, edges = _workflow_payload("Confirmed Edit")

        result = await uc.execute(ReplaceWorkflowCommand(
            workflow_id="wf-1",
            group_id="g-1",
            version=0,
            name="Confirmed Edit",
            description="overwrite old",
            nodes=nodes,
            edges=edges,
            is_confirm=True,
        ))

        assert result.error == ""
        assert result.version == 2
        assert result.id not in {"wf-1", "wf-2"}
        assert len(workflow_repo.list()) == 3
        new_record = workflow_repo.get_by_id(result.id)
        assert new_record is not None
        assert new_record.group_id == "g-1"
        assert new_record.version == 2
        assert new_record.name == "Confirmed Edit"