"""Variable-path suggestions for the workflow builder's autocomplete.

Emits the paths a designer can reference via ``{{$node.…}}`` or
``{{$artifacts.…}}`` when wiring a downstream node's input_binding. Walks the
upstream schema tree recursively and classifies each leaf with a ``kind``
hint so the FE can render the autocomplete chip appropriately.

Kind taxonomy (matches the design-doc ``PathKind`` enum):

- ``scalar``          — primitive value (string / number / boolean)
- ``file_ref``        — file metadata object ``{file_id, filename, …}``
- ``artifact``        — an artifact-bound table (dotted-path id)
- ``artifact_row``    — a row-iterator path ``$.node.orders[*]``
- ``artifact_column`` — a column-extractor path ``$.node.orders[*].sku``

The walker respects a hard depth cap (``MAX_SCHEMA_DEPTH = 10``) so
pathologically-deep schemas can't exhaust the stack or explode the output.

``VariablePath`` shape is a **clean-break** replacement of the pre-Unit-F
shape — the legacy ``type`` / ``label`` fields are gone; consumers read
``output_type`` (the FE-serialized wire type) and, optionally, ``sample``
(a runtime sample value from the current context, when available).
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
from collections.abc import Mapping

from app.layer1_domain.entities.data_reference import (
    FILE_REF_MARKER,
    get_artifacts,
    is_file_ref,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.interfaces.scope_provider_interface import ScopeType
from app.layer2_application.repositories.environment_repo_interface import (
    ListEnvironmentsFilter,
)
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
)

MAX_SCHEMA_DEPTH = 10
PathKind = Literal["scalar", "file_ref", "artifact", "artifact_row", "artifact_column"]

# Keys of the FE file-metadata object (emitted when a field is a file_ref).
_FILE_REF_SUBFIELDS: tuple[tuple[str, str], ...] = (
    ("file_id", "string"),
    ("filename", "string"),
    ("content_type", "string"),
    ("size_bytes", "number"),
)

# Controls known to emit file-ref metadata objects.
_FILE_UPLOAD_CONTROLS = {
    "UploadControl",
    "SingleUploadFieldMappingControl",
}


# ---------------------------------------------------------------------------
# Command / Result types
# ---------------------------------------------------------------------------

@dataclass
class GetVariableSuggestionsCommand:
    run_id: str = ""
    node_id: str = ""   # the target node being configured
    environment_id: str = ""  # SA-2365: preview a chosen environment's $env vars in the panel


@dataclass
class VariablePath:
    path: str = ""
    kind: PathKind = "scalar"
    output_type: str = "string"     # string | number | boolean | object | array
    # When ``output_type == "array"``, the declared element type
    # (``string``/``number``/``boolean``/``object``). None for non-array
    # paths or arrays whose item type is unknown.
    item_type: str | None = None
    sample: Any = None
    # Populated for ``kind=="file_ref"`` paths whose sample carries a file_id.
    # Old FE versions render data tables directly off these without an extra
    # round-trip to the file service. Empty for scalars / non-file paths.
    columns: list[str] = field(default_factory=list)
    preview_rows: list[dict[str, Any]] = field(default_factory=list)
    # FE-friendly fields, set by ``_finalize_path`` once per path before
    # serialization. ``label`` is the raw path tail. ``expandable`` is true
    # iff the path will render as a preview table (file_ref + non-empty
    # columns). ``display_path`` is the full ready-to-insert expression.
    label: str = ""
    expandable: bool = False
    display_path: str = ""


def _finalize_path(p: "VariablePath", *, expression_prefix: str) -> "VariablePath":
    """Populate ``label`` / ``expandable`` / ``display_path`` in place.

    Idempotent — safe to call repeatedly. Returns the same instance for
    convenience in comprehensions.
    """
    p.label = p.path.rsplit(".", 1)[-1]
    p.expandable = p.kind == "file_ref" and bool(p.columns)
    p.display_path = f"{expression_prefix}.{p.path}}}}}" if p.path else f"{expression_prefix}}}}}"
    return p


@dataclass
class ArtifactSuggestion:
    file_id: str = ""
    name: str = ""
    artifact_type: str = ""


@dataclass
class Scope:
    """A uniform scope record returned in the response's ``scopes[]``.

    Replaces the legacy three-collection shape (``input`` / ``sources`` /
    ``artifacts``). Each scope is rendered as one card on the FE; the
    discriminator is ``scope_type``.

    File-backed extras (``has_file_data`` / ``file_id`` / ``columns`` /
    ``preview_rows`` / ``artifacts`` / ``default_artifact_path``) are
    populated only for ``scope_type == "node"`` today.
    """

    scope_type: ScopeType = "node"
    scope_id: str = ""
    label: str = ""
    expression_prefix: str = ""
    paths: list[VariablePath] = field(default_factory=list)
    # ``node_type`` is the upstream node's type (TASK / CONDITION / ...)
    # for ``scope_type == "node"``. None for system / aggregate scopes.
    node_type: str | None = None
    has_file_data: bool = False
    file_id: str | None = None
    columns: list[str] = field(default_factory=list)
    preview_rows: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[ArtifactSuggestion] = field(default_factory=list)
    default_artifact_path: str | None = None


@dataclass
class WorkflowInfo:
    id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    status: str = ""
    purpose: str = ""
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RunInfo:
    id: str = ""
    workflow_id: str = ""
    status: str = ""
    started_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    test_mode: str = ""
    parent_run_id: str | None = None
    parent_node_id: str | None = None
    depth_level: int = 0
    root_run_id: str = ""


@dataclass
class GetVariableSuggestionsResult:
    run_id: str = ""
    node_id: str = ""
    workflow: WorkflowInfo | None = None
    run: RunInfo | None = None
    scopes: list[Scope] = field(default_factory=list)
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DAG helpers (unchanged from pre-F1)
# ---------------------------------------------------------------------------

def _collect_upstream_node_ids(workflow: Any, target_node_id: str) -> list[str]:
    """Walk DAG edges backward; return sorted transitive upstream node ids."""
    upstream: set[str] = set()
    queue: deque[str] = deque([target_node_id])
    while queue:
        current = queue.popleft()
        for edge in workflow.get_incoming_edges(current):
            src = edge.source_node_id
            if src not in upstream:
                upstream.add(src)
                queue.append(src)
    return sorted(upstream)


def _schema_list(value: Any) -> list[dict[str, Any]]:
    """Normalize schema storage (absent / JSON text / already parsed)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------

def _artifact_binding(field_def: dict[str, Any]) -> str | None:
    """Return the artifact_binding string if the field is artifact-bound."""
    cfg = field_def.get("fieldConfig") or {}
    wp = cfg.get("wrapperProps") or {}
    binding = wp.get("artifact_binding")
    return str(binding) if binding else None


def _is_file_ref_scalar(field_def: dict[str, Any]) -> bool:
    """True when the field produces a file-ref metadata object.

    Heuristic: ``fieldControl`` is one of the known upload controls. Output
    type is usually ``"object"`` but we don't require it — the control is the
    authoritative signal.
    """
    cfg = field_def.get("fieldConfig") or {}
    return cfg.get("fieldControl") in _FILE_UPLOAD_CONTROLS


def _output_type(field_def: dict[str, Any]) -> str:
    """Extract ``outputType`` (accept legacy ``type`` alias)."""
    return str(field_def.get("outputType") or field_def.get("type") or "string")


def _infer_path_output_type(value: Any) -> str:
    """Map a Python runtime value to the FE-serialized output_type string."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


# ---------------------------------------------------------------------------
# Schema walker — recursive, bounded, emits all 5 kinds
# ---------------------------------------------------------------------------

def _walk_schema_tree(
    schema_fields: list[dict[str, Any]],
    *,
    path_prefix: str = "",
    depth: int = 0,
    samples: dict[str, Any] | None = None,
) -> list[VariablePath]:
    """DFS the schema; emit one or more ``VariablePath`` per leaf.

    Behaviour per field:

    - **Artifact-bound** (has ``wrapperProps.artifact_binding``): emit three
      variants at the field's own path — ``artifact``, ``artifact_row``
      (``[*]``), and one ``artifact_column`` per declared column. Recurse
      into nested artifact-bound children with the ``[*]`` joint so paths
      look like ``$.node.orders[*].line_items[*].price``.

    - **File-ref scalar** (upload control): emit the path as ``file_ref``
      plus four auto-expanded sub-paths
      (``.file_id`` / ``.filename`` / ``.content_type`` / ``.size_bytes``).

    - **Object with nested fields**: emit the parent path as ``scalar`` (the
      whole object is addressable as a scalar inline value) and recurse into
      children.

    - **Everything else**: emit as ``scalar``.

    ``samples`` is an optional flat ``path → value`` map used to attach
    runtime preview values to emitted paths.
    """
    if depth >= MAX_SCHEMA_DEPTH:
        return []
    paths: list[VariablePath] = []
    samples = samples or {}

    for f in schema_fields or []:
        if not isinstance(f, dict):
            continue
        key = f.get("key")
        if not key:
            continue
        local_path = f"{path_prefix}.{key}" if path_prefix else key
        out_type = _output_type(f)
        binding = _artifact_binding(f)

        if binding is not None:
            # Artifact-bound table → 3 kinds + recurse on nested tables/columns.
            paths.append(VariablePath(
                path=local_path, kind="artifact", output_type="array",
            ))
            row_path = f"{local_path}[*]"
            paths.append(VariablePath(
                path=row_path, kind="artifact_row", output_type="object",
            ))
            for col in (f.get("fields") or []):
                if not isinstance(col, dict):
                    continue
                col_key = col.get("key")
                if not col_key:
                    continue
                col_binding = _artifact_binding(col)
                col_path = f"{row_path}.{col_key}"
                if col_binding is not None:
                    # Nested artifact → recurse; don't emit a flat
                    # artifact_column for it (the whole subtree comes back
                    # from the recursion).
                    paths.extend(_walk_schema_tree(
                        [col], path_prefix=row_path, depth=depth + 1,
                        samples=samples,
                    ))
                elif _is_file_ref_scalar(col):
                    paths.append(VariablePath(
                        path=col_path, kind="file_ref", output_type="object",
                    ))
                    for sub_key, sub_type in _FILE_REF_SUBFIELDS:
                        paths.append(VariablePath(
                            path=f"{col_path}.{sub_key}", kind="scalar",
                            output_type=sub_type,
                        ))
                else:
                    paths.append(VariablePath(
                        path=col_path, kind="artifact_column",
                        output_type=_output_type(col),
                    ))
            continue

        if _is_file_ref_scalar(f):
            paths.append(VariablePath(
                path=local_path, kind="file_ref", output_type="object",
                sample=samples.get(local_path),
            ))
            for sub_key, sub_type in _FILE_REF_SUBFIELDS:
                sub_path = f"{local_path}.{sub_key}"
                paths.append(VariablePath(
                    path=sub_path, kind="scalar", output_type=sub_type,
                    sample=samples.get(sub_path),
                ))
            continue

        children = f.get("fields")
        if out_type == "object" and isinstance(children, list) and children:
            # Nested form object — emit parent as a scalar view and recurse
            # for each child leaf.
            paths.append(VariablePath(
                path=local_path, kind="scalar", output_type="object",
                sample=samples.get(local_path),
            ))
            paths.extend(_walk_schema_tree(
                children, path_prefix=local_path, depth=depth + 1,
                samples=samples,
            ))
            continue

        # Primitive scalar.
        paths.append(VariablePath(
            path=local_path, kind="scalar", output_type=out_type,
            sample=samples.get(local_path),
        ))

    return paths


# ---------------------------------------------------------------------------
# Context → samples
# ---------------------------------------------------------------------------

def _flatten_samples(value: Any, prefix: str = "", out: dict[str, Any] | None = None,
                    depth: int = 0) -> dict[str, Any]:
    """Flatten a dict tree into a ``"a.b.c" → value`` map, for preview samples.

    Non-dict leaves become the scalar value. File-ref dicts are left intact
    at their parent path so ``samples[path]`` returns the whole metadata
    object; we don't recurse into file-ref contents here (the schema walker
    auto-expands those).
    """
    out = {} if out is None else out
    if depth >= MAX_SCHEMA_DEPTH:
        return out
    if not isinstance(value, dict):
        if prefix:
            out[prefix] = value
        return out
    if value.get(FILE_REF_MARKER) is True:
        # Preserve file-ref at its path; skip recursion.
        if prefix:
            out[prefix] = value
        return out
    for k in value.keys():
        child = value[k]
        new_prefix = f"{prefix}.{k}" if prefix else k
        if isinstance(child, dict):
            # Record the dict itself at this level (useful for nested-form samples),
            # then recurse.
            out.setdefault(new_prefix, child)
            _flatten_samples(child, new_prefix, out, depth + 1)
        else:
            out[new_prefix] = child
    return out


def _extracted_samples(value: Any) -> dict[str, Any]:
    """Pull the ``extracted`` cache from a file_ref (Unit A's typed scalars)."""
    if not isinstance(value, dict):
        return {}
    extracted = value.get("extracted") or {}
    result: dict[str, Any] = {}
    for key, entry in extracted.items():
        if isinstance(entry, dict) and "value" in entry and len(entry) == 1:
            result[key] = entry["value"]
        else:
            result[key] = entry
    return result


# ---------------------------------------------------------------------------
# Meta paths + artifact suggestion (file_ref → ``__file_id`` etc.)
# ---------------------------------------------------------------------------

def _meta_paths_for_file_ref(value: dict[str, Any], file_id: str | None) -> list[VariablePath]:
    """Build meta-path suggestions for a file-backed node output.

    These surface the engine-level metadata alongside user-facing scalars,
    so advanced users can reference e.g. ``{{$node.__file_id}}``.
    """
    paths: list[VariablePath] = [
        VariablePath(path="__file_id", kind="scalar", output_type="string",
                     sample=file_id or value.get("file_id", "")),
        VariablePath(path="__row_count", kind="scalar", output_type="number",
                     sample=value.get("row_count", 0)),
        VariablePath(path="__columns", kind="scalar", output_type="array",
                     sample=value.get("columns", [])),
        VariablePath(path="__content_type", kind="scalar", output_type="string",
                     sample=value.get("content_type", "")),
    ]
    artifacts = get_artifacts(value)
    for i, art in enumerate(artifacts):
        name = art.get("name", "")
        paths.append(VariablePath(
            path=f"__artifacts[{i}].file_id", kind="scalar",
            output_type="string", sample=art.get("file_id", ""),
        ))
        if name:
            paths.append(VariablePath(
                path=f"__artifact.{name}", kind="scalar",
                output_type="string", sample=art.get("file_id", ""),
            ))
    return paths


def _artifacts_registry_paths(registry: dict[str, Any]) -> list[VariablePath]:
    """Emit ``$artifacts.<name>`` + sub-field paths for each registry entry.

    Reads the metadata-object shape (Unit A): each ``entry`` is
    ``{file_id, filename, content_type, size_bytes, produced_by_node, ...}``.
    Emits the entry itself as ``kind="file_ref"`` + four scalar sub-paths.
    Legacy bare-string entries are silently skipped (the new contract
    requires metadata objects).
    """
    paths: list[VariablePath] = []
    for name in sorted(registry.keys()):
        entry = registry[name]
        if not isinstance(entry, dict) or not entry.get("file_id"):
            continue
        paths.append(VariablePath(
            path=name, kind="file_ref", output_type="object", sample=entry,
        ))
        for sub_key, sub_type in _FILE_REF_SUBFIELDS:
            paths.append(VariablePath(
                path=f"{name}.{sub_key}", kind="scalar",
                output_type=sub_type, sample=entry.get(sub_key),
            ))
    return paths


# ---------------------------------------------------------------------------
# Node-source suggestion builder
# ---------------------------------------------------------------------------

def _build_source_for_node(
    node: Any,
    ctx_value: Any,
    *,
    expression_prefix: str,
) -> SourceNodeSuggestion:
    """Build the ``SourceNodeSuggestion`` for a node, combining schema walker
    (for all reachable paths) with runtime samples (where available)."""
    # Gather samples from runtime context.
    samples: dict[str, Any] = {}
    file_id: str | None = None
    ref_columns: list[str] = []
    if isinstance(ctx_value, dict):
        if ctx_value.get(FILE_REF_MARKER) is True:
            file_id = ctx_value.get("file_id") or None
            ref_columns = list(ctx_value.get("columns") or [])
            samples.update(_extracted_samples(ctx_value))
        else:
            samples.update(_flatten_samples(ctx_value))

    # Resolve the node's output-describing schema (design-time template, then
    # inline override, then legacy input_schema).
    node_cfg = getattr(node, "config", None) or {}
    node_def = getattr(node, "definition_snapshot", None) or {}
    output_schema = (
        _schema_list(node_def.get("output_schema"))
        or _schema_list(node_cfg.get("output_schema"))
        or _schema_list(node_cfg.get("input_schema"))
    )

    paths = _walk_schema_tree(output_schema, samples=samples)

    # Runtime columns that aren't represented in the schema (e.g., data-factory
    # output where the schema is inferred) — append as scalar paths so the FE
    # can at least autocomplete raw column names.
    schema_paths = {p.path for p in paths}
    for col in ref_columns:
        if col and col not in schema_paths and not col.startswith("__"):
            paths.append(VariablePath(
                path=col, kind="scalar", output_type="string",
                sample=samples.get(col),
            ))
            schema_paths.add(col)

    # Runtime samples not represented in the schema — emit each as a scalar
    # path so the FE can build {{$<node_id>.<key>}} expressions when the
    # node's output_schema is missing or sparse (e.g., HTTP-worker JSON
    # responses, legacy inline-dict outputs).
    for sample_path, sample_value in samples.items():
        if (
            sample_path
            and sample_path not in schema_paths
            and not sample_path.startswith("__")
        ):
            if is_file_ref(sample_value):
                # A file-backed field nested in an otherwise-inline output —
                # e.g. a code node whose large ``result`` array the worker
                # streamed to a CSV file_ref (SA-1905 C2). Emit it as a
                # ``file_ref`` path (not a raw scalar) so the FE renders a
                # data table via OData instead of trying to parse the pointer
                # object as an inline array. ``columns`` + preview rows are
                # attached later by ``_enrich_preview`` / the file service.
                paths.append(VariablePath(
                    path=sample_path, kind="file_ref", output_type="object",
                    sample=sample_value,
                    columns=list(sample_value.get("columns") or []),
                ))
            else:
                paths.append(VariablePath(
                    path=sample_path, kind="scalar",
                    output_type=_infer_path_output_type(sample_value),
                    sample=sample_value,
                ))
            schema_paths.add(sample_path)

    node_type_str = (
        node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type)
    )
    node_id = getattr(node, "id", "") or ""
    return Scope(
        scope_type="node",
        scope_id=node_id,
        label=getattr(node, "name", None) or node_id,
        node_type=node_type_str,
        expression_prefix=expression_prefix,
        paths=paths,
        file_id=file_id,
        columns=ref_columns,
    )


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class GetVariableSuggestionsUseCase:
    """Thin coordinator: resolve run/workflow, dispatch to scope providers."""

    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        workflow_repo: IWorkflowRepository,
        scope_providers: Optional[list[Any]] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        environment_repo: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.workflow_repo = workflow_repo
        self._env_repo = environment_repo
        self._providers = sorted(scope_providers or [], key=lambda p: getattr(p, "order", 0))
        # Phase 3b.1 stopped writing node outputs to ``run.context.variables``
        # — the projector view is now the authoritative read side. Without
        # this view store wired, source-scope columns / preview_rows stay
        # empty for completed file-emitting nodes.
        self._view_store = run_context_view_store

    def _resolve_variables(self, run: Any) -> Mapping[str, Any]:
        """View-on-aggregate merge: projector wins for known keys; the
        aggregate fills gaps (notably ``"artifacts"``, which
        ``register_artifacts`` mutates directly and isn't covered by
        ``NodeOutputProduced`` events).
        """
        aggregate = run.context.variables or {}
        if self._view_store is None:
            return aggregate
        try:
            view = self._view_store.get(run.id)
        except Exception:
            view = None
        if view is None:
            return aggregate
        merged = dict(aggregate)
        for k, v in view.variables.items():
            merged[k] = v
        return merged

    def _union_env_keys(self, run: Any, workflow: Any) -> dict[str, Any]:
        """SA-2365 (Hướng C): union of $env variable KEYS across ALL environments
        (every section), values EMPTY (no preview). Powers the node panel's
        Environment tab so it always offers draggable {{$env.KEY}}. Values are on
        the Environments config page; a future feature keeps every environment's
        key set identical, so no per-key section disambiguation is needed.
        """
        if self._env_repo is None:
            return {}
        tenant = (
            getattr(run, "tenant_id", None)
            or getattr(workflow, "tenant_id", None)
            or "system"
        )
        try:
            page = self._env_repo.list_paged(
                ListEnvironmentsFilter(tenant_id=tenant, include_inactive=False, top=500)
            )
        except Exception:  # noqa: BLE001 — suggestions must never fail the panel
            return {}
        keys: dict[str, Any] = {}
        for env in page.items:
            for key in env.suggestion_env_map().keys():
                keys.setdefault(key, "")  # empty value, no preview
        return keys

    async def execute(
        self, command: GetVariableSuggestionsCommand,
    ) -> GetVariableSuggestionsResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return GetVariableSuggestionsResult(
                run_id=command.run_id, node_id=command.node_id,
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        workflow = await self.workflow_repo.aget_by_id(run.workflow_id)
        if not workflow:
            return GetVariableSuggestionsResult(
                run_id=command.run_id, node_id=command.node_id,
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": run.workflow_id},
            )

        target_node = workflow.get_node(command.node_id)
        if target_node:
            upstream_node_ids = _collect_upstream_node_ids(workflow, command.node_id)
        else:
            # Target node isn't persisted yet — return every node so the FE
            # can filter. (Matches pre-F1 behaviour.)
            upstream_node_ids = sorted(n.id for n in workflow.nodes)

        variables = self._resolve_variables(run)

        # SA-2365: preview the environment chosen in the run-toolbar dropdown,
        # so the node panel offers its $env vars even when the run was not
        # started with that environment (authoring aid). Overrides the run's own
        # env source for suggestions only; credential ids stay masked.
        if command.environment_id and self._env_repo is not None:
            try:
                env = self._env_repo.get_by_id(command.environment_id)
            except Exception:  # noqa: BLE001 — suggestions must never fail the panel
                env = None
            if env is not None:
                variables = {**dict(variables), "env": env.suggestion_env_map()}
        else:
            # SA-2365 (Hướng C): no specific environment selected → still offer the
            # UNION of $env KEYS across ALL environments (every section), values
            # EMPTY, so the node panel's Environment tab ALWAYS shows draggable
            # {{$env.KEY}} entries. Values are previewed on the Environments config
            # page and resolved at run by the environment chosen in the Execute popup.
            variables = {**dict(variables), "env": self._union_env_keys(run, workflow)}

        scopes: list[Scope] = []
        for provider in self._providers:
            produced = await provider.build(
                run=run,
                target_node_id=command.node_id,
                target_workflow=workflow,
                variables=variables,
                upstream_node_ids=upstream_node_ids,
            )
            scopes.extend(produced)

        return GetVariableSuggestionsResult(
            run_id=command.run_id, node_id=command.node_id,
            workflow=self._workflow_info(workflow), run=self._run_info(run),
            scopes=scopes,
        )

    @staticmethod
    def _workflow_info(workflow: Any) -> WorkflowInfo:
        status = (
            workflow.status.value if hasattr(workflow.status, "value")
            else str(workflow.status)
        )
        return WorkflowInfo(
            id=workflow.id, name=workflow.name, version=workflow.version,
            description=workflow.description, status=status,
            purpose=getattr(workflow, "purpose", ""),
            input_schema=workflow.input_schema or [],
            output_schema=getattr(workflow, "output_schema", None) or [],
            created_at=getattr(workflow, "created_at", ""),
            updated_at=getattr(workflow, "updated_at", ""),
        )

    @staticmethod
    def _run_info(run: Any) -> RunInfo:
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return RunInfo(
            id=run.id, workflow_id=run.workflow_id, status=status,
            started_at=getattr(run, "started_at", ""),
            completed_at=getattr(run, "completed_at", None),
            error=run.error,
            test_mode=getattr(run, "test_mode", ""),
            parent_run_id=getattr(run, "parent_run_id", None),
            parent_node_id=getattr(run, "parent_node_id", None),
            depth_level=getattr(run, "depth_level", 0),
            root_run_id=getattr(run, "root_run_id", ""),
        )

