"""Schema providers — per-handler runtime ``/schema`` resolution.

Replaces the standalone ``node_data_projection.resolve_schema`` free
function. Each ``PendingHandlerBase`` subclass owns a ``SchemaProvider``
instance that decides what schema ``GET /schema`` returns for its node
type. Non-pending handlers expose no provider (``get_schema_provider()``
returns ``None``), and the use case short-circuits with
``schema_source = "none"``.

Two concrete providers ship out of the box:

* :class:`StaticSchemaProvider` — the 4-level precedence chain used by
  every PENDING handler except DATA_EDIT.
* :class:`DataEditSchemaProvider` — synthesizes the schema from
  view-store columns when the run has live data; falls through to
  static precedence otherwise.

The version hash is a short SHA-256 of the resolved schema (sorted JSON
form), reused as the ``If-None-Match`` ETag by both ``GET /schema`` and
``PATCH /data``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.layer2_application.resolve.variable_resolver import VariableResolver

logger = logging.getLogger(__name__)



def _hash_schema(schema: Any) -> str:
    """Content hash of a schema payload. Empty → 'v0'.

    Moved verbatim from ``node_data_projection._hash_schema``.
    """
    if not schema:
        return "v0"
    try:
        payload = json.dumps(schema, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(schema)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"  # short form is fine for ETags


class SchemaProvider(ABC):
    """Resolves the runtime input schema for a single node.

    Returns ``(schema, schema_version, schema_source)`` where
    ``schema_source`` is one of:

    * ``"runtime"`` — the schema was synthesised from live state (the
      handler stamped it onto ``task.output_metadata`` at dispatch, or
      a DATA_EDIT view supplied columns).
    * ``"design_time"`` — the schema came from the workflow definition
      (``node.definition_snapshot['input_schema']`` or the raw
      ``config['input_schema']``).
    * ``"none"`` — the node has no schema by design. (Returned by the
      use case when ``get_schema_provider()`` is ``None``; providers
      themselves never return this.)
    """

    @abstractmethod
    async def resolve(
        self,
        *,
        node: Any,
        task: Any,
        run: Any,
        view: Any,
    ) -> tuple[Any, str, str]:
        ...


class StaticSchemaProvider(SchemaProvider):
    """3-level precedence chain shared by every PENDING handler.

    1. Runtime — ``task.output_metadata['input_schema']``.
    2. Design-time — ``node.definition_snapshot['input_schema']``.
    3. Raw — ``node.config['input_schema']`` (the declared ConfigField).
    Empty fallback — ``([], "v0", "design_time")``.

    NodeSpec Phase 4 dropped the JSON-string parsing of raw schemas:
    stored string schemas were parsed once by the migration, so a
    string here is a sender bug (logged, treated as absent).

    Identical semantics to the deleted ``resolve_schema`` free function
    minus the DATA_EDIT runtime override (which lives in
    :class:`DataEditSchemaProvider`), with one addition: the **design-time
    and raw** branches resolve ``{{…}}`` expressions through the *same*
    :class:`VariableResolver` the owning PENDING handler uses at dispatch.
    The runtime branch is returned untouched because the handler already
    resolved it before stamping ``task.output_metadata['input_schema']``.
    Without this, a design-time ``GET /schema`` would hand the FE raw
    templates while a dispatched node hands it resolved values — the two
    must agree.
    """

    def __init__(self, variable_resolver: "VariableResolver | None" = None) -> None:
        self._resolver = variable_resolver

    async def resolve(
        self,
        *,
        node: Any,
        task: Any,
        run: Any,
        view: Any,
    ) -> tuple[Any, str, str]:
        # 1. Runtime (on task) — already resolved at dispatch, return verbatim.
        task_meta = getattr(task, "output_metadata", None) or {}
        runtime_schema = (
            task_meta.get("input_schema") if isinstance(task_meta, dict) else None
        )
        if runtime_schema:
            return runtime_schema, _hash_schema(runtime_schema), "runtime"

        # 2. Design-time (node.definition_snapshot)
        node_def = getattr(node, "definition_snapshot", None) or {}
        template_schema = (
            node_def.get("input_schema") if isinstance(node_def, dict) else None
        )
        if template_schema:
            resolved = await self._resolve_design_time(template_schema, run)
            return resolved, _hash_schema(resolved), "design_time"

        # 3. Fallback: the node's declared config.input_schema. Strings
        # are a sender bug post-migration — logged and treated as absent.
        node_cfg = getattr(node, "config", None) or {}
        raw = node_cfg.get("input_schema")
        if isinstance(raw, str):
            if raw.strip():
                logger.warning(
                    "node %s: config.input_schema is a JSON string — "
                    "unparsed legacy payload, treating as absent",
                    getattr(node, "id", "?"),
                )
            raw = None
        if raw:
            resolved = await self._resolve_design_time(raw, run)
            return resolved, _hash_schema(resolved), "design_time"

        return [], "v0", "design_time"

    async def _resolve_design_time(self, schema: Any, run: Any) -> Any:
        """Resolve ``{{…}}`` in a design-time schema against the run context.

        Uses the handler's own :class:`VariableResolver` (via the shared
        ``resolve_schema`` routine) so the result matches the dispatch-time
        schema byte-for-byte. A no-op when no resolver is wired or the run
        carries no context variables — the schema is returned unchanged so
        ETag/version stays deterministic for both paths.

        Note: ``GET /schema`` is a read path, not an orchestration pass, so
        the engine's ambient ``ResolutionScope`` is **not** active here.
        Dict-backed sources (``$input``, node outputs, …) resolve identically
        to dispatch, but ambient-only overlay sources (``$loop``, ``$parent``,
        ``$root``, ``$variable``) aren't reachable and gracefully degrade to
        the raw template. This branch only fires before dispatch / on
        test-reset, where such overlays in a schema default are rare.
        """
        if self._resolver is None or run is None:
            return schema
        context = getattr(getattr(run, "context", None), "variables", None)
        if not context:
            return schema
        return await self._resolver.resolve_schema(schema, context)


class DataEditSchemaProvider(StaticSchemaProvider):
    """DATA_EDIT runtime override.

    Synthesizes the schema from the live working_ref's columns when
    available, keeping ``GET /schema`` aligned with the actual editable
    working ref (including post-PATCH column ops). Precedence:

    1. View-store ``view.variables[node_id]`` — authoritative post-submit
       and after PATCH /data column ops project into the view.
    2. ``task.resolved_input`` — authoritative at dispatch time, before
       the user submits. PENDING-mode runner intentionally does not
       publish ``NodeOutputProduced`` (would trigger premature
       ``EdgeTraversed``), so the working_ref minted by
       ``provision_artifacts`` only reaches the view-store after submit.
       Reading from the task here lets the FE render the OData-backed
       table on the very first ``GET /schema`` call.
    3. Fall back to :class:`StaticSchemaProvider` (which serves the
       handler's stamped ``task.output_metadata.input_schema``).
    """

    async def resolve(
        self,
        *,
        node: Any,
        task: Any,
        run: Any,
        view: Any,
    ) -> tuple[Any, str, str]:
        node_id = getattr(node, "id", "")
        columns = self._extract_view_columns(view, node_id)
        if columns is None:
            columns = self._extract_task_columns(task)
        if columns is not None:
            # Import here to avoid pulling auto_form_mapper into module
            # import graph for handlers that never need it.
            from app.layer2_application.read.node_data_projection import (
                synthesize_data_edit_schema,
            )

            # Carry the palette's Tab-2 render-config (configurable action
            # buttons, settings, readonly, custom-update key) into the
            # synthesized schema so a runtime GET /schema stays aligned with
            # the dispatch-time schema instead of silently dropping them.
            render_cfg = self._extract_render_config(task)
            schema = synthesize_data_edit_schema(node, columns, **render_cfg)
            return schema, _hash_schema(schema), "runtime"
        return await super().resolve(node=node, task=task, run=run, view=view)

    @staticmethod
    def _extract_render_config(task: Any) -> dict[str, Any]:
        """Pull the DATA_EDIT render-config off ``task.output_metadata``.

        The handler stamps these (already resolved and parsed into lists)
        at dispatch in ``build_output_metadata``. Reading them back here is
        what keeps the runtime schema's buttons/settings/readonly identical
        to what the FE first received. Missing/uninitialised metadata yields
        defaults, so the bare-table behaviour is preserved.
        """
        meta = getattr(task, "output_metadata", None)
        if not isinstance(meta, dict):
            return {}
        return {
            "readonly": bool(meta.get("readonly", False)),
            "custom_data_update": bool(meta.get("custom_data_update", False)),
            "actions": meta.get("actions") or [],
            "extra_settings": meta.get("settings") or [],
            "instruction": meta.get("instruction") or "",
            "query_params": meta.get("query_params") or {},
            # SA-1239: the designer's Select Mode, stamped at dispatch. Passing it
            # (instead of relying on synthesize's node.config fallback) keeps the
            # runtime schema byte-for-byte aligned with the dispatch schema,
            # including the "none" → no selectionMode case. Absent on pre-fix
            # tasks → None → synthesize falls back to node.config.
            "selection_mode": meta.get("selectionMode") or None,
        }

    @staticmethod
    def _extract_view_columns(view: Any, node_id: str) -> Optional[list[str]]:
        """Return the live column list for ``node_id`` from the view-store."""
        if view is None or not node_id:
            return None
        variables = getattr(view, "variables", None) or {}
        file_ref = variables.get(node_id)
        from app.layer2_application.read.node_data_projection import (
            columns_from_working_ref,
        )

        return columns_from_working_ref(file_ref)

    @staticmethod
    def _extract_task_columns(task: Any) -> Optional[list[str]]:
        """Return the working_ref columns from ``task.resolved_input``."""
        if task is None:
            return None
        from app.layer2_application.read.node_data_projection import (
            columns_from_working_ref,
        )

        return columns_from_working_ref(getattr(task, "resolved_input", None))


class ApprovalFlowSchemaProvider(SchemaProvider):
    """APPROVAL_FLOW ``/schema`` — surfaces the computed approval graph.

    The handler resolves the change request and writes its
    ``{nodes, edges, change_request_id}`` graph to the node output at run
    time (immediate completion). ``GET /schema`` returns that graph so the
    FE renders the approval process off the same endpoint as every other
    node. Pre-run / wrong shape → ``([], "v0", "design_time")``.
    """

    async def resolve(
        self,
        *,
        node: Any,
        task: Any,
        run: Any,
        view: Any,
    ) -> tuple[Any, str, str]:
        node_id = getattr(node, "id", "")
        # Projector-backed view first (the run from the repo doesn't carry
        # rebuilt variables); fall back to the live run context.
        entry = None
        for src in (getattr(view, "variables", None),
                    getattr(getattr(run, "context", None), "variables", None)):
            if isinstance(src, dict) or src is not None:
                entry = (src or {}).get(node_id)
            if entry:
                break
        # Immediate nodes store ``{output, metadata}``; tolerate a bare output.
        output = entry.get("output", entry) if isinstance(entry, dict) else {}
        if isinstance(output, dict) and ("nodes" in output or "edges" in output):
            schema = {"nodes": output.get("nodes", []), "edges": output.get("edges", [])}
            return schema, _hash_schema(schema), "runtime"
        return [], "v0", "design_time"


__all__ = [
    "SchemaProvider",
    "StaticSchemaProvider",
    "DataEditSchemaProvider",
    "ApprovalFlowSchemaProvider",
    "_hash_schema",
]
