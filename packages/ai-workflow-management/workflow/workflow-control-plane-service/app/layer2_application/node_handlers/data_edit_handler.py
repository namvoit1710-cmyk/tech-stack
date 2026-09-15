"""DATA_EDIT node handler: pauses workflow so the user can review/edit an upstream artifact."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

from app.layer1_domain.entities.data_reference import (
    refresh_schema_hash,
    ROOT_ARTIFACT_ID,
    add_artifact,
    build_file_ref,
)
from app.layer1_domain.value_objects.csv_document import ROW_ID_COLUMN
from app.layer1_domain.events import (
    DomainEvent,
    EventTopic,
    InputRequested,
    InputSubmitted,
)
from app.layer1_domain.exceptions import ExpressionError
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.node_spec.specs import DataEditConfig
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer2_application.node_handlers.schema_providers import (
    DataEditSchemaProvider,
)
from app.layer2_application.resolve.variable_resolver import (
    VariableResolutionError,
    VariableResolver,
)
from app.layer1_domain.auto_form_mapper.helpers import table_array_field, text_field

logger = logging.getLogger(__name__)

_EXPR_SOURCE_RE = re.compile(r"\{\{\$([a-zA-Z0-9_-]+)")
_EXPR_ARTIFACTS_RE = re.compile(r"\{\{\$artifacts\.([a-zA-Z0-9_-]+)")


def _parse_array_of_objects(raw: Any) -> list[dict[str, Any]]:
    """Coerce a config value into a list of dicts.

    Accepts a JSON string (palette textarea), a list, or a dict. Anything
    else — including parse failures — yields ``[]``. Non-dict items in a
    list are dropped.
    """
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _raw_fields(node: WorkflowNode) -> dict[str, Any]:
    """The node's unresolved DATA_EDIT fields from both canonical homes.

    ``node.config`` (declared spec fields — flat-key API payloads and
    042-migrated data) overlaid by the flattened ``node.input_binding``
    (the FE's ``data_edit_tabs`` container submit — the freshest edit
    always wins). Values are raw: ``{{…}}`` expressions unresolved.
    """
    merged = {
        k: v for k, v in (node.config or {}).items()
        if not k.startswith("_")
    }
    merged.update(_flatten_tab_config(node.input_binding or {}))
    return merged


def _flatten_tab_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Lift values nested under auto-form Tab/TabItem wrappers up one level.

    The palette wraps DATA_EDIT fields in a ``TabWrapper`` with two
    ``TabItemWrapper`` children, which makes the FE submit values as::

        {"data_edit_tabs": {"main": {"file_id": ...}, "settings_tab": {...}}}

    The handler reads field keys directly off ``node.config``, so we
    flatten the two nested levels before any reads. Top-level entries
    that are not tab containers (heuristic: a dict whose values include
    further dicts) pass through unchanged, so legacy flat payloads —
    and field-level dicts like ``input_types`` — keep working.
    """
    flat: dict[str, Any] = {}
    for k, v in cfg.items():
        if isinstance(v, dict) and any(isinstance(iv, dict) for iv in v.values()):
            for iv in v.values():
                if isinstance(iv, dict):
                    flat.update(iv)
        else:
            flat[k] = v
    return flat


class DataEditNodeHandler(PendingHandlerBase):
    """Pauses the run so the user can review/edit an upstream table artifact.

    Adopts :class:`DataEditSchemaProvider` so ``GET /schema`` reflects
    post-PATCH column ops via the view-store's working ref. Bespoke
    ``resolve_prefill`` flattens the FE's TabWrapper nesting and
    recovers ``$artifacts.<name>`` references when the global
    namespace is empty. Bespoke ``provision_artifacts`` mints a
    working ``__file_ref`` tree (object_field root + nested
    ``table_data`` array_field) that PATCH /data targets directly.
    """

    pending_kind: ClassVar[str] = "data_edit"

    def __init__(
        self,
        variable_resolver: VariableResolver,
        file_service_client: Any = None,
        data_store_service: Any = None,
    ) -> None:
        super().__init__(variable_resolver)
        self._file_client = file_service_client
        self._data_store = data_store_service
        # DATA_EDIT's /schema endpoint follows live working_ref columns; it
        # shares this handler's resolver so the static-fallback branch resolves
        # ``{{…}}`` identically to dispatch.
        self._schema_provider = DataEditSchemaProvider(variable_resolver)

    # ------------------------------------------------------------------
    # PendingHandlerBase hooks
    # ------------------------------------------------------------------

    async def resolve_prefill(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        # Field sources, lowest precedence first (NodeSpec Phase 6):
        #
        # 1. ``node.config`` — the canonical home of the declared
        #    DATA_EDIT fields (file_id, query, readonly, actions, …).
        #    Flat-key payloads land here via the spec split, and the
        #    042 migration re-homed stored flat bindings here.
        # 2. ``node.input_binding`` — the FE submits values nested
        #    under the ``data_edit_tabs`` TabWrapper container, which
        #    is not a declared config key, so fresh FE edits arrive
        #    on the binding side and must win over migrated config.
        #
        # Both go through the same resolve_mapping so ``{{…}}``
        # expressions resolve regardless of which side carried them.
        cfg = _raw_fields(node)

        resolved: dict[str, Any] = {}
        if cfg:
            try:
                type_map = cfg.get("input_types")
                resolved = await self._resolver.resolve_mapping(
                    cfg, run.context.variables, type_map=type_map,
                )
            except VariableResolutionError as exc:
                # Recovery for ``{{$artifacts.<name>}}``: re-resolve
                # each binding entry, swapping the failing
                # ``$artifacts`` lookup for an artifact found by
                # scanning file_refs in the run context.
                recovered = await self._recover_with_artifact_lookup(
                    cfg, run.context.variables, type_map,
                )
                if recovered is None:
                    raise ExpressionError(
                        f"Node '{node.id}': input resolution failed: {exc}"
                    ) from exc
                resolved = recovered

        if "data_mode" not in resolved:
            resolved["data_mode"] = cfg.get("data_mode", "csv_single")

        ctx["cfg"] = cfg
        return resolved

    async def build_input_schema(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> list:
        # Resolve upstream file_id from the resolved binding. When the
        # binding evaluates to a metadata dict (``$artifacts.<name>``
        # returns the registered metadata object), the extractor pulls
        # out the file_id string. Write it back to ``prefilled`` so
        # ``task.resolved_input["file_id"]`` is the unwrapped string —
        # the FE / submit path treats this field as opaque text.
        file_id = self._extract_file_id(prefilled, node, run)
        if file_id and prefilled.get("file_id") != file_id:
            prefilled["file_id"] = file_id

        # Source-info extraction depends on the workflow definition. The
        # resolved file_id gates column adoption: a binding like
        # ``{{$node.response_body.data.someFileId}}`` points at a file
        # *named inside* the source node's output — the source ref's own
        # tracked columns describe a different file and must not be used.
        source_node_id, source_node_name, source_columns, source_row_count = (
            self._extract_source_info(node, workflow, run, resolved_file_id=file_id)
        )

        raw_query = prefilled.get("query")
        query_params: dict[str, Any] = {}
        if isinstance(raw_query, str) and raw_query.strip():
            from urllib.parse import parse_qsl
            query_params = dict(parse_qsl(
                raw_query.strip().lstrip("?"),
                keep_blank_values=True,
            ))

        fallback_columns = list(source_columns or []) or list(
            prefilled.get("columns") or []
        )
        readonly = bool(prefilled.get("readonly", False))
        custom_data_update = bool(prefilled.get("custom_data_update", False))
        actions = _parse_array_of_objects(prefilled.get("actions"))
        extra_settings = _parse_array_of_objects(prefilled.get("settings"))
        instruction = prefilled.get("instruction", node.instruction or "")
        # SA-1239: the designer's Select Mode ("none"/"single"/"multiple", or ""
        # when unset) must reach the dispatch-time schema — without this the
        # approval "Input Requested" JSON carried no selectionMode and the FE
        # fell back to multi-select regardless of the configured mode.
        selection_mode = str(prefilled.get("selectionMode") or "")

        # Stash intermediate state for downstream hooks
        ctx["source_node_id"] = source_node_id
        ctx["source_node_name"] = source_node_name
        ctx["source_columns"] = source_columns
        ctx["source_row_count"] = source_row_count
        ctx["file_id"] = file_id
        ctx["query_params"] = query_params
        ctx["fallback_columns"] = fallback_columns
        ctx["readonly"] = readonly
        ctx["custom_data_update"] = custom_data_update
        ctx["actions"] = actions
        ctx["extra_settings"] = extra_settings
        ctx["instruction"] = instruction
        ctx["selection_mode"] = selection_mode

        schema = await self._build_dynamic_table_schema(
            file_id, node, query_params, fallback_columns, readonly,
            actions, extra_settings, instruction, custom_data_update,
            selection_mode,
        )
        return schema or []

    async def provision_artifacts(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        input_schema: list,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Any | None:
        if self._file_client is None:
            return None

        file_id = ctx.get("file_id", "")
        source_columns = ctx.get("source_columns", []) or []

        # Seed columns precedence:
        #   1. Upstream source variable's tracked columns (cheapest).
        #   2. Headers of the resolved upstream file via an OData ``$top=1``
        #      probe — reads only the first row, never the full file body.
        #      This makes the working ref's ``columns`` authoritative for
        #      the schema-from-view path so GET /schema returns the real
        #      column set on the very first call after dispatch.
        #   3. Author-declared ``node.config.columns`` (no-upstream
        #      "DATA_EDIT authors data without upstream" case).
        seed_columns = list(source_columns)
        if not seed_columns and file_id:
            try:
                seed_columns = await self._file_client.get_columns_via_odata(file_id)
            except Exception:  # pragma: no cover — probe is best-effort
                logger.debug("OData column probe failed for %s", file_id)
        if not seed_columns:
            seed_columns = list(DataEditConfig.from_node(node).columns or ())

        # ``build_output_metadata`` runs after this hook; reflect the
        # probed columns so ``task.output_metadata.source_columns``
        # describes the file actually being edited.
        if seed_columns and not source_columns:
            ctx["source_columns"] = list(seed_columns)

        working_ref = await self._build_working_ref(
            upstream_file_id=file_id,
            columns=seed_columns,
            node_id=node.id,
        )
        return working_ref

    def build_output_metadata(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        resolved_input: Any,
        prefilled: dict[str, Any],
        input_schema: list,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "node_type": "DATA_EDIT",
            "input_schema": input_schema or [],
            "instruction": ctx.get("instruction", ""),
            "prefilled": {},
            "timeout_minutes": prefilled.get("timeout_minutes"),
            "file_id": ctx.get("file_id", ""),
            "source_file_id": ctx.get("file_id", ""),
            "source_node_id": ctx.get("source_node_id", ""),
            "source_node_name": ctx.get("source_node_name", ""),
            "source_columns": ctx.get("source_columns", []),
            "source_row_count": ctx.get("source_row_count", 0),
            "artifact_key": prefilled.get("artifact_key", ""),
            "query_params": ctx.get("query_params", {}),
            "readonly": ctx.get("readonly", False),
            "custom_data_update": ctx.get("custom_data_update", False),
            "actions": ctx.get("actions", []),
            "settings": ctx.get("extra_settings", []),
            # SA-1239: raw designer value ("none"/"single"/"multiple"/"") so a
            # runtime GET /schema resolves selectionMode identically to dispatch.
            "selectionMode": ctx.get("selection_mode", ""),
        }

    def build_input_requested_event(
        self,
        *,
        task: Task,
        run: WorkflowRun,
        node: WorkflowNode,
        instruction: str,
        prefilled: dict[str, Any],
        input_schema: list,
        ctx: dict[str, Any],
    ) -> DomainEvent:
        return InputRequested(
            task_id=task.id,
            run_id=run.id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            instruction=ctx.get("instruction", instruction),
            parent_run_id=run.parent_run_id or "",
            pending_kind="data_edit",
            source_file_id=ctx.get("file_id", ""),
            source_node_id=ctx.get("source_node_id", ""),
            source_node_name=ctx.get("source_node_name", ""),
            root_run_id=run.root_run_id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )

    async def resolve_instruction(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> str:
        # DATA_EDIT prefers the resolved binding's instruction (set by
        # ``build_input_schema``) over the static node.config value. The
        # binding value is already resolved via ``resolve_prefill``; the
        # base helper is a no-op when no ``{{…}}`` remains.
        raw = ctx.get(
            "instruction",
            prefilled.get("instruction", node.instruction or ""),
        )
        return await self._resolve_instruction_text(raw, run, ctx)

    def build_completion_event(
        self,
        task: Task,
        run: WorkflowRun,
        node: WorkflowNode,
        action: str,
    ) -> tuple[str, DomainEvent]:
        file_id = (task.resolved_input or {}).get("file_id", "")
        source_node_id = (task.output_metadata or {}).get("source_node_id", "")
        payload = InputSubmitted(
            task_id=task.id,
            run_id=task.run_id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            pending_kind="data_edit",
            file_id=file_id,
            source_node_id=source_node_id,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )
        return EventTopic.INPUT_SUBMITTED, payload

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_file_id(
        resolved: dict[str, Any],
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> str:
        """Return the upstream file_id, unwrapping artifact dicts as needed."""
        file_id = resolved.get("file_id", "")

        if isinstance(file_id, dict):
            from app.layer1_domain.entities.data_reference import is_file_ref, get_artifacts
            artifact_key = resolved.get("artifact_key", "")
            if artifact_key and is_file_ref(file_id):
                for art in get_artifacts(file_id):
                    if art.get("name") == artifact_key:
                        return art.get("file_id", "") or ""
                return file_id.get("file_id", "") or ""
            return file_id.get("file_id", "") or ""

        if not file_id:
            # Empty file_id + artifact_key → look up via the source ref.
            artifact_key = resolved.get("artifact_key", "")
            if artifact_key:
                source_ref = DataEditNodeHandler._extract_source_ref(node)
                if source_ref:
                    from app.layer1_domain.entities.data_reference import (
                        is_file_ref,
                        get_artifacts,
                    )
                    source_data = run.context.variables.get(source_ref, {})
                    if isinstance(source_data, dict) and is_file_ref(source_data):
                        for art in get_artifacts(source_data):
                            if art.get("name") == artifact_key:
                                return art.get("file_id", "") or ""
        return file_id or ""

    async def _build_working_ref(
        self,
        *,
        upstream_file_id: str,
        columns: list[str],
        node_id: str,
    ) -> dict[str, Any]:
        """Construct the working ``__file_ref`` tree consumed by PATCH /data.

        Shape:
        - root: ``object_field`` with a header-only (``__row_id``-only) CSV.
          DATA_EDIT has no scalar fields, but the root still gets a non-empty
          ``file_id`` so it matches the invariant the other PENDING handlers
          uphold via ``provision_schema_artifacts`` — "every input-emitting
          node has a non-empty ``__root__.file_id``."
        - one nested ``table_data`` ``array_field`` whose ``file_id`` is
          the upstream's. PATCH /data on ``table_data`` reads this file,
          applies the op, and (data-factory v2) writes a new version of the
          same file_id.

        When no upstream is bound, mint a header-only CSV via the file
        service so the row-transform factory doesn't reject the very
        first insert.
        """
        ref = build_file_ref(file_id="", artifact_id=ROOT_ARTIFACT_ID)
        ref["artifact_type"] = "object_field"
        ref["content_type"] = "text/csv"

        # Root holds no scalars; a header-only ``__row_id`` CSV keeps the
        # root file_id non-empty (uniform with the other pending types).
        # On upload failure, file_id stays "" — same graceful fallback as
        # the table mint below, so a file-service hiccup never blocks dispatch.
        root_file_id = await self._mint_empty_csv(
            columns=[], node_id=node_id, name="root",
        )
        if root_file_id:
            ref["file_id"] = root_file_id
            ref["columns"] = [ROW_ID_COLUMN]
            refresh_schema_hash(ref)

        table_file_id = upstream_file_id or ""
        if not table_file_id:
            table_file_id = await self._mint_empty_csv(
                columns=columns, node_id=node_id, name="table_data",
            )

        add_artifact(
            ref,
            file_id=table_file_id,
            name="table_data",
            artifact_id="table_data",
            artifact_type="array_field",
        )
        if ref.get("artifacts"):
            ref["artifacts"][0]["columns"] = list(columns or [])
            refresh_schema_hash(ref["artifacts"][0])
        return ref

    async def _mint_empty_csv(
        self,
        *,
        columns: list[str],
        node_id: str,
        name: str,
    ) -> str:
        """Upload a header-only CSV (``__row_id`` + ``columns``) and return
        its file_id. ``name`` distinguishes the artifact in the filename
        (e.g. ``root``, ``table_data``). Returns ``""`` on upload failure."""
        cols = list(columns or [])
        if ROW_ID_COLUMN not in cols:
            cols.insert(0, ROW_ID_COLUMN)
        header = ",".join(cols) + "\n"
        filename = f"{node_id or 'data_edit'}_{name}.csv"
        try:
            up = await self._file_client.upload_file(
                header.encode("utf-8"), filename, "text/csv",
            )
        except Exception:  # pragma: no cover — network safety net
            logger.exception("DATA_EDIT %s provisioning failed", name)
            return ""
        return (up.get("file_id") or "") if isinstance(up, dict) else ""

    async def _build_dynamic_table_schema(
        self,
        file_id: str,
        node: WorkflowNode,
        query_params: dict[str, Any] | None = None,
        fallback_columns: list[str] | None = None,
        readonly: bool = False,
        actions: list[dict[str, Any]] | None = None,
        extra_settings: list[dict[str, Any]] | None = None,
        instruction: str | None = None,
        custom_data_update: bool = False,
        selection_mode: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """Build an odata_array_field schema from file headers + OData default data."""
        headers: list[dict[str, Any]] | None = None

        if file_id and self._file_client:
            header_query = {**(query_params or {}), "$top": "1"}
            try:
                resp = await self._file_client.query_file_data(file_id, header_query)
                rows = resp.get("data", [])
                if rows:
                    headers = [
                        {"name": k} for k in rows[0].keys()
                        if not k.startswith("_")
                    ]
            except Exception:
                logger.warning("header extraction failed for file_id=%s", file_id)

        if not headers:
            user_cols = [
                c for c in (fallback_columns or [])
                if c and not c.startswith("_")
            ]
            if not user_cols:
                return None
            headers = [{"name": c} for c in user_cols]

        columns = []
        for hdr in headers:
            col_name = hdr.get("name", "")
            if col_name:
                columns.append(text_field(
                    key=col_name, label=col_name,
                    readonly=True if readonly else None,
                ))
        if not columns:
            return None

        # Row data is intentionally not embedded in the schema — the FE
        # fetches rows directly from the file-service via OData. Keeping
        # the schema row-free avoids loading large CSVs into control-plane
        # memory and matches ``synthesize_data_edit_schema``.
        table_key = "__table_data" if custom_data_update else "table_data"
        table = table_array_field(
            key=table_key,
            label=instruction or node.name or "Data Table",
            columns=columns,
        )
        table_dict = table.to_dict()

        wrapper_props = table_dict.setdefault("fieldConfig", {}).setdefault("wrapperProps", {})
        if readonly:
            wrapper_props["readonly"] = True
        else:
            wrapper_props["isAdd"] = True
            wrapper_props["isDelete"] = True

        # SA-1239: forward the designer's row-selection mode so the dispatch-time
        # schema (task.output_metadata.input_schema — what the FE first receives)
        # matches the runtime GET /schema (synthesize_data_edit_schema). Readonly
        # approval tables default to single (radio); "none" suppresses the prop
        # entirely (and overrides that default) so no selectionMode is emitted.
        # Shared resolver keeps this byte-for-byte aligned with the runtime path.
        # Lazy import mirrors schema_providers — avoids pulling the read module's
        # import graph into the handler at module load.
        from app.layer2_application.read.node_data_projection import (
            resolve_selection_mode,
        )

        resolved_selection = resolve_selection_mode(
            selection_mode, readonly=readonly,
        )
        if resolved_selection:
            wrapper_props["selectionMode"] = resolved_selection

        if actions:
            wrapper_props["actions"] = actions
        # Carry the configured OData query (``$filter``/``$orderby``/…) so the
        # FE's OdataWrapper seeds its row fetch with the author's filter instead
        # of pulling the full dataset. Without this the grid renders unfiltered.
        if query_params:
            wrapper_props["query"] = dict(query_params)
        if extra_settings:
            table_dict.setdefault("fields", []).extend(extra_settings)

        return [table_dict]

    @staticmethod
    def _extract_source_info(
        node: WorkflowNode,
        workflow: Workflow,
        run: WorkflowRun,
        resolved_file_id: str = "",
    ) -> tuple[str, str, list[str], int]:
        """Parse the file_id binding to identify the source node and its file metadata.

        ``resolved_file_id`` is the file the binding actually resolved to.
        Tracked columns/row_count are adopted only when the candidate ref
        describes that same file — otherwise the binding points at a
        different file (e.g. an id plucked from inside the source node's
        response payload) and its real columns come from the OData probe
        in ``provision_artifacts``.
        """
        source_node_id = ""
        source_node_name = ""
        source_columns: list[str] = []
        source_row_count = 0

        binding = _raw_fields(node)
        file_id_expr = binding.get("file_id", "")
        if not isinstance(file_id_expr, str):
            return source_node_id, source_node_name, source_columns, source_row_count

        artifacts_match = _EXPR_ARTIFACTS_RE.search(file_id_expr)
        if artifacts_match:
            artifact_name = artifacts_match.group(1)
            owner_var, art = DataEditNodeHandler._find_artifact_owner(
                run.context.variables, artifact_name,
            )
            if art is not None and (
                not resolved_file_id
                or art.get("file_id", "") == resolved_file_id
            ):
                source_columns = art.get("columns", []) or []
                source_row_count = art.get("row_count", 0) or 0

            if owner_var:
                source_node = workflow.get_node(owner_var)
                if source_node is None:
                    for n in workflow.nodes:
                        if n.name == owner_var:
                            source_node = n
                            break
                if source_node is not None:
                    source_node_id = source_node.id
                    source_node_name = source_node.name
                else:
                    source_node_id = owner_var
            return source_node_id, source_node_name, source_columns, source_row_count

        match = _EXPR_SOURCE_RE.search(file_id_expr)
        if not match:
            return source_node_id, source_node_name, source_columns, source_row_count

        source_ref = match.group(1)

        source_node = workflow.get_node(source_ref)
        if source_node:
            source_node_id = source_node.id
            source_node_name = source_node.name
        else:
            for n in workflow.nodes:
                if n.name == source_ref:
                    source_node_id = n.id
                    source_node_name = n.name
                    break
            if not source_node_id:
                source_node_id = source_ref

        source_data = run.context.variables.get(source_ref, {})
        if (
            isinstance(source_data, dict)
            and source_data.get("__file_ref")
            and (
                not resolved_file_id
                or source_data.get("file_id", "") == resolved_file_id
            )
        ):
            source_columns = source_data.get("columns", [])
            source_row_count = source_data.get("row_count", 0)

        return source_node_id, source_node_name, source_columns, source_row_count

    async def _recover_with_artifact_lookup(
        self,
        input_binding: dict[str, Any],
        variables: dict[str, Any],
        type_map: dict[str, str] | None,
    ) -> dict[str, Any] | None:
        """Resolve an input binding when ``$artifacts.<name>`` lookups failed.

        For each binding entry whose expression is a sole ``{{$artifacts.<name>}}``
        reference, scan the run context's file_refs for an artifact with that
        name and substitute the artifact's file_id directly. Other entries are
        re-resolved through the normal resolver. Returns the recovered mapping
        on success, or ``None`` if recovery isn't possible.
        """
        recovered: dict[str, Any] = {}
        recovered_at_least_one = False
        for key, value in input_binding.items():
            artifacts_match = (
                _EXPR_ARTIFACTS_RE.search(value)
                if isinstance(value, str) else None
            )
            if artifacts_match:
                _, art = self._find_artifact_owner(
                    variables, artifacts_match.group(1),
                )
                if art is None:
                    return None
                resolved_value: Any = art.get("file_id", "") or ""
                recovered_at_least_one = True
            else:
                try:
                    resolved_value = await self._resolver._resolve_value(value, variables)
                except VariableResolutionError:
                    return None
            if type_map and key in type_map:
                resolved_value = self._resolver.coerce_value(
                    resolved_value, type_map[key],
                )
            recovered[key] = resolved_value
        return recovered if recovered_at_least_one else None

    @staticmethod
    def _find_artifact_owner(
        variables: dict[str, Any],
        artifact_name: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Find the node-context variable that owns a named artifact."""
        from app.layer1_domain.entities.data_reference import is_file_ref
        for key, value in variables.items():
            if key == "artifacts" or not is_file_ref(value):
                continue
            for art in value.get("artifacts", []) or []:
                if art.get("name") == artifact_name:
                    return key, art
        return "", None

    @staticmethod
    def _extract_source_ref(node: WorkflowNode) -> str:
        """Extract the source node reference from the file_id binding expression."""
        binding = _raw_fields(node)
        file_id_expr = binding.get("file_id", "")
        if not isinstance(file_id_expr, str):
            return ""
        match = _EXPR_SOURCE_RE.search(file_id_expr)
        return match.group(1) if match else ""
