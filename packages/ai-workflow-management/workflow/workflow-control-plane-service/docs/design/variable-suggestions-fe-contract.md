# Variable Suggestions — FE Contract

End-to-end reference for `GET /api/v1/runs/{run_id}/nodes/{node_id}/variable-suggestions`,
the endpoint backing an n8n-style variable picker on the FE.

**Status**: current as of 2026-05-07 (post block-level `__root__` provisioning, post per-source path enrichment).

---

## 1. Target UX

The FE renders a tree:

```
[Source node A]                 ← block-level summary (columns + preview_rows)
 ├── path1                      ← scalar leaf:    click inserts {{$<uid>.path1}}
 ├── path2 ▶                    ← expandable:     opens 5-row preview table
 │    ├── col1, col2, ...
 │    └── 5 preview rows
 └── __artifacts[0].file_id     ← engine meta-path (escape hatch)
```

Per node:
- A **block-level table** preview (the node's `__root__` CSV).
- A flat list of **bindable paths**, each with a label, a type icon, and a sample value on hover.
- For paths that resolve to a file-backed array, an **expand-on-click** preview pane (5 rows).

The endpoint returns one source per upstream node plus a synthetic `input` source and an aggregated `artifacts` source. Click any path → FE inserts a templated expression.

---

## 2. Endpoint

```
GET /api/v1/runs/{run_id}/nodes/{node_id}/variable-suggestions
→ 200 { "status": "ok", "data": GetVariableSuggestionsResult }
```

`{node_id}` is the **target** node — the one being configured. The response gives every upstream source it can bind to.

---

## 3. Response shape (current)

Backed by dataclasses in
[`get_variable_suggestions_use_case.py:73-153`](../../app/layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py).

### 3.1 Top level — `GetVariableSuggestionsResult`

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | Echo of the path param |
| `node_id` | `str` | Echo of the target node id |
| `workflow` | `WorkflowInfo` | Snapshot of the parent workflow |
| `run` | `RunInfo` | Snapshot of the run (status, timing, parent linkage) |
| `input` | `SourceNodeSuggestion` | Synthetic source representing the workflow's `input_schema` (run-trigger bindings via `{{$input.<key>}}`) |
| `sources` | `list[SourceNodeSuggestion]` | One per upstream node reachable in the DAG (transitive predecessors of the target) |
| `artifacts` | `SourceNodeSuggestion \| null` | Aggregated `$artifacts.<name>` registry (parent run + child runs merged) — null when nothing has been registered |
| `error`, `error_code`, `error_details` | — | Populated only on failure paths |

### 3.2 `SourceNodeSuggestion`

| Field | Type | Notes |
|---|---|---|
| `node_id` | `str \| null` | null for the synthetic `input` and `artifacts` sources |
| `node_name` | `str` | Display name of the upstream node |
| `node_type` | `str` | `INPUT` / `TASK` / `WORKFLOW` / `CONDITION` / `SWITCH` / … |
| `expression_prefix` | `str` | Prefix to use when composing an expression — e.g. `"{{$<uid>"` for nodes, `"{{$input"` for the input source, `"{{$artifacts"` for artifacts |
| `paths` | `list[VariablePath]` | All bindable paths under this source (see §3.3) |
| `has_file_data` | `bool` | True when the source produced a file_ref. Today: every input-emitting node (post root-uniformity change) |
| `file_id` | `str \| null` | `__root__.file_id` of the source's file_ref, or null |
| `columns` | `list[str]` | Columns of `__root__` only (scalar root fields). Empty for nodes whose schema declares only array fields — root-uniformity provisions a header-only `["__row_id"]` CSV |
| `preview_rows` | `list[dict]` | Up to 5 rows of `__root__` |
| `artifacts` | `list[ArtifactSuggestion]` | `{file_id, name, artifact_type}` per top-level array field |

### 3.3 `VariablePath`

| Field | Type | Notes |
|---|---|---|
| `path` | `str` | The path **inside** the source (no `expression_prefix` baked in). E.g. `"orderTable"`, `"metadata.timeout_ms"`, `"__file_id"` |
| `kind` | `"scalar" \| "file_ref" \| "artifact" \| "artifact_row" \| "artifact_column"` | What this path resolves to. `file_ref` = file-backed (table or upload). `artifact*` kinds come from the older artifact-binding flow. |
| `output_type` | `"string" \| "number" \| "boolean" \| "object" \| "array"` | Type of the resolved value |
| `sample` | `Any` | Optional preview value (scalar value, dict, array...) — when known |
| `columns` | `list[str]` | Populated for `kind == "file_ref"` paths — the array's CSV columns |
| `preview_rows` | `list[dict]` | Up to 5 rows for the same `file_ref` paths |

### 3.4 Meta paths (engine escape hatches)

Emitted by [`_meta_paths_for_file_ref`](../../app/layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py) for every file-backed source. These are **not** schema fields — they're engine-level addresses surfaced for advanced bindings. Today:

| Path | Sample | Use |
|---|---|---|
| `__file_id` | `"<uuid>_<node_id>_root.csv"` | The `__root__.file_id` |
| `__row_count` | `0` | Row count of root |
| `__columns` | `["__row_id", ...]` | Root CSV columns |
| `__content_type` | `"text/csv"` | Always `text/csv` today |
| `__artifacts[i].file_id` | `"<uuid>_<node_id>_<name>.csv"` | Indexed by position |
| `__artifact.<name>` | same `file_id` string | Indexed by name |

All meta paths are `kind: "scalar"` with empty `columns` / `preview_rows` — they're identifiers, not tables.

### 3.5 Worked example

INPUT node with schema `[{key: "orderTable", outputType: "array", fields: [...]}]` (no scalar root fields). After root-uniformity provisioning, the response for this source looks like:

```json
{
  "node_id": "<uid>", "node_name": "Select row", "node_type": "INPUT",
  "expression_prefix": "{{$<uid>",
  "paths": [
    { "path": "orderTable", "kind": "file_ref", "output_type": "array",
      "sample": null,
      "columns": ["__row_id", "orderNumber", "customerName", "..."],
      "preview_rows": [ {"__row_id": "...", "orderNumber": "ORD-001", "..." }, "...5 rows" ] },
    { "path": "__file_id", "kind": "scalar", "output_type": "string",
      "sample": "<uuid>_<uid>_root.csv", "columns": [], "preview_rows": [] },
    { "path": "__row_count", "kind": "scalar", "output_type": "number",
      "sample": 0, "columns": [], "preview_rows": [] },
    "...other meta paths"
  ],
  "has_file_data": true,
  "file_id": "<uuid>_<uid>_root.csv",
  "columns": ["__row_id"],          // root has only __row_id (no scalar root fields)
  "preview_rows": [],
  "artifacts": [
    { "file_id": "<uuid>_<uid>_orderTable.csv", "name": "orderTable",
      "artifact_type": "array_field" }
  ]
}
```

Key points:
- `orderTable` (the array) lives in **`paths[0]`** with full columns + preview rows.
- Block-level `columns` / `preview_rows` reflect `__root__` (which has only `__row_id` here).
- Both views are returned simultaneously — the FE picks which to render based on context.

---

## 4. FE consumption rules

| FE need | Read from |
|---|---|
| Render the node header preview (collapsed view) | Block-level `columns` + `preview_rows` |
| List bindable expressions under a node | `paths[]` |
| Insert an expression on click | `expression_prefix + "." + path + "}}"` |
| Decide if a leaf is expandable into a preview table | `kind == "file_ref"` and `columns` non-empty |
| Show a type icon | `output_type` (5 values, each maps to one icon) |
| Show a hover tooltip with sample | `sample` |
| Show the array preview (5 rows, columns) | `columns` + `preview_rows` on that path |

---

## 5. Gaps and recommended additions

The current shape has everything the FE needs *in principle* but forces the FE to compute three things on every render:

1. **A label per path** — today the FE has to take `path.rsplit('.', 1)[-1]` itself.
2. **An "is this expandable?" flag** — today the FE checks `kind === "file_ref" && columns.length > 0`.
3. **The full insertable expression** — today the FE composes `${expression_prefix}.${path}}}`.

And the source has no hint about which path to render when the node card is collapsed (the user clicks the node name, not a specific path).

### 5.1 Recommended fields (additive, non-breaking)

On `VariablePath`:

| Field | Type | Rule |
|---|---|---|
| `label` | `str` | `path.rsplit(".", 1)[-1]` — the raw path tail. No prettification, no schema lookup; FE prettifies if it wants. |
| `expandable` | `bool` | `kind == "file_ref" and bool(columns)`. One-line FE check replaced by one boolean. |
| `display_path` | `str` | The full `{{$<expression_prefix-tail>.<path>}}` ready to insert. Built once in BE so FE just copies on click. |

On `SourceNodeSuggestion`:

| Field | Type | Rule |
|---|---|---|
| `default_artifact_path` | `str \| null` | First `paths[]` entry where `expandable == true`, or null. The path the FE should render when the user clicks the source's collapsed header. |

### 5.2 What we deliberately don't do

- **Don't re-add legacy `type` / `label` aliases of `output_type`.** The FE migration is already half-done (a unit test enforces their absence), and the rename was cosmetic. The new `label` field is a *new field with a different meaning* (path tail, not a schema-author label) — not a revival.
- **Don't prettify or i18n the label in BE.** Consistent across locales is the FE's job; BE returns raw tails.
- **Don't add `display_path` to meta-paths conditionally** — emit it for every path uniformly. The FE doesn't care that some are advanced.
- **Don't paginate `preview_rows`.** Fixed at 5. The FE's "see all rows" action calls the file-service OData endpoint directly.

---

## 6. Migration plan

All changes are additive. No FE breakage; the new fields show up alongside the existing ones.

### 6.1 BE code changes

| File | Change |
|---|---|
| [`get_variable_suggestions_use_case.py`](../../app/layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py) | Add `label`, `expandable`, `display_path` to `VariablePath`. Add `default_artifact_path` to `SourceNodeSuggestion`. Add `_finalize_path(path, *, expression_prefix)` helper (sets all three string/bool fields in-place). Call it once per path right before the source is returned (after `_walk_schema_tree`, after `_meta_paths_for_file_ref`, after `_artifacts_registry_paths`, after per-source enrichment). Set `default_artifact_path` in `_enrich_with_preview` when at least one path was upgraded to `file_ref`. |
| [`tests/unit/test_get_variable_suggestions.py`](../../tests/unit/test_get_variable_suggestions.py) | Update `test_variable_path_shape_has_no_legacy_fields` to also assert `label`, `expandable`, `display_path` are present. Add focused tests for the new behaviour (label = path tail, `expandable` toggles correctly, `display_path` formatting, `default_artifact_path` selection rule). |

No DTO / controller / smart-DI changes — `dataclasses.asdict` picks up the new fields automatically.

### 6.2 Verification

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_get_variable_suggestions.py tests/integration/test_variable_suggestions_endpoint.py -v
```

Manual: re-run [`test-input-condition-suggestions.html`](../../test-input-condition-suggestions.html) and confirm:
- Each `paths[i]` carries non-empty `label` and `display_path`.
- The `orderTable` path has `expandable: true`.
- The source has `default_artifact_path: "orderTable"`.
- A scalar-only source (e.g. WORKFLOW) has `default_artifact_path: null`.

---

## 7. Out of scope

- Renaming `output_type` → `type` (cosmetic, would re-collide with TS keyword in some tooling).
- FE changes — they live in the FE repo. This doc is the BE-side contract.
- Per-path custom OData filters / pagination on previews.
- `kind` value reform (the existing 5 values stay; documented in §3.3).
