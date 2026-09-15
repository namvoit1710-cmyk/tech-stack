# Variable Suggestions — Scope-based Redesign (DRAFT)

**Status**: DRAFT for review — once approved, supersedes [`variable-suggestions-fe-contract.md`](variable-suggestions-fe-contract.md).
**Date**: 2026-05-07
**Cutover**: hard. No backwards-compat window. FE and BE move in lockstep.

---

## 1. Why redesign

The current response has three sibling collections at the top level (`input`, `sources[]`, `artifacts`), each shaped slightly differently. The FE special-cases each. Adding a new "kind of bindable thing" (run/workflow metadata, future `secrets`/`env`) requires another sibling field + another FE branch.

Goals:

1. **Uniform scopes** — one `scopes[]` collection. Every scope is the same shape; the FE iterates once.
2. **Pluggable BE** — each scope is produced by a `ScopeProvider`. Adding a scope = adding a provider; the use case stays a thin coordinator.
3. **First-class `metadata` scope** — surface run-level + workflow-level identifiers (`run_id`, `workflow_id`, `started_at`, …) that the engine's `MetadataResolverStrategy` already resolves at runtime. Until now they've been resolvable in expressions but invisible in the picker.
4. **Hard cutover** — no aliasing of `input` / `sources` / `artifacts`. Frontend updates in lockstep.

---

## 2. Target wire shape

```jsonc
{
  "status": "ok",
  "data": {
    "run_id":  "104b83fe-…",
    "node_id": "2fd7d2d9-…",
    "workflow": { "id": "…", "name": "…", … },
    "run":      { "id": "…", "status": "RUNNING", … },

    "scopes": [
      {
        "scope_type": "metadata",
        "scope_id":   "metadata",
        "label":      "Metadata",
        "expression_prefix": "{{$metadata",
        "paths": [
          { "path": "run.id", "kind": "scalar", "output_type": "string",
            "sample": "104b83fe-…", "label": "id", "expandable": false,
            "display_path": "{{$metadata.run.id}}", "columns": [], "preview_rows": [] },
          { "path": "run.workflow_id",  "...": "..." },
          { "path": "run.started_at",   "...": "..." },
          { "path": "run.status",       "...": "..." },
          { "path": "run.test_mode",    "...": "..." },
          { "path": "run.parent_run_id","...": "..." },
          { "path": "workflow.id",      "...": "..." },
          { "path": "workflow.name",    "...": "..." },
          { "path": "workflow.version", "...": "..." }
        ],
        "node_type": null,
        "has_file_data": false,
        "file_id": null,
        "columns": [],
        "preview_rows": [],
        "artifacts": [],
        "default_artifact_path": null
      },

      {
        "scope_type": "input",
        "scope_id":   "input",
        "label":      "Input",
        "expression_prefix": "{{$input",
        "paths": [ /* schema-walked paths from workflow.input_schema */ ],
        "node_type": null,
        "has_file_data": false,
        "file_id": null,
        "columns": [],
        "preview_rows": [],
        "artifacts": [],
        "default_artifact_path": null
      },

      {
        "scope_type": "node",
        "scope_id":   "ab7ed4df-…",
        "label":      "Select row",
        "expression_prefix": "{{$ab7ed4df-…",
        "paths": [ /* per-source paths, meta-paths, enriched columns/preview_rows */ ],
        "node_type": "INPUT",
        "has_file_data": true,
        "file_id":  "…_root.csv",
        "columns":  ["__row_id"],
        "preview_rows": [],
        "artifacts": [{ "file_id": "…", "name": "orderTable", "artifact_type": "array_field" }],
        "default_artifact_path": "orderTable"
      },

      {
        "scope_type": "artifacts",
        "scope_id":   "artifacts",
        "label":      "Artifacts",
        "expression_prefix": "{{$artifacts",
        "paths": [ /* registry-derived $artifacts.<key> paths + sub-fields */ ],
        "node_type": null,
        "has_file_data": false,
        "file_id": null,
        "columns": [],
        "preview_rows": [],
        "artifacts": [],
        "default_artifact_path": null
      }
    ],

    "error": null,
    "error_code": "",
    "error_details": {}
  }
}
```

### Ordering rules

`scopes[]` is ordered:

1. `metadata` (always first — system-wide, never empty)
2. `input` (workflow trigger — appears even with empty schema)
3. `node` × N (transitive DAG predecessors of the target node, in upstream-DAG order — preserves the current `_collect_upstream_node_ids` sort)
4. `artifacts` (only when the registry is non-empty; otherwise omitted entirely)

Empty scopes are **omitted** *except* `metadata` (always present) and `input` (always present, paths may be empty).

### Discriminator

`scope_type` is the discriminator. FE switches on this for rendering icons / headers. Values today: `"metadata" | "input" | "node" | "artifacts"`.

---

## 3. BE redesign — ScopeProvider pattern

### 3.1 New dataclass — replaces `SourceNodeSuggestion`

```python
ScopeType = Literal["metadata", "input", "node", "artifacts"]

@dataclass
class Scope:
    scope_type: ScopeType
    scope_id:   str          # "metadata" | "input" | "<node_uid>" | "artifacts"
    label:      str          # human-readable, used for header rendering
    expression_prefix: str   # "{{$metadata" / "{{$input" / "{{$<uid>" / "{{$artifacts"

    paths: list[VariablePath] = field(default_factory=list)

    # File-backed extras — populated only for scope_type == "node" today
    node_type: str | None = None
    has_file_data: bool = False
    file_id: str | None = None
    columns: list[str] = field(default_factory=list)
    preview_rows: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[ArtifactSuggestion] = field(default_factory=list)
    default_artifact_path: str | None = None
```

`VariablePath` and `ArtifactSuggestion` carry over unchanged from the current shape (with the §5 additions of [`variable-suggestions-fe-contract.md`](variable-suggestions-fe-contract.md): `label`, `expandable`, `display_path`).

### 3.2 `ScopeProvider` protocol

```python
class IScopeProvider(Protocol):
    """Build zero-or-more `Scope` records for a given run + target node."""

    scope_type: ScopeType  # the kind this provider emits
    order: int             # ordering anchor (lower = earlier in scopes[])

    async def build(
        self, *,
        run: Any,
        target_node_id: str,
        target_workflow: Any,
        # Coordination state shared across providers:
        variables: Mapping[str, Any],   # merged view+aggregate
        upstream_node_ids: Sequence[str],
    ) -> Sequence[Scope]:
        ...
```

The use case becomes a thin coordinator:

```python
async def execute(self, command):
    run = self.run_repo.get_by_id(command.run_id)
    workflow = self.workflow_repo.get_by_id(run.workflow_id)
    upstream = _collect_upstream_node_ids(workflow, command.node_id)
    variables = self._resolve_variables(run)

    scopes: list[Scope] = []
    for provider in sorted(self._providers, key=lambda p: p.order):
        scopes.extend(await provider.build(
            run=run, target_node_id=command.node_id,
            target_workflow=workflow,
            variables=variables, upstream_node_ids=upstream,
        ))
    return GetVariableSuggestionsResult(
        run_id=command.run_id, node_id=command.node_id,
        workflow=_workflow_info(workflow), run=_run_info(run),
        scopes=scopes,
    )
```

### 3.3 Provider catalogue

| Provider | scope_type | Source of paths |
|---|---|---|
| `MetadataScopeProvider` | `metadata` | Mirrors the engine's `MetadataResolverStrategy` (run.* and workflow.* identifiers). Path list is static — generated from the current `run` / `workflow` snapshot. |
| `InputScopeProvider` | `input` | `_walk_schema_tree(workflow.input_schema)` + samples from `variables["input"]` if it's a file_ref. |
| `NodeScopeProvider` | `node` | One `Scope` per upstream node. Wraps existing logic (`_build_source_for_node` + `_enrich_with_preview`) but emits `Scope` instead of `SourceNodeSuggestion`. Reuses child-run merging from `_collect_child_sources`. |
| `ArtifactsScopeProvider` | `artifacts` | `_artifacts_registry_paths(merged)` + per-path enrichment. Returns 0 scopes when the registry is empty. |

The four providers are independent; new ones (`secrets`, `env`, `previous_run`) drop in by adding to the registered list.

### 3.4 Wiring (smart-DI)

`bootstrap.py` builds the four providers (each may declare its own deps — `IRunContextViewStore`, `IFileServiceClient`) and passes them to `GetVariableSuggestionsUseCase` via a single `scope_providers: list[IScopeProvider]` parameter. The smart-DI signature inspector already handles list-typed params.

### 3.5 Result dataclass

```python
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
```

`input`, `sources`, `artifacts` fields are **deleted** (hard cutover). The DTO mirror in [`variable_suggestions_dto.py`](../../app/layer3_adapters/controllers/restful/v1/dtos/variable_suggestions_dto.py) (or wherever the response model lives) follows.

---

## 4. Metadata scope detail

The first iteration emits a fixed list of paths derived from `run` + `workflow` snapshots:

| Path | output_type | sample |
|---|---|---|
| `run.id` | string | run UUID |
| `run.workflow_id` | string | workflow UUID |
| `run.status` | string | "RUNNING" / "COMPLETED" / … |
| `run.started_at` | string | ISO 8601 |
| `run.completed_at` | string | nullable |
| `run.test_mode` | string | "full" / "step" / "" |
| `run.parent_run_id` | string | nullable |
| `run.parent_node_id` | string | nullable |
| `run.depth_level` | number | 0 = root |
| `run.root_run_id` | string | self for root runs |
| `workflow.id` | string | workflow UUID |
| `workflow.name` | string | display name |
| `workflow.version` | string | version |
| `workflow.status` | string | DRAFT/PUBLISHED |

All `kind: "scalar"`. No file-backed entries. `expression_prefix == "{{$metadata"` so `display_path` looks like `{{$metadata.run.id}}` — matches what `MetadataResolverStrategy` already resolves at runtime.

**Out of scope for v1**: secrets, env vars, per-step timing breakdowns. Add as separate providers later.

---

## 5. FE contract impact

### Breaking changes

| Old | New |
|---|---|
| `data.input` (object) | `data.scopes[i]` where `scope_type == "input"` |
| `data.sources` (array) | `data.scopes[i]` where `scope_type == "node"` |
| `data.artifacts` (object \| null) | `data.scopes[i]` where `scope_type == "artifacts"` (omitted when empty) |
| (none) | `data.scopes[i]` where `scope_type == "metadata"` (new) |

Field-level shape inside each scope is identical to the prior `SourceNodeSuggestion` modulo:
- `node_id` → `scope_id` (now string for all scopes, never null)
- `node_name` → `label`
- `node_type` becomes optional (null for non-`node` scopes)

### FE migration steps

1. Rename `response.input` / `response.sources` / `response.artifacts` reads → iterate `response.scopes`.
2. Switch on `scope_type` for rendering: metadata = key-value list, input = form-binding picker, node = node card with preview, artifacts = registry list.
3. Drop any "is this an input vs source vs artifact?" branches — the discriminator does it.

---

## 6. Out of scope

- **Internal storage** of paths/scopes (no new DB / Redis structures). Scopes are computed per-request from the same sources as today (run.context, view store, workflow schema, file service). The "re-design how we store scope" wording is interpreted here as **redesigning the in-memory result structure**, not introducing a persistence layer.
- **Real-time scope updates** via Socket.IO — the picker re-fetches on demand; no live push.
- **Scope-level OData** for paginating large `paths[]` — N is bounded by schema; no pagination needed.
- **`$json`-style scope** (n8n's "current item") — depends on iteration semantics we don't have yet (LOOP doesn't expose per-iteration state through this endpoint today).

---

## 7. Migration plan

Single PR, one cutover. No flag.

### 7.1 BE changes

| File | Change |
|---|---|
| [`get_variable_suggestions_use_case.py`](../../app/layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py) | Replace `SourceNodeSuggestion` with `Scope` (rename + add `scope_type`/`scope_id`). Replace `GetVariableSuggestionsResult.{input, sources, artifacts}` with `scopes: list[Scope]`. Extract scope-building logic into the four providers (new module: [`scope_providers.py`](../../app/layer2_application/features/get_variable_suggestions/use_cases/scope_providers.py)). Use case becomes thin coordinator. Keep `_resolve_variables`, `_walk_schema_tree`, `_meta_paths_for_file_ref`, `_artifacts_registry_paths`, `_finalize_path` unchanged — providers reuse them. |
| New: [`scope_providers.py`](../../app/layer2_application/features/get_variable_suggestions/use_cases/scope_providers.py) | `IScopeProvider` protocol + four concrete providers (`MetadataScopeProvider`, `InputScopeProvider`, `NodeScopeProvider`, `ArtifactsScopeProvider`). Each ≈30-80 lines. |
| [`bootstrap.py`](../../bootstrap.py) | Wire the four providers; inject as `scope_providers` list. |
| Controller / DTO ([`variable_suggestions_controller.py`](../../app/layer3_adapters/controllers/restful/v1/variable_suggestions_controller.py) and the response DTO file) | Update Pydantic response model to mirror `Scope`. Drop the old per-collection fields. |

### 7.2 Test changes

| File | Change |
|---|---|
| [`tests/unit/test_get_variable_suggestions.py`](../../tests/unit/test_get_variable_suggestions.py) | Migrate every assertion off `result.input` / `result.sources` / `result.artifacts` to `next(s for s in result.scopes if s.scope_type == "...")`. Add tests for the new `metadata` scope (path list, samples, `display_path` formatting). Add a test asserting scope ordering. Add per-provider unit tests in `tests/unit/test_scope_providers.py`. |
| [`tests/integration/test_variable_suggestions_endpoint.py`](../../tests/integration/test_variable_suggestions_endpoint.py) | Same migration. Assert metadata scope is always present; assert artifacts scope absent when registry empty. |

### 7.3 FE coordination

This PR is BE-only and **breaks the FE on merge**. Coordinate:

1. Land the FE PR (consuming `scopes[]`) in a feature branch behind a build-time flag if needed, or behind a stub that handles either shape.
2. Merge BE PR.
3. Roll FE forward.

Or, more practically, ship them together in a single change-window.

---

## 8. Verification

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_get_variable_suggestions.py tests/unit/test_scope_providers.py tests/integration/test_variable_suggestions_endpoint.py -v
```

Manual smoke (after deploy):

- Hit `GET /api/v1/runs/<run>/nodes/<node>/variable-suggestions` for a run with one INPUT (`__orderTable`) and one downstream CONDITION.
  - `scopes[0].scope_type == "metadata"` with `paths` covering `run.*` and `workflow.*`.
  - `scopes[1].scope_type == "input"` with paths from `workflow.input_schema`.
  - `scopes[2].scope_type == "node"`, `scope_id` matches the INPUT uid, `default_artifact_path == "orderTable"`.
  - `scopes[3]` either `artifacts` (if registry non-empty) or absent.
- Confirm `display_path` for a metadata path is `"{{$metadata.run.id}}"`.

Update the browser harness [`test-input-condition-suggestions.html`](../../test-input-condition-suggestions.html) to assert against `scopes[]` instead of `sources[]`.

---

## 9. Open questions for review

1. **Scope ordering** — is `metadata → input → node × N → artifacts` what the FE wants? Alternative: nodes first, system scopes last (folded under a "More" expander).
2. **Metadata path naming** — `run.id` (dotted) vs `run_id` (flat)? n8n uses `$execution.id` (dotted-prefix style). Plan above goes dotted; both `MetadataResolverStrategy` and the FE picker need to agree.
3. **Workflow-level paths in metadata** — should they live in `metadata.workflow.*` or get their own `workflow` scope? Plan above bundles them under `metadata`. Splitting later is cheap (new provider).
4. **`label` for the `node` scope** — currently the upstream node's `node.name`. Some node names are awkward (e.g. workflow imports). Should we fall back to `node.id` truncated when name is empty?
