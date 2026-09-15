"""PATCH /data terminal-failure routing.

When a PATCH op fails with a *terminal* error (data-factory 500 /
FACTORY_BAD_RESPONSE, internal error) the node is marked FAILED and the
run is driven through ``orchestrate_fail_locked`` (route a failure edge,
else fail the run). *Retryable* errors (validation, precondition, stale
ETag) leave the node PENDING so the FE can fix the input and retry.

Mirrors the harness in ``test_cancel_via_patch_data.py`` but wires a stub
data-factory + a real working ref so ``execute`` reaches ``_apply_array_ops``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_input_etag import (
    NodeInputEtag,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.node_data_factory_service_interface import (
    RowTransformResult,
)
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
    EditNodeDataUseCase,
)

NODE_ID = "n1"
RUN_ID = "r1"


# ─────────────────────────── helpers ─────────────────────────────


class _StubFactory:
    """Returns a canned RowTransformResult for every row op."""

    def __init__(self, result: RowTransformResult) -> None:
        self._result = result
        self.calls: list[str] = []

    async def apply_row_operations(
        self, *, file_id: str, operations, file_format: str = "csv",
        version_id: str | None = None,
    ) -> RowTransformResult:
        self.calls.append(file_id)
        return self._result


class _RecordingFactory:
    """Keeps file_id stable and mints an incrementing version per call,
    recording the (file_id, version_id) it was invoked with."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._n = 0

    async def apply_row_operations(
        self, *, file_id: str, operations, file_format: str = "csv",
        version_id: str | None = None,
    ) -> RowTransformResult:
        self.calls.append({"file_id": file_id, "version_id": version_id})
        self._n += 1
        return RowTransformResult(
            success=True, file_id=file_id, version_id=f"ver-{self._n}",
            affected_rows=1,
        )


def _working_ref() -> dict:
    ref = build_file_ref(file_id="", artifact_id="__root__")
    ref["artifact_type"] = "object_field"
    add_artifact(
        ref, file_id="src_file", name="table_data",
        artifact_id="table_data", artifact_type="array_field",
    )
    ref["artifacts"][0]["columns"] = ["a", "b"]
    return ref


def _make_task() -> Task:
    task = Task(id="t-n1", run_id=RUN_ID, node_id=NODE_ID, status=TaskStatus.PENDING)
    task.output_metadata = {"input_schema": [{"key": "a", "outputType": "string"}]}
    return task


def _make_engine(workflow: Workflow) -> MagicMock:
    engine = MagicMock()
    engine.load_workflow_for_run = MagicMock(return_value=workflow)
    engine.emit_node_failed = MagicMock()
    engine.emit_node_data_edited = MagicMock()
    engine.orchestrate_fail = AsyncMock()
    engine.orchestrate_fail_locked = AsyncMock()
    return engine


def _make_usecase(
    *, task: Task, run: WorkflowRun, workflow: Workflow, working_ref: dict,
    factory,
) -> tuple[EditNodeDataUseCase, MagicMock]:
    run_repo = MagicMock()
    run_repo.aget_by_id = AsyncMock(return_value=run)
    task_repo = MagicMock()
    task_repo.alist_by_run = AsyncMock(return_value=[task])
    task_repo.asave = AsyncMock()
    view_store = MagicMock()
    view_store.get = MagicMock(
        return_value=SimpleNamespace(variables={NODE_ID: working_ref}),
    )
    file_client = MagicMock()
    file_client.delete_file = AsyncMock()
    file_client.download_file = AsyncMock(return_value=b"")
    engine = _make_engine(workflow)

    uc = EditNodeDataUseCase(
        logger=MagicMock(),
        run_repo=run_repo,
        run_context_view_store=view_store,
        task_repo=task_repo,
        workflow_repo=MagicMock(),
        file_service_client=file_client,
        node_input_etag_calculator=NodeInputEtag(),
        node_data_factory_service=factory,
        orchestration_engine=engine,
    )
    return uc, engine


def _matching_if_match(working_ref: dict) -> str:
    # handler_registry is unwired → schema_version == "v0".
    return NodeInputEtag.of(working_ref, schema_version="v0")


# ─────────────────────────── tests ───────────────────────────────


@pytest.mark.asyncio
class TestTerminalFailure:
    async def test_factory_failure_marks_node_and_run_failed(self):
        task = _make_task()
        run = WorkflowRun(
            id=RUN_ID, workflow_id="wf1", status=RunStatus.RUNNING,
            context=RunContext(topology=GraphTopology()),
        )
        workflow = Workflow(
            id="wf1",
            nodes=[WorkflowNode(id=NODE_ID, name="Edit", node_type=NodeType.DATA_EDIT)],
            edges=[],
        )
        ref = _working_ref()
        factory = _StubFactory(RowTransformResult(
            success=False, error_code="FACTORY_ERROR", message="factory 500",
        ))
        uc, engine = _make_usecase(
            task=task, run=run, workflow=workflow, working_ref=ref, factory=factory,
        )

        result = await uc.execute(EditNodeDataCommand(
            run_id=RUN_ID, node_id=NODE_ID,
            if_match=_matching_if_match(ref),
            artifacts=[{
                "artifact_id": "table_data",
                "operations": [{"operation": "insert", "row_id": None, "data": {"a": "1"}}],
            }],
        ))

        # Error surfaced to the FE…
        assert result.error_code == "FACTORY_ERROR"
        assert result.error
        # …and the node + run were driven to FAILED.
        assert task.status == TaskStatus.FAILED
        assert task.output_metadata["error_code"] == "FACTORY_ERROR"
        engine.emit_node_failed.assert_called_once()
        engine.orchestrate_fail_locked.assert_awaited_once_with(RUN_ID, task.id)
        engine.orchestrate_fail.assert_not_awaited()

    async def test_retryable_error_leaves_node_pending(self):
        # An unknown artifact_id is a client error (ARTIFACT_NOT_FOUND) —
        # NOT in the terminal set, so the node stays PENDING for retry.
        task = _make_task()
        run = WorkflowRun(
            id=RUN_ID, workflow_id="wf1", status=RunStatus.RUNNING,
            context=RunContext(topology=GraphTopology()),
        )
        workflow = Workflow(
            id="wf1",
            nodes=[WorkflowNode(id=NODE_ID, name="Edit", node_type=NodeType.DATA_EDIT)],
            edges=[],
        )
        ref = _working_ref()
        factory = _StubFactory(RowTransformResult(success=True, file_id="never"))
        uc, engine = _make_usecase(
            task=task, run=run, workflow=workflow, working_ref=ref, factory=factory,
        )

        result = await uc.execute(EditNodeDataCommand(
            run_id=RUN_ID, node_id=NODE_ID,
            if_match=_matching_if_match(ref),
            artifacts=[{
                "artifact_id": "does_not_exist",
                "operations": [{"operation": "insert", "row_id": None, "data": {}}],
            }],
        ))

        assert result.error_code == "ARTIFACT_NOT_FOUND"
        assert task.status == TaskStatus.PENDING
        assert factory.calls == []  # never reached the factory
        engine.emit_node_failed.assert_not_called()
        engine.orchestrate_fail_locked.assert_not_awaited()


@pytest.mark.asyncio
class TestVersionThreading:
    """data-factory v2: file_id stays stable, version_id is threaded across
    chained row ops and persisted onto the working ref."""

    async def test_multi_op_threads_version_and_persists_final(self):
        task = _make_task()
        run = WorkflowRun(
            id=RUN_ID, workflow_id="wf1", status=RunStatus.RUNNING,
            context=RunContext(topology=GraphTopology()),
        )
        workflow = Workflow(
            id="wf1",
            nodes=[WorkflowNode(id=NODE_ID, name="Edit", node_type=NodeType.DATA_EDIT)],
            edges=[],
        )
        ref = _working_ref()  # table_data file_id="src_file", no version yet
        factory = _RecordingFactory()
        uc, engine = _make_usecase(
            task=task, run=run, workflow=workflow, working_ref=ref, factory=factory,
        )

        result = await uc.execute(EditNodeDataCommand(
            run_id=RUN_ID, node_id=NODE_ID,
            if_match=_matching_if_match(ref),
            artifacts=[{
                "artifact_id": "table_data",
                "operations": [
                    {"operation": "insert", "row_id": None, "data": {"a": "1"}},
                    {"operation": "insert", "row_id": None, "data": {"a": "2"}},
                ],
            }],
        ))

        assert result.error is None
        # file_id is stable across both ops…
        assert [c["file_id"] for c in factory.calls] == ["src_file", "src_file"]
        # …and op2's read is pinned to the version op1 produced.
        assert factory.calls[0]["version_id"] is None
        assert factory.calls[1]["version_id"] == "ver-1"

        # The committed working ref carries the final version + stable file_id.
        new_ref = engine.emit_node_data_edited.call_args.kwargs["new_ref"]
        table = new_ref["artifacts"][0]
        assert table["file_id"] == "src_file"
        assert table["version_id"] == "ver-2"
