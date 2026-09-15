"""Resume a PENDING task after user review/edit."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.data.data_store_service import MaterializeTooLargeError
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import PENDING_NODE_TYPES
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.run_status_reader import read_authoritative_status
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    ResumeTaskAction,
)
from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient
from app.layer2_application.errors import ErrorCode

_logger = logging.getLogger(__name__)

# Node types that share DATA_EDIT's file-ref working-copy / submit semantics.
# TEMPLATE_DATA_EDIT reuses the same edit machinery via DataEditNodeHandler.
_DATA_EDIT_LIKE = frozenset({NodeType.DATA_EDIT, NodeType.TEMPLATE_DATA_EDIT})

# Maps UI field types to coercion target types (mirrors InputNodeHandler pattern)
_TYPE_COERCION_MAP: dict[str, str] = {
    "string": "string", "number": "number", "boolean": "boolean",
    "object": "object", "array": "array", "textarea": "string",
    "select": "string", "date": "string", "file": "string",
}


def _extract_type_map(input_schema: list) -> dict[str, str]:
    """Extract {field_key: coercion_type} from input_schema."""
    type_map: dict[str, str] = {}
    for field_def in input_schema:
        if not isinstance(field_def, dict):
            continue
        key = field_def.get("key")
        if not key:
            continue
        raw_type = field_def.get("type") or field_def.get("outputType") or "string"
        type_map[key] = _TYPE_COERCION_MAP.get(raw_type, raw_type)
    return type_map


# Artifact types that ``DataStoreService.store_data`` materialises as a
# separate CSV — but only when the array carries at least one row. An *empty*
# table field (``[]``) is not an array-of-dicts, so ``store_data`` collapses
# it into a scalar root column and the file-backed table is lost. The two
# helpers below let the resume path recover that table from the working ref.
_ARRAY_ARTIFACT_TYPES = frozenset({"array_field", "nested_array_field"})


def _array_artifact_names(file_ref: Any) -> set[str]:
    """Names of top-level array artifacts on a working ``__file_ref`` tree."""
    from app.layer1_domain.entities.data_reference import is_file_ref
    if not is_file_ref(file_ref):
        return set()
    names: set[str] = set()
    for art in file_ref.get("artifacts") or []:
        if art.get("artifact_type") in _ARRAY_ARTIFACT_TYPES:
            name = art.get("name") or art.get("artifact_id")
            if name:
                names.add(name)
    return names


def _regraft_dropped_array_artifacts(node_output: Any, working_file_ref: Any) -> None:
    """Re-attach array artifacts ``store_data`` dropped because they were empty.

    A PENDING node is provisioned with a file-backed table artifact (e.g.
    INPUT's ``table_3_lg2h0``). If the user submits without adding rows, the
    materialise→``store_data`` round-trip collapses the empty array into a
    scalar root column and drops the artifact — so a later
    ``PATCH /data?artifact_id=<table>`` 404s with ``ARTIFACT_NOT_FOUND``.

    Re-graft (deep-copied so nested children come along) any array artifact
    present on the working ref but missing from ``node_output``. Non-empty
    tables are untouched: ``store_data`` already created their artifacts, so
    they are ``present`` and skipped here.
    """
    import copy
    from app.layer1_domain.entities.data_reference import is_file_ref
    if not is_file_ref(node_output) or not is_file_ref(working_file_ref):
        return
    present = {
        (a.get("artifact_id") or a.get("name"))
        for a in node_output.get("artifacts") or []
    }
    grafted = node_output.setdefault("artifacts", [])
    for art in working_file_ref.get("artifacts") or []:
        if art.get("artifact_type") not in _ARRAY_ARTIFACT_TYPES:
            continue
        aid = art.get("artifact_id") or art.get("name")
        if aid and aid not in present:
            grafted.append(copy.deepcopy(art))


@dataclass
class ResumePendingTaskCommand:
    run_id: str = ""
    task_id: str = ""
    updated_input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResumePendingTaskResult:
    resumed: bool = False
    task_id: str = ""
    status: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ResumePendingTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        stage_recorder: StageRecorder,
        orchestration_engine: Optional[IOrchestrationEngine] = None,
        data_store_service: Any = None,
        file_service_client: Optional[IFileServiceClient] = None,
        orchestration_queue: Optional[IOrchestrationQueue] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        outbox_awaiter: Any = None,
        outbox_repo: Any = None,
        event_log_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._stage_recorder = stage_recorder
        self._orchestration_engine = orchestration_engine
        self._data_store_service = data_store_service
        self._file_client = file_service_client
        self._view_store = run_context_view_store
        self._queue = orchestration_queue if orchestration_queue is not None else getattr(getattr(self, "_engine", None) or getattr(self, "_orchestration_engine", None), "_queue", None)
        # Phase 3 Sub 3.4 — outbox-routed HITL resume. When all three
        # deps are wired (``OUTBOX_HITL_RESUME=on``) the producer
        # appends ``OutboxRequested`` envelopes for stage records and
        # queue enqueues; the dispatcher performs the side effects
        # asynchronously. Default ``None`` falls through to the
        # pre-Sub-3.4 direct-call path.
        self._outbox_awaiter = outbox_awaiter
        self._outbox_repo = outbox_repo
        self._outbox_event_log = event_log_repo

    @property
    def _outbox_enabled(self) -> bool:
        return (
            self._outbox_awaiter is not None
            and self._outbox_event_log is not None
        )

    def _emit_stage_record_outbox(
        self,
        *,
        run_id: str,
        op_index: int,
        stage_kwargs: dict[str, Any],
    ) -> bool:
        """Append ``OutboxRequested(kind="stage_record", ...)`` to the
        event log. Returns True on success, False if the append fails;
        callers fall back to a direct ``stage_recorder.record`` on False
        so the resume HTTP path keeps its 200 contract.
        """
        try:
            from app.layer1_domain.events import EventTopic
            from app.layer1_domain.value_objects.event_id import EventId
            from app.layer2_application.interfaces.event_log_repository_interface import (
                EventLogEnvelope,
            )

            outbox_id = EventId.derive_outbox(
                run_id, str(stage_kwargs.get("task_id") or ""), op_index, "stage_record",
            )
            if self._outbox_repo is not None and hasattr(self._outbox_repo, "mark_run"):
                self._outbox_repo.mark_run(run_id)
            envelope = EventLogEnvelope(
                event_id=EventId.derive(outbox_id, "requested"),
                run_id=run_id,
                topic=str(EventTopic.OUTBOX_REQUESTED),
                event_type="OutboxRequested",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "outbox_id": outbox_id,
                    "run_id": run_id,
                    "workflow_id": "",
                    "kind": "stage_record",
                    "payload": {"stage_kwargs": dict(stage_kwargs)},
                    "idempotency_key": outbox_id,
                },
                causation_id=str(stage_kwargs.get("task_id") or run_id),
            )
            self._outbox_event_log.append(envelope)
            return True
        except Exception as exc:
            _logger.warning(
                "outbox stage_record append failed for run=%s op=%d: %s — "
                "falling back to direct stage_recorder.record",
                run_id, op_index, exc,
            )
            return False

    def _emit_queue_enqueue_outbox(
        self,
        *,
        run_id: str,
        op_index: int,
        action_type: str,
        action_payload: dict[str, Any],
        causation_id: str = "",
    ) -> bool:
        try:
            from app.layer1_domain.events import EventTopic
            from app.layer1_domain.value_objects.event_id import EventId
            from app.layer2_application.interfaces.event_log_repository_interface import (
                EventLogEnvelope,
            )

            outbox_id = EventId.derive_outbox(
                run_id, action_type, op_index, "queue_enqueue",
            )
            if self._outbox_repo is not None and hasattr(self._outbox_repo, "mark_run"):
                self._outbox_repo.mark_run(run_id)
            envelope = EventLogEnvelope(
                event_id=EventId.derive(outbox_id, "requested"),
                run_id=run_id,
                topic=str(EventTopic.OUTBOX_REQUESTED),
                event_type="OutboxRequested",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={
                    "outbox_id": outbox_id,
                    "run_id": run_id,
                    "workflow_id": "",
                    "kind": "queue_enqueue",
                    "payload": {
                        "action_type": action_type,
                        "action_payload": dict(action_payload),
                    },
                    "idempotency_key": outbox_id,
                },
                causation_id=causation_id or run_id,
            )
            self._outbox_event_log.append(envelope)
            return True
        except Exception as exc:
            _logger.warning(
                "outbox queue_enqueue append failed for run=%s action=%s op=%d: %s — "
                "falling back to direct queue.enqueue",
                run_id, action_type, op_index, exc,
            )
            return False

    @staticmethod
    def _is_recoverable_swept_task(task: Any) -> bool:
        """Return True if this task was cancelled by the historical child
        timeout sweeper. Such tasks are CANCELLED and carry
        ``output_metadata={"reason": "parent_timeout"}``.
        """
        if task.status != TaskStatus.CANCELLED:
            return False
        meta = task.output_metadata or {}
        return isinstance(meta, dict) and meta.get("reason") == "parent_timeout"

    async def _rehydrate_swept_run(self, run: Any, task: Any) -> None:
        """Reverse the side-effects of a previous sweep so the run+task can
        re-enter the normal PENDING resume flow.

        - Run: FAILED → PENDING (cleared error/completed_at). The next branch
          flips it to RUNNING and publishes RunResumed.
        - Task: CANCELLED → PENDING (cleared completed_at; drop parent_timeout
          marker).
        - Parent tracking task (if any): roll back its CANCELLED/parent_timeout
          state so the parent can keep waiting for the child.
        """
        # Reset the target task back to PENDING.
        task.status = TaskStatus.PENDING
        task.completed_at = None
        if isinstance(task.output_metadata, dict) and task.output_metadata.get("reason") == "parent_timeout":
            task.output_metadata = {
                k: v for k, v in task.output_metadata.items() if k != "reason"
            }
        await self.task_repo.asave(task)

        # Reset run status (clear FAILED bookkeeping).
        run.status = RunStatus.PENDING
        run.error = None
        run.completed_at = None

        def _apply_rehydrated(r: Any) -> None:
            r.status = RunStatus.PENDING
            r.error = None
            r.completed_at = None

        await save_run_with_cas_retry(self.run_repo, run.id, _apply_rehydrated)

        self._stage_recorder.record(
            run_id=run.id,
            node_id=task.node_id,
            task_id=task.id,
            event_type="run.reopened",
            from_status=RunStatus.FAILED.value,
            to_status=RunStatus.PENDING.value,
            metadata={"reason": "parent_timeout_recovery"},
        )

        # Reset the parent's tracking task so the parent can wait for this
        # child again instead of being stuck in its previous failed state.
        if run.parent_run_id and run.parent_node_id and self._orchestration_engine is not None:
            tracking_task = await self._orchestration_engine._find_tracking_task(
                run.parent_run_id, run.parent_node_id,
            )
            if tracking_task is not None and self._is_recoverable_swept_task(tracking_task):
                tracking_task.status = TaskStatus.RUNNING
                tracking_task.completed_at = None
                tracking_task.error = None
                if isinstance(tracking_task.output_metadata, dict):
                    tracking_task.output_metadata = {
                        k: v for k, v in tracking_task.output_metadata.items()
                        if k != "reason"
                    }
                await self.task_repo.asave(tracking_task)

            # Also clear the FAILED state on the parent run if it was failed
            # because of this child.
            parent_run = await self.run_repo.aget_by_id(run.parent_run_id)
            if parent_run and parent_run.status == RunStatus.FAILED:
                parent_run.status = RunStatus.RUNNING
                parent_run.error = None
                parent_run.completed_at = None

                def _apply_parent_unfreeze(r: Any) -> None:
                    r.status = RunStatus.RUNNING
                    r.error = None
                    r.completed_at = None

                await save_run_with_cas_retry(
                    self.run_repo, parent_run.id, _apply_parent_unfreeze,
                )

    async def execute(self, command: ResumePendingTaskCommand) -> ResumePendingTaskResult:
        self.logger.info("Resuming pending task", task_id=command.task_id, run_id=command.run_id)

        # 1. Validate run
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return ResumePendingTaskResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        current_status = read_authoritative_status(run, self._view_store)
        # FAILED is allowed through here so the swept-task recovery path below
        # (``run.status == RunStatus.FAILED``) can run — it re-opens runs left
        # FAILED by the removed child-timeout sweeper. A FAILED run whose task
        # is NOT a recoverable parent_timeout cancellation is rejected at the
        # task level with INVALID_TASK_STATUS, not here.
        if current_status not in (
            RunStatus.RUNNING.value,
            RunStatus.PENDING.value,
            RunStatus.FAILED.value,
        ):
            return ResumePendingTaskResult(
                error=ErrorCode.INVALID_RUN_STATUS.default_message,
                error_code=ErrorCode.INVALID_RUN_STATUS.code,
                error_details={"run_id": command.run_id, "current_status": current_status},
            )

        # Track whether run was PENDING so we can publish RunResumed later
        was_pending = current_status == RunStatus.PENDING.value
        if was_pending:
            # Phase 3b.2c: ``RunResumed`` (emitted below via
            # ``emit_run_resumed``) is authoritative — projector
            # rebuilds status from the event. Aggregate write removed.
            run_resumed_kwargs = dict(
                run_id=command.run_id,
                event_type="run.resumed",
                from_status=RunStatus.PENDING.value,
                to_status=RunStatus.RUNNING.value,
            )
            if not (
                self._outbox_enabled
                and self._emit_stage_record_outbox(
                    run_id=command.run_id,
                    op_index=0,
                    stage_kwargs=run_resumed_kwargs,
                )
            ):
                self._stage_recorder.record(**run_resumed_kwargs)

        # 2. Validate task
        task = await self.task_repo.aget_by_id(command.task_id)
        if not task:
            return ResumePendingTaskResult(
                error=ErrorCode.TASK_NOT_FOUND.default_message,
                error_code=ErrorCode.TASK_NOT_FOUND.code,
                error_details={"task_id": command.task_id},
            )

        if task.run_id != command.run_id:
            return ResumePendingTaskResult(
                error=ErrorCode.TASK_RUN_MISMATCH.default_message,
                error_code=ErrorCode.TASK_RUN_MISMATCH.code,
                error_details={"task_id": command.task_id, "run_id": command.run_id},
            )

        # Re-open historical FAILED runs that were swept by the (now-removed)
        # child-workflow timeout sweeper. Such runs have a CANCELLED task
        # tagged with output_metadata={"reason": "parent_timeout"}. Reset the
        # task and run state so the normal PENDING resume path applies.
        if run.status == RunStatus.FAILED:
            if not self._is_recoverable_swept_task(task):
                return ResumePendingTaskResult(
                    error=ErrorCode.INVALID_TASK_STATUS.default_message,
                    error_code=ErrorCode.INVALID_TASK_STATUS.code,
                    error_details={
                        "task_id": command.task_id,
                        "current_status": task.status.value,
                        "reason": "run is FAILED and task is not a recoverable parent_timeout cancellation",
                    },
                )
            await self._rehydrate_swept_run(run, task)

        # Track whether run was PENDING so we can publish RunResumed later
        was_pending = run.status == RunStatus.PENDING
        if was_pending:
            run.status = RunStatus.RUNNING
            await self.run_repo.asave(run)
            self._stage_recorder.record(
                run_id=command.run_id,
                event_type="run.resumed",
                from_status=RunStatus.PENDING.value,
                to_status=RunStatus.RUNNING.value,
            )

        if task.status != TaskStatus.PENDING:
            return ResumePendingTaskResult(
                error=ErrorCode.INVALID_TASK_STATUS.default_message,
                error_code=ErrorCode.INVALID_TASK_STATUS.code,
                error_details={"task_id": command.task_id, "current_status": task.status.value},
            )

        # 3. Get workflow for dispatch metadata
        workflow = self._orchestration_engine.load_workflow_for_run(run)
        if not workflow:
            return ResumePendingTaskResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": run.workflow_id},
            )

        node = workflow.get_node(task.node_id)

        # Route based on node type
        if node and node.node_type in PENDING_NODE_TYPES:
            return await self._resume_human_interaction_task(command, run, task, workflow, node, was_pending)

        # 4. Merge updated_input into resolved_input
        if command.updated_input:
            if task.resolved_input is None:
                task.resolved_input = {}
            task.resolved_input.update(command.updated_input)

        # 5. Transition task to RUNNING
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc).isoformat()
        await self.task_repo.asave(task)

        # 6. Record stage event
        running_stage_kwargs = dict(
            run_id=command.run_id,
            node_id=task.node_id,
            task_id=command.task_id,
            event_type="task.resumed",
            from_status=TaskStatus.PENDING.value,
            to_status=TaskStatus.RUNNING.value,
            metadata={"worker_type": task.worker_type or ""},
        )
        if not (
            self._outbox_enabled
            and self._emit_stage_record_outbox(
                run_id=command.run_id,
                op_index=1,
                stage_kwargs=running_stage_kwargs,
            )
        ):
            self._stage_recorder.record(**running_stage_kwargs)

        # 7. Publish node.dispatched event (resume)
        if self._orchestration_engine:
            self._orchestration_engine.emit_node_dispatched(
                task, run, node, dispatch_reason="resume",
            )

        # 9. Publish RunResumed if run transitioned from PENDING
        if was_pending and self._orchestration_engine:
            self._orchestration_engine.emit_run_status(run, "resumed")

        return ResumePendingTaskResult(
            resumed=True,
            task_id=command.task_id,
            status=TaskStatus.RUNNING.value,
        )

    async def _resume_human_interaction_task(
        self,
        command: ResumePendingTaskCommand,
        run: Any,
        task: Any,
        workflow: Any,
        node: Any,
        was_pending: bool = False,
    ) -> ResumePendingTaskResult:
        """Resume an INPUT or HUMAN_ACTION node: complete immediately and process successors."""
        # The file-backed working ref this node was provisioned with (root
        # CSV + any table artifacts). Captured below before it is
        # materialised into a dict, so the finalised node output can recover
        # a table artifact that ``store_data`` would otherwise drop when empty.
        working_file_ref: Any = None
        # 0. For node types with a pre-provisioned file-backed working
        #    copy (INPUT → form fields; HUMAN_ACTION → ``_action``;
        #    FILE_UPLOAD → uploaded metadata columns), pull the PATCH
        #    /data writes out of the root CSV and into
        #    ``task.resolved_input`` so the type-specific extraction
        #    logic below sees them. Without this, fields PATCHed into
        #    __root__ never reach submitted_data — INPUT submits its
        #    empty dispatch-time provisioned ref, HUMAN_ACTION silently
        #    defaults to "confirm", and FILE_UPLOAD's ``raw_file`` lookup
        #    misses.
        if (
            node.node_type in (
                NodeType.INPUT, NodeType.HUMAN_ACTION, NodeType.FILE_UPLOAD,
            )
            and self._data_store_service is not None
        ):
            from app.layer1_domain.entities.data_reference import is_file_ref as _is_file_ref
            working: Any = None
            if self._view_store is not None:
                view = self._view_store.get(run.id)
                if view is not None:
                    working = view.variables.get(node.id)
            if not _is_file_ref(working):
                working = task.resolved_input
            if _is_file_ref(working):
                working_file_ref = working
                try:
                    # SA-1942 — size-guarded. A HITL working copy (form fields /
                    # action / upload metadata) is small; the guard only trips on
                    # a pathologically large one, and the existing except turns
                    # that into a graceful skip (submitted-field extraction is
                    # bypassed) instead of OOMing on resume.
                    materialized = await self._data_store_service.materialize_guarded(working)
                except MaterializeTooLargeError as exc:
                    # Not silent: a too-large working copy means the submission is
                    # resumed WITHOUT its extracted fields — surface it so the
                    # empty/default submit is diagnosable, not a mystery.
                    _logger.warning(
                        "resume: working copy for node %s too large to materialize "
                        "(%s); resuming without extracted submitted fields",
                        node.id, exc,
                    )
                    materialized = None
                except Exception:
                    materialized = None
                if isinstance(materialized, dict):
                    task.resolved_input = materialized

        # 0b. For DATA_EDIT: keep the file_ref shape end-to-end. Swap
        #    ``task.resolved_input`` to the latest working ref from the
        #    projector view so the post-PATCH file_id (with the user's
        #    edits) flows into the node output. We do NOT materialize
        #    into a dict — the file_ref *is* the node output, and the
        #    ``is_file_ref(node_output)`` short-circuit below will skip
        #    re-storing it as a single-row CSV.
        if node.node_type in _DATA_EDIT_LIKE and self._view_store is not None:
            from app.layer1_domain.entities.data_reference import is_file_ref as _is_file_ref
            view = self._view_store.get(run.id)
            if view is not None:
                latest = view.variables.get(node.id)
                if _is_file_ref(latest):
                    task.resolved_input = latest
        # 0. Read-only guard for DATA_EDIT — silently strip any row-data
        # mutation (the ``table_data`` array) so the resume still
        # succeeds. Control fields (e.g. ``_action``) pass through. We
        # log the drop so a caller submitting against a readonly node
        # is observable in the logs without failing the workflow.
        if (
            node.node_type in _DATA_EDIT_LIKE
            and bool(node.config.get("readonly", False))
            and isinstance(command.updated_input, dict)
            and "table_data" in command.updated_input
        ):
            self.logger.warning(
                "Dropping table_data from readonly DATA_EDIT resume",
                node_id=node.id, task_id=command.task_id,
            )
            command.updated_input = {
                k: v for k, v in command.updated_input.items()
                if k != "table_data"
            }

        # 1. Merge updated_input into resolved_input
        if command.updated_input:
            if task.resolved_input is None:
                task.resolved_input = {}
            task.resolved_input.update(command.updated_input)

        # 1b. Coerce submitted values against input_schema outputType
        input_schema = (task.output_metadata or {}).get("input_schema", [])
        if isinstance(input_schema, list) and task.resolved_input:
            type_map = _extract_type_map(input_schema)
            if type_map:
                from app.layer1_domain.node_spec import coerce_to_type
                for key, target_type in type_map.items():
                    if key in task.resolved_input:
                        task.resolved_input[key] = coerce_to_type(
                            task.resolved_input[key], target_type,
                        )

        # 2. For HUMAN_ACTION: extract action (default to "confirm") and
        #    classify outgoing edges into taken vs not-taken.
        #
        # ``_CONFIRM_PORTS`` / ``_REJECT_PORTS`` are the synonyms the engine
        # recognises for the two HA branches; either ``edge.condition`` OR
        # ``edge.source_port_id`` may carry the label depending on whether
        # the workflow was authored against the legacy ``condition=`` syntax
        # or the port-id syntax.
        _CONFIRM_PORTS = frozenset({"confirm", "success", "true_branch", "then", "true"})
        _REJECT_PORTS = frozenset({"reject", "failure", "false_branch", "else", "false"})
        action = "confirm"
        # Populated only for HUMAN_ACTION; other pending types fall through
        # with ``output_port`` = their spec out-port (``out_default``) and an
        # empty taken-edge map.
        ha_taken_targets: set[str] = set()
        ha_skipped_targets: set[str] = set()
        ha_taken_edge_ports: dict[str, str] = {}
        if node.node_type == NodeType.HUMAN_ACTION:
            topology = run.context.topology
            outgoing_edges = topology.edge_map.get(node.id, [])
            action = (task.resolved_input or {}).pop("_action", "")
            # Normalize: "Confirm" / " REJECT " etc. must classify the same
            # as the canonical lowercase labels — a non-matching action is
            # treated as reject by the ``== "confirm"`` check below, which
            # would silently route the wrong branch.
            action = action.strip().lower() if isinstance(action, str) else ""
            # If _action was not explicitly provided, infer from other keys or
            # outgoing edge port IDs.  The frontend may send {"reject": true} or
            # {"confirm": true} instead of {"_action": "reject"}.
            if not action:
                reject_ports = {
                    (e.source_port_id or "").lower()
                    for e in outgoing_edges
                    if (e.source_port_id or "").lower() in _REJECT_PORTS
                }
                resolved = task.resolved_input or {}
                for port_name in reject_ports:
                    if resolved.get(port_name):
                        action = "reject"
                        break
                if not action:
                    action = "confirm"
            # Classify edges: explicit opposite-branch edges are skipped;
            # everything else (matching port, unrecognised, or unconditional)
            # is taken so a single-output HA always continues.
            for edge in outgoing_edges:
                edge_condition = (edge.condition or "").lower()
                port_id = (edge.source_port_id or "").lower()
                if action == "confirm":
                    is_opposite = edge_condition in _REJECT_PORTS or port_id in _REJECT_PORTS
                else:
                    is_opposite = edge_condition in _CONFIRM_PORTS or port_id in _CONFIRM_PORTS
                if is_opposite:
                    ha_skipped_targets.add(edge.target_node_id)
                else:
                    ha_taken_targets.add(edge.target_node_id)
                    # ``taken_edge_ports`` drives DispatcherConsumer._emit_branching:
                    # carry the edge's own port_id when present, otherwise stamp
                    # the action itself so EdgeTraversed has a meaningful port.
                    ha_taken_edge_ports[edge.id] = edge.source_port_id or action

        # 3. Mark task COMPLETED (no worker dispatch)
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.now(timezone.utc).isoformat()

        # 4. Store submitted data in run.context.variables[node_id]
        # Store unwrapped — matches TASK/FILE_UPLOAD pattern. Array-of-dict
        # fields in submitted_data get split into artifact CSVs automatically
        # via DataStoreService.store_data(). Legacy expressions using
        # `.output.field` and `.metadata.action` are supported via backward-
        # compat path rewrite in VariableResolver.
        submitted_data = dict(task.resolved_input or {})
        if node.node_type == NodeType.HUMAN_ACTION:
            submitted_data["_action"] = action
        node_output: Any = submitted_data

        # NOTE: the former ``{node.id}_input`` CSV upload was removed here — it
        # set ``task.output_metadata["input_file_id"]`` which nothing in the
        # codebase reads, so it cost one redundant file-service upload of the
        # same submitted values on every submit. The canonical node output is
        # stored once below; the user-edited working copy already exists from
        # the prior ``PATCH /data`` call.

        # For FILE_UPLOAD: store upload metadata as node output (not the raw file data).
        # The actual file is attached as an artifact for $artifacts resolution.
        _upload_artifact_info = None
        if node.node_type == NodeType.FILE_UPLOAD:
            raw_file = submitted_data.get("file", "")
            # Handle both formats:
            #   flat:   {"file": "<file_id>", "file_name": "...", "content_type": "...", "file_size": 123}
            #   nested: {"file": {"file_id": "...", "filename": "...", "content_type": "...", "size_bytes": 123}}
            if isinstance(raw_file, dict):
                uploaded_file_id = raw_file.get("file_id", "")
                upload_content_type = raw_file.get("content_type", "")
                upload_file_name = raw_file.get("filename") or raw_file.get("file_name", "uploaded_file")
                upload_size_bytes = int(raw_file.get("size_bytes", 0) or raw_file.get("file_size", 0) or 0)
            else:
                uploaded_file_id = raw_file
                upload_content_type = submitted_data.get("content_type", "")
                upload_file_name = submitted_data.get("file_name", "uploaded_file")
                upload_size_bytes = int(submitted_data.get("file_size", 0) or 0)
            artifact_name = node.config.get("artifact_key", "") or "uploaded_file"
            if uploaded_file_id:
                # Query file service for headers and row_count
                file_headers: list[str] = []
                file_row_count = 0
                if self._file_client:
                    try:
                        qr = await self._file_client.query_file_data(uploaded_file_id, {"$top": "1"})
                        file_row_count = qr.get("total_matches", 0)
                        rows = qr.get("data", [])
                        if rows:
                            file_headers = sorted(k for k in rows[0].keys() if k != "_row_index")
                    except Exception:
                        self.logger.warning("Failed to fetch file metadata for %s", uploaded_file_id)
                # Node output = upload metadata (columns show metadata fields, not file data)
                node_output = {
                    "file_id": uploaded_file_id,
                    "filename": upload_file_name,
                    "content_type": upload_content_type,
                    "size_bytes": upload_size_bytes,
                    "row_count": file_row_count,
                    "columns": file_headers,
                    "col_count": len(file_headers),
                }
                _upload_artifact_info = {
                    "file_id": uploaded_file_id,
                    "name": artifact_name,
                    "content_type": upload_content_type,
                    "size_bytes": upload_size_bytes,
                    "row_count": file_row_count,
                }

        # Empty table fields (``[]``) would be collapsed into a scalar root
        # column by ``store_data`` — drop them here so the root CSV stays
        # clean, then re-graft the real (file-backed) table artifact from the
        # working ref after storage. INPUT/HUMAN_ACTION only: FILE_UPLOAD
        # rebuilt ``node_output`` as a metadata dict above, and DATA_EDIT keeps
        # the file_ref shape and never reaches this branch.
        if (
            node.node_type in (NodeType.INPUT, NodeType.HUMAN_ACTION)
            and isinstance(node_output, dict)
        ):
            for _k in list(_array_artifact_names(working_file_ref)):
                _v = node_output.get(_k)
                if isinstance(_v, list) and not _v:
                    node_output.pop(_k, None)

        # Store as CSV file via DataStoreService
        if self._data_store_service:
            from app.layer1_domain.entities.data_reference import is_file_ref
            if not is_file_ref(node_output):
                node_output = await self._data_store_service.store_data(node.id, node_output)

        # Restore any file-backed table artifact that was empty at submit time
        # so a later ``PATCH /data?artifact_id=<table>`` still targets it.
        if node.node_type in (NodeType.INPUT, NodeType.HUMAN_ACTION):
            _regraft_dropped_array_artifacts(node_output, working_file_ref)

        # For FILE_UPLOAD: attach the uploaded file as an artifact on the stored file_ref
        if _upload_artifact_info is not None:
            from app.layer1_domain.entities.data_reference import add_artifact as _add_art, is_file_ref as _is_ref
            if _is_ref(node_output):
                art = _add_art(
                    node_output,
                    file_id=_upload_artifact_info["file_id"],
                    name=_upload_artifact_info["name"],
                    content_type=_upload_artifact_info["content_type"],
                    size_bytes=_upload_artifact_info["size_bytes"],
                    artifact_type="upload",
                )
                art["row_count"] = _upload_artifact_info["row_count"]

        # For DATA_EDIT: alias the table_data array_field artifact under the
        # configured artifact_key so $artifacts.<artifact_key> resolves to
        # the edited rows. artifact_key is read from config/input_binding
        # (matching what data_edit_handler reads for the input side).
        if node.node_type in _DATA_EDIT_LIKE:
            from app.layer2_application.node_handlers.data_edit_handler import _raw_fields
            from app.layer1_domain.entities.data_reference import add_artifact as _add_art, is_file_ref as _is_ref
            # ``_raw_fields`` reads both homes: DATA_EDIT carries artifact_key
            # nested in ``input_binding`` (the data_edit_tabs container), while
            # TEMPLATE_DATA_EDIT — which declares no form_tabs_key — lands it as
            # a flat declared field in ``node.config``.
            cfg = _raw_fields(node)
            artifact_key = cfg.get("artifact_key", "")
            if artifact_key and _is_ref(node_output):
                table_artifact = next(
                    (a for a in node_output.get("artifacts", [])
                     if a.get("name") == "table_data"),
                    None,
                )
                already_aliased = any(
                    a.get("name") == artifact_key
                    for a in node_output.get("artifacts", [])
                )
                if table_artifact and not already_aliased:
                    alias = _add_art(
                        node_output,
                        file_id=table_artifact["file_id"],
                        name=artifact_key,
                        content_type=table_artifact.get("content_type", "text/csv"),
                        size_bytes=table_artifact.get("size_bytes", 0),
                        artifact_type=table_artifact.get("artifact_type", "array_field"),
                    )
                    if "row_count" in table_artifact:
                        alias["row_count"] = table_artifact["row_count"]

        # Phase 3b.2b: route the pending-node output through
        # ``apply_state_updates_as_files`` — emits ``NodeOutputProduced``
        # so the projector rebuilds ``variables`` from the event log.
        # Alias propagation (node.name) is handled inside the helper.
        # The handler-specific completion event (``InputSubmitted`` /
        # ``HumanActionCompleted`` / ``DataEditCompleted``) still fires
        # downstream via ``emit_pending_node_completed`` for consumers
        # that need the semantic distinction — only the variables
        # write-set carries through ``NodeOutputProduced``.
        # Non-HUMAN_ACTION pending nodes (INPUT / DATA_EDIT / FILE_UPLOAD) are
        # NOT port-routed; they continue via their single declared out-port
        # (``out_default`` per NodeSpec), which the resume-router
        # (process_successors) treats as the "traverse normally" sentinel.
        # Reporting the worker convention "success" here made compute_fanout's
        # port filter drop the node's real ``out_default`` edge — pruning the
        # successor and completing the run after the first node. (Latent until
        # the NodeSpec move made edge ``source_port_id`` explicit; port-less
        # edges had masked it.)
        if node.node_type == NodeType.HUMAN_ACTION:
            _completion_port = action
        else:
            from app.layer1_domain.node_spec import get_spec as _get_spec
            _spec_out_ports = _get_spec(node.node_type).out_ports
            _completion_port = _spec_out_ports[0].id if _spec_out_ports else "out_default"

        if self._orchestration_engine:
            # HUMAN_ACTION carries its branch decision through
            # ``taken_edge_ports`` so the DispatcherConsumer's
            # ``_emit_branching`` path emits EdgeTraversed only for the
            # taken edge. Without this, the dispatcher port-filters
            # against ``output_port="success"`` and drops port-id'd
            # confirm/reject edges, leaving the run with no fan-out.
            # The action itself rides on ``output_port`` so the
            # EdgeTraversed source_port_id is meaningful when edges
            # carry no source_port_id (legacy ``condition=`` shape).
            await self._orchestration_engine.apply_state_updates_as_files(
                {node.id: node_output},
                run,
                workflow,
                source_node_id=node.id,
                causation_id=task.id,
                output_port=_completion_port,
                taken_edge_ports=ha_taken_edge_ports or None,
                attempt_id=task.idempotency_key or "",
            )
        # Register named artifacts into global $artifacts namespace
        from app.layer1_domain.entities.data_reference import register_artifacts
        register_artifacts(
            run.context.variables,
            node_output,
            node_id=node.id,
            task_id=task.id,
        )

        # 5. Add to completed_nodes
        run.context.completed_nodes.add(node.id)

        # 6. For HUMAN_ACTION: propagate the skip downstream so the engine-
        #    side ``compute_fanout`` (which has no port awareness for HA)
        #    doesn't dispatch the opposite branch. Edge classification was
        #    done above where ``ha_taken_edge_ports`` was built.
        if node.node_type == NodeType.HUMAN_ACTION:
            topology = run.context.topology
            # Don't skip a target that is ALSO reachable via a taken edge.
            skipped_targets = ha_skipped_targets - ha_taken_targets
            accumulated_skipped = set(run.context.skipped_nodes)
            live_sources = (
                run.context.completed_nodes
                | run.context.triggered_nodes
                | set(topology.entry_nodes)
                | ha_taken_targets
            ) - run.context.skipped_nodes
            for target_id in skipped_targets:
                downstream = topology.downstream_to_skip(target_id, accumulated_skipped, live_sources,
                )
                for sid in downstream:
                    run.context.skipped_nodes.add(sid)
                    if self._orchestration_engine:
                        self._orchestration_engine.emit_node_cancelled(
                            run, node_id=sid, reason="branch_not_taken",
                        )
                accumulated_skipped.update(downstream)

        await self.task_repo.asave(task)
        await self.run_repo.asave(run)

        # 7. Record stage event
        completed_stage_kwargs = dict(
            run_id=command.run_id,
            node_id=task.node_id,
            task_id=command.task_id,
            event_type="task.resumed",
            from_status=TaskStatus.PENDING.value,
            to_status=TaskStatus.COMPLETED.value,
            metadata={"node_type": node.node_type.value, "action": action},
        )
        if not (
            self._outbox_enabled
            and self._emit_stage_record_outbox(
                run_id=command.run_id,
                op_index=2,
                stage_kwargs=completed_stage_kwargs,
            )
        ):
            self._stage_recorder.record(**completed_stage_kwargs)

        # 8. Publish NODE_COMPLETED + secondary topic (INPUT_SUBMITTED, etc.)
        #    BEFORE enqueueing the ResumeTaskAction. ``queue.enqueue`` is an
        #    await point — the orchestration queue runner is a separate
        #    coroutine awaiting ``dequeue`` and can resume at that yield,
        #    immediately processing the action and publishing successor
        #    NodeDispatched events. If we published NodeCompleted after
        #    enqueue, the parent's terminal event would stamp a higher
        #    per-run ``_run_seq`` than its successors', breaking frontend
        #    node-status rendering (same shape as the _exec_uniform fix).
        if self._orchestration_engine:
            self._orchestration_engine.emit_pending_node_completed(task, run, node, action)

        # Publish RunResumed if run transitioned from PENDING — also
        # before the await so the resumption is observed before any
        # successor activity.
        if was_pending and self._orchestration_engine:
            self._orchestration_engine.emit_run_status(run, "resumed")

        # 9. Enqueue successor processing for async dispatch
        resume_action_payload = dict(
            run_id=command.run_id,
            task_id=command.task_id,
            test_mode=getattr(run, "test_mode", None),
        )
        if not (
            self._outbox_enabled
            and self._emit_queue_enqueue_outbox(
                run_id=command.run_id,
                op_index=3,
                action_type="ResumeTaskAction",
                action_payload=resume_action_payload,
                causation_id=command.task_id,
            )
        ):
            await self._queue.enqueue(ResumeTaskAction(**resume_action_payload))

        return ResumePendingTaskResult(
            resumed=True,
            task_id=command.task_id,
            status=TaskStatus.COMPLETED.value,
        )

