# Node Input Review & Edit APIs — Design Document

> Status: **Design / Approved plan**
> Service: `workflow-control-plane-service`
> Scope: Review + edit of runtime node-input data (including arbitrary-depth nested tables, 1M+ row tables), applied to pending nodes, completed-node review, and completed-node edit-with-rerun.
> Principles: Clean redesign (no backward compatibility). Thin control-plane — file statistics, rows, OData delegated to the external file service. Single coherent API surface.

---

## 1. Context

Every workflow node stores its resolved input via `DataStoreService` as CSV in the external file service. The in-memory descriptor lives on `run.context.variables[node_id]` (and mirrors on `task.resolved_input`) as a `__file_ref` dict. Today:

- Nesting supports **one level** — array-of-objects fields become separate CSV "artifacts". No tables-inside-table-rows.
- Review/edit endpoints are scattered: `input-data`, `output`, `data-info`, `artifacts`, `files/{id}/odata`, `files/{id}/patch`, `tasks/{id}/resume`, `nodes/{id}/resume-input`.
- No ETag/concurrency; no server-side schema validation on edit; no configurable rerun scope; `row_count` and previews returned inline.

Requirements:

1. Arbitrary-depth nested tables (table inside row inside table inside row …).
2. All pending node kinds (INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD) + completed-node review + completed-node edit-with-rerun.
3. OData entirely delegated to file service. Control-plane returns `file_id` + identity only.
4. Bulk edits scoped by the frontend's current OData filter.
5. Optimistic concurrency (ETag / `If-Match`), atomic batch edits across scopes, schema validation on write, configurable auto-rerun.
6. Schema delivered as a separate ETag-cacheable endpoint.

---

## 2. Final API Surface

Three addressing scopes — **run**, **node (current iteration)**, **task (specific iteration)**. Node-scoped endpoints default to the most recent task for the node; `?task_id=` promotes any read/write to a specific loop iteration.

```
# Schema / data / query
GET    /runs/{run_id}/nodes/{node_id}/schema
GET    /runs/{run_id}/nodes/{node_id}/data[?side=input|output|both&task_id=...]
GET    /runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata[?task_id=...]&<OData>

# Edit / submit
PATCH  /runs/{run_id}/nodes/{node_id}/data[?task_id=...]
POST   /runs/{run_id}/nodes/{node_id}/submit[?task_id=...]

# Run-level actions
GET    /runs/{run_id}/artifacts                       # $artifacts registry (run-level)
POST   /runs/{run_id}/resolve                         # batched path evaluator (builder preview)
POST   /runs/{run_id}/rerun
POST   /runs/{run_id}/cancel                          (existing, unchanged)

# Design-time (unchanged, walker updated — see §18)
GET    /runs/{run_id}/nodes/{node_id}/variable-suggestions

# Worker-facing (NOT for frontend review/edit)
POST   /runs/{run_id}/tasks/{task_id}/complete
POST   /runs/{run_id}/tasks/{task_id}/fail
POST   /runs/{run_id}/tasks/{task_id}/rerun
```

### Endpoints removed

| Removed | Replaced by |
|---|---|
| `GET .../input-data` | `GET .../data?side=input` |
| `GET .../output` | `GET .../data?side=output` |
| `GET .../data-info` | `GET .../data` descriptor |
| `GET .../artifacts` (list) | Descriptor tree on `GET .../data` |
| `POST .../artifacts` (attach) | `PATCH .../data` with `insert_rows` |
| `PATCH .../input` | `PATCH .../data` |
| `POST .../resume-input` | `POST .../submit[?task_id=...]` |
| `POST /runs/.../tasks/{id}/resume` | `POST .../nodes/{node_id}/submit?task_id=...` |
| `/files/{id}/odata`, `/files/{id}/patch` proxy | `/nodes/.../artifacts/{aid}/odata` scoped wrapper |
| `GET/DELETE /files/{id}` raw, `/files/{id}/presigned-url` | Internal-only; not part of FE surface |

---

## 3. Endpoint Contracts

All responses wrap in the project envelope `{ "status": "ok"|"error", "data": ... | "error": ... }` via `layer3_adapters/controllers/restful/v1/dtos/response_envelope.py`. ETag is carried in the HTTP header, not the body.

### 3.1 `GET /runs/{run_id}/nodes/{node_id}/schema`

Returns the auto-form schema for a node.

**Response** (200 OK, `ETag: W/"<schema_etag>"`, `Cache-Control: private, max-age=30`):

```json
{
  "schema": {
    "fields": [
      { "key": "customer_name", "outputType": "string", "fieldConfig": { "rules": [...] } },
      { "key": "orders", "outputType": "object", "fieldConfig": { "artifact_binding": "orders" },
        "fields": [
          { "key": "sku", "outputType": "string" },
          { "key": "line_items", "outputType": "object",
            "fieldConfig": { "artifact_binding": "orders.line_items" },
            "fields": [ ... ] }
        ]
      }
    ]
  },
  "schema_version": "v7",
  "node_kind": "HUMAN_ACTION",
  "node_state": "PENDING"
}
```

**Additional fields for dynamic schemas** (DATA_EDIT and nodes where the handler computes the schema at runtime):

```json
{
  "schema": { ... },
  "schema_version": "v8",
  "schema_source": "runtime",
  "node_kind": "DATA_EDIT",
  "node_state": "PENDING"
}
```

`schema_source` values:

| Value | Meaning | Generated by |
|---|---|---|
| `design_time` | Schema authored by FE in the workflow builder | Stored on node definition |
| `runtime` | Schema computed by the node handler at execution time (e.g., columns derived from upstream output) | Handler writes to `task.output_metadata["input_schema"]` |

FE uses `schema_source` to decide whether to show a "Refresh schema" affordance. Schema resolution precedence: runtime (`task.output_metadata`) > design-time (`node.config`) > fallback.

**304** on `If-None-Match` match. **404** `NODE_NOT_FOUND`. **409** `RUN_NOT_READY`.

### 3.2 `GET /runs/{run_id}/nodes/{node_id}/data`

Scalars + recursive descriptor tree. Identity only — **no inline rows, no row_count**. Frontend reads rows via `GET .../artifacts/{artifact_id}/odata` (or directly against the file service) and uses OData `$count=true` for counts.

`side=input|output|both` (default `input`). `task_id=...` to target a specific loop iteration.

**Input/output separation — the DATA_EDIT model:**

For DATA_EDIT (and any node where the user edits upstream data), the node maintains two distinct data references:

- **`side=input`** — the **original** upstream data (`task.resolved_input`). **Frozen; never modified by PATCHes.** This is what the upstream node produced.
- **`side=output`** — the **working copy** (`run.context.variables[node_id]`). **This is what PATCH modifies.** Starts as a copy-on-write reference to the same `file_id`s as input; diverges on first edit.

At initialization (node reaches PENDING):
```
task.resolved_input  = upstream file_ref  (frozen original)
variables[node_id]   = same file_ref      (shared file_ids — zero copy cost)
```

After a PATCH:
```
task.resolved_input  = unchanged          (still the original)
variables[node_id]   = new file_ref       (modified artifacts have new file_ids;
                                           unmodified keep the original file_ids)
```

**Diff capability**: FE compares `file_id` at each artifact level between `data` (input) and `output`. Same `file_id` = unchanged. Different = modified by user.

For INPUT / HUMAN_ACTION / FILE_UPLOAD nodes, `side=input` and `side=output` point to the same data (there is no separate "original" to compare against — the user is creating data, not editing existing data). FE uses `?side=input` as default.

**Response** (200 OK, `ETag: W/"<node_etag>"`):

```json
{
  "run_id": "r_abc",
  "node_id": "n_human_review",
  "task_id": "t_...",
  "node_kind": "HUMAN_ACTION",
  "node_state": "PENDING",
  "schema_ref": {
    "url": "/runs/r_abc/nodes/n_human_review/schema",
    "etag": "W/\"...\""
  },
  "data": {
    "etag": "sha256:7d3f...",
    "version": 12,
    "scalars": { "customer_name": "Acme", "order_total": 1200 },
    "root": {
      "artifact_id": "__root__",
      "file_id": "f_main_abc",
      "columns": ["customer_name", "order_total"],
      "parent_row_key_column": null,
      "children": [
        {
          "artifact_id": "orders",
          "file_id": "f_orders_xyz",
          "columns": ["sku", "qty", "line_items"],
          "parent_row_key_column": null,
          "children": [
            {
              "artifact_id": "orders.line_items",
              "file_id": "f_line_items_qrs",
              "columns": ["__parent_row_id", "part_no", "price", "attrs"],
              "parent_row_key_column": "__parent_row_id",
              "children": [
                {
                  "artifact_id": "orders.line_items.attrs",
                  "file_id": "f_attrs_tuv",
                  "columns": ["__parent_row_id", "key", "value"],
                  "parent_row_key_column": "__parent_row_id",
                  "children": []
                }
              ]
            }
          ]
        }
      ]
    }
  }
}
```

**304** on `If-None-Match`. **404** `NODE_NOT_FOUND`. **410** `INPUT_EVICTED` if `file_id`s no longer resolvable.

### 3.3 `GET /runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata`

Tenancy-enforced OData wrapper. All query params pass through to the file service. Authorization: verifies the `artifact_id` belongs to the node's `__file_ref` tree (scoped by run + node + optional task_id).

Request: `?$filter=...&$orderby=...&$skip=...&$top=...&$count=true` (file service supports the full OData vocabulary; control plane passes it through unmodified).

Response: 200 OK — payload streamed from the file service as-is. 404 `ARTIFACT_NOT_FOUND`. 403 `FORBIDDEN` if artifact doesn't belong to the addressed node.

### 3.4 `PATCH /runs/{run_id}/nodes/{node_id}/data`

Atomic batch edit. Requires `If-Match` header. **Always targets the working copy** (`variables[node_id]`), never the frozen input (`task.resolved_input`).

**Request body**:

```json
{
  "rerun_strategy": "cascade",
  "reset_to_input": false,
  "scalars": { "customer_name": "Acme Corp" },
  "artifact_ops": [
    {
      "op": "update_cell",
      "row_path": [
        { "artifact_id": "orders", "row_index": 3, "filter": "$filter=region eq 'APAC'&$orderby=sku asc" }
      ],
      "column": "qty",
      "value": 150
    },
    {
      "op": "bulk_update",
      "artifact_id": "orders.line_items",
      "row_path": [ { "artifact_id": "orders", "row_index": 3, "filter": "$filter=region eq 'APAC'" } ],
      "filter": "$filter=price lt 10",
      "assignments": { "status": "review" }
    },
    {
      "op": "insert_rows",
      "artifact_id": "orders.line_items",
      "row_path": [ { "artifact_id": "orders", "row_index": 3, "filter": "" } ],
      "rows": [ { "part_no": "X-1", "price": 9.5 } ]
    },
    {
      "op": "delete_row",
      "row_path": [
        { "artifact_id": "orders", "row_index": 7, "filter": "$filter=region eq 'APAC'" }
      ]
    },
    {
      "op": "delete_rows",
      "artifact_id": "orders",
      "filter": "$filter=region eq 'EU'"
    },
    {
      "op": "add_column",
      "artifact_id": "orders",
      "column": "flagged",
      "output_type": "boolean",
      "default_value": false
    }
  ]
}
```

**Op set** (full list of what can appear in `artifact_ops`):

| Op | Scope | Targeting | Notes |
|---|---|---|---|
| `update_cell` | 1 cell | `row_path` + `column` | Single inline edit |
| `bulk_update` | N rows | `filter` | Mass update under view |
| `insert_rows` | Append N | — | Server assigns `__row_id` |
| `delete_row` | 1 row | `row_path` (terminal hop = target) | Single-row delete from a grid; precise |
| `delete_rows` | N rows | `filter` (non-empty) | Bulk delete under view |
| `reset_artifact` | 1 artifact | `artifact_id` | Revert to original input |
| `replace_artifact` | 1 artifact | `artifact_id` + external `file_id` | External service swaps in a pre-uploaded file (ETL / enrichment / AI) |
| `add_column` | Schema + data | `artifact_id` + `column` | **DATA_EDIT only** (schema_source=runtime) |
| `drop_column` | Schema + data | `artifact_id` + `column` | **DATA_EDIT only** |
| `rename_column` | Schema + data | `artifact_id` + old/new | **DATA_EDIT only** |

Column ops are schema-data coupled: they mutate both the artifact CSV (header + rows) and the runtime schema (`task.output_metadata["input_schema"]`), and bump the **schema ETag** — FE must refetch `GET /schema` after a successful column op. Attempting any column op on a node whose `schema_source == "design_time"` returns `422 STATIC_SCHEMA_IMMUTABLE` — static schemas are FE-authored at design time and cannot be mutated at runtime.

`replace_artifact` lets an external service (e.g., a data-factory / enrichment / AI service) swap in a pre-uploaded file as the new content of an artifact, without cell-by-cell PATCHes. The external service uploads the file directly to the file service, obtains a `file_id`, then calls `PATCH /data` with a `replace_artifact` op. The control-plane verifies the file exists (file-service HEAD), checks column compatibility, swaps the artifact's `file_id` in the working-copy tree, and bumps the node ETag. Under `schema_compatibility: "drift_ok"` (DATA_EDIT only) the runtime schema is also updated to match the replacement file's columns.

Reserved columns (`__row_id`, `__parent_row_id`) cannot be targeted by `drop_column` or `rename_column`; attempting so returns `422 VALIDATION_FAILED`, rule `"reserved_column"`.

`delete_rows.filter` must be non-empty. An empty filter is rejected (`422 ROW_PATH_AMBIGUOUS`) to prevent accidental "delete all". To wipe everything explicitly, use `$filter=true eq true`.

`row_path` walks root→leaf, each hop carries the OData `filter` (and optional `$orderby`) that was in effect on the FE grid when the user targeted that row. The server uses this to resolve `row_index → __row_id` via a bounded file-service query (`$skip=row_index&$top=1`). Only `__row_id` is used for the actual mutation; `row_index` + `filter` are view-targeting inputs.

**Reset operations** (for DATA_EDIT / any node with input/output separation):

`reset_to_input: true` — discard ALL edits. Replaces `variables[node_id]` with a fresh copy-on-write reference to `task.resolved_input`. All `artifact_ops` and `scalars` in the same request are ignored; only the reset + `rerun_strategy` are applied. Returns the input's ETag as the new ETag.

`reset_artifact` op — revert a single artifact to its original version:

```json
{ "op": "reset_artifact", "artifact_id": "orders.line_items" }
```

Backend: reads `task.resolved_input`, finds the matching artifact's `file_id`, swaps it back into `variables[node_id]`'s tree for that artifact. Only that artifact reverts; other edits are preserved. Other ops in the same batch execute against the post-reset state.

**Schema-revert on drifted reset**: if a prior `replace_artifact` with `schema_compatibility: "drift_ok"` altered this artifact's column set, `reset_artifact` also rebuilds the runtime-schema entry for the artifact from the input file's columns (the schema follows the data). Both the data ETag and the schema ETag bump; FE must refetch `GET /schema`.

For INPUT / HUMAN_ACTION (no separate original), `reset_to_input` and `reset_artifact` return `422 VALIDATION_FAILED` with `code: "NO_INPUT_REFERENCE"` — there's nothing to reset to.

**Response** (200 OK, `ETag: W/"<new_etag>"`):

```json
{
  "etag": "sha256:new...",
  "version": 13,
  "file_lineage": {
    "f_orders_xyz": "f_orders_xyz_v2",
    "f_line_items_qrs": "f_line_items_qrs_v2"
  },
  "results": [
    { "op_index": 0, "status": "ok", "rows_affected": 1, "row_ids": ["r_ab12"] },
    { "op_index": 1, "status": "ok", "rows_affected": 27 },
    { "op_index": 2, "status": "ok", "rows_affected": 1, "row_ids": ["r_nw41"] },
    { "op_index": 3, "status": "ok", "rows_affected": 5 }
  ],
  "rerun": {
    "strategy": "cascade",
    "triggered": true,
    "degraded_from": null,
    "reset_node_ids": ["n_human_review", "n_scoring", "n_notify"],
    "task_ids": ["t_new_1"]
  }
}
```

**Pending-node degradation**: if the caller sends `rerun_strategy != "none"` while the target node is in state `PENDING` (nothing has run yet, so there's nothing to rerun), the server silently degrades the strategy to `"none"`. PATCH still commits data atomically. Response:

```json
{
  "rerun": {
    "strategy":      "none",          // actual behaviour applied
    "triggered":     false,
    "degraded_from": "cascade",       // caller's original value; null when no degradation
    "reset_node_ids": [],
    "task_ids":      []
  }
}
```

**Errors**:

- 409 `PRECONDITION_REQUIRED` — `If-Match` missing
- 412 `PRECONDITION_FAILED` — stale ETag; body carries `details.current_etag`
- 422 `VALIDATION_FAILED` — per-op error list in `details.ops[]`
- 423 `NODE_STATE_INVALID` — node not editable (e.g., run cancelled)
- 404 `NODE_NOT_FOUND`

### 3.5 `POST /runs/{run_id}/nodes/{node_id}/submit`

Terminal state transition for pending nodes. **No body.** Data edits happen via PATCH beforehand; `submit` finalizes.

Flow:
1. Resolve target task (latest for the node, or `?task_id=`).
2. Validate the full current `__file_ref` subtree against schema (`NodeInputValidator`).
3. Transition node from pending → submitted; mark task complete with `resolved_input` pointing at the current `__file_ref`.
4. Trigger downstream dispatch via the existing orchestration engine path.

Response: 200 OK with `{task_id, node_state, downstream_dispatched: true}`. 422 on validation failure. 423 if node not pending.

### 3.6 `POST /runs/{run_id}/rerun`

Run-level rerun (future use case).

**Request body** (all optional):

```json
{
  "from_node_id": "n_scoring",
  "rerun_strategy": "cascade",
  "variables": { "threshold": 0.7 }
}
```

- `from_node_id` omitted → restart from entry nodes.
- Otherwise reset state from `from_node_id` using `collect_downstream_nodes_to_reset` and dispatch.
- `variables` → override run-level variables before dispatch.

Response: `{new_run_id_or_continuation, reset_node_ids[], dispatched_task_ids[]}`.

### 3.7 `GET /runs/{run_id}/artifacts`

Returns the run-level `$artifacts` registry (see §4.4).

**Query**: `task_id?` (accepted for interface symmetry; ignored — registry is run-scoped).

**Response headers**: `ETag: W/"<artifacts_etag>"` derived from content hash + run revision; supports `If-None-Match` → 304.

**Response** (200 OK):

```json
{
  "artifacts": {
    "invoice_pdf": {
      "file_id":          "f_abc",
      "filename":         "invoice.pdf",
      "content_type":     "application/pdf",
      "size_bytes":       284112,
      "produced_by_node": "upload_invoice",
      "produced_by_task": "t_1",
      "registered_at":    "2026-04-16T10:30:00Z"
    },
    "cleaned_data": { ... }
  }
}
```

**Errors**: 404 `RUN_NOT_FOUND`.

**Use cases**: FE reviewer panel "Files produced by this run"; builder's artifact explorer during test runs.

### 3.8 `POST /runs/{run_id}/resolve`

Batched variable-path evaluator. Thin wrapper over `VariableResolver`; no writes.

**Request**:

```json
{
  "paths": [
    "$.review_node.customer_name",
    "$.review_node.orders[0].line_items[0].price",
    "$artifacts.invoice_pdf.filename"
  ],
  "task_id": "optional"
}
```

**Response** (200 OK):

```json
{
  "results": [
    { "path": "$.review_node.customer_name",
      "status": "ok", "kind": "scalar", "value": "Acme" },
    { "path": "$.review_node.orders[0].line_items[0].price",
      "status": "ok", "kind": "scalar", "value": 9.5 },
    { "path": "$artifacts.invoice_pdf.filename",
      "status": "ok", "kind": "scalar", "value": "invoice.pdf" }
  ]
}
```

**Per-path error shape** (within `results[]` when a single path fails):

```json
{ "path": "...", "status": "error",
  "error": { "code": "PATH_NOT_FOUND", "message": "..." } }
```

**Behaviour**:

- Evaluates each path via the existing `VariableResolver` chain (`FileBackedResolverStrategy`, `MetadataResolverStrategy`, `TraversalResolverStrategy`).
- For nested-artifact paths (`$.node.orders[0].line_items[0].price`), reuses PATCH's `RowPathResolver` to walk via file-service queries on `__row_id` / `__parent_row_id`.
- For `$artifacts.<name>[.<subkey>]`, reads the new metadata-object shape (§4.4).
- Per-path failures don't fail the whole request — errors surface in `results[]`.

**Limits**: up to 100 paths per request; each path subject to the 16-hop nesting cap.

**Errors (request-level)**: 400 `INVALID_PATH_SYNTAX` (if a path can't be parsed); 404 `RUN_NOT_FOUND`; 413 `BATCH_TOO_LARGE`.

**Primary consumer**: the workflow builder UI — when a user drags a variable chip, the builder calls `/resolve` to preview the concrete value under the current test run.

---

## 4. Data Model Evolution

### 4.1 Recursive `__file_ref`

Every level is a full file-ref (recursive, not a leaf descriptor):

```json
{
  "__file_ref": true,
  "version": 3,
  "etag": "sha256:7d3f...",
  "file_id": "f_main_abc",
  "content_type": "text/csv",
  "columns": ["customer_name", "order_total"],
  "extracted": { "customer_name": {"value": "Acme"}, "order_total": {"value": 1200} },
  "artifacts": [
    {
      "__file_ref": true,
      "artifact_id": "orders",
      "name": "orders",
      "artifact_type": "array_field",
      "file_id": "f_orders_xyz",
      "columns": ["__row_id", "sku", "qty", "line_items"],
      "parent_row_key_column": null,
      "artifacts": [
        {
          "__file_ref": true,
          "artifact_id": "orders.line_items",
          "artifact_type": "nested_array_field",
          "file_id": "f_line_items_qrs",
          "columns": ["__row_id", "__parent_row_id", "part_no", "price", "attrs"],
          "parent_row_key_column": "__parent_row_id",
          "artifacts": [
            {
              "__file_ref": true,
              "artifact_id": "orders.line_items.attrs",
              "file_id": "f_attrs_tuv",
              "columns": ["__row_id", "__parent_row_id", "key", "value"],
              "parent_row_key_column": "__parent_row_id",
              "artifacts": []
            }
          ]
        }
      ]
    }
  ]
}
```

Key additions:

- `artifact_id` — stable dotted path; unique within the tree; used as the addressing key on the wire.
- `__row_id` — synthetic ULID injected into every CSV row on write. The durable row identity.
- `__parent_row_id` — injected into every nested-array CSV, referencing the parent row's `__row_id`. Enables OData drilldown: `GET .../artifacts/orders.line_items/odata?$filter=__parent_row_id eq '<id>'`.
- `version` — monotonic counter, bumped per successful edit.
- `etag` — see §5.

Note: **no `row_count`** at any level — file service answers via OData `$count=true`.

### 4.2 Schema — static vs dynamic

Schemas come from two sources depending on the node kind:

| Source | `schema_source` | Node kinds | Who authors | When generated |
|---|---|---|---|---|
| **Design-time (static)** | `design_time` | INPUT, HUMAN_ACTION, FILE_UPLOAD | FE (workflow builder) | At design time; stored on node definition |
| **Runtime (dynamic)** | `runtime` | DATA_EDIT, potentially others | **Backend** (node handler) | When the handler executes; stored in `task.output_metadata["input_schema"]` |

For **static schemas**, the backend stores the schema as-is and returns it unchanged via `GET /schema`. The backend does not define, interpret, or validate rendering details (wrappers, controls, conditions) — those are FE-owned.

For **dynamic schemas**, the node handler inspects the upstream node's output (e.g., CSV columns and types) and generates the schema at runtime. The DATA_EDIT handler writes the computed schema into `task.output_metadata["input_schema"]`. When a `rerun_strategy=cascade` or `rerun_strategy=node_only` re-runs the node, the handler regenerates the schema from the (possibly changed) upstream data.

**Schema resolution precedence** (same as today):
1. Runtime: `task.output_metadata["input_schema"]` (handler-computed)
2. Design-time: `node.config["_node_definition"]["input_schema"]` (stored in workflow)
3. Fallback: `node.config["input_schema"]`

**Schema regeneration**: when upstream data changes (via edit + cascade rerun), the DATA_EDIT handler re-executes and re-derives the schema. The schema ETag changes; FE's `If-None-Match` catches it. There is no separate "regenerate schema" action — `rerun_strategy=node_only` on the DATA_EDIT node achieves this naturally.

**Backend conventions** (applies to both static and dynamic schemas):

1. **`outputType`** (`string | number | boolean | object | array`) — the wire shape the FE will serialize. Used by the validator.
2. **Artifact binding** — `wrapperProps.artifact_binding: "<dotted.path>"` maps a schema field to the descriptor tree (§4.1).
3. **Validation rules** (`rules: [{method, value, message}]`) — enforced by a generic method-dispatching evaluator (§8). Rule semantics are FE-defined; the backend does not own the catalogue.

Everything else in the schema (rendering, UI props, conditions, nested grouping) is pass-through.

### 4.3 File uploads — metadata in scalars, bytes in file service

File-upload components in the FE auto-form (e.g., `UploadControl`) produce a scalar `outputType: "object"` field. The value is a metadata object:

```json
{ "file_id": "f_abc", "filename": "contract.pdf", "size_bytes": 284112, "content_type": "application/pdf" }
```

The metadata is stored in the main scalar CSV alongside other scalars and returned inline in `data.scalars` on `GET /data`. Collections of files are modelled as a normal CSV artifact where each row carries the same metadata columns (`file_id`, `filename`, `size_bytes`, `content_type`).

Bytes live in the file service. FE uploads directly to the file service to obtain a `file_id`, then references it in `PATCH /data`. FE downloads bytes directly from the file service using `file_id`. **The control-plane never accepts or serves file bytes.**

Consequently, `artifact_type` has only two values: `array_field` and `nested_array_field`. There is no `blob` artifact type.

See reference doc §4.8 for the full integration flow.

### 4.4 `$artifacts` registry (run-level)

`run.context.variables["artifacts"]` is a **run-level cross-node registry** of named file references, populated whenever a node's output contains a named artifact. Scope: one dict per run, keyed by artifact `name`. Not per-node.

**Stored shape** (metadata object, keyed by name):

```python
run.context.variables["artifacts"] = {
    "invoice_pdf": {
        "file_id":          "f_abc",
        "filename":         "invoice.pdf",
        "content_type":     "application/pdf",
        "size_bytes":       284112,
        "produced_by_node": "upload_invoice",
        "produced_by_task": "t_1",
        "registered_at":    "2026-04-16T10:30:00Z"
    },
    ...
}
```

**Population** (existing mechanism, shape upgraded): `register_artifacts()` in `layer1_domain/entities/data_reference.py` is called from:

- `layer2_application/features/complete_task/use_cases/complete_task_use_case.py:186` (worker completion)
- `layer2_application/features/resume_pending_task/use_cases/resume_pending_task_use_case.py:354` (pending node resume — FILE_UPLOAD, INPUT, etc.)
- `layer2_application/features/add_node_artifact/use_cases/add_node_artifact_use_case.py:82` (artifact attach)

Signature gains `node_id` + `task_id` parameters so provenance is preserved. Registration still walks `node_output["artifacts"]` and registers entries with a non-empty `name`.

**Resolver impact** (clean redesign — no legacy):

- `{{$artifacts.invoice_pdf}}` → now resolves to the **full metadata object** (previously a bare file_id string).
- `{{$artifacts.invoice_pdf.file_id}}` → the opaque file_id handle.
- `{{$artifacts.invoice_pdf.filename}}` → display name.
- The DATA_EDIT handler fallback path (`layer1_domain/services/node_handlers/data_edit_handler.py:67-80`) is updated to read `.file_id` from the metadata object.
- Existing node definitions with mappings like `{{$artifacts.<name>}}` that expect a bare id must be rewritten to `{{$artifacts.<name>.file_id}}`. One-shot update; no dual-read.

**Why expose it separately from per-node `/data`** — `$artifacts` is run-scoped, not node-scoped. Folding it into every `/data` response would duplicate the registry on every per-node fetch. A dedicated endpoint `GET /runs/{run_id}/artifacts` serves the whole registry in one round-trip and can be independently ETag-tracked.

**Per-node link**: `GetDataResponse` gains an optional `produced_artifacts: string[]` — the names this node contributed to the registry during its most recent completion. Lets FE navigate from a per-node view back to the run-level registry without re-querying.

See §3.7 for the new `GET /runs/{run_id}/artifacts` endpoint, §3.8 for `POST /runs/{run_id}/resolve`, and §18 for variable-suggestions walker updates.

---

## 5. ETag Strategy

Per-node weak ETag, returned on every GET, required on PATCH via `If-Match`.

```
etag = "sha256:" + sha256(
    main_file_id
  | "|"
  | sorted(descendant_file_ids).join(",")
  | "|"
  | schema_version
  | "|"
  | monotonic_revision
)
```

- Stored on `__file_ref.etag` at the root; bumped whenever any descendant changes (because any descendant change produces a new `file_id` → new hash input).
- `monotonic_revision` persisted on `__file_ref.version`.
- Computation lives in a new domain service `NodeInputETagCalculator` (`layer1_domain/services/node_input_etag_calculator.py`).
- HTTP header: `ETag: W/"sha256:..."` (weak — row ordering can vary).
- PATCH acquires a per-run `asyncio.Lock` inside `EditNodeDataUseCase` for read-modify-write atomicity.
- 412 error body:

```json
{
  "status": "error",
  "error": {
    "code": "PRECONDITION_FAILED",
    "message": "Node input was modified since last read.",
    "details": { "current_etag": "sha256:..." }
  }
}
```

---

## 6. Atomicity (Snapshot-and-Swap)

The file service is **content-addressed** — uploads produce new `file_id`s, never mutating in place. This gives us snapshotting for free; 2PC is unnecessary.

1. Read current `__file_ref`; record pre-image set (all `file_id`s in the tree).
2. For each op: download affected CSV → mutate in memory → upload → capture new `file_id`. Journal entry: `{artifact_id, old_file_id, new_file_id, op_index}`.
3. On any failure (validation, upload error, file-service error): **abort**. Don't swap the run-context pointer. Best-effort GC orphaned new `file_id`s. Pre-image remains authoritative.
4. On success: rebuild `__file_ref` tree with new `file_id`s, bump `version`, recompute `etag`, then swap `run.context.variables[node_id]` pointer. **This pointer swap is the commit point.** Readers before the swap see old state; readers after see new.
5. Persist journal on `stage_record` (`layer1_domain/entities/stage_record.py`) for audit / rollback-if-needed.

Per-run `asyncio.Lock` serializes read-modify-write sequences inside the use case, preventing lost updates across concurrent requests (ETag is the cross-request guard; the lock is the in-request guard).

---

## 7. Rerun Wiring

| Strategy | Action |
|---|---|
| `none` | Pointer swap only |
| `node_only` | Swap + `RerunTaskUseCase.execute` (existing, `layer2_application/features/rerun_task/use_cases/rerun_task_use_case.py:61`) |
| `cascade` | Swap + `collect_downstream_nodes_to_reset` BFS → reset affected task states to `PENDING_RERUN`, clear `run.context.variables[node_id]`, dispatch start node; existing `SuccessorProcessor` handles onward flow |

`collect_downstream_nodes_to_reset` is a new sibling of `collect_downstream_nodes_to_skip` (`layer1_domain/services/dag_traversal.py:10`). Same BFS; different return-set semantics.

Run-level rerun (`POST /runs/{run_id}/rerun`) uses the same `collect_downstream_nodes_to_reset` from `from_node_id`, or resets all nodes if omitted.

---

## 8. Schema Validation (generic rule dispatcher)

New domain service `NodeInputValidator` (`layer1_domain/services/node_input_validator.py`). The backend does **not** own the rule vocabulary — rules are strings authored by the FE auto-form. The validator is a generic method-dispatching evaluator.

- Input: the FE-authored schema blob, `__file_ref` subtree, pending edit list.
- For each field touched by an edit, iterates the field's `rules: [{method, value?, message?}]` and dispatches on `method` to a pluggable evaluator registry (`required`, range/length comparators, string predicates, number predicates, regex, etc.).
- For `bulk_update` ops, validates the assignment value once against the target column's rules (not per-row current state).
- For `update_cell` and `insert_rows`, validates each proposed cell value.
- Called from `EditNodeDataUseCase` before any file-service write.
- Also called from `SubmitNodeUseCase` as a full-tree validation before state transition.
- Unknown rule methods are rejected with `422 VALIDATION_FAILED` so that FE-side additions to the rule catalogue fail loudly and prompt a backend evaluator registration.
- 422 error body: `details.ops[{op_index, field, rule, message, value?}]`.

---

## 9. Row-Path Resolution

New domain service `RowPathResolver` (`layer1_domain/services/row_path_resolver.py`):

Walks a `row_path` list from root→leaf. For each hop `{artifact_id, row_index, filter}`:

1. Construct file-service OData query: `<filter> & <orderby?> & $skip=row_index & $top=1 & $select=__row_id`.
2. Send to file service; get back the single `__row_id`.
3. For the next hop (child artifact), prepend `$filter=__parent_row_id eq '<resolved>'` to any filter carried on that hop.

At the leaf, the resolved `__row_id` is the target of `update_cell`; for `bulk_update` / `delete_rows` at an artifact level, the file service receives the composed filter and executes server-side.

This resolver is the single piece that turns frontend view-coordinates (`row_index + filter`) into backend identity (`__row_id`). If `row_index >= actual row count under filter`, returns `ROW_NOT_FOUND` and the enclosing op fails with 422.

---

## 10. Use Cases & File Layout

### 10.1 New features

Under `workflow-control-plane-service/app/layer2_application/features/`:

| Feature | Purpose |
|---|---|
| `get_node_schema` | Returns schema + schema_version; ETag |
| `get_node_data` | Returns scalars + recursive descriptor tree; ETag |
| `edit_node_data` | Batch edit (scalars + `artifact_ops[]`); snapshot-and-swap; rerun dispatch |
| `submit_node` | Terminal pending-node submission; full-tree validation; downstream dispatch |
| `rerun_run` | Run-level rerun with optional `from_node_id` |
| `query_node_artifact` | Scoped OData wrapper around file service |
| `validate_node_input` | Thin wrapper around `NodeInputValidator` |
| `get_run_artifacts` | Returns the `$artifacts` registry for a run (§3.7) |
| `resolve_variable_paths` | Batched path evaluator for builder preview (§3.8) |

Each follows the repo convention: `features/{name}/use_cases/{name}_usecase.py` exporting `{PascalCase}UseCase`. Dependencies declared in `__init__`; picked up by the `bootstrap.py` smart-injection scan.

### 10.2 Features deleted

- `features/get_node_input_data/` — replaced by `get_node_data`
- `features/edit_node_input/` — replaced by `edit_node_data`
- `features/resume_input_node/` — replaced by `submit_node`
- Node-input branch of `features/resume_pending_task/` — moved to `submit_node`. Task-completion path for worker-driven flow stays.

### 10.3 Domain services (new)

Under `workflow-control-plane-service/app/layer1_domain/services/`:

- `node_input_validator.py`
- `node_input_etag_calculator.py`
- `row_path_resolver.py`

### 10.4 Files modified

| File | Change |
|---|---|
| `layer1_domain/entities/data_reference.py` | Recursive `build_file_ref`; `walk_file_ref`, `find_artifact`; add `version` + `etag` fields; remove flat-artifact helpers. **`register_artifacts` signature gains `node_id`/`task_id`; stored shape changes from `{name: file_id}` to `{name: metadata_object}` (§4.4).** |
| `layer2_application/features/complete_task/use_cases/complete_task_use_case.py:186` | Pass `node_id`/`task_id` to `register_artifacts`. |
| `layer2_application/features/resume_pending_task/use_cases/resume_pending_task_use_case.py:354` | Same. |
| `layer2_application/features/add_node_artifact/use_cases/add_node_artifact_use_case.py:82` | Same. |
| `layer1_domain/services/node_handlers/data_edit_handler.py:67-80` | Update fallback path to read `.file_id` from the metadata object. |
| `layer2_application/features/get_variable_suggestions/...` | Recurse schema tree; emit per-kind suggestions (§17.2); `$artifacts` section reads metadata-object registry. |
| `layer1_domain/services/data_store_service.py` | Recurse on nested array-of-dict rows; inject `__row_id` / `__parent_row_id`; add `materialize_tree`; remove flat path |
| `layer1_domain/services/file_backed_resolver.py` | Walk recursive artifacts for variable resolution |
| `layer1_domain/services/dag_traversal.py` | Add `collect_downstream_nodes_to_reset` |
| `layer2_application/errors.py` | New error codes |
| `layer3_adapters/controllers/restful/v1/run_controller.py` | Replace route list with the final surface; remove `input-data`, `output`, `data-info`, `artifacts`, `edit-node-input`, `resume-input`, `tasks/{id}/resume`; add `GET /runs/{id}/artifacts` and `POST /runs/{id}/resolve`. |
| `layer3_adapters/controllers/restful/v1/file_controller.py` | Delete (raw proxy gone) |
| `layer3_adapters/controllers/restful/v1/artifact_controller.py` | Delete |
| `layer3_adapters/controllers/restful/v1/dtos/` | New DTOs: `GetSchemaResponse`, `GetDataResponse`, `PatchDataRequest`, `PatchDataResponse`, `SubmitNodeResponse`, `RerunRunRequest`, `RerunRunResponse`, `GetRunArtifactsResponse`, `ArtifactRef`, `ResolvePathsRequest`, `ResolvePathsResponse`. Extend `VariablePath` with `kind`. |

---

## 11. Error Codes

| HTTP | Code | Meaning |
|---|---|---|
| 404 | `NODE_NOT_FOUND` | node_id unknown or not in this run |
| 404 | `ARTIFACT_NOT_FOUND` | artifact_id not in the node's tree |
| 403 | `FORBIDDEN` | artifact doesn't belong to this run/node/task |
| 409 | `PRECONDITION_REQUIRED` | `If-Match` missing on PATCH |
| 410 | `INPUT_EVICTED` | file_ids no longer resolvable |
| 412 | `PRECONDITION_FAILED` | stale ETag; `details.current_etag` supplied |
| 422 | `VALIDATION_FAILED` | per-op validation errors in `details.ops[]` |
| 422 | `STATIC_SCHEMA_IMMUTABLE` | column op attempted on a node whose `schema_source == "design_time"` |
| 422 | `ROW_PATH_AMBIGUOUS` | `delete_row` without a terminal row-targeting hop; also `delete_rows` with empty filter |
| 422 | `NO_INPUT_REFERENCE` | `reset_to_input` / `reset_artifact` on a node without input/output separation |
| 422 | `FILE_NOT_FOUND` | `replace_artifact.file_id` doesn't exist in the file service (tenancy-scoped lookup) |
| 422 | `MISSING_SYNTHETIC_COLUMNS` | `replace_artifact` target file lacks `__row_id` (or `__parent_row_id` for nested) |
| 422 | `COLUMN_MISMATCH` | `replace_artifact` under `strict` compatibility and replacement columns differ from artifact's declared columns |
| 422 | `PARENT_ROW_ID_MISMATCH` | Nested `replace_artifact` whose `__parent_row_id` values don't match the resolved parent row |
| 423 | `NODE_STATE_INVALID` | node not editable / not pending |

---

## 12. Edge Cases

1. **Row-index under filter is fragile under concurrent edits.** ETag catches the inconsistency (any descendant change bumps the node ETag), but FE must be told: on 412, reload, re-render, and re-target. Documented in the API contract: "`row_index + filter` is valid only under a matching ETag."
2. **1M-row tables on human-filled pending INPUT nodes are not realistic.** Realistic only for `DATA_EDIT` nodes seeded from upstream. Schema may set `editable_rows_cap` per artifact via `FieldConfig.wrapper_props` to gate per-row editing in the FE; bulk-update-by-filter is always available.
3. **Loop iterations.** Without `?task_id`, all endpoints operate on the most recent task for the node (same precedent as the old `ResumeInputNodeUseCase`). With `?task_id=...`, endpoints operate on that specific iteration's `task.resolved_input`.
4. **`extracted` scalar cache invalidation.** After every successful edit, `EditNodeDataUseCase` re-runs `DataStoreService._build_extracted` over the new main-CSV dict. Nested-artifact edits don't invalidate root `extracted` (arrays are not cached there); only the affected sub-artifact's `extracted` (if any) is recomputed. The `__file_ref.version` bump forces any downstream variable-resolver cache to miss.
5. **Concurrent PATCH + worker completion.** Per-run `asyncio.Lock` serializes. Worker completion uses a different path (`complete` endpoint) but also acquires the same lock to avoid ETag drift.
6. **Large payload on PATCH.** `artifact_ops[]` can grow; cap at e.g. 1000 ops per request. Caller chunks with ETag round-trip for atomic batches larger than the cap.

---

## 13. Verification

### Unit tests

- `NodeInputValidator` — one test per rule-method evaluator in the dispatcher registry; nested propagation; bulk-update value-only validation; unknown-method rejection.
- `NodeInputETagCalculator` — determinism, sensitivity to any descendant `file_id` change, ordering invariance of descendant list.
- `RowPathResolver` — 1/2/3-deep happy paths; with and without filter/orderby; out-of-bound `row_index`; missing `__parent_row_id`.
- `DataStoreService.materialize_tree` — round-trip on an arbitrary-depth fixture.
- `walk_file_ref` / `find_artifact` — on recursive trees.

### Integration tests

- Pending HUMAN_ACTION with 3-level nested table: `GET schema` + `GET data` + scoped OData + `PATCH data` (bulk_update at depth 2, insert_rows at depth 3, scalar at root) atomically → verify new ETag + rerun dispatched.
- Stale `If-Match` → 412 with current ETag body.
- One-op validation failure → whole batch rolled back; old `file_id`s still referenced; new `file_id`s GC'd.
- `rerun_strategy=cascade` → correct downstream reset set + redispatch.
- `rerun_strategy=none` → data mutation only, no task scheduled.
- `POST submit` on pending node → full-tree validation; node transitions; downstream dispatch.
- `POST /runs/{run_id}/rerun` with/without `from_node_id` → correct reset set + dispatch.
- Loop iteration: two tasks for same node_id; `?task_id=` targets the right one.

### Manual end-to-end

With `INFRA_MODE=mock python main.py` in `workflow-control-plane-service`:

1. Start workflow with a HUMAN_ACTION node that has a 3-level nested-table schema.
2. `GET .../schema` → 200 + ETag. Retry with `If-None-Match` → 304.
3. `GET .../data?side=input` → descriptor tree + ETag.
4. `GET .../artifacts/orders.line_items/odata?$filter=__parent_row_id eq 'r_ab12'&$top=50&$count=true` → rows + total.
5. `PATCH .../data` with `If-Match`, mixed ops at different depths, `rerun_strategy=cascade` → 200 + new ETag + rerun info.
6. Repeat PATCH with the old ETag → 412 carrying current ETag.
7. `POST .../submit` → node transitions; downstream nodes begin executing.
8. `POST /runs/{run_id}/rerun` with `from_node_id` of an earlier node → that subtree resets and re-runs.

---

## 14. Sequence Sketches

### PATCH happy path

```
FE → CP: PATCH /runs/.../nodes/.../data (If-Match: W/"v12")
CP: acquire run-lock
CP: load __file_ref → compute current_etag
CP: If-Match matches? yes
CP → Validator: validate(ops, schema)
CP → RowPathResolver → FS: resolve row_index per hop → __row_id
CP → FS: download → mutate → upload (per op); capture new file_ids; journal
CP: recompute etag; build new __file_ref
CP: swap run.context.variables[node_id]  ← COMMIT
CP → DAG: cascade reset + dispatch (if rerun_strategy != none)
CP → FE: 200 {etag, file_lineage, results, rerun}
CP: release run-lock
```

### PATCH with stale ETag

```
FE → CP: PATCH (If-Match: W/"v10")
CP: acquire run-lock
CP: compute current_etag → W/"v12"
CP: If-Match mismatch
CP → FE: 412 {code: PRECONDITION_FAILED, details.current_etag: "v12"}
CP: release run-lock
```

### SUBMIT

```
FE → CP: POST /runs/.../nodes/.../submit (no body)
CP: resolve task (latest or ?task_id)
CP: load full __file_ref tree → Validator.validate_all()
CP: pass? yes → task.status = COMPLETED, task.resolved_input = __file_ref
CP → Engine: dispatch_successors
CP → FE: 200 {task_id, node_state: SUBMITTED, downstream_dispatched: true}
```

---

## 15. Open Items

- Exact shape of the file service OData response for `$count=true` (assumed OData v4 `@odata.count`). Confirm with the file-service team during implementation.
- Schema `schema_version` source: derived from node definition revision? Surface the decision when implementing `GetNodeSchemaUseCase`.
- `FILE_UPLOAD` node kind: uploaded files are stored **in the file service**, not as separate control-plane artifacts. The schema's file-upload component produces a scalar `outputType: "object"` field whose value is `{ file_id, filename, size_bytes, content_type }`; the metadata sits in `data.scalars` (or as columns inside a normal CSV artifact for collections of files). FE uploads and downloads bytes directly against the file service using `file_id`. Control-plane never accepts or serves file bytes — see the reference doc §4.8.
- **Per-run lock distribution in multi-instance deployments — unresolved, must be addressed before scaling out.** `RunLockRegistry` uses `asyncio.Lock` in a single-process `OrderedDict`. This serialises read-modify-write cycles **within one control-plane process**, but has no cross-instance visibility. In a multi-instance deployment a true race is possible:

  ```
  T=0  Instance A: GET run → etag v12      Instance B: GET run → etag v12
  T=1  A: If-Match v12 == v12 ✓            B: If-Match v12 == v12 ✓
  T=2  A: upload, compute new etag v13     B: upload, compute new etag v13
  T=3  A: run_repo.save() → persisted v13  B: run_repo.save() → overwrites A's v13
  ```

  Both clients receive `200 OK` with `ETag: v13`, but A's edits are silently lost. ETag-only protects the **sequential** case (a second commit that still carries a stale `If-Match`) but not the **simultaneous** case where both instances load the run before either writes.

  Three mitigations, any of which closes the hole:

  | Option | Mechanism | Trade-offs |
  |---|---|---|
  | **(a) Sticky routing** | L7 load balancer hashes `run_id` so every request for a given run lands on the same instance. In-process `RunLockRegistry` then suffices because the two-instance race cannot occur for any single run. | Simplest. Needs consistent-hash ingress (Envoy / Nginx / Istio / service mesh). Hot runs may unbalance the fleet. |
  | **(b) Repo-level optimistic concurrency** | Add a monotonic `version` column on the run row. `run_repo.save(run, expected_version=prev)` performs a CAS (`UPDATE runs SET … WHERE id=? AND version=?`); on zero rows affected, surface as `412 PRECONDITION_FAILED` so the client retries with a fresh ETag. | Schema change. Works across regions and across instance restarts. Recommended safety net. |
  | **(c) Distributed lock** | Swap the in-process `asyncio.Lock` for a Redis-backed lock (`SET key NX PX <ttl>`) keyed by `run_id`. `RunLockRegistry`'s interface stays the same; only the backend changes. | Adds Redis as a hard write-path dependency. Lock-expiry tuning is delicate (crashes while holding a lock need TTL fallback). |

  **Recommendation**: ship **(a) + (b) together** before the first multi-instance deployment. (a) handles the steady-state by construction; (b) is the safety net for rebalances, restarts, and admin operations that break sticky routing.

  **Scope now**: single-instance only. The `RunLockRegistry` interface is deliberately narrow so a later swap is a local change. An integration test that simulates two concurrent commits (and asserts exactly one receives 412) must be added when (a)/(b) land.

---

## 16. Frontend TypeScript Interfaces

Canonical types for FE. Every API response is wrapped in `ApiEnvelope<T>`; request shapes are given directly.

### 16.1 Common envelope + error shapes

```ts
export type ApiEnvelope<T> =
  | { status: "ok"; data: T }
  | { status: "error"; error: ApiError };

export interface ApiError {
  code: ApiErrorCode;
  message: string;
  details?: Record<string, unknown>;
}

export type ApiErrorCode =
  | "NODE_NOT_FOUND"
  | "ARTIFACT_NOT_FOUND"
  | "FORBIDDEN"
  | "PRECONDITION_REQUIRED"
  | "PRECONDITION_FAILED"
  | "INPUT_EVICTED"
  | "VALIDATION_FAILED"
  | "NODE_STATE_INVALID"
  | "RUN_NOT_READY";

export type NodeKind = "INPUT" | "HUMAN_ACTION" | "DATA_EDIT" | "FILE_UPLOAD";
export type NodeState =
  | "PENDING" | "SUBMITTED" | "RUNNING" | "COMPLETED"
  | "FAILED"  | "SKIPPED"   | "PENDING_RERUN";
```

### 16.2 `GET /schema` response

The schema body is **opaque to the backend** — it's the FE-authored auto-form definition stored with the node. The backend returns it unchanged. The FE team owns the typing of its own schema structure.

```ts
export interface GetSchemaResponse {
  schema: unknown;             // FE-owned auto-form schema (opaque to BE)
  schema_version: string;
  node_kind: NodeKind;
  node_state: NodeState;
}
// HTTP headers: ETag: W/"<schema_etag>" (weak); respects If-None-Match → 304
```

### 16.3 `GET /data` response — recursive descriptor

```ts
export interface ArtifactDescriptor {
  artifact_id: string;                // "__root__" at the top, dotted path elsewhere
  file_id: string;                    // opaque; FE hands to file service
  columns: string[];                  // includes __row_id (and __parent_row_id for nested)
  parent_row_key_column: string | null; // "__parent_row_id" on nested children; null at root
  children: ArtifactDescriptor[];     // recursive; [] at leaves
  artifact_type?: "array_field" | "nested_array_field";
  content_type?: string;              // "text/csv" (artifacts are always CSV-backed)
}

export interface NodeData {
  etag: string;                       // "sha256:..."  (also sent as weak ETag header)
  version: number;                    // monotonic; bumped on every commit
  scalars: Record<string, unknown>;   // flat scalar values at root
  root: ArtifactDescriptor;
}

export interface GetDataResponse {
  run_id: string;
  node_id: string;
  task_id: string;
  node_kind: NodeKind;
  node_state: NodeState;
  schema_ref: { url: string; etag: string };
  data: NodeData;                     // when side="input"
  output?: NodeData;                  // when side="output" or "both"
}
// HTTP headers: ETag: W/"<node_etag>"
// Query: ?side=input|output|both (default input), ?task_id=... (loop iteration)
```

### 16.4 `PATCH /data` request / response

> For the full integration guide (op semantics, row_path protocol, error recovery, limits, worked examples), see the FE reference doc `node-input-api-reference.md` §7.

```ts
export type RerunStrategy = "none" | "node_only" | "cascade";

export interface RowPathHop {
  artifact_id: string;
  row_index: number;                  // index under the given filter+order
  filter: string;                     // full OData query string, e.g. "$filter=region eq 'APAC'&$orderby=sku asc"
                                      // empty string if no filter was active
}

export type ArtifactOp =
  | UpdateCellOp
  | BulkUpdateOp
  | InsertRowsOp
  | DeleteRowsOp;

export interface UpdateCellOp {
  op: "update_cell";
  row_path: RowPathHop[];             // root → leaf (inclusive of target artifact)
  column: string;
  value: unknown;
}

export interface BulkUpdateOp {
  op: "bulk_update";
  artifact_id: string;
  row_path?: RowPathHop[];            // omit for root-level artifacts
  filter: string;                     // OData filter scoping the rows
  assignments: Record<string, unknown>;
}

export interface InsertRowsOp {
  op: "insert_rows";
  artifact_id: string;
  row_path?: RowPathHop[];            // parent row_path when inserting into a nested artifact
  rows: Array<Record<string, unknown>>;
}

export interface DeleteRowsOp {
  op: "delete_rows";
  artifact_id: string;
  row_path?: RowPathHop[];
  filter: string;                     // OData filter scoping deletion
}

export interface PatchDataRequest {
  rerun_strategy: RerunStrategy;
  scalars?: Record<string, unknown>;
  artifact_ops?: ArtifactOp[];
}
// Headers: If-Match: W/"<etag>" REQUIRED

export interface OpResult {
  op_index: number;
  status: "ok" | "failed";
  rows_affected?: number;
  row_ids?: string[];                 // __row_ids of inserted/updated rows
  error?: ApiError;                   // set when status=failed (whole batch still rolled back)
}

export interface PatchDataResponse {
  etag: string;
  version: number;
  file_lineage: Record<string, string>; // old_file_id → new_file_id
  results: OpResult[];
  rerun: {
    strategy: RerunStrategy;
    triggered: boolean;
    reset_node_ids: string[];
    task_ids: string[];               // newly dispatched tasks
  };
}
// Headers: ETag: W/"<new_etag>"
// 412 PRECONDITION_FAILED body: { status:"error", error:{ code:"PRECONDITION_FAILED", details:{ current_etag:"..." } } }
// 422 VALIDATION_FAILED  body:  { status:"error", error:{ code:"VALIDATION_FAILED",  details:{ ops:[{op_index,field,rule,message}] } } }
```

### 16.5 `POST /submit`

```ts
// Request: no body
// Query: ?task_id=... (optional; defaults to latest task for the node)

export interface SubmitNodeResponse {
  task_id: string;
  node_state: NodeState;               // "SUBMITTED"
  downstream_dispatched: boolean;
}
// 422 VALIDATION_FAILED on full-tree validation failure (same shape as PATCH)
```

### 16.6 `POST /runs/{run_id}/rerun`

```ts
export interface RerunRunRequest {
  from_node_id?: string;               // omit → restart from entry nodes
  rerun_strategy?: RerunStrategy;      // defaults to "cascade"
  variables?: Record<string, unknown>; // run-level variable overrides
}

export interface RerunRunResponse {
  reset_node_ids: string[];
  dispatched_task_ids: string[];
}
```

### 16.7 Scoped OData wrapper

```
GET /runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata?<OData>&task_id?
```

Pass-through to the file service. FE treats it as the file service with tenancy pre-checked. Response shape follows OData v4:

```ts
export interface ODataPage<Row> {
  "@odata.count"?: number;             // present when $count=true
  value: Row[];                        // each row includes __row_id (+ __parent_row_id for nested)
}
```

### 16.8 FE read/write flow (reference)

```
On node open
├─ GET /schema  (If-None-Match: cached.etag → 304 or fresh schema)
├─ GET /data    (remember data.etag)
└─ For each ArtifactDescriptor the user opens:
   └─ GET /artifacts/{artifact_id}/odata?$top=50&$count=true[&$filter=…][&$orderby=…]

On cell edit (single row)
└─ PATCH /data { If-Match: data.etag, artifact_ops:[ update_cell(row_path=[{artifact_id,row_index,filter}],column,value) ] }
   ├─ 200 → store new etag; optionally refresh visible page from file service
   ├─ 412 → reload /data (+ visible OData pages) and re-try with new etag
   └─ 422 → highlight failing ops from details.ops[]

On bulk update
└─ PATCH /data { If-Match, artifact_ops:[ bulk_update(artifact_id,filter,assignments) ] }

On final submission (pending node)
└─ POST /submit
```

### 16.9 `GET /runs/{run_id}/artifacts` + `POST /runs/{run_id}/resolve`

```ts
export interface ArtifactRef {
  file_id:          string;
  filename?:        string;
  content_type:     string;
  size_bytes:       number;
  produced_by_node: string;
  produced_by_task: string;
  registered_at:    string;   // ISO-8601
}

export interface GetRunArtifactsResponse {
  artifacts: Record<string, ArtifactRef>;
}
// HTTP: ETag weak; supports If-None-Match → 304

export interface ResolvePathsRequest {
  paths:    string[];          // variable expressions, e.g. "$.node.orders[0].sku"
  task_id?: string;
}

export type PathKind =
  | "scalar" | "file_ref" | "artifact" | "artifact_row" | "artifact_column";

export interface ResolveResult {
  path:   string;
  status: "ok" | "error";
  kind?:  PathKind;            // set when status="ok"
  value?: unknown;             // set when status="ok"
  error?: ApiError;            // set when status="error"
}

export interface ResolvePathsResponse {
  results: ResolveResult[];
}
```

---

## 17. Variable Suggestions (design-time) — interaction with this design

`GET /runs/{run_id}/nodes/{node_id}/variable-suggestions` (existing endpoint, `GetVariableSuggestionsUseCase`) stays as the design-time path picker. **No endpoint change; walker logic change.**

### 17.1 Why keep it separate from `/data`

| Aspect | `/variable-suggestions` | `/data` |
|---|---|---|
| Purpose | "What paths *can* I use?" | "What does this node's state look like *right now*?" |
| Input | Workflow + current node position | run_id + node_id |
| Output | List of reachable paths + `kind` | Actual values + descriptor tree |
| Run needed? | No (design-time); or Yes when previewing under a test run | Yes |

Use `/resolve` (§3.8) to evaluate a picked path against a concrete run.

### 17.2 Walker updates

With recursive artifacts, file-metadata scalars, and the new `$artifacts` registry shape, the walker must emit a richer path set and tag each with `kind`:

For each upstream node, walking its schema tree:

| Emitted path | `kind` | When |
|---|---|---|
| `$.node.scalar_key` | `scalar` | `outputType ∈ {string, number, boolean}` |
| `$.node.file_field` | `file_ref` | `outputType: "object"` with upload control |
| `$.node.file_field.file_id` | `scalar` | Auto-expand one level into file-metadata objects (file_id, filename, size_bytes, content_type) |
| `$.node.object_field.sub_key` | `scalar` | Nested `outputType: "object"` without file-upload control |
| `$.node.table` | `artifact` | `outputType: "array"` + `wrapperProps.artifact_binding` |
| `$.node.table[*]` | `artifact_row` | Table row iterator (LOOP/PARALLEL consumers) |
| `$.node.table[*].col` | `artifact_column` | Column extractor |
| `$.node.table[*].nested[*].col` | `artifact_column` | Recurses arbitrarily deep |

For `$artifacts` (run-level):

| Emitted path | `kind` |
|---|---|
| `$artifacts.invoice_pdf` | `file_ref` |
| `$artifacts.invoice_pdf.file_id` | `scalar` |
| `$artifacts.invoice_pdf.filename` | `scalar` |
| `$artifacts.invoice_pdf.content_type` | `scalar` |
| `$artifacts.invoice_pdf.size_bytes` | `scalar` |

### 17.3 DTO change

Extend `VariablePath` with `kind: PathKind` (same enum as `ResolveResult.kind`). Existing consumers ignoring `kind` are unaffected.

### 17.4 Runtime resolver behaviour per `kind`

| `kind` resolved at runtime | Returned value |
|---|---|
| `scalar` | Plain value (from main CSV scalars or `extracted` cache) |
| `file_ref` | Metadata object `{file_id, filename, content_type, size_bytes, ...}` — NOT bytes |
| `artifact` | Opaque `__file_ref` subtree; LOOP/worker SDK materializes via file service |
| `artifact_row` / `artifact_column` | Materialized rows/values via scoped file-service queries (same `RowPathResolver` used by PATCH) |

No changes to existing resolver strategies beyond making `FileBackedResolverStrategy` walk recursive artifacts (already in the design doc §10).

---

## 18. References

- Plan file: `/root/.claude/plans/smooth-leaping-aho.md`
- FE integration reference: [`node-input-api-reference.md`](./node-input-api-reference.md) — compact interface/enum reference + full `PATCH /data` integration guide.
- Existing code:
  - `workflow-control-plane-service/app/layer1_domain/entities/data_reference.py`
  - `workflow-control-plane-service/app/layer1_domain/services/data_store_service.py`
  - `workflow-control-plane-service/app/layer1_domain/services/file_backed_resolver.py`
  - `workflow-control-plane-service/app/layer1_domain/services/dag_traversal.py:10` (`collect_downstream_nodes_to_skip`)
  - `workflow-control-plane-service/app/layer2_application/features/rerun_task/use_cases/rerun_task_use_case.py:61`
  - `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/run_controller.py`
