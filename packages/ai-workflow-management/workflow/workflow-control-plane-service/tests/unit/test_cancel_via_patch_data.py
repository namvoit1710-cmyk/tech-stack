"""Cancel-via-PATCH regression tests.

When the FE clicks Cancel on an INPUT / FILE_UPLOAD / DATA_EDIT pending
node it sends ``PATCH /data {data: {_action: "reject"}}``.
``EditNodeDataUseCase`` detects this signal and short-circuits the
request to a task-cancel + failure-edge routing path. HUMAN_ACTION is
excluded by node type — its ``_action`` write is a real working-copy
edit that POST /submit reads to take the reject branch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import (
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
    EditNodeDataUseCase,
    _input_schema_keys,
    _is_cancel_payload,
)


# ─────────────────────────── helpers ─────────────────────────────


def _cancel_payload(*, action: str = "reject") -> list[dict]:
    return [
        {
            "artifact_id": "__root__",
            "operations": [
                {"operation": "insert", "row_id": None, "data": {"_action": action}},
            ],
        },
    ]


def _make_task(
    *,
    node_id: str,
    schema_keys: list[str] | None = None,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    schema = [{"key": k, "outputType": "string"} for k in (schema_keys or [])]
    task = Task(id=f"t-{node_id}", run_id="r1", node_id=node_id, status=status)
    task.output_metadata = {"input_schema": schema}
    return task


def _make_run(*, status: RunStatus = RunStatus.RUNNING) -> WorkflowRun:
    return WorkflowRun(
        id="r1", workflow_id="wf1", status=status,
        context=RunContext(topology=GraphTopology()),
    )


def _make_workflow(node_type: NodeType) -> Workflow:
    return Workflow(
        id="wf1",
        nodes=[WorkflowNode(id="n1", name="Node1", node_type=node_type)],
        edges=[],
    )


def _make_usecase(
    *,
    task: Task,
    run: WorkflowRun,
    workflow: Workflow,
    engine: MagicMock | None = None,
) -> tuple[EditNodeDataUseCase, MagicMock]:
    """Wire a use case with the minimum mocks needed to exercise cancel."""
    run_repo = MagicMock()
    run_repo.get_by_id = MagicMock(return_value=run)
    run_repo.aget_by_id = AsyncMock(return_value=run)
    workflow_repo = MagicMock()
    workflow_repo.get_by_id = MagicMock(return_value=workflow)
    workflow_repo.aget_by_id = AsyncMock(return_value=workflow)
    task_repo = MagicMock()
    task_repo.list_by_run = MagicMock(return_value=[task])
    task_repo.alist_by_run = AsyncMock(return_value=[task])
    task_repo.save = MagicMock()
    task_repo.asave = AsyncMock()
    view_store = MagicMock()
    view_store.get = MagicMock(return_value=None)
    file_client = MagicMock()
    file_client.delete_file = AsyncMock()
    etag_calc = MagicMock()

    engine = engine or _make_engine_mock(workflow=workflow)

    uc = EditNodeDataUseCase(
        logger=MagicMock(),
        run_repo=run_repo,
        run_context_view_store=view_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        file_service_client=file_client,
        node_input_etag_calculator=etag_calc,
        orchestration_engine=engine,
    )
    return uc, engine


def _make_engine_mock(*, workflow=None) -> MagicMock:
    engine = MagicMock()
    engine.emit_node_cancelled = MagicMock()
    # _cancel_pending_task drives failure routing via the ``_locked``
    # variant because the OrchestrationWorker already holds the per-run
    # lock around EditNodeDataAction (asyncio.Lock is not reentrant).
    engine.orchestrate_fail = AsyncMock()
    engine.orchestrate_fail_locked = AsyncMock()
    # Phase B+C: use cases resolve the workflow via the engine. Return
    # the workflow handed to the helper instead of the auto-Mock so
    # ``workflow.get_node`` returns a real ``WorkflowNode`` (the cancel
    # branch checks ``node.node_type``).
    engine.load_workflow_for_run = MagicMock(return_value=workflow)
    return engine


# ─────────────────── helper-function unit tests ──────────────────


class TestInputSchemaKeys:
    def test_returns_top_level_keys(self):
        task = MagicMock()
        task.output_metadata = {
            "input_schema": [
                {"key": "name", "outputType": "string"},
                {"key": "age", "outputType": "number"},
            ]
        }
        assert _input_schema_keys(task) == frozenset({"name", "age"})

    def test_skips_entries_without_key(self):
        task = MagicMock()
        task.output_metadata = {
            "input_schema": [{"outputType": "string"}, {"key": "ok"}]
        }
        assert _input_schema_keys(task) == frozenset({"ok"})

    def test_returns_empty_when_no_metadata(self):
        task = MagicMock()
        task.output_metadata = None
        assert _input_schema_keys(task) == frozenset()

    def test_returns_empty_when_schema_not_a_list(self):
        task = MagicMock()
        task.output_metadata = {"input_schema": "not-a-list"}
        assert _input_schema_keys(task) == frozenset()


class TestIsCancelPayload:
    def test_reject_value_outside_schema_triggers(self):
        assert _is_cancel_payload(_cancel_payload(action="reject"), frozenset({"file"}))

    def test_cancel_value_outside_schema_triggers(self):
        assert _is_cancel_payload(_cancel_payload(action="cancel"), frozenset({"file"}))

    def test_case_insensitive(self):
        assert _is_cancel_payload(_cancel_payload(action="REJECT"), frozenset())

    def test_action_in_schema_falls_through(self):
        # HUMAN_ACTION declares _action as a real column → not a cancel.
        assert not _is_cancel_payload(
            _cancel_payload(action="reject"), frozenset({"_action", "comments"})
        )

    def test_other_action_values_do_not_trigger(self):
        assert not _is_cancel_payload(
            _cancel_payload(action="confirm"), frozenset()
        )

    def test_no_action_key_does_not_trigger(self):
        ops = [{"artifact_id": "__root__", "operations": [
            {"operation": "insert", "row_id": None, "data": {"file": "x"}},
        ]}]
        assert not _is_cancel_payload(ops, frozenset())


# ───────────────── cancel sub-routine integration ────────────────


@pytest.mark.asyncio
class TestCancelViaPatch:
    async def test_input_pending_cancel_marks_task_failed(self):
        task = _make_task(node_id="n1", schema_keys=["name", "email"])
        run = _make_run()
        workflow = _make_workflow(NodeType.INPUT)
        uc, engine = _make_usecase(task=task, run=run, workflow=workflow)

        cmd = EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match='W/"any"',
            artifacts=_cancel_payload(),
        )
        result = await uc.execute(cmd)

        assert result.cancelled is True
        assert result.error is None
        assert task.status == TaskStatus.FAILED
        assert task.error == "user_cancelled"
        assert task.output_metadata["reason"] == "user_cancelled"
        assert task.output_metadata["cancelled_by"] == "user"
        engine.emit_node_cancelled.assert_called_once()
        engine.orchestrate_fail_locked.assert_awaited_once_with("r1", task.id)
        engine.orchestrate_fail.assert_not_awaited()

    async def test_file_upload_cancel_works_without_working_csv(self):
        # FILE_UPLOAD with outputType=object → no provisioned root CSV.
        # Detection runs before working_ref lookup so the cancel still fires.
        task = _make_task(node_id="n1", schema_keys=["file"])
        run = _make_run()
        workflow = _make_workflow(NodeType.FILE_UPLOAD)
        uc, engine = _make_usecase(task=task, run=run, workflow=workflow)

        result = await uc.execute(EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match='W/"any"',
            artifacts=_cancel_payload(),
        ))

        assert result.cancelled is True
        # Crucially: no 404 surfaced, no etag preconditions enforced.
        assert result.error_code != "ARTIFACT_NOT_FOUND"
        assert task.status == TaskStatus.FAILED
        engine.orchestrate_fail_locked.assert_awaited_once()

    async def test_data_edit_cancel_marks_task_failed(self):
        # DATA_EDIT is the third cancellable pending type; same path as
        # INPUT but exercised explicitly so the node-type matrix is complete.
        task = _make_task(node_id="n1", schema_keys=["rows"])
        run = _make_run()
        workflow = _make_workflow(NodeType.DATA_EDIT)
        uc, engine = _make_usecase(task=task, run=run, workflow=workflow)

        result = await uc.execute(EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match='W/"any"',
            artifacts=_cancel_payload(),
        ))

        assert result.cancelled is True
        assert task.status == TaskStatus.FAILED
        assert task.error == "user_cancelled"
        engine.orchestrate_fail_locked.assert_awaited_once_with("r1", task.id)

    async def test_cancel_short_circuits_before_if_match_check(self):
        # The cancel signal is detected before the If-Match precondition,
        # so a reject PATCH with no If-Match header still cancels (the FE
        # reject button does not carry an ETag). Contrast with the
        # HUMAN_ACTION case below, where missing If-Match → 428.
        task = _make_task(node_id="n1", schema_keys=["name"])
        run = _make_run()
        workflow = _make_workflow(NodeType.INPUT)
        uc, engine = _make_usecase(task=task, run=run, workflow=workflow)

        result = await uc.execute(EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match=None,  # FE reject button sends no ETag
            artifacts=_cancel_payload(),
        ))

        assert result.cancelled is True
        assert result.error_code != ErrorCode.PRECONDITION_REQUIRED.code
        assert task.status == TaskStatus.FAILED
        engine.orchestrate_fail_locked.assert_awaited_once_with("r1", task.id)

    async def test_human_action_reject_falls_through_to_normal_path(self):
        # HUMAN_ACTION is excluded from cancel detection by node type, NOT
        # by schema: HumanActionNodeHandler keeps `_action` out of the
        # FE-facing input_schema, so the realistic schema here has only
        # the user-defined `comments` field. A `_action: reject` PATCH
        # must still flow through the normal write path (the `_action`
        # column write that POST /submit reads to take the reject edge).
        # It then fails on missing If-Match — that's fine, we only assert
        # the cancel branch wasn't taken.
        task = _make_task(node_id="n1", schema_keys=["comments"])
        run = _make_run()
        workflow = _make_workflow(NodeType.HUMAN_ACTION)
        uc, engine = _make_usecase(task=task, run=run, workflow=workflow)

        result = await uc.execute(EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match=None,  # missing on purpose
            artifacts=_cancel_payload(),
        ))

        # Falls through to normal path → If-Match missing → PRECONDITION_REQUIRED.
        # Cancel-side mutations must NOT have happened.
        assert result.cancelled is False
        assert task.status == TaskStatus.PENDING
        engine.emit_node_cancelled.assert_not_called()
        engine.orchestrate_fail.assert_not_awaited()
        engine.orchestrate_fail_locked.assert_not_awaited()

    async def test_already_terminal_task_returns_409(self):
        task = _make_task(
            node_id="n1", schema_keys=["name"], status=TaskStatus.COMPLETED,
        )
        run = _make_run()
        workflow = _make_workflow(NodeType.INPUT)
        uc, engine = _make_usecase(task=task, run=run, workflow=workflow)

        result = await uc.execute(EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match='W/"any"',
            artifacts=_cancel_payload(),
        ))

        assert result.cancelled is False
        assert result.error_code == ErrorCode.INVALID_TASK_STATUS.code
        # Task state is not mutated by a 409.
        assert task.status == TaskStatus.COMPLETED
        engine.emit_node_cancelled.assert_not_called()
        engine.orchestrate_fail.assert_not_awaited()
        engine.orchestrate_fail_locked.assert_not_awaited()

    async def test_cancel_against_non_pending_node_type_rejected(self):
        # Defence in depth: a TASK node should never see a cancel-via-PATCH.
        task = _make_task(node_id="n1", schema_keys=["x"])
        run = _make_run()
        workflow = Workflow(
            id="wf1",
            nodes=[WorkflowNode(
                id="n1", name="Node1",
                node_type=NodeType.TASK, worker_type="w1",
            )],
        )
        uc, engine = _make_usecase(task=task, run=run, workflow=workflow)

        result = await uc.execute(EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match='W/"any"',
            artifacts=_cancel_payload(),
        ))

        assert result.cancelled is False
        assert result.error_code == "CANCEL_NOT_SUPPORTED"
        engine.emit_node_cancelled.assert_not_called()
        engine.orchestrate_fail.assert_not_awaited()
        engine.orchestrate_fail_locked.assert_not_awaited()


# ─────────────── worker-lock-held deadlock regression ────────────


@pytest.mark.asyncio
class TestCancelUnderWorkerLock:
    """Regression for the 30 s PATCH timeout caused by the cancel path
    re-acquiring the per-run lock the OrchestrationWorker already holds.

    Production trace: worker locks ``run_lock(run_id)`` → calls
    ``EditNodeDataUseCase.execute`` → cancel branch calls
    ``orchestrate_fail`` → ``orchestrate_fail`` does
    ``async with self._get_run_lock(run_id):`` on the same non-reentrant
    ``asyncio.Lock`` → deadlock → HTTP caller's
    ``asyncio.wait_for(future, timeout=30.0)`` surfaces a 500.

    Fix: cancel path calls ``orchestrate_fail_locked`` (no lock
    acquisition). This test holds the lock externally and asserts the
    use case still returns promptly — would hang 30 s pre-fix.
    """

    async def test_cancel_does_not_deadlock_when_worker_holds_run_lock(self):
        import asyncio

        task = _make_task(node_id="n1", schema_keys=["name"])
        run = _make_run()
        workflow = _make_workflow(NodeType.INPUT)

        run_lock = asyncio.Lock()

        async def fake_orchestrate_fail(run_id: str, task_id: str) -> None:
            # Simulates the real ``orchestrate_fail``: tries to acquire
            # the per-run lock before doing any work. Pre-fix, the cancel
            # path routed here while the lock was held → deadlock.
            async with run_lock:
                pass

        async def fake_orchestrate_fail_locked(run_id: str, task_id: str) -> None:
            # Simulates ``orchestrate_fail_locked``: assumes the caller
            # already holds the lock and does NOT try to re-acquire it.
            assert run_lock.locked(), (
                "orchestrate_fail_locked called without the per-run lock held — "
                "caller contract violated"
            )

        engine = _make_engine_mock(workflow=workflow)
        engine.orchestrate_fail = AsyncMock(side_effect=fake_orchestrate_fail)
        engine.orchestrate_fail_locked = AsyncMock(
            side_effect=fake_orchestrate_fail_locked,
        )
        uc, _ = _make_usecase(
            task=task, run=run, workflow=workflow, engine=engine,
        )

        cmd = EditNodeDataCommand(
            run_id="r1", node_id="n1",
            if_match='W/"any"',
            artifacts=_cancel_payload(),
        )

        async with run_lock:  # simulate OrchestrationWorker's critical section
            result = await asyncio.wait_for(uc.execute(cmd), timeout=2.0)

        assert result.cancelled is True
        assert task.status == TaskStatus.FAILED
        engine.orchestrate_fail_locked.assert_awaited_once_with("r1", task.id)
        # Critical: the locking variant must NOT have been called from
        # inside the worker's critical section.
        engine.orchestrate_fail.assert_not_awaited()
