"""Node-output panel — the output-side analogue of ``variable-suggestions``.

Surfaces a *selected node's* output as the same FE-ready ``Scope`` /
``VariablePath`` records the variable-suggestions endpoint emits, one
entry per node execution (``iterations``). Where ``variable-suggestions``
is a binding picker over *upstream* nodes, this is a result inspector for
*one* node.

Design: ``docs/design/node-output-panel-api.md``.

Iteration source (§5 of the design):
- The event log's ``NodeOutputProduced`` envelopes carry every iteration's
  full structured output — ``run.context.variables[node_id]`` only keeps
  the latest. So the log is the primary source.
- ``Task`` records co-drive the list: they supply ``task_id`` / timing /
  ``status`` and contribute RUNNING / FAILED iterations that emit no
  output event.

Per node type (§9): LOOP expands its ``results[]`` into one iteration
each; PENDING nodes collapse their dispatch + resume events into one
iteration driven by the ``Task`` status; everything else pairs completed
tasks with output events positionally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer1_domain.entities.data_reference import get_artifacts, is_file_ref
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.get_variable_suggestions.use_cases.get_variable_suggestions_use_case import (
    Scope,
    VariablePath,
    _artifacts_registry_paths,
    _build_source_for_node,
    _finalize_path,
)
from app.layer2_application.features.get_variable_suggestions.use_cases.scope_providers import (
    _fetch_columns_and_preview,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import skipped_nodes_of
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import (
    ITaskRepository,
)
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
)

# Node types that pause the run for external input (§9.1 PENDING).
_PENDING_TYPES = {"INPUT", "HUMAN_ACTION", "DATA_EDIT", "FILE_UPLOAD"}

# ``Task.status`` → the iteration status vocabulary (§4).
_STATUS_MAP = {
    "COMPLETED": "COMPLETED",
    "FAILED": "FAILED",
    "CANCELLED": "FAILED",
    "RUNNING": "RUNNING",
    "RETRYING": "RUNNING",
    "PENDING": "PENDING",
    "WAITING": "PENDING",
    "READY": "PENDING",
}

# The envelope ``event_type`` for ``NodeOutputProduced`` (the appending
# publisher stamps ``type(event).__name__``).
_NODE_OUTPUT_PRODUCED = "NodeOutputProduced"

# Bounded preview — at most this many rows are embedded per file-backed
# table (root output + each nested artifact). The full dataset stays
# behind the per-node OData endpoint.
_PREVIEW_ROW_LIMIT = 5


# ---------------------------------------------------------------------------
# Command / Result types
# ---------------------------------------------------------------------------

@dataclass
class GetNodeOutputCommand:
    run_id: str = ""
    node_id: str = ""


@dataclass
class NodeOutputIteration:
    iteration_index: int | None = None
    task_id: str | None = None
    status: str = "COMPLETED"   # COMPLETED | FAILED | RUNNING | PENDING | SKIPPED
    started_at: str | None = None
    completed_at: str | None = None
    output_port: str = ""
    taken_edge_ports: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    error_code: str = ""
    # An array of Scope records — same shape as variable-suggestions'
    # ``scopes``. Always carries the node's own output scope
    # (``scope_type="node"``); a file-producing node additionally gets an
    # ``artifacts`` scope listing the artifacts it produced. Empty for
    # iterations with no output (FAILED / RUNNING / PENDING / SKIPPED).
    scopes: list[Scope] = field(default_factory=list)


@dataclass
class GetNodeOutputResult:
    run_id: str = ""
    node_id: str = ""
    node_name: str = ""
    node_type: str = ""
    iterations: list[NodeOutputIteration] = field(default_factory=list)
    # LOOP only — the loop's ``metadata`` block (reason / mode / ...).
    node_summary: dict[str, Any] | None = None
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node_type_str(node: Any) -> str:
    nt = getattr(node, "node_type", None)
    return nt.value if hasattr(nt, "value") else str(nt)


def _iter_index(task: Any) -> int | None:
    """The loop-iteration index of a task, or ``None`` outside any loop."""
    stack = getattr(task, "scope_stack", None)
    if stack:
        return getattr(stack[-1], "iter", None)
    return None


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class GetNodeOutputUseCase:
    """Builds the node-output panel payload for one selected node."""

    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        workflow_repo: IWorkflowRepository,
        task_repo: ITaskRepository,
        event_log_repo: IEventLogRepository,
        file_service_client: Optional[IFileServiceClient] = None,
        node_output_repo: Any = None,
        node_output_read_mode: str = "event",
        run_context_view_store: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.workflow_repo = workflow_repo
        self.task_repo = task_repo
        self.event_log_repo = event_log_repo
        self._view_store = run_context_view_store
        # Step 4 of solace/specs/wcp-node-output-storage-redesign.md — the read
        # migration, staged the way this service already migrated the projector
        # (live / shadow / projector), for the same reason: flipping a read path
        # blind is how a silently-wrong value reaches the UI.
        #
        #   "event"  (default) — read state_updates, as always. The table is
        #                        ignored even when populated.
        #   "shadow"           — read BOTH, log any divergence, serve the event
        #                        value. Produces the evidence to flip on.
        #   "table"            — serve the table, falling back to the event for
        #                        runs written before it existed.
        #
        # Default is "event" because the table is new: at the time of writing it
        # holds zero rows, and a mode that silently returns nothing for every
        # historical run is worse than one that changes nothing.
        self._node_output_repo = node_output_repo
        self._node_output_read_mode = (node_output_read_mode or "event").lower()
        # Optional — when wired, file-backed outputs get a bounded row
        # preview. Absent (no file service) → ``preview_rows`` stays empty.
        self._file_client = file_service_client

    async def execute(
        self, command: GetNodeOutputCommand,
    ) -> GetNodeOutputResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return self._error(command, ErrorCode.RUN_NOT_FOUND,
                               {"run_id": command.run_id})

        workflow = await self.workflow_repo.aget_by_id(run.workflow_id)
        if not workflow:
            return self._error(command, ErrorCode.WORKFLOW_NOT_FOUND,
                               {"workflow_id": run.workflow_id})

        node = workflow.get_node(command.node_id)
        if node is None:
            return self._error(command, ErrorCode.NODE_NOT_FOUND,
                               {"node_id": command.node_id})

        node_type = _node_type_str(node)

        # NodeOutputProduced envelopes for this node, in append (= sequence)
        # order — one per output-bearing execution.
        events = [
            e for e in self.event_log_repo.list_by_run(command.run_id)
            if e.event_type == _NODE_OUTPUT_PRODUCED
            and (e.payload or {}).get("node_id") == command.node_id
        ]
        tasks = sorted(
            (t for t in await self.task_repo.alist_by_run(command.run_id)
             if t.node_id == command.node_id),
            key=lambda t: _iter_index(t) or 0,
        )

        node_summary: dict[str, Any] | None = None
        if node_type == "LOOP":
            iterations = await self._expand_loop(node, events)
            node_summary = self._loop_summary(node, events)
        elif node_type in _PENDING_TYPES:
            iterations = await self._pending_iterations(node, tasks, events)
        else:
            iterations = await self._merge_iterations(node, tasks, events)

        # Skipped node — no events, no tasks, but the engine marked it
        # skipped (§9.7). Never-reached nodes stay at ``iterations: []``.
        if not iterations and command.node_id in skipped_nodes_of(
            command.run_id, self._view_store, run,
        ):
            iterations = [NodeOutputIteration(status="SKIPPED")]

        return GetNodeOutputResult(
            run_id=command.run_id,
            node_id=command.node_id,
            node_name=getattr(node, "name", "") or command.node_id,
            node_type=node_type,
            iterations=iterations,
            node_summary=node_summary,
        )

    # ─────────────────────────── builders ────────────────────────────

    async def _build_scopes(
        self, node: Any, value: Any, expression_prefix: str,
    ) -> list[Scope]:
        """Build the iteration's ``scopes`` array — the node's own output
        scope, plus an ``artifacts`` scope when the node produced
        artifacts. Same multi-scope shape as ``variable-suggestions``."""
        node_scope = _build_source_for_node(
            node, value, expression_prefix=expression_prefix,
        )
        for p in node_scope.paths:
            _finalize_path(p, expression_prefix=expression_prefix)
        node_scope.has_file_data = node_scope.file_id is not None
        await self._enrich_preview(node_scope, value)

        scopes = [node_scope]
        artifacts_scope = await self._build_artifacts_scope(value)
        if artifacts_scope is not None:
            scopes.append(artifacts_scope)
        return scopes

    async def _build_artifacts_scope(self, value: Any) -> Scope | None:
        """An ``artifacts`` scope listing the artifacts a file-producing
        node emitted — same shape as variable-suggestions' artifacts scope
        (``{{$artifacts.<name>}}`` paths). ``None`` when the output is
        inline or produced no named artifacts."""
        if not (isinstance(value, dict) and is_file_ref(value)):
            return None
        registry: dict[str, Any] = {}
        for art in get_artifacts(value):
            name = art.get("name") or ""
            file_id = art.get("file_id") or ""
            if not name or not file_id:
                continue
            registry[name] = {
                "file_id": file_id,
                "filename": name,
                "content_type": art.get("content_type", "text/csv"),
                "size_bytes": art.get("size_bytes", 0),
            }
        if not registry:
            return None

        prefix = "{{$artifacts"
        paths = _artifacts_registry_paths(registry)
        if self._file_client is not None:
            cache: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
            for p in paths:
                if p.kind != "file_ref" or not isinstance(p.sample, dict):
                    continue
                file_id = str(p.sample.get("file_id") or "")
                if not file_id:
                    continue
                if file_id not in cache:
                    cache[file_id] = await _fetch_columns_and_preview(
                        self._file_client, self.logger, file_id,
                        top=_PREVIEW_ROW_LIMIT,
                    )
                cols, rows = cache[file_id]
                p.columns = list(cols)
                p.preview_rows = [dict(r) for r in rows]
        for p in paths:
            _finalize_path(p, expression_prefix=prefix)
        return Scope(
            scope_type="artifacts", scope_id="artifacts", label="Artifacts",
            expression_prefix=prefix, paths=paths,
        )

    async def _enrich_preview(self, scope: Scope, value: Any) -> None:
        """Attach up to ``_PREVIEW_ROW_LIMIT`` rows to a file-backed scope —
        the root output plus each nested artifact. Also enriches file_ref
        fields nested inside an otherwise-inline output dict (e.g. a code
        node's streamed ``result`` — SA-1905 C2). No-op without a file client
        or for a purely-inline output with no file_ref fields."""
        if self._file_client is None:
            return
        # File_ref fields nested in an inline output dict (``{result: <ref>}``)
        # are not whole-value file_refs, so the root/nested-artifact passes
        # below skip them. Enrich each such path directly from its sample's
        # file_id so the Output panel renders a data table instead of a raw
        # pointer object. Runs for both file-backed and inline outputs.
        await self._enrich_nested_file_ref_paths(scope)
        if not (isinstance(value, dict) and is_file_ref(value)):
            return
        # Root output rows.
        if scope.file_id:
            cols, rows = await _fetch_columns_and_preview(
                self._file_client, self.logger, scope.file_id,
                top=_PREVIEW_ROW_LIMIT,
            )
            if rows:
                scope.preview_rows = rows
                if not scope.columns:
                    scope.columns = cols
                # No-``extracted`` model: file_refs are pointer-only, so
                # ``_build_source_for_node`` left every scalar path
                # ``sample=None``. The first OData preview row is now the sole
                # source of sample values — backfill each sampleless scalar
                # path from its matching column. Samples are CSV cells
                # (strings); ``output_type`` stays schema-derived (do NOT
                # re-infer it from the stringified sample), and the
                # ``is None`` guard preserves real ``0`` / ``""`` / ``False``.
                # Mirrors ``NodeScopeProvider._enrich``.
                first_row = rows[0] if isinstance(rows[0], dict) else {}
                for p in scope.paths:
                    if (
                        p.kind == "scalar"
                        and p.sample is None
                        and not p.path.startswith("__")
                        and p.path in first_row
                    ):
                        p.sample = first_row[p.path]
                # Surface CSV columns no schema/ctx path covered (e.g. a
                # file_ref with empty ``columns`` metadata) as sampled scalar
                # paths, finalized inline since ``_build_scopes`` already ran
                # its finalize pass over the original paths.
                existing = {p.path for p in scope.paths}
                for col in cols:
                    if col and col not in existing and not col.startswith("__"):
                        scope.paths.append(_finalize_path(
                            VariablePath(
                                path=col, kind="scalar", output_type="string",
                                sample=first_row.get(col),
                            ),
                            expression_prefix=scope.expression_prefix,
                        ))
                        existing.add(col)
        # Nested artifacts → attach rows to the matching path entry.
        cache: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        path_index = {p.path: p for p in scope.paths}
        for art in get_artifacts(value):
            name = art.get("name") or ""
            file_id = str(art.get("file_id") or "")
            target = path_index.get(name)
            if not name or not file_id or target is None:
                continue
            if file_id not in cache:
                cache[file_id] = await _fetch_columns_and_preview(
                    self._file_client, self.logger, file_id,
                    top=_PREVIEW_ROW_LIMIT,
                )
            cols, rows = cache[file_id]
            target.columns = list(cols)
            target.preview_rows = [dict(r) for r in rows]
            target.kind = "file_ref"

    async def _enrich_nested_file_ref_paths(self, scope: Any) -> None:
        """Fetch columns + a bounded row preview for every ``kind=="file_ref"``
        path whose ``sample`` carries a file_id and that isn't already enriched.

        Covers file-backed fields nested in an inline output dict — e.g. the
        code worker returns ``{result: <file_ref>, logs, ...}`` after streaming
        a large ``result`` array to CSV (SA-1905 C2). ``_build_source_for_node``
        emits ``result`` as a ``file_ref`` path; here we attach its real
        columns/rows so ``_finalize_path`` marks it ``expandable`` and the FE
        renders a table. Never raises — a file-service miss leaves empty rows."""
        if self._file_client is None:
            return
        cache: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        for p in getattr(scope, "paths", None) or []:
            if getattr(p, "kind", "") != "file_ref" or p.preview_rows:
                continue
            sample = p.sample
            file_id = str(sample.get("file_id") or "") if isinstance(sample, dict) else ""
            if not file_id:
                continue
            if file_id not in cache:
                cache[file_id] = await _fetch_columns_and_preview(
                    self._file_client, self.logger, file_id,
                    top=_PREVIEW_ROW_LIMIT,
                )
            cols, rows = cache[file_id]
            if cols and not p.columns:
                p.columns = list(cols)
            p.preview_rows = [dict(r) for r in rows]
            # ``_finalize_path`` already ran (it precedes enrichment), so a
            # ref whose ``columns`` metadata was empty at build time would be
            # left non-expandable. Re-assert now that we have real columns.
            if p.columns:
                p.expandable = True

    def _resolve_descriptor(self, *, run_id: str, node: Any, from_event: Any) -> Any:
        """Where a node's descriptor comes from — ``AIW_RUN_NODE_OUTPUT`` or the event.

        See ``_node_output_read_mode`` for the three modes. Whatever happens
        here, the caller gets a usable value: a repo that is absent, empty or
        broken falls back to ``from_event``, which is the behaviour this API
        has always had.

        The table is authoritative only in ``table`` mode, and even then only
        when it actually has the row — a run that completed before the write
        path shipped has none, and returning nothing for it would blank the UI
        for every historical run.
        """
        mode = self._node_output_read_mode
        # Anything that is not one of the three known modes is treated as
        # "event". A typo in the env var must not flip a read path — failing
        # open here would let ``NODE_OUTPUT_READ_MODE=talbe`` quietly serve
        # from a table that may not have the row.
        if mode not in ("shadow", "table"):
            return from_event
        if self._node_output_repo is None or not run_id:
            return from_event
        try:
            from_table = self._node_output_repo.get_latest(run_id, node.id)
        except Exception as exc:
            self.logger.warning(
                f"node-output table read failed for run={run_id} node={node.id}: {exc}"
            )
            return from_event

        if mode == "shadow":
            # Serve the old value; report disagreement. Both being absent is
            # agreement, and the table simply not having the row yet is the
            # expected state for every run older than the write path.
            if from_table is not None and from_table != from_event:
                self.logger.warning(
                    "node-output divergence run=%s node=%s: table and event differ",
                    run_id, node.id,
                )
            return from_event

        return from_table if from_table is not None else from_event

    async def _iteration_from_event(
        self, node: Any, envelope: Any, task: Any | None,
        *, iteration_index: int | None,
    ) -> NodeOutputIteration:
        payload = envelope.payload or {}
        value = self._resolve_descriptor(
            run_id=str(payload.get("run_id") or ""),
            node=node,
            from_event=(payload.get("state_updates") or {}).get(node.id),
        )
        scopes = await self._build_scopes(node, value, f"{{{{${node.id}")
        port = payload.get("output_port", "") or ""
        edges = dict(payload.get("taken_edge_ports") or {})
        if task is not None:
            return NodeOutputIteration(
                iteration_index=_iter_index(task),
                task_id=task.id,
                status=_STATUS_MAP.get(task.status.value, task.status.value),
                started_at=task.started_at,
                completed_at=task.completed_at,
                output_port=port,
                taken_edge_ports=edges,
                error=task.error,
                error_code=(task.output_metadata or {}).get("error_code", ""),
                scopes=scopes,
            )
        return NodeOutputIteration(
            iteration_index=iteration_index,
            task_id=None,
            status="COMPLETED",
            started_at=None,
            completed_at=envelope.timestamp or None,
            output_port=port,
            taken_edge_ports=edges,
            error=None,
            scopes=scopes,
        )

    @staticmethod
    def _status_only_iteration(task: Any) -> NodeOutputIteration:
        """A FAILED / RUNNING / PENDING task that produced no output event."""
        return NodeOutputIteration(
            iteration_index=_iter_index(task),
            task_id=task.id,
            status=_STATUS_MAP.get(task.status.value, task.status.value),
            started_at=task.started_at,
            completed_at=task.completed_at,
            error=task.error,
            error_code=(task.output_metadata or {}).get("error_code", ""),
        )

    async def _merge_iterations(
        self, node: Any, tasks: list[Any], events: list[Any],
    ) -> list[NodeOutputIteration]:
        """TASK + non-LOOP BUILTIN nodes. Completed tasks pair with output
        events positionally; FAILED / RUNNING tasks become status-only
        iterations. BUILTIN nodes (no Task) come purely from events."""
        if not tasks:
            multi = len(events) > 1
            return [
                await self._iteration_from_event(
                    node, ev, None, iteration_index=(i if multi else None),
                )
                for i, ev in enumerate(events)
            ]
        iterations: list[NodeOutputIteration] = []
        cursor = 0
        for task in tasks:
            # FAILED tasks also emit a NodeOutputProduced (failure-port) event
            # carrying the failure body, so they pair with their event the
            # same as COMPLETED — otherwise the error body never surfaces.
            if task.status.value in ("COMPLETED", "FAILED") and cursor < len(events):
                ev = events[cursor]
                cursor += 1
                iterations.append(
                    await self._iteration_from_event(node, ev, task, iteration_index=None),
                )
            else:
                iterations.append(self._status_only_iteration(task))
        return iterations

    async def _pending_iterations(
        self, node: Any, tasks: list[Any], events: list[Any],
    ) -> list[NodeOutputIteration]:
        """PENDING nodes emit NodeOutputProduced twice (dispatch scaffold +
        resume). Collapse to one iteration driven by the latest Task; the
        scope comes from the *last* event (the resume) — §9.5."""
        if not tasks:
            if events:
                return [await self._iteration_from_event(
                    node, events[-1], None, iteration_index=None)]
            return []
        task = tasks[-1]
        if task.status.value in ("COMPLETED", "FAILED") and events:
            return [await self._iteration_from_event(
                node, events[-1], task, iteration_index=None)]
        return [self._status_only_iteration(task)]

    async def _expand_loop(
        self, node: Any, events: list[Any],
    ) -> list[NodeOutputIteration]:
        """Expand the LOOP node's ``output.results`` array — one iteration
        per body pass (§9.4)."""
        if not events:
            return []
        value = ((events[-1].payload or {}).get("state_updates") or {}).get(node.id) or {}
        results = ((value.get("output") or {}).get("results")) or []
        iterations: list[NodeOutputIteration] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            idx = r.get("index")
            prefix = f"{{{{${node.id}.results[{idx}].output"
            scopes = await self._build_scopes(node, r.get("output"), prefix)
            scopes[0].label = (
                f"{scopes[0].label} · iteration {idx} (item: {r.get('item')})"
            )
            iterations.append(NodeOutputIteration(
                iteration_index=idx,
                task_id=None,
                status="COMPLETED",
                scopes=scopes,
            ))
        return iterations

    @staticmethod
    def _loop_summary(node: Any, events: list[Any]) -> dict[str, Any] | None:
        if not events:
            return None
        value = ((events[-1].payload or {}).get("state_updates") or {}).get(node.id) or {}
        meta = value.get("metadata")
        return meta if isinstance(meta, dict) else None

    # ──────────────────────────── errors ─────────────────────────────

    @staticmethod
    def _error(
        command: GetNodeOutputCommand, code: ErrorCode, details: dict[str, Any],
    ) -> GetNodeOutputResult:
        return GetNodeOutputResult(
            run_id=command.run_id,
            node_id=command.node_id,
            error=code.default_message,
            error_code=code.code,
            error_details=details,
        )
