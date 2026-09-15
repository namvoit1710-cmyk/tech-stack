"""Row-level atomic edit of a node's working-copy ``__file_ref`` tree.

Serves ``PATCH /runs/{run_id}/nodes/{node_id}/data``. Contract:

- Body carries ``artifacts = [{artifact_id, operations: [...]}, ...]``. Each
  entry targets a single artifact (``__root__`` for the node's object CSV
  of scalars, a table's artifact id for row-level edits), and every op is
  applied **atomically** across all artifacts in order.
- Per-op ``{operation: insert|edit|delete, row_id, data}`` row-level
  mutations.
- Object artifacts (root CSV) hold one row, so they accept ``insert`` and
  ``edit`` interchangeably — both upsert the single row in place.
  ``delete`` and column ops are rejected for object artifacts. Array
  artifacts accept all three row ops and are forwarded to the data-factory
  row-transform service.
- Requires ``If-Match`` (409 if absent, 412 on stale ETag).
- On any failure nothing is committed, newly-uploaded file_ids are orphan-GC'd.
- On success emits ``NodeDataEdited`` so the projector rebuilds
  ``run.context.variables`` to point at the new file_ids.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer1_domain.entities.data_reference import (
    refresh_schema_hash,
    ROOT_ARTIFACT_ID,
    build_file_ref,
    drop_arrow_sidecar,
    find_artifact,
    is_file_ref,
)
from app.layer1_domain.node_spec.model import NodeRuntime
from app.layer1_domain.node_spec.registry import node_types_for_runtime
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.csv_document import CsvDocument, ROW_ID_COLUMN
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    VALUE_COLUMN,
    reconcile_after_edit,
    uses_value_column,
)
from app.layer1_domain.exceptions import (
    UnsupportedNodeTypeError,
    WorkflowSnapshotMissingError,
)
from app.layer2_application.read.node_data_projection import (
    find_latest_task_for_node,
)
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer1_domain.value_objects.node_input_etag import (
    NodeInputEtag,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.app_config_interface import IAppConfig
from app.layer2_application.interfaces.node_data_factory_service_interface import (
    INodeDataFactoryService,
    RowIdentifier,
    RowOperation as FactoryRowOperation,
)
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository

_logger = logging.getLogger(__name__)

# Columns the row-transform pipeline owns and the user must never mutate.
# ``__row_id`` is the synthetic primary key minted on insert; ``__parent_row_id``
# threads nested array artifacts back to their parent. Dropping or renaming
# either silently corrupts the working ref.
_RESERVED_COLUMNS = frozenset({"__row_id", "__parent_row_id"})

_COLUMN_OP_KINDS = frozenset({"add_column", "drop_column", "rename_column"})
_ROW_OP_KINDS = frozenset({"insert", "edit", "delete"})

# Op-error codes that represent a genuine processing/infrastructure failure
# (vs. a retryable client/validation/precondition error). Reaching one of
# these during PATCH marks the node + run FAILED rather than leaving the
# node PENDING for the FE to retry.
_TERMINAL_OP_ERROR_CODES = frozenset({
    "FACTORY_ERROR",
    "FACTORY_UNAVAILABLE",
    "FACTORY_BAD_RESPONSE",
    ErrorCode.INTERNAL_ERROR.code,
})

# Cancel-via-PATCH detection. The FE wires its "Cancel" button on the
# INPUT / FILE_UPLOAD / DATA_EDIT pending nodes to the PATCH /data
# endpoint with ``data._action`` ∈ {reject, cancel}; for those types
# ``_action`` is not a working-copy column, so the request is a cancel
# signal. HUMAN_ACTION is excluded by node type at the call site: its
# confirm/reject buttons PATCH a real ``_action`` column that POST
# /submit reads to pick the branch (see HumanActionNodeHandler), so a
# HUMAN_ACTION ``_action`` write must flow through the normal upsert
# path — never the cancel path.
_ACTION_KEY = "_action"
_CANCEL_VALUES = frozenset({"reject", "cancel"})
# Derived from the domain NodeSpec registry (runtime="pending"), not
# hand-listed — same single source of truth as the orchestrator's
# PENDING_NODE_TYPES. A new pending node type is picked up automatically.
_PENDING_NODE_TYPES_FOR_CANCEL = node_types_for_runtime(NodeRuntime.PENDING)


def _input_schema_keys(task: Any) -> frozenset[str]:
    """Top-level field keys declared on the task's auto-form input_schema.

    Empty when the task has no schema metadata. Used to decide whether
    ``_action`` in an inbound PATCH is a real form field (HUMAN_ACTION)
    or a cancel signal (INPUT / FILE_UPLOAD / DATA_EDIT).
    """
    metadata = getattr(task, "output_metadata", None) or {}
    schema = metadata.get("input_schema")
    if not isinstance(schema, list):
        return frozenset()
    return frozenset(
        f.get("key", "")
        for f in schema
        if isinstance(f, dict) and f.get("key")
    )


def _is_cancel_payload(
    artifacts: list[dict[str, Any]],
    schema_keys: frozenset[str],
) -> bool:
    """True iff the inbound PATCH is a cancel signal.

    Triggers when any op carries ``data._action`` ∈ {reject, cancel}.
    The HUMAN_ACTION exclusion is enforced by the caller via node type
    (a HUMAN_ACTION ``_action`` write is a real working-copy edit, not a
    cancel); the ``schema_keys`` guard here is a secondary safety net for
    any node type that declares ``_action`` as a real form field.
    """
    if _ACTION_KEY in schema_keys:
        return False
    for spec in artifacts or []:
        for op in spec.get("operations") or []:
            data = op.get("data") or {}
            if not isinstance(data, dict):
                continue
            value = data.get(_ACTION_KEY)
            if isinstance(value, str) and value.lower() in _CANCEL_VALUES:
                return True
    return False


# ---------------------------------------------------------------------------
# Command / Result
# ---------------------------------------------------------------------------

@dataclass
class EditNodeDataCommand:
    run_id: str = ""
    node_id: str = ""
    task_id: Optional[str] = None
    if_match: Optional[str] = None
    # Each entry: {"artifact_id": str,
    #              "operations": [{"operation": "insert"|"edit"|"delete",
    #                              "row_id": Optional[str],
    #                              "data": Optional[dict]}, ...]}
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OpResult:
    artifact_id: str
    op_index: int
    status: str                         # "ok" | "failed"
    rows_affected: Optional[int] = None
    row_ids: Optional[list[str]] = None
    error: Optional[dict[str, Any]] = None


@dataclass
class EditNodeDataResult:
    etag: str = ""
    version: int = 0
    file_lineage: dict[str, str] = field(default_factory=dict)
    results: list[OpResult] = field(default_factory=list)

    # Set when the FE's PATCH /data was a "cancel pending node" signal
    # (``data._action`` ∈ {"reject", "cancel"} on a node whose schema does
    # not declare ``_action``). The PATCH is short-circuited: no working-
    # copy mutation, no etag bump; the task is moved to FAILED with
    # ``error="user_cancelled"`` and routed via the engine's failure path.
    cancelled: bool = False

    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class EditNodeDataUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        run_context_view_store: IRunContextViewStore,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        file_service_client: IFileServiceClient,
        node_input_etag_calculator: NodeInputEtag,
        handler_registry: Optional[NodeHandlerRegistry] = None,
        node_data_factory_service: Optional[INodeDataFactoryService] = None,
        orchestration_engine: Any = None,
        # Phase 3 Sub 3.3 — optional outbox routing dependencies. When
        # all four are wired (set when ``OUTBOX_DATA_EDIT=on``) every
        # ``_upload`` and orphan-GC call is appended as
        # ``OutboxRequested`` and the use case awaits ``OutboxResolved``
        # before returning. When any is ``None`` the use case takes the
        # legacy direct-call path (today's behaviour).
        outbox_awaiter: Any = None,
        outbox_bytes_channel: Any = None,
        outbox_repo: Any = None,
        event_log_repo: Any = None,
        outbox_http_wait_timeout_s: float = 5.0,
        app_config: IAppConfig | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self._app_config = app_config
        self.run_repo = run_repo
        self._view_store = run_context_view_store
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._fs = file_service_client
        self._etag_calc = node_input_etag_calculator
        self._handler_registry = handler_registry
        self._factory = node_data_factory_service
        self._engine = orchestration_engine
        self._outbox_awaiter = outbox_awaiter
        self._outbox_bytes_channel = outbox_bytes_channel
        self._outbox_repo = outbox_repo
        self._outbox_event_log = event_log_repo
        self._outbox_wait_timeout_s = outbox_http_wait_timeout_s

    @property
    def _outbox_enabled(self) -> bool:
        """``True`` when every Sub 3.3 dependency was injected."""
        return (
            self._outbox_awaiter is not None
            and self._outbox_bytes_channel is not None
            and self._outbox_event_log is not None
        )

    # ------------------------------------------------------------------
    # Top-level orchestration
    # ------------------------------------------------------------------

    async def execute(self, cmd: EditNodeDataCommand) -> EditNodeDataResult:
        run = await self.run_repo.aget_by_id(cmd.run_id)
        if not run:
            return self._error(ErrorCode.RUN_NOT_FOUND, {"run_id": cmd.run_id})

        # Empty artifacts is a no-op — skip workflow / node / task / etag
        # work entirely so the endpoint stays resilient when the run's
        # snapshot is missing or the node is unresolvable.
        if not cmd.artifacts:
            return EditNodeDataResult(etag=cmd.if_match or "", version=0)

        try:
            workflow = self._engine.load_workflow_for_run(run)
        except WorkflowSnapshotMissingError:
            return self._error(
                ErrorCode.WORKFLOW_NOT_FOUND, {"workflow_id": run.workflow_id}
            )
        if not workflow:
            return self._error(
                ErrorCode.WORKFLOW_NOT_FOUND, {"workflow_id": run.workflow_id}
            )

        node = self._get_node(workflow, cmd.node_id)
        if node is None:
            return self._error(
                ErrorCode.NODE_NOT_FOUND,
                {"run_id": cmd.run_id, "node_id": cmd.node_id},
            )

        task = await self._resolve_task(cmd.run_id, cmd.node_id, cmd.task_id)

        # Cancel-via-PATCH short-circuit. Runs before working_ref / etag
        # so it works for FILE_UPLOAD too (which doesn't pre-provision a
        # root CSV under outputType=object). HUMAN_ACTION is excluded by
        # node type: its ``_action`` column is a real working-copy write
        # that POST /submit reads to pick the confirm/reject branch —
        # routing it to cancel would FAIL the task instead of taking the
        # reject edge.
        if (
            task is not None
            and node.node_type != NodeType.HUMAN_ACTION
            and _is_cancel_payload(cmd.artifacts, _input_schema_keys(task))
        ):
            return await self._cancel_pending_task(run, workflow, node, task)

        working_ref = self._current_working_ref(cmd.run_id, cmd.node_id, task)
        # Use the same SchemaProvider that GET /schema uses, so the
        # schema_version backing the etag stays coherent with what the
        # FE just fetched.
        schema_version = await self._compute_schema_version(
            node=node, task=task, run=run, working_ref=working_ref,
        )

        # If-Match check.
        current_etag = self._etag_calc.of(working_ref, schema_version=schema_version)
        if not cmd.if_match:
            return self._error(
                ErrorCode.PRECONDITION_REQUIRED,
                {"message": "If-Match header is required on PATCH /data"},
            )
        if not NodeInputEtag.matches_if_match(cmd.if_match, current_etag):
            return self._error(
                ErrorCode.PRECONDITION_FAILED,
                {"current_etag": current_etag},
            )

        # Apply every artifact's ops atomically on a deep-copied ref.
        new_ref = copy.deepcopy(working_ref) if is_file_ref(working_ref) else working_ref
        file_lineage: dict[str, str] = {}
        op_results: list[OpResult] = []
        newly_uploaded: list[str] = []

        try:
            for artifact_spec in cmd.artifacts:
                artifact_id = artifact_spec.get("artifact_id", "")
                operations = artifact_spec.get("operations") or []
                target = self._resolve_target(new_ref, artifact_id)
                if target is None:
                    raise _OpError(
                        ErrorCode.NOT_FOUND,
                        {"artifact_id": artifact_id},
                        f"artifact {artifact_id!r} not in node's working tree",
                        "ARTIFACT_NOT_FOUND",
                    )

                # Object vs array routing. Object artifacts (a node's
                # ``__root__`` scalar form, ``artifact_type == "object_field"``)
                # upsert their single row locally; table artifacts
                # (``array_field`` / ``nested_array_field``) forward to the
                # data-factory. The root is treated as an object unless it is
                # an explicit array type — this also covers the working-ref
                # fallback placeholder (``build_file_ref`` carries no
                # ``artifact_type``), which would otherwise route an
                # empty-file_id root to the factory and 400 with
                # "file_id is required".
                artifact_type = target.get("artifact_type") or ""
                is_root_artifact = not artifact_id or artifact_id == ROOT_ARTIFACT_ID
                is_array_artifact = artifact_type in ("array_field", "nested_array_field")
                if artifact_type == "object_field" or (
                    is_root_artifact and not is_array_artifact
                ):
                    op_results.extend(await self._apply_object_ops(
                        artifact_id, target, operations,
                        file_lineage, newly_uploaded,
                        run_id=run.id,
                        task_id=getattr(task, "id", "") if task is not None else "",
                    ))
                else:
                    op_results.extend(await self._apply_array_ops(
                        artifact_id, target, operations,
                        file_lineage, newly_uploaded,
                        run_id=run.id,
                        task_id=getattr(task, "id", "") if task is not None else "",
                    ))
        except _OpError as exc:
            await self._gc_orphans(newly_uploaded, run_id=run.id)
            # Terminal processing/infra failures (data-factory errors,
            # internal errors) mark the node + run FAILED; retryable
            # validation/precondition errors leave the node PENDING so the
            # FE can fix the input and retry.
            effective_code = exc.code_override or exc.error_code.code
            if effective_code in _TERMINAL_OP_ERROR_CODES and task is not None:
                await self._fail_pending_node(
                    run, workflow, node, task,
                    error_code=effective_code, error_msg=exc.message,
                )
            return self._error(
                exc.error_code, exc.details,
                message=exc.message,
                code_override=exc.code_override,
            )
        except Exception as exc:  # pragma: no cover — safety net
            _logger.exception("edit_node_data: unexpected failure")
            await self._gc_orphans(newly_uploaded, run_id=run.id)
            if task is not None:
                await self._fail_pending_node(
                    run, workflow, node, task,
                    error_code=ErrorCode.INTERNAL_ERROR.code, error_msg=str(exc),
                )
            return self._error(
                ErrorCode.INTERNAL_ERROR, {"message": str(exc)},
            )

        # Commit: bump version + compute new etag + emit event.
        # SA-1905 — the Arrow mirror is stale the moment any file_id in this
        # tree was swapped. Drop it here, at the single commit point, rather
        # than at each of the two swap sites: an edit to a child invalidates the
        # ROOT sidecar, and only this scope holds the root.
        drop_arrow_sidecar(new_ref)
        new_ref["version"] = (
            int(working_ref.get("version", 0) or 0) + 1
            if is_file_ref(working_ref) else 1
        )
        # Re-derive schema_version from the *post-PATCH* columns. Column
        # ops change the column set, which rewrites the schema and the
        # hash. Reusing the pre-PATCH schema_version would make the
        # returned etag disagree with what the next GET / PATCH computes,
        # silently breaking If-Match across consecutive PATCHes.
        post_schema_version = await self._compute_schema_version(
            node=node, task=task, run=run, working_ref=new_ref,
        )
        new_etag = self._etag_calc.of(new_ref, schema_version=post_schema_version)
        new_ref["etag"] = new_etag
        if self._engine:
            self._engine.emit_node_data_edited(
                run, workflow,
                node_id=cmd.node_id,
                new_ref=new_ref,
                task_id=getattr(task, "id", "") or (cmd.task_id or ""),
                edited_by="user",
                reason="edit_node_data",
            )

        # Files carried on task.resolved_input are the node's immutable
        # input (for DATA_EDIT, the upstream source the working copy was
        # seeded from). They belong to the upstream node's output, not
        # this node's working copy, and must never be GC'd — deleting one
        # dangles the upstream node's output and breaks reruns / any other
        # consumer of the same artifact. Only superseded working-copy
        # files (pre-images from *prior* PATCHes) and multi-op
        # intermediates are reclaimed.
        protected = set(_descendant_file_ids(getattr(task, "resolved_input", None)))

        # Orphan pre-image file_ids.
        pre_image = [
            old_fid for old_fid, new_fid in file_lineage.items()
            if old_fid and old_fid != new_fid and old_fid not in protected
        ]
        # Plus chained intermediates: every uploaded file_id that didn't
        # land as the *final* file_id in any artifact of the new ref.
        # Without this, multi-op PATCHes (e.g. add_column then insert)
        # leak every intermediate factory upload.
        final_ids = set(_descendant_file_ids(new_ref))
        intermediates = [
            fid for fid in newly_uploaded
            if fid and fid not in final_ids and fid not in protected
        ]
        await self._gc_orphans(pre_image + intermediates, run_id=run.id)

        return EditNodeDataResult(
            etag=new_etag,
            version=int(new_ref.get("version", 0) or 0),
            file_lineage=file_lineage,
            results=op_results,
        )

    # ------------------------------------------------------------------
    # Object artifact (root CSV): upsert-row-1 semantics
    # ------------------------------------------------------------------

    # Object artifacts hold exactly one row, so ``insert`` and ``edit`` are
    # semantically the same upsert against that single row. ``delete`` and
    # column ops still don't make sense (no row to address, and column
    # mutations would corrupt the declared scalar schema).
    _OBJECT_UPSERT_KINDS = frozenset({"insert", "edit"})

    async def _apply_object_ops(
        self,
        artifact_id: str,
        target: dict[str, Any],
        operations: list[dict[str, Any]],
        file_lineage: dict[str, str],
        newly_uploaded: list[str],
        *,
        run_id: str = "",
        task_id: str = "",
    ) -> list[OpResult]:
        results: list[OpResult] = []
        for idx, op in enumerate(operations):
            kind = op.get("operation")
            if kind not in self._OBJECT_UPSERT_KINDS:
                # ``delete`` and column ops have no meaning on a single-row
                # object artifact; reject uniformly so the FE handles them
                # the same as before.
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": idx, "operation": kind},
                    f"object artifact only accepts 'insert' or 'edit'; got {kind!r}",
                    "OBJECT_OP_NOT_SUPPORTED",
                )
            data = op.get("data") or {}
            self._guard_declared_kind(artifact_id, target, data, idx)
            old_file_id = target.get("file_id", "")
            content = await self._download(old_file_id) if old_file_id else b""
            mutated, row_id = _upsert_single_row(content, data)
            new_file_id = await self._upload(
                mutated, filename="root.csv",
                run_id=run_id, task_id=task_id, op_index=idx,
            )
            newly_uploaded.append(new_file_id)
            if old_file_id:
                file_lineage[old_file_id] = new_file_id
            target["file_id"] = new_file_id
            # Refresh the column metadata so an auto-expanded schema (a new key
            # not previously declared) actually surfaces: GET /data derives the
            # displayed columns from the ref, not the file bytes.
            target["columns"] = CsvDocument(mutated).list_columns()
            refresh_schema_hash(target)
            # SA-1977 A6 — the declaration must survive the edit. Promote an
            # object that has grown past one row (and refresh the row_count the
            # edit paths used to leave stale).
            reconcile_after_edit(target, len(CsvDocument(mutated).list_row_ids()))
            results.append(OpResult(
                artifact_id=artifact_id,
                op_index=idx, status="ok",
                rows_affected=1, row_ids=[row_id],
            ))
        return results

    @staticmethod
    def _guard_declared_kind(
        artifact_id: str, target: dict[str, Any], data: dict[str, Any], op_index: int,
    ) -> None:
        """Refuse an edit that would make the declared kind a LIE (SA-1977 A6).

        A ref stored in the reserved ``__value`` column — a scalar root, or an
        array whose elements are not objects (``[1,2,3]``, ``["a","b"]``) — holds
        its value in that ONE column. Adding any other column to it, which
        ``upsert_scalar_row`` does happily (it auto-expands the schema), leaves a
        ref that says "I am an array of numbers" pointing at a two-column table.
        Every reader trusts that declaration; ``materialize`` would stop
        unwrapping and hand back dicts where numbers were promised.

        There is no sensible promotion here (a number with an extra field is not
        an object — the extra field has nowhere to live), so this is the
        ``refused`` half of the A6 contract, with a typed, readable error.

        The predicate is ``uses_value_column`` — the DECLARATION — and not a scan
        of ``target["columns"]``. Guarding on the stored shape instead of the
        declared one is the same mistake that made ``_unwrap_value`` destroy a
        list of one-key dicts.
        """
        if not isinstance(data, dict) or not data:
            return
        if not uses_value_column(
            str(target.get("value_kind") or ""), str(target.get("item_kind") or ""),
        ):
            return
        foreign = [
            k for k in data
            if k not in _RESERVED_COLUMNS and k != VALUE_COLUMN
        ]
        if not foreign:
            return
        declared = target.get("value_kind")
        if declared == ARRAY:
            declared = f"array of {target.get('item_kind') or 'value'}"
        raise _OpError(
            ErrorCode.INPUT_VALIDATION_FAILED,
            {
                "artifact_id": artifact_id, "op_index": op_index,
                "value_kind": target.get("value_kind"),
                "item_kind": target.get("item_kind"),
                "columns": foreign,
            },
            f"this node's output is a {declared} — its value lives in the reserved "
            f"{VALUE_COLUMN!r} column, so it has no field(s) {foreign!r} to set. "
            f"Edit {VALUE_COLUMN!r} instead.",
            "VALUE_KIND_CONFLICT",
        )

    # ------------------------------------------------------------------
    # Array artifact (table): forward ops to data-factory
    # ------------------------------------------------------------------

    async def _apply_array_ops(
        self,
        artifact_id: str,
        target: dict[str, Any],
        operations: list[dict[str, Any]],
        file_lineage: dict[str, str],
        newly_uploaded: list[str],
        *,
        run_id: str = "",
        task_id: str = "",
    ) -> list[OpResult]:
        """Router for array-artifact ops.

        Row ops (``insert``/``edit``/``delete``) forward to the data-factory.
        Column ops (``add_column``/``drop_column``/``rename_column``) are
        applied in-process via ``csv_ops`` — the existing column primitives
        already handle empty-data CSVs, and a remote round-trip per header
        rewrite is wasteful.

        Both paths chain ``current_file_id`` so a single PATCH can interleave
        e.g. ``add_column`` then ``insert(new_col)``. Every successful
        intermediate file_id is appended to ``newly_uploaded`` so the
        outer commit step can GC the ones that didn't end up final.

        Row ops (data-factory v2) keep ``file_id`` stable and mint a new
        ``version_id`` each call; ``current_version_id`` is threaded back so
        the next row op reads the revision the previous one produced. Column
        ops fork a fresh ``file_id`` via the file service (no version), so
        they reset the pinned version to "" (subsequent reads use latest).
        """
        results: list[OpResult] = []
        current_file_id = target.get("file_id", "")
        starting_file_id = current_file_id
        current_version_id = target.get("version_id", "") or ""
        current_columns = list(target.get("columns") or [])
        # Live row count, used only to pick the insert anchor (``at_index`` when
        # the table may be empty — the factory rejects ``after`` index 0 on a
        # 0-row frame). ``None`` = not yet known; fetched lazily on the first
        # insert/edit that needs it, then kept current from each op's response.
        current_row_count: Optional[int] = None

        for idx, op in enumerate(operations):
            kind = op.get("operation")
            if kind in _ROW_OP_KINDS:
                if self._factory is None:
                    raise _OpError(
                        ErrorCode.INTERNAL_ERROR,
                        {"artifact_id": artifact_id},
                        "data-factory service not configured",
                        "FACTORY_NOT_CONFIGURED",
                    )
                # Auto-expand the schema: any key in the row data not yet in the
                # artifact's columns is created via the data-factory column
                # transform first, because the row endpoint rejects unknown
                # columns ("Column 'X' not found in dataframe"). ``delete``
                # carries no data, so it is skipped.
                if kind in ("insert", "edit"):
                    op_data = op.get("data")
                    missing = [
                        k for k in op_data
                        if k and k not in current_columns
                        and k not in _RESERVED_COLUMNS and k != ROW_ID_COLUMN
                    ] if isinstance(op_data, dict) else []
                    if missing:
                        current_file_id, current_version_id, current_row_count = (
                            await self._add_columns_via_factory(
                                artifact_id=artifact_id,
                                file_id=current_file_id,
                                version_id=current_version_id,
                                columns=missing,
                                op_index=idx,
                            )
                        )
                        current_columns = current_columns + missing
                        if current_file_id and current_file_id != starting_file_id:
                            newly_uploaded.append(current_file_id)
                # Decide the insert anchor. ``insert`` is unsafe (out of bounds)
                # when the table has 0 rows; ``edit`` (delete+insert) when it has
                # <=1 (the delete can empty a single-row table). Fetch the count
                # lazily once if a column add didn't already supply it.
                table_maybe_empty = False
                if kind in ("insert", "edit"):
                    if current_row_count is None:
                        current_row_count = await self._table_row_count(current_file_id)
                    threshold = 0 if kind == "insert" else 1
                    table_maybe_empty = current_row_count <= threshold
                current_file_id, current_version_id, current_row_count, op_result = (
                    await self._apply_row_op_via_factory(
                        artifact_id=artifact_id,
                        file_id=current_file_id,
                        version_id=current_version_id,
                        op=op,
                        op_index=idx,
                        table_maybe_empty=table_maybe_empty,
                    )
                )
            elif kind in _COLUMN_OP_KINDS:
                current_file_id, current_columns, op_result = await self._apply_column_op(
                    artifact_id=artifact_id,
                    file_id=current_file_id,
                    columns=current_columns,
                    op=op,
                    op_index=idx,
                    run_id=run_id,
                    task_id=task_id,
                )
                # Column op uploaded a new file; its latest version is the
                # upload itself — drop any pin so later reads use latest.
                current_version_id = ""
            else:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": idx, "operation": kind},
                    f"unknown operation: {kind!r}",
                    "OP_UNKNOWN",
                )

            if current_file_id and current_file_id != starting_file_id:
                newly_uploaded.append(current_file_id)
            results.append(op_result)

        if starting_file_id and starting_file_id != current_file_id:
            file_lineage[starting_file_id] = current_file_id
        target["file_id"] = current_file_id
        target["version_id"] = current_version_id
        target["columns"] = current_columns
        refresh_schema_hash(target)
        # SA-1977 A6 — deliberately NOT reconciling row_count from the factory here.
        #
        # An array artifact's declared kind cannot be falsified by a row op (an
        # array is an array at 0, 1 or N rows — it is never demoted), so there is
        # no kind work to do. That leaves only the row_count refresh, and
        # ``current_row_count`` comes from ``RowTransformResult.total_rows``, which
        # ``row_transform_factory_service`` fills with ``int(result.get("total_rows",
        # 0) or 0)`` — i.e. it DEFAULTS TO ZERO when the remote factory omits the
        # field. Writing that onto the ref would stamp ``row_count = 0`` on a table
        # that just gained a row, and ``_items_total`` reads ``row_count`` to size a
        # lazy LOOP — so the next loop over this artifact would run ZERO iterations.
        # That is the very silent-zero failure this Story exists to remove; a stale
        # row_count (the pre-existing behaviour) is strictly safer than a wrong one.
        return results

    async def _apply_row_op_via_factory(
        self,
        *,
        artifact_id: str,
        file_id: str,
        version_id: str,
        op: dict[str, Any],
        op_index: int,
        table_maybe_empty: bool = False,
    ) -> tuple[str, str, int, OpResult]:
        kind = op.get("operation")
        row_id = op.get("row_id")
        data = op.get("data") or {}
        factory_ops = _translate_to_factory(
            kind, row_id, data, artifact_id=artifact_id, op_index=op_index,
            table_maybe_empty=table_maybe_empty,
        )
        factory_result = await self._factory.apply_row_operations(
            file_id=file_id,
            operations=factory_ops,
            file_format="csv",
            version_id=version_id or None,
        )
        if not factory_result.success or not factory_result.file_id:
            raise _OpError(
                ErrorCode.INTERNAL_ERROR,
                {"artifact_id": artifact_id, "op_index": op_index,
                 "factory_error": factory_result.error_code or "FACTORY_ERROR"},
                factory_result.message or "data-factory call failed",
                factory_result.error_code or "FACTORY_ERROR",
            )
        return factory_result.file_id, factory_result.version_id, factory_result.total_rows, OpResult(
            artifact_id=artifact_id,
            op_index=op_index,
            status="ok",
            rows_affected=factory_result.affected_rows,
            row_ids=[row_id] if row_id else None,
        )

    async def _table_row_count(self, file_id: str) -> int:
        """Best-effort data-row count of a table CSV, used only to pick the
        insert anchor. Reads the latest content and counts rows; on any failure
        returns 0 → the always-safe ``at_index`` anchor, so a transient read
        never becomes a wrong-anchor 500.
        """
        if not file_id:
            return 0
        try:
            content = await self._download(file_id)
        except Exception:  # pragma: no cover — defensive
            return 0
        return len(CsvDocument(content).list_row_ids())

    async def _add_columns_via_factory(
        self,
        *,
        artifact_id: str,
        file_id: str,
        version_id: str,
        columns: list[str],
        op_index: int,
    ) -> tuple[str, str, int]:
        """Create ``columns`` on the artifact via the data-factory column
        transform, returning the (stable) file_id, the new version_id, and the
        artifact's row count (a column add preserves rows, so this is a free
        signal for the empty-table insert-anchor decision).

        Raises ``_OpError(FACTORY_ERROR)`` on failure so the outer try/except
        rolls the whole PATCH back — consistent with row-op failures.
        """
        result = await self._factory.add_columns(
            file_id=file_id,
            columns=columns,
            version_id=version_id or None,
        )
        if not result.success or not result.file_id:
            raise _OpError(
                ErrorCode.INTERNAL_ERROR,
                {"artifact_id": artifact_id, "op_index": op_index,
                 "columns": columns,
                 "factory_error": result.error_code or "FACTORY_ERROR"},
                result.message or "data-factory add_columns failed",
                result.error_code or "FACTORY_ERROR",
            )
        return result.file_id, result.version_id, result.total_rows

    async def _apply_column_op(
        self,
        *,
        artifact_id: str,
        file_id: str,
        columns: list[str],
        op: dict[str, Any],
        op_index: int,
        run_id: str = "",
        task_id: str = "",
    ) -> tuple[str, list[str], OpResult]:
        """Apply one column-level mutation via csv_ops.

        Returns the new file_id, new columns list, and an OpResult.
        Validation errors are translated into ``_OpError`` with structured
        codes so the outer try/except can roll back uniformly.
        """
        kind = op.get("operation")
        content = await self._download(file_id) if file_id else b""

        if kind == "add_column":
            col = op.get("column") or ""
            _validate_column_name(col, artifact_id, op_index, field="column")
            if col in columns:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": op_index, "column": col},
                    f"column {col!r} already exists",
                    "DUPLICATE_COLUMN",
                )
            try:
                new_bytes = CsvDocument(content).add_column( column=col,
                    default_value=op.get("default_value"),
                )
            except ValueError as exc:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": op_index, "column": col},
                    str(exc), "DUPLICATE_COLUMN",
                ) from exc
            new_columns = list(columns) + [col]

        elif kind == "drop_column":
            col = op.get("column") or ""
            _validate_column_name(col, artifact_id, op_index, field="column")
            if col not in columns and content:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": op_index, "column": col},
                    f"column {col!r} not in artifact",
                    "UNKNOWN_COLUMN",
                )
            try:
                new_bytes = CsvDocument(content).drop_column( col)
            except ValueError as exc:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": op_index, "column": col},
                    str(exc), "UNKNOWN_COLUMN",
                ) from exc
            new_columns = [c for c in columns if c != col]

        elif kind == "rename_column":
            old = op.get("column") or ""
            new = op.get("new_name") or ""
            _validate_column_name(old, artifact_id, op_index, field="column")
            _validate_column_name(new, artifact_id, op_index, field="new_name")
            if old not in columns and content:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": op_index, "column": old},
                    f"column {old!r} not in artifact",
                    "UNKNOWN_COLUMN",
                )
            if new != old and new in columns:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": op_index,
                     "column": old, "new_name": new},
                    f"column {new!r} already exists — would collide",
                    "DUPLICATE_COLUMN",
                )
            try:
                new_bytes = CsvDocument(content).rename_column( old_name=old, new_name=new,
                )
            except ValueError as exc:
                raise _OpError(
                    ErrorCode.INPUT_VALIDATION_FAILED,
                    {"artifact_id": artifact_id, "op_index": op_index,
                     "column": old, "new_name": new},
                    str(exc), "UNKNOWN_COLUMN",
                ) from exc
            new_columns = [new if c == old else c for c in columns]

        else:  # pragma: no cover — caller already validated kind
            raise _OpError(
                ErrorCode.INPUT_VALIDATION_FAILED,
                {"artifact_id": artifact_id, "op_index": op_index, "operation": kind},
                f"unknown column op: {kind!r}",
                "OP_UNKNOWN",
            )

        new_file_id = await self._upload(
            new_bytes, filename=f"{artifact_id}.csv",
            run_id=run_id, task_id=task_id, op_index=op_index,
        )
        return new_file_id, new_columns, OpResult(
            artifact_id=artifact_id,
            op_index=op_index,
            status="ok",
            rows_affected=None,
            row_ids=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_target(
        file_ref: dict[str, Any], artifact_id: str,
    ) -> Optional[dict[str, Any]]:
        if not is_file_ref(file_ref):
            return None
        if not artifact_id or artifact_id == ROOT_ARTIFACT_ID:
            return file_ref
        return find_artifact(file_ref, artifact_id)

    async def _download(self, file_id: str) -> bytes:
        if not file_id:
            return b""
        raw = await self._fs.download_file(file_id)
        return raw or b""

    async def _upload(
        self,
        content: bytes,
        *,
        filename: str,
        run_id: str = "",
        task_id: str = "",
        op_index: int = 0,
    ) -> str:
        """Upload ``content`` and return the new ``file_id``.

        Phase 3 Sub 3.3: when the outbox dependencies are wired
        (``OUTBOX_DATA_EDIT=on``) and ``run_id`` / ``task_id`` are
        present, route through ``OutboxRequested`` → dispatcher →
        ``OutboxResolved``. Otherwise fall through to the direct
        ``IFileServiceClient.upload_file`` call.

        Falls back to direct upload on any outbox-side failure
        (``OutboxTimeoutError`` or resolved-failed outcome) so the
        PATCH path keeps its 200 contract while we collect production
        signal. Sub 3.5 will replace the fallback with a 202 Accepted
        response model.
        """
        if self._outbox_enabled and run_id and task_id:
            try:
                return await self._upload_via_outbox(
                    content,
                    filename=filename,
                    run_id=run_id,
                    task_id=task_id,
                    op_index=op_index,
                )
            except Exception as exc:
                _logger.warning(
                    "outbox upload failed for run=%s task=%s op=%d: %s — "
                    "falling back to direct upload",
                    run_id, task_id, op_index, exc,
                )
        res = await self._fs.upload_file(content, filename, "text/csv")
        file_id = res.get("file_id", "") if isinstance(res, dict) else ""
        if not file_id:
            # The file service did not return a usable file_id (auth/5xx/empty
            # response — see HttpFileServiceClient.upload_file logs). Surface it
            # here too so the PATCH /data write path is traceable: the node will
            # be left with file_id="" and the edited data is NOT persisted.
            _logger.error(
                "PATCH _upload got NO file_id from file service "
                "filename=%s run_id=%s task_id=%s op=%d bytes=%d resp=%r",
                filename, run_id, task_id, op_index, len(content or b""), res,
            )
        else:
            _logger.info(
                "PATCH _upload OK filename=%s run_id=%s task_id=%s op=%d file_id=%s",
                filename, run_id, task_id, op_index, file_id,
            )
        return file_id

    async def _upload_via_outbox(
        self,
        content: bytes,
        *,
        filename: str,
        run_id: str,
        task_id: str,
        op_index: int,
    ) -> str:
        from hashlib import sha256

        from app.layer1_domain.events import EventTopic
        from app.layer1_domain.value_objects.event_id import EventId
        from app.layer2_application.interfaces.event_log_repository_interface import (
            EventLogEnvelope,
        )

        outbox_id = EventId.derive_outbox(run_id, task_id, op_index, "file_upload")
        content_hash = sha256(content).hexdigest()

        # Register the awaiter BEFORE appending the event so the
        # dispatcher's same-tick ``deliver`` can never race a missing
        # future.
        self._outbox_awaiter.register(outbox_id)
        # Bytes side-channel — see ``OutboxBytesChannel``. The handler
        # pops the bytes when it processes the entry; replay re-runs
        # this method and re-puts identical bytes.
        self._outbox_bytes_channel.put(outbox_id, content)
        # Mark the run so the memory outbox repo's scan picks it up.
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
                "kind": "file_upload",
                "payload": {
                    "filename": filename,
                    "content_hash": content_hash,
                    "content_type": "text/csv",
                },
                "idempotency_key": content_hash,
            },
            causation_id=task_id,
        )
        self._outbox_event_log.append(envelope)

        resolution = await self._outbox_awaiter.wait(
            outbox_id, timeout=self._outbox_wait_timeout_s,
        )
        if resolution.outcome != "ok":
            raise RuntimeError(
                f"outbox file_upload resolved failed: {resolution.error}",
            )
        new_file_id = str(resolution.result.get("file_id") or "")
        if not new_file_id:
            raise RuntimeError(
                "outbox file_upload resolved ok but no file_id in result",
            )
        return new_file_id

    def _current_working_ref(
        self, run_id: str, node_id: str, task: Any = None,
    ) -> Any:
        """Return the working-copy file_ref for the node.

        Precedence:
          1. ``view.variables[node_id]`` — the projector-rebuilt working copy
             after prior PATCHes / the last ``NodeOutputProduced``.
          2. ``task.resolved_input`` — the frozen input ref the node was
             dispatched with. Used on the first PATCH before any edit has
             landed in the view, or when the view-store entry is missing /
             malformed for any reason.
          3. An empty file_ref placeholder so ETag computation still works.
        """
        view = self._view_store.get(run_id)
        variables = view.variables if view is not None else {}
        ref = variables.get(node_id)
        if is_file_ref(ref):
            return ref
        input_ref = getattr(task, "resolved_input", None) if task is not None else None
        if is_file_ref(input_ref):
            return input_ref
        return build_file_ref(file_id="")

    async def _compute_schema_version(
        self,
        *,
        node: Any,
        task: Any,
        run: Any,
        working_ref: Any,
    ) -> str:
        """Return the schema_version backing PATCH /data's etag.

        Delegates to the handler's :class:`SchemaProvider` against a
        synthetic view that surfaces ``working_ref`` under the node's
        id — so DATA_EDIT's runtime synthesis sees the live columns we
        just computed (pre- or post-PATCH) rather than what's in the
        global view-store.

        Returns ``"v0"`` when no handler registry is wired (test
        harnesses that bypass the schema-version contribution to the
        etag).
        """
        if self._handler_registry is None:
            return "v0"
        try:
            handler = self._handler_registry.get(node.node_type)
        except UnsupportedNodeTypeError:
            return "v0"
        provider = handler.get_schema_provider()
        if provider is None:
            return "v0"

        class _View:
            def __init__(self, variables: dict[str, Any]) -> None:
                self.variables = variables

        view = _View({node.id: working_ref})
        _, version, _ = await provider.resolve(
            node=node, task=task, run=run, view=view,
        )
        return version

    @staticmethod
    def _get_node(workflow: Any, node_id: str) -> Any:
        node = workflow.get_node(node_id) if hasattr(workflow, "get_node") else None
        if node is not None:
            return node
        for n in getattr(workflow, "nodes", []) or []:
            if getattr(n, "id", "") == node_id:
                return n
        return None

    async def _resolve_task(self, run_id: str, node_id: str, task_id: Optional[str]) -> Any:
        tasks = await self.task_repo.alist_by_run(run_id)
        if task_id:
            for t in tasks:
                if getattr(t, "id", "") == task_id and getattr(t, "node_id", "") == node_id:
                    return t
            return None
        return find_latest_task_for_node(tasks, node_id)

    async def _cancel_pending_task(
        self, run: Any, workflow: Any, node: Any, task: Any,
    ) -> EditNodeDataResult:
        """Short-circuit a cancel-via-PATCH signal.

        Marks ``task`` as ``FAILED`` with ``error="user_cancelled"``,
        emits ``NodeCancelled``, GCs working-copy file_ids, and drives
        ``orchestrate_fail`` so failure-edge routing kicks in (or the
        run transitions to ``FAILED`` if no failure path exists).
        Returns an ``EditNodeDataResult`` with ``cancelled=True`` and
        no etag bump (nothing was written to the working copy).
        """
        # Defence in depth: only pending node types are cancellable here.
        # The FE shouldn't aim a cancel at a TASK / builtin node, but
        # rejecting it explicitly avoids surprising side-effects if it
        # somehow happens.
        node_type = getattr(node, "node_type", None)
        if node_type not in _PENDING_NODE_TYPES_FOR_CANCEL:
            return self._error(
                ErrorCode.INPUT_VALIDATION_FAILED,
                {"node_id": getattr(node, "id", ""),
                 "node_type": getattr(node_type, "value", str(node_type))},
                message=(
                    "cancel-via-PATCH is only valid for pending node types "
                    "(INPUT, FILE_UPLOAD, DATA_EDIT, HUMAN_ACTION)"
                ),
                code_override="CANCEL_NOT_SUPPORTED",
            )

        # Re-cancel / late cancel: the task already finished. The FE may
        # be racing a submit; surface 409 so it can refresh.
        if getattr(task, "status", None) != TaskStatus.PENDING:
            current = getattr(task, "status", None)
            return self._error(
                ErrorCode.INVALID_TASK_STATUS,
                {
                    "task_id": getattr(task, "id", ""),
                    "current_status": getattr(current, "value", str(current)),
                    "expected_status": TaskStatus.PENDING.value,
                },
            )

        # Working-copy file_ids are intentionally KEPT on reject/cancel
        # so the user can inspect what they entered before changing their
        # mind. The normal PATCH-supersede path still GCs pre-images via
        # _gc_orphans; only this cancel-path skips it.

        # Mark task FAILED so the engine's existing failure-routing path
        # treats this like any other task failure. ``error`` distinguishes
        # user cancels from real worker failures for log readers.
        now = datetime.now(timezone.utc).isoformat()
        task.status = TaskStatus.FAILED
        task.error = "user_cancelled"
        task.completed_at = now
        existing_meta = getattr(task, "output_metadata", None) or {}
        existing_meta.update({"reason": "user_cancelled", "cancelled_by": "user"})
        task.output_metadata = existing_meta
        try:
            await self.task_repo.asave(task)
        except Exception:
            _logger.exception(
                "cancel_pending_task: failed to persist task %s",
                getattr(task, "id", ""),
            )
            return self._error(
                ErrorCode.INTERNAL_ERROR,
                {"task_id": getattr(task, "id", "")},
            )

        # Emit NodeCancelled so the projector marks the node terminal
        # in the same shape CancelRunUseCase uses for whole-run cancel.
        if self._engine is not None:
            try:
                self._engine.emit_node_cancelled(
                    run, task=task, cancelled_by="user", reason="user_cancelled",
                )
            except Exception:
                _logger.exception(
                    "cancel_pending_task: emit_node_cancelled failed for task %s",
                    getattr(task, "id", ""),
                )

            # Drive failure-edge routing. ``orchestrate_fail`` either
            # dispatches a failure successor (run keeps going) or marks
            # the run FAILED when no failure path exists.
            #
            # Use the ``_locked`` variant: when this use case runs under
            # the OrchestrationWorker (EditNodeDataAction path) the worker
            # already holds ``engine.run_lock(run_id)``, and asyncio.Lock
            # is not reentrant — calling ``orchestrate_fail`` here would
            # deadlock and the HTTP caller would surface a 30 s TimeoutError.
            try:
                await self._engine.orchestrate_fail_locked(run.id, task.id)
            except Exception:
                _logger.exception(
                    "cancel_pending_task: orchestrate_fail failed for "
                    "run=%s task=%s", run.id, getattr(task, "id", ""),
                )

        return EditNodeDataResult(etag="", version=0, cancelled=True)

    async def _fail_pending_node(
        self, run: Any, workflow: Any, node: Any, task: Any,
        *, error_code: str, error_msg: str,
    ) -> None:
        """Mark a pending node FAILED after a terminal PATCH failure.

        Mirrors :meth:`_cancel_pending_task` (same lock context — this runs
        inside ``execute`` under the orchestration worker's ``run_lock``, so
        it uses the non-reentrant-safe ``orchestrate_fail_locked``) but for
        a system/processing failure (data-factory / internal error) rather
        than a user cancel: emits ``NodeFailed`` and drives failure-edge
        routing, which marks the run FAILED when no failure path exists.

        Best-effort and idempotent-ish — every step is guarded so a
        secondary failure here never masks the original PATCH error the
        caller is about to return.
        """
        now = datetime.now(timezone.utc).isoformat()
        task.status = TaskStatus.FAILED
        task.error = error_msg or error_code
        task.completed_at = now
        existing_meta = getattr(task, "output_metadata", None) or {}
        existing_meta.update({"error_code": error_code, "failed_by": "edit_node_data"})
        task.output_metadata = existing_meta
        try:
            await self.task_repo.asave(task)
        except Exception:
            _logger.exception(
                "fail_pending_node: failed to persist task %s",
                getattr(task, "id", ""),
            )
            return

        if self._engine is None:
            return
        try:
            self._engine.emit_node_failed(
                task, run, error_code=error_code, error_msg=error_msg,
            )
        except Exception:
            _logger.exception(
                "fail_pending_node: emit_node_failed failed for task %s",
                getattr(task, "id", ""),
            )
        # Route failure edges or mark the run FAILED. Uses the ``_locked``
        # variant for the same reason as _cancel_pending_task: the
        # orchestration worker already holds ``run_lock(run_id)`` and
        # asyncio.Lock is not reentrant.
        try:
            await self._engine.orchestrate_fail_locked(run.id, task.id)
        except Exception:
            _logger.exception(
                "fail_pending_node: orchestrate_fail failed for "
                "run=%s task=%s", run.id, getattr(task, "id", ""),
            )

    async def _gc_orphans(self, file_ids: list[str], *, run_id: str = "") -> None:
        if not file_ids:
            return

        # Settings-gated kill switch. Tests / browser-driven debugging
        # set ORPHAN_GC_ENABLED=false so every intermediate file (the
        # pre-provisioned __root__, PATCH pre-images, multi-op
        # intermediates) stays queryable on the file service.
        if not getattr(self._app_config, "ORPHAN_GC_ENABLED", True):
            _logger.debug("orphan GC disabled — keeping %d file_ids", len(file_ids))
            return

        # Phase 3 Sub 3.3 — when the outbox is wired, append a
        # ``file_delete`` entry per file_id and return; the dispatcher
        # performs the delete idempotently. ``run_id`` is required for
        # the deterministic id seed; if the caller did not supply it,
        # fall back to direct delete (cancel-path callers always do).
        if self._outbox_enabled and run_id:
            self._gc_via_outbox(file_ids, run_id=run_id)
            return

        async def _gc() -> None:
            for fid in file_ids:
                if not fid:
                    continue
                try:
                    await self._fs.delete_file(fid)
                except Exception as exc:  # pragma: no cover
                    _logger.debug("orphan GC failed for %s: %s", fid, exc)

        try:
            asyncio.create_task(_gc())
        except RuntimeError:  # pragma: no cover — no running loop (tests)
            pass

    def _gc_via_outbox(self, file_ids: list[str], *, run_id: str) -> None:
        """Append one ``OutboxRequested(kind="file_delete")`` per file_id.

        Fire-and-forget — the GC path doesn't wait for the dispatcher.
        Idempotency key is the file_id itself; replay collapses to a
        single delete because the deterministic ``outbox_id`` seed is
        also the file_id.
        """
        from app.layer1_domain.events import EventTopic
        from app.layer1_domain.value_objects.event_id import EventId
        from app.layer2_application.interfaces.event_log_repository_interface import (
            EventLogEnvelope,
        )

        if self._outbox_repo is not None and hasattr(self._outbox_repo, "mark_run"):
            self._outbox_repo.mark_run(run_id)

        for fid in file_ids:
            if not fid:
                continue
            outbox_id = EventId.derive_outbox(run_id, fid, 0, "file_delete")
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
                    "kind": "file_delete",
                    "payload": {"file_id": fid},
                    "idempotency_key": fid,
                },
                causation_id=fid,
            )
            self._outbox_event_log.append(envelope)

    @staticmethod
    def _error(
        code: ErrorCode,
        details: dict[str, Any],
        *,
        message: Optional[str] = None,
        code_override: Optional[str] = None,
    ) -> EditNodeDataResult:
        return EditNodeDataResult(
            error=message or code.default_message,
            error_code=code_override or code.code,
            error_details=details,
        )


# ---------------------------------------------------------------------------
# Helpers — file_ref introspection
# ---------------------------------------------------------------------------

def _descendant_file_ids(file_ref: Any) -> list[str]:
    """Return every non-empty file_id in the ref tree (root + nested)."""
    ids: list[str] = []
    if not is_file_ref(file_ref):
        return ids
    root_fid = file_ref.get("file_id", "")
    if root_fid:
        ids.append(root_fid)
    for art in file_ref.get("artifacts") or []:
        fid = art.get("file_id", "")
        if fid:
            ids.append(fid)
        # Recurse into deeper nested arrays.
        for child in art.get("artifacts") or []:
            child_fid = child.get("file_id", "")
            if child_fid:
                ids.append(child_fid)
    return ids


def _validate_column_name(
    name: str, artifact_id: str, op_index: int, *, field: str,
) -> None:
    """Reject empty names and reserved column identifiers."""
    if not name:
        raise _OpError(
            ErrorCode.INPUT_VALIDATION_FAILED,
            {"artifact_id": artifact_id, "op_index": op_index, "field": field},
            f"{field!r} is required for column op",
            "INVALID_COLUMN",
        )
    if name in _RESERVED_COLUMNS:
        raise _OpError(
            ErrorCode.INPUT_VALIDATION_FAILED,
            {"artifact_id": artifact_id, "op_index": op_index,
             "field": field, "column": name},
            f"column {name!r} is reserved and cannot be added/dropped/renamed",
            "RESERVED_COLUMN",
        )


# ---------------------------------------------------------------------------
# Helpers — object upsert + factory op translation
# ---------------------------------------------------------------------------

def _upsert_single_row(content: bytes, data: dict[str, Any]) -> tuple[bytes, str]:
    """Upsert the single data row in an object CSV, auto-expanding the schema.

    Keys not yet in the header are added as new columns; existing columns
    (including provisioned-but-unfilled ones) are preserved; the row's
    ``__row_id`` is kept (or minted as ``1`` for a fresh CSV). Returns
    ``(new_bytes, row_id)``. See :func:`csv_ops.upsert_scalar_row`.
    """
    from app.layer1_domain.value_objects.csv_document import CsvDocument
    return CsvDocument(content).upsert_scalar_row( dict(data))


def _insert_position(table_maybe_empty: bool) -> str:
    """Anchor position for a factory insert.

    The factory rejects ``before``/``after`` index 0 on a 0-row frame ("Index 0
    is out of bounds"); ``at_index`` works on both empty and non-empty frames.
    Use ``at_index`` only when the (effective) table may be empty so non-empty
    inserts keep their existing ``after`` position semantics.
    """
    return "at_index" if table_maybe_empty else "after"


def _translate_to_factory(
    kind: str,
    row_id: Optional[str],
    data: dict[str, Any],
    *,
    artifact_id: str,
    op_index: int,
    table_maybe_empty: bool = False,
) -> list[FactoryRowOperation]:
    """Translate a single high-level op to the data-factory row-transform
    op(s). ``edit`` becomes delete+insert keyed on ``__row_id`` so the row
    survives the update (the factory has no native "update" op)."""
    if kind == "insert":
        return [FactoryRowOperation(
            operation="insert",
            row_identifier=RowIdentifier(type="index", index=0),
            data=dict(data),
            position=_insert_position(table_maybe_empty),
        )]
    if kind == "delete":
        if not row_id:
            raise _OpError(
                ErrorCode.INPUT_VALIDATION_FAILED,
                {"artifact_id": artifact_id, "op_index": op_index},
                "delete requires row_id",
                "ROW_ID_REQUIRED",
            )
        return [FactoryRowOperation(
            operation="delete",
            row_identifier=RowIdentifier(
                type="values",
                match_values={ROW_ID_COLUMN: row_id},
            ),
        )]
    if kind == "edit":
        if not row_id:
            raise _OpError(
                ErrorCode.INPUT_VALIDATION_FAILED,
                {"artifact_id": artifact_id, "op_index": op_index},
                "edit requires row_id",
                "ROW_ID_REQUIRED",
            )
        return [
            FactoryRowOperation(
                operation="delete",
                row_identifier=RowIdentifier(
                    type="values",
                    match_values={ROW_ID_COLUMN: row_id},
                ),
            ),
            FactoryRowOperation(
                operation="insert",
                row_identifier=RowIdentifier(type="index", index=0),
                data={**dict(data), ROW_ID_COLUMN: row_id},
                position=_insert_position(table_maybe_empty),
            ),
        ]
    raise _OpError(
        ErrorCode.INPUT_VALIDATION_FAILED,
        {"artifact_id": artifact_id, "op_index": op_index, "operation": kind},
        f"unknown operation: {kind!r}",
        "OP_UNKNOWN",
    )


# ---------------------------------------------------------------------------
# Internal exception
# ---------------------------------------------------------------------------

class _OpError(Exception):
    def __init__(
        self,
        error_code: ErrorCode,
        details: dict[str, Any],
        message: str,
        code_override: str = "",
    ) -> None:
        self.error_code = error_code
        self.details = details
        self.message = message
        self.code_override = code_override
        super().__init__(message)
