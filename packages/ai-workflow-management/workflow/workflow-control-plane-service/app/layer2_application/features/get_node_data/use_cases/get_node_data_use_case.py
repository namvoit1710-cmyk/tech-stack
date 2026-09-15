"""Return scalars + recursive artifact descriptor tree for a node.

Serves ``GET /runs/{run_id}/nodes/{node_id}/data``. Identity only — the
response carries ``file_id``s, column lists, and the node's ETag, but **no
inline rows and no row_count**. The frontend queries the file service directly
(via the scoped ``/artifacts/{artifact_id}/odata`` endpoint) for rows and uses
``$count=true`` for counts.

Side semantics (``side=input|output|both``):

- ``input``  → ``task.resolved_input`` — the frozen upstream reference. For
  DATA_EDIT this is the **original** data fed into the node; for other node
  kinds it's whatever resolved binding was used at dispatch time.
- ``output`` → ``run.context.variables[node_id]`` — the **working copy** that
  PATCH modifies. On initial entry this shares ``file_id``s with the input
  (copy-on-write); after any edit, modified artifacts have new ids.
- ``both``   → both fields populated in the response.

``produced_artifacts`` lists the names this node contributed to the run-level
``$artifacts`` registry (see ``GetRunArtifactsUseCase``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from app.layer1_domain.entities.data_reference import build_file_ref, is_file_ref
from app.layer1_domain.exceptions import UnsupportedNodeTypeError
from app.layer2_application.read.node_data_projection import (
    build_descriptor_tree,
    build_inline_descriptor,
    compute_node_state,
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
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository


Side = Literal["input", "output", "both"]


@dataclass
class GetNodeDataCommand:
    run_id: str = ""
    node_id: str = ""
    side: Side = "input"
    task_id: Optional[str] = None


@dataclass
class NodeDataPayload:
    etag: str = ""
    version: int = 0
    root: Optional[dict[str, Any]] = None
    # Populated on the ``output`` side only — the port/edges the last
    # ``NodeOutputProduced`` emission took. Lets the Output panel show
    # "took success" vs "took failure" (TASK) or "took false branch"
    # (CONDITION/SWITCH) without a second API call. None on the input side.
    output_port: Optional[str] = None
    taken_edge_ports: Optional[dict[str, str]] = None


@dataclass
class GetNodeDataResult:
    run_id: str = ""
    node_id: str = ""
    task_id: str = ""
    node_kind: str = ""
    node_state: str = "PENDING"
    schema_ref: dict[str, Any] = field(default_factory=dict)
    data: Optional[NodeDataPayload] = None       # side=input | both
    output: Optional[NodeDataPayload] = None     # side=output | both
    produced_artifacts: list[str] = field(default_factory=list)

    # Primary node-data ETag — reflects whichever side the caller asked for.
    # ``side=both`` uses the working-copy (output) ETag since that's what PATCH
    # requires on If-Match.
    etag: str = ""

    # Node-level failure reason — populated when this node FAILED, for both
    # worker failures (a ``failure_info`` file-ref also lands on the output
    # side) and in-engine handler/expression failures (which write no output
    # ref, so this is the only place the reason surfaces here). Distinct from
    # the ``error`` fields below, which carry *operation* errors (run/workflow
    # not found) and drive the HTTP error response.
    node_error: Optional[dict[str, Any]] = None

    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetNodeDataUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_context_view_store: IRunContextViewStore,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        handler_registry: Optional[NodeHandlerRegistry] = None,
        file_service_client: Optional[IFileServiceClient] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_context_view_store = run_context_view_store
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._handler_registry = handler_registry
        # Optional — when wired, ``_payload_for`` post-walks the descriptor
        # tree and attaches a ``preview_rows`` page per file_id so old FE
        # versions can render data tables without a per-artifact OData call.
        self._file_client = file_service_client

    async def execute(self, command: GetNodeDataCommand) -> GetNodeDataResult:
        view = self.run_context_view_store.get(command.run_id)
        if not view:
            return self._error(ErrorCode.RUN_NOT_FOUND, {"run_id": command.run_id})

        workflow = await self.workflow_repo.aget_by_id(view.workflow_id)
        if not workflow:
            return self._error(
                ErrorCode.WORKFLOW_NOT_FOUND, {"workflow_id": view.workflow_id}
            )

        node = self._get_node(workflow, command.node_id)
        if node is None:
            return self._error(
                ErrorCode.NODE_NOT_FOUND,
                {"run_id": command.run_id, "node_id": command.node_id},
            )

        task = await self._resolve_task(
            run_id=command.run_id,
            node_id=command.node_id,
            task_id=command.task_id,
        )

        # Pull the two file_ref sources. Input is the frozen upstream
        # reference on the task; output is the working copy, served by
        # the projector reducer (``NodeOutputProduced`` +
        # ``NodeDataEdited``). Post-Phase 3b.2a the aggregate is no
        # longer updated for edited nodes — reads must go through the
        # view store.
        input_ref = getattr(task, "resolved_input", None) if task else None
        output_ref = view.variables.get(command.node_id)

        # Compute schema_version via the handler's SchemaProvider so the
        # etag matches what GET /schema and PATCH /data compute —
        # otherwise PATCH's If-Match check 412s spuriously after a
        # column op. Prefer the working ref (output_ref); fall back to
        # input_ref via a synthetic view passed to the provider.
        ref_for_schema = output_ref if is_file_ref(output_ref) else input_ref
        schema_version = await self._compute_schema_version(
            node=node, task=task, run=view, ref=ref_for_schema,
        )

        side = command.side or "input"
        include_input = side in ("input", "both")
        include_output = side in ("output", "both")

        # Memoize file_id → (columns, rows) across input + output sides so a
        # ``side=both`` request only round-trips once per file_id, even when
        # input and output share descriptor file_ids (typical pre-edit copy-
        # on-write state).
        preview_cache: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}

        data_payload = (
            await self._payload_for(input_ref, schema_version, preview_cache)
            if include_input else None
        )
        output_payload = (
            await self._payload_for(output_ref, schema_version, preview_cache)
            if include_output else None
        )

        if include_output and output_payload is not None:
            port = view.last_output_ports.get(command.node_id)
            edges = view.last_taken_edge_ports.get(command.node_id)
            output_payload.output_port = port if port else None
            output_payload.taken_edge_ports = dict(edges) if edges else None

        # The top-level ETag is the value the FE will use as ``If-Match`` on
        # PATCH /data. PATCH validates against the working copy
        # (``view.variables[node_id]``, with fallback to ``task.resolved_input``
        # then an empty file_ref) — see ``EditNodeDataUseCase._current_working_ref``.
        # Mirror that precedence here so GET → PATCH round-trip is stable
        # regardless of ``side``: clients calling GET (default ``side=input``)
        # then PATCH would otherwise 412 whenever the auto-provisioner has
        # expanded the working tree past the frozen ``task.resolved_input``
        # (e.g. INPUT nodes with table artifacts).
        if is_file_ref(output_ref):
            primary_ref_for_etag: Any = output_ref
        elif is_file_ref(input_ref):
            primary_ref_for_etag = input_ref
        else:
            primary_ref_for_etag = build_file_ref(file_id="")
        primary_etag = NodeInputEtag.of(
            primary_ref_for_etag, schema_version=schema_version,
        )

        node_kind_value = getattr(getattr(node, "node_type", None), "value", None) or str(
            getattr(node, "node_type", "")
        )

        produced = self._produced_artifacts(view, command.node_id)

        node_state = compute_node_state(view, command.node_id, task)
        # Surface the failure reason for ANY failed node — handler/expression
        # failures write no output ref, so without this the only failure
        # signal here is ``node_state``. Kept separate from the ``error``
        # fields (those are operation errors → HTTP error).
        node_error = None
        if node_state == "FAILED" and task is not None:
            meta = getattr(task, "output_metadata", None) or {}
            node_error = {
                "message": getattr(task, "error", None) or "",
                "error_code": meta.get("error_code", ""),
            }

        return GetNodeDataResult(
            run_id=command.run_id,
            node_id=command.node_id,
            task_id=getattr(task, "id", "") if task else "",
            node_kind=str(node_kind_value),
            node_state=node_state,
            node_error=node_error,
            schema_ref={
                "url": f"/runs/{command.run_id}/nodes/{command.node_id}/schema",
                "etag": NodeInputEtag.as_weak_header(schema_version),
            },
            data=data_payload,
            output=output_payload,
            produced_artifacts=produced,
            etag=primary_etag,
        )

    # ------------------------------------------------------------------

    async def _compute_schema_version(
        self,
        *,
        node: Any,
        task: Any,
        run: Any,
        ref: Any,
    ) -> str:
        """Return the schema_version backing GET /data's etag.

        Delegates to the handler's :class:`SchemaProvider` against a
        synthetic view that surfaces ``ref`` (output_ref preferred,
        fall back to input_ref) under the node's id — keeping the
        provider call shape identical to GET /schema while letting
        this use case choose which file_ref the schema is derived
        from. Returns ``"v0"`` when no handler registry is wired.
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

        view = _View({node.id: ref})
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

    async def _resolve_task(
        self,
        *,
        run_id: str,
        node_id: str,
        task_id: Optional[str],
    ) -> Any:
        tasks = await self.task_repo.alist_by_run(run_id)
        if task_id:
            for t in tasks:
                if getattr(t, "id", "") == task_id and getattr(t, "node_id", "") == node_id:
                    return t
            return None
        return find_latest_task_for_node(tasks, node_id)

    async def _payload_for(
        self,
        file_ref: Any,
        schema_version: str,
        preview_cache: dict[str, tuple[list[str], list[dict[str, Any]]]],
    ) -> NodeDataPayload:
        payload = NodeDataPayload(
            etag=NodeInputEtag.of(file_ref, schema_version=schema_version),
            version=0,
            root=None,
        )
        if isinstance(file_ref, dict):
            payload.version = int(file_ref.get("version", 0) or 0)
            if is_file_ref(file_ref):
                payload.root = build_descriptor_tree(file_ref)
                if payload.root is not None:
                    await self._populate_preview_rows(payload.root, preview_cache)
            else:
                # Inline-output node (code): render the raw values directly.
                payload.root = build_inline_descriptor(file_ref)
                # An inline output may still carry file-backed CHILD fields
                # (e.g. a code node's streamed ``result`` — SA-1905 C2). The
                # root itself has no file_id, but its children do; walk the
                # tree so each child gets its preview_rows page.
                if payload.root is not None and payload.root.get("children"):
                    await self._populate_preview_rows(payload.root, preview_cache)
        return payload

    async def _populate_preview_rows(
        self,
        descriptor: dict[str, Any],
        cache: dict[str, tuple[list[str], list[dict[str, Any]]]],
    ) -> None:
        """Walk the descriptor tree and attach ``preview_rows`` per node with
        a non-empty ``file_id``. Cache shared across the call so duplicate
        file_ids only round-trip once. Failures fall back to empty rows —
        ``GET /data`` keeps working when the file service is offline."""
        if self._file_client is None:
            return
        await self._fill_descriptor(descriptor, cache)

    async def _fill_descriptor(
        self,
        descriptor: dict[str, Any],
        cache: dict[str, tuple[list[str], list[dict[str, Any]]]],
    ) -> None:
        file_id = descriptor.get("file_id") or ""
        if file_id and "preview_rows" in descriptor:
            if file_id not in cache:
                cache[file_id] = await self._fetch_columns_and_preview(file_id)
            _, rows = cache[file_id]
            descriptor["preview_rows"] = [dict(r) for r in rows]
        for child in descriptor.get("children") or []:
            await self._fill_descriptor(child, cache)

    async def _fetch_columns_and_preview(
        self, file_id: str, *, top: int = 5,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Mirror of the helper in ``GetVariableSuggestionsUseCase``. Kept
        local so layer-2 features don't share a use-case private utility.
        Returns ``([], [])`` on no client / failure — never raises."""
        if not file_id or self._file_client is None:
            return [], []
        try:
            result = await self._file_client.query_file_data(
                file_id, {"$top": str(top)},
            )
        except Exception:
            self.logger.warning("preview fetch failed for %s", file_id)
            return [], []
        rows = result.get("data") or []
        cleaned = [
            {k: v for k, v in row.items() if k != "_row_index"}
            for row in rows
        ]
        cols = list(cleaned[0].keys()) if cleaned else []
        return cols, cleaned

    @staticmethod
    def _produced_artifacts(view: Any, node_id: str) -> list[str]:
        """Names this node contributed to the run-level $artifacts registry.

        The registry lives in ``variables["artifacts"]`` and is rebuilt by the
        projector from ``NodeOutputProduced`` write-sets."""
        variables = getattr(view, "variables", {}) or {}
        registry = variables.get("artifacts") or {}
        if not isinstance(registry, dict):
            return []
        produced = [
            name for name, entry in registry.items()
            if isinstance(entry, dict) and entry.get("produced_by_node") == node_id
        ]
        produced.sort()
        return produced

    @staticmethod
    def _error(code: ErrorCode, details: dict[str, Any]) -> GetNodeDataResult:
        return GetNodeDataResult(
            error=code.default_message,
            error_code=code.code,
            error_details=details,
        )
