"""Scope providers for the variable-suggestions endpoint.

Each provider owns one ``scope_type`` and emits zero or more :class:`Scope`
records. The use case sorts providers by ``order`` and concatenates their
outputs into the response's ``scopes`` list.

Adding a new scope kind = adding one provider class + one bootstrap line.
"""
from __future__ import annotations

from typing import Any, Optional
from collections.abc import Mapping, Sequence

from app.layer1_domain.entities.data_reference import (
    FILE_REF_MARKER,
    get_artifacts,
    is_file_ref,
)
from app.layer2_application.features.get_variable_suggestions.use_cases.get_variable_suggestions_use_case import (
    ArtifactSuggestion,
    Scope,
    VariablePath,
    _artifacts_registry_paths,
    _build_source_for_node,
    _extracted_samples,
    _finalize_path,
    _flatten_samples,
    _infer_path_output_type,
    _meta_paths_for_file_ref,
    _walk_schema_tree,
)
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import child_runs_of
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
)


async def _fetch_columns_and_preview(
    file_client: Optional[IFileServiceClient],
    logger: Optional[ILogger],
    file_id: str,
    *,
    top: int = 5,
) -> tuple[list[str], list[dict[str, Any]]]:
    """``(columns, preview_rows)`` for ``file_id`` via the file service.

    Returns ``([], [])`` when no client is wired or the fetch fails — never
    raises. Callers should memoize per file_id within a single request.
    """
    if not file_id or file_client is None:
        return [], []
    try:
        result = await file_client.query_file_data(file_id, {"$top": str(top)})
    except Exception:
        if logger is not None:
            logger.warning("preview fetch failed for %s", file_id)
        return [], []
    rows = result.get("data") or []
    cleaned = [
        {k: v for k, v in row.items() if k != "_row_index"}
        for row in rows
    ]
    cols = list(cleaned[0].keys()) if cleaned else []
    return cols, cleaned


def _finalize_paths(scope: Scope) -> Scope:
    """Apply ``_finalize_path`` to every path in a scope. Returns the same
    instance for fluent chaining."""
    for p in scope.paths:
        _finalize_path(p, expression_prefix=scope.expression_prefix)
    return scope


_RUN_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "string"),
    ("workflow_id", "string"),
    ("status", "string"),
    ("started_at", "string"),
    ("completed_at", "string"),
    ("test_mode", "string"),
    ("parent_run_id", "string"),
    ("parent_node_id", "string"),
    ("depth_level", "number"),
    ("root_run_id", "string"),
)


class RunScopeProvider:
    """Surfaces run-level identifiers (``{{$run.id}}``, etc.).

    Always present. Path samples are pulled live from the run aggregate so
    callers see the current snapshot.
    """

    scope_type = "run"
    order = 10

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        prefix = "{{$run"
        paths: list[VariablePath] = []
        for key, output_type in _RUN_FIELDS:
            sample = _safe_attr(run, key)
            paths.append(_finalize_path(
                VariablePath(path=key, kind="scalar", output_type=output_type, sample=sample),
                expression_prefix=prefix,
            ))
        return [Scope(
            scope_type="run", scope_id="run", label="Run",
            expression_prefix=prefix, paths=paths,
        )]


_WORKFLOW_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "string"),
    ("name", "string"),
    ("version", "string"),
    ("description", "string"),
    ("status", "string"),
    ("purpose", "string"),
    ("created_at", "string"),
    ("updated_at", "string"),
)


class WorkflowScopeProvider:
    """Surfaces workflow-level identifiers (``{{$workflow.id}}``, etc.)."""

    scope_type = "workflow"
    order = 20

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        prefix = "{{$workflow"
        paths: list[VariablePath] = []
        for key, output_type in _WORKFLOW_FIELDS:
            sample = _safe_attr(target_workflow, key)
            paths.append(_finalize_path(
                VariablePath(path=key, kind="scalar", output_type=output_type, sample=sample),
                expression_prefix=prefix,
            ))
        return [Scope(
            scope_type="workflow", scope_id="workflow", label="Workflow",
            expression_prefix=prefix, paths=paths,
        )]


def _safe_attr(obj: Any, name: str) -> Any:
    """Read attribute, coerce enums to their value, return None on miss."""
    if obj is None:
        return None
    val = getattr(obj, name, None)
    if val is None:
        return None
    # Enums (RunStatus, etc.) — surface the .value
    inner = getattr(val, "value", None)
    return inner if inner is not None else val


class VariablesScopeProvider:
    """Surfaces ``{{$variables.<name>}}`` — runtime mutable state.

    Reads from ``run.context.variables["__vars"]`` (merged with view-store
    if a view is available — handled by the use case before calling
    ``build``). Always emits the scope card so the picker has a
    discoverability hint, even when no variables are set yet.

    Declared type wins: when a name is declared on any ``SET_VARIABLE`` node
    in the workflow, the declared ``type``/``item_type`` is reported via
    ``output_type``/``item_type``. This is stable across runs (and is the
    only signal available before the variable has been set). When no
    declaration is found, the type is inferred from the runtime sample.
    """

    scope_type = "variables"
    order = 30

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        prefix = "{{$variables"
        # No-``extracted`` model: a file-backed ``__vars`` exposes no inline
        # scalars, so the picker surfaces declared names (below) without sample
        # values — sample fetch would be a bounded OData read (deferred). A
        # plain-dict ``__vars`` (legacy / in-process) still yields name→value.
        vars_raw = (variables or {}).get("__vars") or {}
        vars_view = (
            vars_raw
            if isinstance(vars_raw, dict) and not vars_raw.get("__file_ref")
            else {}
        )
        declared = _collect_variable_declarations(target_workflow)
        # Surface every declared name, even if not yet written, so the
        # picker shows the variable as soon as the SET_VARIABLE node is
        # added to the workflow.
        names = sorted(set(vars_view.keys()) | set(declared.keys()))
        paths: list[VariablePath] = []
        for name in names:
            sample = vars_view.get(name)
            decl = declared.get(name)
            if decl is not None and decl[0]:
                output_type, item_type = decl
            else:
                output_type, item_type = _infer_output_type(sample), None
            paths.append(_finalize_path(
                VariablePath(
                    path=name, kind="scalar",
                    output_type=output_type, item_type=item_type,
                    sample=sample,
                ),
                expression_prefix=prefix,
            ))
        return [Scope(
            scope_type="variables", scope_id="variables", label="Variables",
            expression_prefix=prefix, paths=paths,
        )]


def _collect_variable_declarations(
    workflow: Any,
) -> dict[str, tuple[str | None, str | None]]:
    """Walk ``workflow.nodes`` for ``SET_VARIABLE`` declarations.

    Returns a ``{name: (type, item_type)}`` map; ``type`` is ``None`` when the
    author hasn't picked one (the ``type`` field has no form default), so the
    name still surfaces in the picker and the provider infers the type from the
    runtime sample. Last writer wins on name collision (parallel-write conflicts
    are blocked by the DAG validator, so ordering only affects sequential
    overwrites — which authors typically declare with the same type anyway).
    """
    decls: dict[str, tuple[str | None, str | None]] = {}
    nodes = getattr(workflow, "nodes", None) or []
    for node in nodes:
        node_type = getattr(node, "node_type", None)
        type_value = getattr(node_type, "value", node_type)
        if type_value != "SET_VARIABLE":
            continue
        config = getattr(node, "config", None) or {}
        for entry in (config.get("variables") or []):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            declared = entry.get("type")
            if not isinstance(declared, str) or not declared:
                # ponytail: name alone is enough for autocomplete; type inferred below.
                declared = None
            item = entry.get("item_type")
            decls[name] = (
                declared,
                item if isinstance(item, str) and item else None,
            )
    return decls


def _infer_output_type(value: Any) -> str:
    """Map a Python runtime value to the FE wire type string."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


class CredentialsScopeProvider:
    """Surfaces ``{{$credentials.<name>}}`` — named runtime credential map.

    Emits one path card per credential name for autocomplete. Never sets
    ``sample`` — no plaintext credential leaks to the frontend or event log.
    Always emits the scope card so the picker has a discoverability hint
    even before a run has started.
    """

    scope_type = "credentials"
    order = 25  # between workflow=20 and variables=30

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        prefix = "{{$credentials"
        # __credentials is an EncryptedCredential at-rest; we only surface the
        # names, never the decrypted config. We can't enumerate names without
        # decrypting, so emit a generic path hint only when we know credentials
        # were provided (key present). Callers who want per-name completion
        # should pass credential_ref explicitly in the node binding.
        has_creds = "__credentials" in (variables or {})
        paths: list[VariablePath] = []
        if has_creds:
            paths.append(_finalize_path(
                VariablePath(
                    path="default", kind="scalar", output_type="object",
                    sample=None,  # NEVER set — no plaintext to frontend
                ),
                expression_prefix=prefix,
            ))
        return [Scope(
            scope_type="credentials", scope_id="credentials", label="Credentials",
            expression_prefix=prefix, paths=paths,
        )]


class EnvScopeProvider:
    """Surfaces ``{{$env.<key>}}`` — the SELECTED environment's config variables (SA-2365).

    Reads the ``env`` source in ``variables`` — at run time the run's bound
    environment (StartRun injects it), and at design time the environment the
    node-suggestions caller passed via ``environment_id`` (the use case loads it
    and injects its ``non_secret_env_map`` here). No environment selected → the
    ``env`` source is absent and this scope is empty, so the panel shows nothing
    to drag until the designer picks one.

    A ``list`` (token pool) variable is exposed as a structured object so its
    pool fields (``.current`` / ``.all`` / ``.index`` / ``.count``) are offered
    as sub-paths. A ``credential`` variable resolves to its reference id (drop it
    on a node's ``credential_ref``); no secret is ever sampled here.
    """

    scope_type = "env"
    order = 35  # after variables (30)

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        prefix = "{{$env"
        env_map = (variables or {}).get("env") or {}
        paths: list[VariablePath] = []
        if isinstance(env_map, dict):
            for key in sorted(env_map.keys()):
                value = env_map[key]
                # A pool variable is a dict {all, current, index, count}.
                if isinstance(value, dict) and "current" in value and "all" in value:
                    for sub, out_type in (
                        ("current", "string"), ("all", "array"),
                        ("index", "number"), ("count", "number"),
                    ):
                        paths.append(_finalize_path(
                            VariablePath(
                                path=f"{key}.{sub}", kind="scalar",
                                output_type=out_type, sample=value.get(sub),
                            ),
                            expression_prefix=prefix,
                        ))
                else:
                    paths.append(_finalize_path(
                        VariablePath(
                            path=key, kind="scalar",
                            output_type=_infer_output_type(value), sample=value,
                        ),
                        expression_prefix=prefix,
                    ))
        return [Scope(
            scope_type="env", scope_id="env", label="Environment",
            expression_prefix=prefix, paths=paths,
        )]


class InputScopeProvider:
    """Surfaces ``{{$input.<field>}}`` — workflow trigger payload bindings.

    Schema-driven walk of ``workflow.input_schema``; runtime samples come
    from ``variables["input"]`` when the trigger payload is present.
    """

    scope_type = "input"
    order = 40

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        prefix = "{{$input"
        input_value = variables.get("input")
        samples = (
            _extracted_samples(input_value)
            if isinstance(input_value, dict) and input_value.get(FILE_REF_MARKER) is True
            else _flatten_samples(input_value)
        )
        # Fallback: when ``start_run`` stored the trigger payload as a file_ref
        # whose ``extracted`` cache is empty (current behaviour for arbitrary
        # ``input_data`` dicts), the file-ref path yields nothing. Read the
        # raw ``run.input_data`` to surface the original keys; existing
        # ``extracted`` values win on overlap so typed scalars survive.
        raw_input = getattr(run, "input_data", None)
        if isinstance(raw_input, dict) and raw_input:
            fallback = _flatten_samples(raw_input)
            for k, v in fallback.items():
                samples.setdefault(k, v)
        paths = _walk_schema_tree(
            target_workflow.input_schema or [], samples=samples,
        )
        # Append sample-derived paths missing from the schema, mirroring the
        # node-scope behaviour in ``_build_source_for_node``. Without this,
        # workflows that omit ``input_schema`` get an empty $input scope even
        # when input_data was supplied.
        schema_paths = {p.path for p in paths}
        for sample_path, sample_value in samples.items():
            if (
                sample_path
                and sample_path not in schema_paths
                and not sample_path.startswith("__")
            ):
                paths.append(VariablePath(
                    path=sample_path, kind="scalar",
                    output_type=_infer_path_output_type(sample_value),
                    sample=sample_value,
                ))
                schema_paths.add(sample_path)
        scope = Scope(
            scope_type="input", scope_id="input", label="Input",
            expression_prefix=prefix, paths=paths,
        )
        return [_finalize_paths(scope)]


class NodeScopeProvider:
    """Emits one scope per upstream node (transitive DAG predecessors of the
    target). Includes per-source path enrichment for file-backed nodes
    (columns + preview_rows) and child-run sources when the target node
    spawned a sub-workflow.
    """

    scope_type = "node"
    order = 50

    def __init__(
        self,
        *,
        run_repo: IRunRepository,
        workflow_repo: IWorkflowRepository,
        file_service_client: Optional[IFileServiceClient] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        logger: Optional[ILogger] = None,
        node_output_repo: Any = None,
        node_output_read_mode: str = "event",
    ) -> None:
        self._run_repo = run_repo
        self._workflow_repo = workflow_repo
        self._file_client = file_service_client
        self._view_store = run_context_view_store
        self._logger = logger
        # Step 4 — see NODE_OUTPUT_READ_MODE. Same three modes as
        # GET /nodes/{id}/output, and the same default: "event" changes nothing.
        self._node_output_repo = node_output_repo
        self._node_output_read_mode = (node_output_read_mode or "event").lower()

    def _table_values(
        self, run: Any, upstream_node_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Descriptors for every upstream node, in ONE query — or ``{}``.

        This is the whole point of step 4 for this endpoint. Today building
        these scopes costs **N×M** file-service round-trips: ``_enrich`` reaches
        ``_fetch_columns_and_preview`` per source per path, each an HTTP
        ``query_file_data($top=5)``. Fetching per node here would only trade N
        HTTP calls for N SQL ones.

        Returns ``{}`` on any problem, so the caller falls back to ``variables``
        exactly as before — a suggestions panel must not break because a new
        table is unavailable.
        """
        if (self._node_output_read_mode not in ("shadow", "table")
                or self._node_output_repo is None):
            return {}
        try:
            return self._node_output_repo.get_latest_many(
                getattr(run, "id", ""), list(upstream_node_ids),
            ) or {}
        except Exception as exc:
            if self._logger:
                self._logger.warning(f"node-output batch read failed: {exc}")
            return {}

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        scopes: list[Scope] = []
        from_table = self._table_values(run, upstream_node_ids)

        for uid in upstream_node_ids:
            node = target_workflow.get_node(uid)
            if node is None:
                continue
            ctx_value = variables.get(uid)
            table_value = from_table.get(uid)
            if self._node_output_read_mode == "shadow":
                if table_value is not None and table_value != ctx_value and self._logger:
                    self._logger.warning(
                        f"node-output divergence node={uid}: table and variables differ",
                    )
            elif table_value is not None:
                # "table" mode, and only when the row exists — a node whose
                # output predates the write path must still surface its
                # ``columns``, or the suggestion dropdown goes silently empty.
                ctx_value = table_value
            scope = _build_source_for_node(
                node, ctx_value, expression_prefix=f"{{{{${uid}",
            )
            await self._enrich(scope, ctx_value)
            _finalize_paths(scope)
            scopes.append(scope)

        # Child-run sources (when target node spawned a sub-workflow).
        scopes.extend(await self._collect_child_scopes(run, target_node_id))
        return scopes

    async def _enrich(self, scope: Scope, ctx_value: Any) -> None:
        """Populate file-backed extras on a node scope. Block-level
        columns/preview_rows reflect ``__root__`` only; per-path enrichment
        attaches array_field columns/preview_rows to the matching path entry
        and upgrades its kind to ``file_ref``.
        """
        if not (isinstance(ctx_value, dict) and is_file_ref(ctx_value)):
            return
        root_file_id = ctx_value.get("file_id") or None
        scope.has_file_data = True
        scope.file_id = str(root_file_id) if root_file_id else None

        if root_file_id:
            cols, rows = await _fetch_columns_and_preview(
                self._file_client, self._logger, str(root_file_id),
            )
            if rows:
                scope.preview_rows = rows
                if not scope.columns:
                    scope.columns = cols
                # Backfill per-path scalar samples from the root's single
                # preview row. A submitted INPUT node arrives here wire-thinned
                # — ``NodeOutputProduced`` strips the file_ref's ``extracted``
                # cache to stay under broker size limits — so
                # ``_build_source_for_node`` set no samples and every scalar
                # path is ``sample=None``. The root CSV row carries the values;
                # map each sampleless scalar path to its column. Mirrors the
                # ``$input`` scope's ``run.input_data`` fallback.
                #
                # Samples are CSV cells (strings); ``output_type`` is left as
                # the schema-derived value — we do NOT re-infer it from the
                # stringified sample. ``extracted``-sourced samples (when
                # present) already won upstream, so the ``is None`` guard keeps
                # typed values and never overwrites a real ``0`` / ``""`` /
                # ``False``.
                first_row = rows[0] if isinstance(rows[0], dict) else {}
                for p in scope.paths:
                    if (
                        p.kind == "scalar"
                        and p.sample is None
                        and not p.path.startswith("__")
                        and p.path in first_row
                    ):
                        p.sample = first_row[p.path]

                # Surface file columns that no schema/ctx path covered. A node
                # with a sparse ``output_schema`` and an empty/stale ``columns``
                # list (``_build_source_for_node`` had nothing to emit a path
                # from) would otherwise drop the file's real columns entirely —
                # the FE sees no path and no sample even though the root row
                # carries the value. Emit each uncovered column as a scalar
                # path sampled from the first row, mirroring the ref_columns
                # fallback in ``_build_source_for_node``. Skip synthetic
                # ``__``-prefixed columns (row ids etc.).
                existing = {p.path for p in scope.paths}
                for col in cols:
                    if col and col not in existing and not col.startswith("__"):
                        scope.paths.append(VariablePath(
                            path=col, kind="scalar", output_type="string",
                            sample=first_row.get(col),
                        ))
                        existing.add(col)

        scope.paths.extend(_meta_paths_for_file_ref(ctx_value, scope.file_id))

        cache: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        artifacts = get_artifacts(ctx_value)
        path_index = {p.path: p for p in scope.paths}
        for art in artifacts:
            name = art.get("name") or ""
            file_id = str(art.get("file_id") or "")
            target = path_index.get(name)
            if not name or not file_id or target is None:
                continue
            if file_id not in cache:
                cache[file_id] = await _fetch_columns_and_preview(
                    self._file_client, self._logger, file_id,
                )
            cols, rows = cache[file_id]
            target.columns = list(cols)
            target.preview_rows = [dict(r) for r in rows]
            target.kind = "file_ref"
            if scope.default_artifact_path is None:
                scope.default_artifact_path = target.path

        for art in artifacts:
            scope.artifacts.append(ArtifactSuggestion(
                file_id=art.get("file_id", ""),
                name=art.get("name", ""),
                artifact_type=art.get("artifact_type", ""),
            ))

    async def _collect_child_scopes(
        self, run: Any, target_node_id: str,
    ) -> list[Scope]:
        scopes: list[Scope] = []
        if run is None:  # design time — child runs only exist at runtime
            return scopes
        child_runs_map = child_runs_of(run.id, self._view_store, run)
        child_run_id = child_runs_map.get(target_node_id)
        if not child_run_id:
            return scopes
        child_run = self._run_repo.get_by_id(child_run_id)
        if child_run is None:
            return scopes
        child_workflow = self._workflow_repo.get_by_id(child_run.workflow_id)
        if child_workflow is None:
            return scopes

        child_variables = self._resolve_variables(child_run)

        for child_node in child_workflow.nodes:
            cid = child_node.id
            ctx_value = child_variables.get(cid)
            scope = _build_source_for_node(
                child_node, ctx_value, expression_prefix=f"{{{{${cid}",
            )
            await self._enrich(scope, ctx_value)
            _finalize_paths(scope)
            scopes.append(scope)
        return scopes

    def _resolve_variables(self, run: Any) -> Mapping[str, Any]:
        """Same view-on-aggregate merge the use case applies — duplicated
        here so the child-run lookup can re-merge with the child's own
        view-store entry. (Use case merges the parent run only.)
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


class ArtifactsScopeProvider:
    """Surfaces ``{{$artifacts.<name>}}`` — the global registry of file-backed
    artifacts produced anywhere in the run (parent + child sub-workflows).

    Always emits the scope card (with an empty ``paths`` list when no
    artifacts have been produced yet) so the picker has a discoverability
    hint — mirroring :class:`VariablesScopeProvider`. Entries appear
    automatically as workers register artifacts into the run registry.
    """

    scope_type = "artifacts"
    order = 60

    def __init__(
        self,
        *,
        run_repo: IRunRepository,
        workflow_repo: IWorkflowRepository,
        file_service_client: Optional[IFileServiceClient] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        logger: Optional[ILogger] = None,
    ) -> None:
        self._run_repo = run_repo
        self._workflow_repo = workflow_repo
        self._file_client = file_service_client
        self._view_store = run_context_view_store
        self._logger = logger

    async def build(
        self,
        *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        variables: Mapping[str, Any],
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        parent = variables.get("artifacts") or {}
        merged: dict[str, Any] = {}
        if isinstance(parent, dict):
            merged.update(parent)

        # Fold in any child-run registry when the target node has child runs.
        merged.update(self._child_artifacts_registry(run, target_node_id))

        prefix = "{{$artifacts"
        paths = _artifacts_registry_paths(merged)

        cache: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        for p in paths:
            if p.kind != "file_ref" or not isinstance(p.sample, dict):
                continue
            file_id = str(p.sample.get("file_id") or "")
            if not file_id:
                continue
            if file_id not in cache:
                cache[file_id] = await _fetch_columns_and_preview(
                    self._file_client, self._logger, file_id,
                )
            cols, rows = cache[file_id]
            p.columns = list(cols)
            p.preview_rows = [dict(r) for r in rows]

        scope = Scope(
            scope_type="artifacts", scope_id="artifacts", label="Artifacts",
            expression_prefix=prefix, paths=paths,
        )
        return [_finalize_paths(scope)]

    def _child_artifacts_registry(
        self, run: Any, target_node_id: str,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if run is None:  # design time — child runs only exist at runtime
            return out
        child_runs_map = child_runs_of(run.id, self._view_store, run)
        child_run_id = child_runs_map.get(target_node_id)
        if not child_run_id:
            return out
        child_run = self._run_repo.get_by_id(child_run_id)
        if child_run is None:
            return out

        aggregate = child_run.context.variables or {}
        merged: Mapping[str, Any] = aggregate
        if self._view_store is not None:
            try:
                view = self._view_store.get(child_run.id)
            except Exception:
                view = None
            if view is not None:
                m = dict(aggregate)
                for k, v in view.variables.items():
                    m[k] = v
                merged = m
        reg = merged.get("artifacts")
        if isinstance(reg, dict):
            out.update(reg)
        return out
