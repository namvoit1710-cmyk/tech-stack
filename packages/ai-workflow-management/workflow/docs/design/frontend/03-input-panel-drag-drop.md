# Input Panel — Variable Palette & Drag-Drop

When the user is configuring a node's input, the frontend needs to show a list of variables the node can bind to, render each as a drag-gable chip, and emit a valid expression when the chip is dropped into a text field. This doc covers all of that.

---

## 1. The single endpoint

```
GET /runs/{run_id}/nodes/{node_id}/variable-suggestions
```

Walks the DAG backward from `node_id` (depth cap 10), gathers upstream node outputs, workflow-level `$input`, and the run-level `$artifacts` registry, and classifies each reachable path with a `kind`.

The target node is the **one being configured**, i.e. the node the user is currently editing in the input panel.

### Response shape

```jsonc
{
  "run_id": "…",
  "node_id": "…",
  "workflow": { "id": "…", "name": "…", "input_schema": [...], "output_schema": [...], ... },
  "run": { "id": "…", "status": "running", ... },
  "input":      SourceNodeSuggestion | null,   // workflow-level $input
  "sources":   [SourceNodeSuggestion, …],      // one per upstream node
  "artifacts":  SourceNodeSuggestion | null,   // global $artifacts registry
  "error":      null
}
```

Note: `input` and `artifacts` are **single** `SourceNodeSuggestion` objects (not arrays). `sources` is the array, one entry per reachable upstream node.

### `SourceNodeSuggestion`

```jsonc
{
  "node_id":           "n_review" | null,   // null for $input / $artifacts
  "node_name":         "Review step",
  "node_type":         "HUMAN_ACTION",
  "expression_prefix": "{{$n_review",
  "paths":             [VariablePath, …],
  "has_file_data":     true,
  "file_id":           "f_abc",            // main CSV file id (if file-backed)
  "columns":           ["customer_id", "score"],
  "preview_rows":      [ { … }, … ],       // up to 5 sample rows
  "artifacts":         [ArtifactSuggestion, …]
}
```

### `VariablePath`

```jsonc
{
  "path":        "orders[*].sku",
  "kind":        "scalar" | "file_ref" | "artifact" | "artifact_row" | "artifact_column",
  "output_type": "string" | "number" | "boolean" | "object" | "array",
  "sample":      "ABC-123"          // runtime sample if context has a value; else null
}
```

---

## 2. What goes in the drag-drop palette

Render **one collapsible section per `SourceNodeSuggestion`**:

1. **Workflow input** — section for `input` (if non-null). Header: "Workflow input".
2. **Upstream nodes** — one section per entry in `sources`, labeled `node_name` + `node_type`.
3. **Artifacts** — section for `artifacts` (if non-null). Header: "Artifacts".

Inside each section, render `paths[]` — each path is one drag-chip.

Show `preview_rows` in a "Preview" popover when the user hovers the file-backed source node (`has_file_data === true`).

---

## 3. Path kinds → chip UI → expression emitted

The `kind` field determines how you render the chip and what expression you emit on drop.

| kind | Chip UI | Expression emitted on drop |
|---|---|---|
| `scalar` | Text chip; suffix with `output_type` badge (`str`, `num`, `bool`). | `{{$<node_id>.<path>}}` (or `{{$input.<path>}}` / `{{$artifacts.<name>.<path>}}`) |
| `file_ref` | File icon + filename (from `sample` when present). Expandable sub-menu with four standard sub-fields: `.file_id`, `.filename`, `.content_type`, `.size_bytes`. | Base: `{{$<node>.<path>}}` — expands to `{{$<node>.<path>.file_id}}` (etc.) when a sub-field is picked. |
| `artifact` | Table icon + row count if known. Dropping the whole chip refers to the entire sub-table. | `{{$artifacts.<name>}}` or `{{$<node>.<path>}}` for a node-owned artifact. |
| `artifact_row` | Row-iterator chip. Usually drop-disabled outside a LOOP node; in a LOOP, this becomes `$loop.current`. | `{{$<node>.<path>[*]}}`; inside LOOP body: `{{$loop.current.<col>}}`. |
| `artifact_column` | Column chip. Useful for array-of-scalars mapping. | `{{$<node>.<path>[*].<col>}}`. |

The `expression_prefix` on `SourceNodeSuggestion` is the first half of the expression; you append `.<path>}}`. For the `$artifacts` and `$input` sources, the prefix already reflects the right namespace.

### Output-type badge

`output_type` is one of `string`, `number`, `boolean`, `object`, `array`. Show it as a small badge next to the chip so designers can tell what they are binding.

### `sample`

When available, `sample` holds a runtime value from the current run context. Render it to the right of the chip (truncated). For `file_ref`, sample is the file metadata object — show just the filename.

### Required-field validation

If the target node's `input_schema` field declares a type, warn when the chip's `output_type` won't coerce. Coercion rules: string ↔ number ↔ boolean are allowed; `object`/`array` only into `object`/`array` slots.

---

## 4. Special source prefixes

The expression parser understands these source namespaces; `variable-suggestions` emits them via the `input` / `artifacts` / per-node entries, but the FE expression-builder should also know them directly:

| Prefix | Meaning | Emitted by |
|---|---|---|
| `{{$<node_id>.<path>}}` | Output of a specific upstream node. | `sources[]` entries. |
| `{{$input.<path>}}` | Workflow-level input (from `POST /runs`). | `input` entry. |
| `{{$artifacts.<name>.<path>}}` | Run-level named artifact registry. | `artifacts` entry. |
| `{{$loop.index}}` / `{{$loop.current.<col>}}` | Loop iteration context (only valid inside a LOOP body node). | Not in suggestions; add statically when the target node is inside a loop. |
| `{{$metadata.<key>}}` | Run metadata (`run_id`, `workflow_id`, `tenant_id`, `started_at`, `correlation_id`). | Not in suggestions; add statically. |

---

## 5. Preview before dropping (optional but strongly recommended)

`POST /runs/{run_id}/resolve` lets you batch-evaluate expressions and show sample values inline:

```http
POST /runs/{run_id}/resolve
Content-Type: application/json

{
  "paths": [
    "{{$n_review.customer_name}}",
    "{{$n_review.orders[0].line_items[0].price}}",
    "{{$artifacts.invoice_pdf.filename}}"
  ],
  "task_id": null
}
```

Response:

```jsonc
{
  "results": [
    { "path": "{{$n_review.customer_name}}",           "status": "ok",    "kind": "scalar",   "value": "Acme" },
    { "path": "{{$n_review.orders[0].line_items[0]…}}","status": "ok",    "kind": "scalar",   "value": 12.5 },
    { "path": "{{$artifacts.invoice_pdf.filename}}",   "status": "error", "error": { "code": "NOT_FOUND", "message": "…" } }
  ]
}
```

Cap: 100 paths per call. Use for the "resolved preview" shown next to each chip and for the binding-builder's "Preview" affordance.

---

## 6. When to refresh suggestions

| Event | Action |
|---|---|
| User opens a node's input panel | `GET /variable-suggestions` fresh. |
| `task.completed` on an upstream node | Refresh — new fields may now be reachable with populated `sample`s. |
| `artifact.added` | Refresh — the `artifacts` section has a new entry. |
| `context.updated` | Refresh at a debounced cadence (2–3 s) — samples are stale. |
| Run status transitions to `running` (first dispatch) | Refresh — runtime samples become available. |
| Any `node_input.edited` in the current run | Refresh if the edited node is upstream of the target. |

Cache per `(run_id, node_id)` but invalidate on the above. No ETag on this endpoint yet; it is cheap.

---

## 7. Design-time vs runtime

`variable-suggestions` works at any lifecycle stage. The difference:

- **Design time / before run starts**: `sample` is `null` for every path. Suggestions come from `output_schema` / `input_schema` only. Use this to wire the workflow; show only the chip (no sample column).
- **Runtime**: `sample` is filled from `run.context.variables[node_id]` when available. `has_file_data` and `preview_rows` are populated for file-backed upstream outputs.

The chip UI is identical; only the "sample" preview column is conditional.

---

## 8. Edge cases

- **Upstream node not yet completed**: its `paths` are still listed (from schema), but every `sample` is `null`. Chip is still draggable; resolve preview will report `error.code = "UNRESOLVED"`.
- **Path kind `artifact_row` outside a LOOP**: chip is visible but drop-disabled. Tooltip should say "use inside a Loop body to iterate".
- **Stale suggestions after rerun**: a `cascade` rerun invalidates downstream context. After `POST /runs/{run_id}/rerun`, re-call `variable-suggestions`.
- **Very deep schemas**: walker stops at depth 10. Deeper paths won't appear — users must type them by hand (and `POST /resolve` will still evaluate them).

---

## 9. Source of truth

- Use case: `workflow-control-plane-service/app/layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py`
- Classification rules: same file (walk the node's `output_schema`; wrapperProps `artifact_binding` triggers artifact variants; `UploadControl*` emits `file_ref`).
- Expression parser: `workflow-control-plane-service/app/layer1_domain/services/variable_resolver.py` (`parse_expression`, lines ~68–125).
