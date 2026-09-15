# INPUT Node Runtime Integration

End-to-end runtime integration guide for an INPUT (or DATA_EDIT) node whose
auto-form schema combines **two scalar inputs** with **one table artifact**.
Covers every HTTP API the frontend calls between "user opens the node" and
"node is submitted and downstream work runs".

**Status**: current as of 2026-04-24 (post data-factory proxy / `PATCH /data-v2`).

---

## 1. The node this document targets

Schema (shape the FE auto-form renders from `GET /schema`):

```json
[
  { "key": "input",   "outputType": "string",
    "fieldConfig": {"fieldWrapper": "FormItemWrapper", "fieldControl": "InputControl",
                    "wrapperProps": {"label": "Input 1"}} },

  { "key": "input_1", "outputType": "string",
    "fieldConfig": {"fieldWrapper": "FormItemWrapper", "fieldControl": "InputControl",
                    "wrapperProps": {"label": "Input 2"}} },

  { "key": "table_3_lg2h0", "outputType": "array",
    "fieldConfig": {"fieldWrapper": "TableWrapper",
                    "wrapperProps": {"label": "Table", "artifact_binding": "table_3_lg2h0"}},
    "fields": [
      { "key": "table_3_lg2h0_row", "outputType": "object",
        "fieldConfig": {"fieldWrapper": "FragmentWrapper", "wrapperProps": {}},
        "fields": [
          { "key": "input_2", "outputType": "string",
            "fieldConfig": {"fieldWrapper": "FormItemWrapper", "fieldControl": "InputControl",
                            "wrapperProps": {"label": "col A"}} },
          { "key": "input_3", "outputType": "string",
            "fieldConfig": {"fieldWrapper": "FormItemWrapper", "fieldControl": "InputControl",
                            "wrapperProps": {"label": "col B"}} }
        ] }
    ] }
]
```

**How it maps to storage.** The node's working data is a `__file_ref` tree:

```
__file_ref (root)
├── file_id       → "main" CSV holding the two scalars (input, input_1)
├── extracted     → {"input": {"value": "..."}, "input_1": {"value": "..."}}
├── columns       → ["input", "input_1"]
└── artifacts[0]  (artifact_id = "table_3_lg2h0", artifact_type = "array_field")
    ├── file_id   → separate CSV: "__row_id,input_2,input_3\n..."
    └── columns   → ["__row_id", "input_2", "input_3"]
```

Two things the FE must know up-front:

1. **Labels are UI-only.** `"col A"` / `"col B"` are *display* strings. The
   **storage keys** are the `key` fields — `input_2` and `input_3`. Every row
   mutation below uses the keys.
2. **The table body is a separate CSV** addressed by `artifact_id =
   "table_3_lg2h0"` (from `wrapperProps.artifact_binding`). Scalars live in
   the root CSV; each table is its own file.

---

## 2. Endpoint quick reference

All node endpoints are under `/api/v1/runs/{run_id}/nodes/{node_id}/…`.

| Method | Path                                      | Purpose                                     | If-Match    | Rerun?  |
| :----- | :---------------------------------------- | :------------------------------------------ | :---------- | :------ |
| GET    | `/schema`                                 | Opaque auto-form schema blob                | —           | —       |
| GET    | `/data`                                   | Scalars + artifact descriptor tree          | —           | —       |
| GET    | `/artifacts/{artifact_id}/odata`          | Paged row data (OData query against CSV)    | —           | —       |
| PATCH  | `/data` **(legacy)**                      | Atomic batch edit, 10 op kinds              | **required**| yes     |
| PATCH  | `/data-v2` **(proxy)**                    | Row insert/delete via data-factory          | no          | no      |
| POST   | `/submit`                                 | Transition PENDING → COMPLETED              | no          | —       |
| POST   | `/api/v1/runs/{run_id}/rerun`             | Restart from this node (cascade)            | no          | yes     |
| POST   | `/api/v1/files/upload`                    | Upload file to file service (multipart)     | —           | —       |

**Rule of thumb**

- **Scalars, cell edits, column ops, `reset_to_input`** → `PATCH /data` (legacy).
- **Adding rows, deleting rows** → `PATCH /data-v2` (proxy).
- **Read paged table data** → `GET …/artifacts/{artifact_id}/odata`.
- **Finalize user input** → `POST /submit`.
- **Kick off downstream after edits** → `POST …/rerun` (or set `rerun_strategy` on PATCH /data).

---

## 3. Node lifecycle

An INPUT node task goes through these states (from `node_data_projection.py`):

```
 PENDING ──submit───► COMPLETED ──rerun──► PENDING_RERUN ──engine──► RUNNING ──► COMPLETED
    │                     │
    └── edit freely ──────┘   (edits after COMPLETED require rerun to propagate)
```

| State           | Can PATCH /data? | Can PATCH /data-v2? | Can submit? | Can rerun?        |
| :-------------- | :--------------- | :------------------ | :---------- | :---------------- |
| PENDING         | yes              | yes                 | **yes**     | degrades to none  |
| SUBMITTED       | yes              | yes                 | no          | yes               |
| RUNNING         | read-only        | read-only           | no          | no                |
| COMPLETED       | yes              | yes                 | no          | yes               |
| FAILED          | yes              | yes                 | no          | yes               |
| PENDING_RERUN   | yes              | yes                 | yes         | degrades to none  |
| SKIPPED         | read-only        | read-only           | no          | no                |

**Degradation rule** — on a PENDING node, the engine has nothing to re-execute,
so any `rerun_strategy` of `"node_only"`/`"cascade"` silently degrades to
`"none"` with `rerun.degraded_from` set in the response. Submit first, then
rerun.

---

## 4. ETag & concurrency

**Format.** Weak ETag, returned by every read and mutation:

```
ETag: W/"sha256:7c1a…f0"
```

**Computed from** main file_id + sorted descendant file_ids + schema_version +
version counter (`node_input_etag_calculator.py`). Any successful PATCH bumps
the version, so the ETag changes even for semantically-identical edits.

**Semantics.**

- `PATCH /data` requires `If-Match`. Missing → **409 PRECONDITION_REQUIRED**,
  stale → **412 PRECONDITION_FAILED** with the current ETag in the response
  details so the FE can refetch + retry.
- `PATCH /data-v2` does **not** check `If-Match` (the data-factory has no
  ETag concept). It still returns a fresh ETag in the response so the FE
  can chain subsequent reads without a round-trip.
- `GET /schema`, `GET /data`, `GET /artifacts/…` accept `If-None-Match` and
  return **304 Not Modified** on match.

Always pass the ETag you most recently observed — either from the last GET
or from a previous PATCH response header — as `If-Match` on `PATCH /data`.

---

## 5. Flow 1 — Open the node

### 5.1 Fetch the form schema

```http
GET /api/v1/runs/{run_id}/nodes/{node_id}/schema
If-None-Match: W/"sha256:<last-known-etag>"   (optional)
```

**Response** (`GetNodeSchemaResponse`):

```json
{
  "schema": [ /* the AutoFormField array shown in §1 */ ],
  "schema_version": "hash_or_timestamp",
  "schema_source": "design_time",
  "node_kind": "INPUT",
  "node_state": "PENDING"
}
```

The schema blob is opaque to the backend — whatever is stored on the node
definition (`config._node_definition.input_schema`) is returned verbatim.

### 5.2 Fetch current data

```http
GET /api/v1/runs/{run_id}/nodes/{node_id}/data
If-None-Match: W/"sha256:<last-known-etag>"   (optional)
```

**Response** (`GetNodeDataResponse`):

```json
{
  "run_id":    "r_abc",
  "node_id":   "n1",
  "task_id":   "t_xyz",
  "node_kind": "INPUT",
  "node_state": "PENDING",
  "schema_ref": {"version": "…"},
  "data": {
    "etag":    "sha256:7c1a…f0",
    "version": 3,
    "scalars": { "input": "hello", "input_1": "" },
    "root": {
      "artifact_id": "__root__",
      "file_id":     "f_main_csv",
      "columns":     ["input", "input_1"],
      "children": [
        {
          "artifact_id":            "table_3_lg2h0",
          "file_id":                "f_table_csv",
          "columns":                ["__row_id", "input_2", "input_3"],
          "parent_row_key_column":  null,
          "artifact_type":          "array_field",
          "content_type":           "text/csv",
          "children": []
        }
      ]
    },
    "output_port":       null,
    "taken_edge_ports":  null
  },
  "output": null,
  "produced_artifacts": [],
  "etag": "sha256:7c1a…f0"
}
```

**What the FE uses**:

- `data.scalars` → prefill the two `FormItemWrapper` controls (`input`, `input_1`).
- `data.root.children[0].file_id` → the CSV file_id to query for table rows.
- `data.root.children[0].artifact_id` → what to pass to `/data-v2` or
  `/artifacts/{artifact_id}/odata`.
- Top-level `etag` → store as the current `If-Match` value.

### 5.3 Read table rows (paged)

The control-plane deliberately does **not** return row data in `GET /data` —
only identity (`file_id`, `columns`). Pull rows through the OData proxy:

```http
GET /api/v1/runs/{run_id}/nodes/{node_id}/artifacts/table_3_lg2h0/odata
    ?$select=__row_id,input_2,input_3
    &$top=50
    &$skip=0
    &$count=true
    &$orderby=input_2%20asc
```

**Response** (pass-through of the file service's OData shape):

```json
{
  "@odata.count": 12,
  "value": [
    { "__row_id": "r_0001", "input_2": "alpha", "input_3": "A" },
    { "__row_id": "r_0002", "input_2": "beta",  "input_3": "B" }
  ]
}
```

**Always `$select` `__row_id`** — you need it to identify rows for
cell-edit and delete-by-id operations below.

---

## 6. Flow 2 — Edit the two scalar inputs

Scalars live in the **root** CSV, not in the table artifact. The
data-factory proxy is row-scoped and doesn't handle scalars, so this goes
through the **legacy** `PATCH /data`.

```http
PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data
If-Match: W/"sha256:7c1a…f0"
Content-Type: application/json

{
  "rerun_strategy": "none",
  "scalars": {
    "input":   "hello world",
    "input_1": "second value"
  }
}
```

**Response** (`PatchNodeDataResponse`, status 200):

```json
{
  "etag":    "sha256:9d4e…11",
  "version": 4,
  "file_lineage": { "f_main_csv": "f_main_csv_v2" },
  "results": [],
  "rerun":   { "strategy": "none", "triggered": false,
               "degraded_from": null, "reset_node_ids": [], "task_ids": [] }
}
```

The response `ETag` header carries the weak-form of the new etag
(`W/"sha256:9d4e…11"`). **Update your stored ETag** before the next call.

**Bulk edit**: you can send `scalars` and `artifact_ops` in the same PATCH —
scalars apply first, then each op in order, atomically. If any op fails
the whole batch rolls back and newly-uploaded files are orphan-GC'd.

---

## 7. Flow 3 — Add rows to the table (via /data-v2)

This is the primary reason `/data-v2` exists. Pass operations **verbatim** in
the data-factory row-transform shape (row-transform.md), with `artifact_id`
replacing `file_path`.

### 7.1 Append a single row

```http
PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data-v2
Content-Type: application/json

{
  "artifact_id": "table_3_lg2h0",
  "file_format": "csv",
  "operations": [
    {
      "operation":      "insert",
      "row_identifier": { "type": "index", "index": 0 },
      "data":           { "input_2": "gamma", "input_3": "C" },
      "position":       "after"
    }
  ]
}
```

**Notes**

- `data` keys are the schema **`key`** values (`input_2`, `input_3`),
  **not** the labels (`col A`, `col B`).
- Do **not** include `__row_id` in `data` — the data-factory assigns it.
- `position` is one of `before | after | at_index`, relative to the
  `row_identifier.index`. For "append at end", target the last index with
  `after`, or use `at_index` = current row count.
- You do **not** send `If-Match`.

### 7.2 Insert multiple rows

```json
{
  "artifact_id": "table_3_lg2h0",
  "operations": [
    { "operation": "insert",
      "row_identifier": { "type": "index", "index": 0 },
      "data": { "input_2": "x1", "input_3": "X1" },
      "position": "before" },
    { "operation": "insert",
      "row_identifier": { "type": "index", "index": 1 },
      "data": { "input_2": "x2", "input_3": "X2" },
      "position": "before" }
  ]
}
```

Operations run in order, so indices refer to the file state **at the start
of the batch** (per the row-transform server's contract).

### 7.3 Response

```json
{
  "success":       true,
  "file_id":       "f_table_csv_v3",
  "affected_rows": 1,
  "total_rows":    13,
  "message":       "ok",
  "etag":          "sha256:b2a6…42"
}
```

The use case automatically:

1. Swaps `table_3_lg2h0`'s `file_id` to the returned one.
2. Bumps the root `__file_ref` version and computes a fresh ETag.
3. Emits `NodeDataEdited` so projector-backed reads (GET /data) see the
   change immediately.
4. Best-effort-deletes the pre-image `file_id`.

---

## 8. Flow 4 — Edit a cell inside the table

**Cell-level edits are NOT in the /data-v2 proxy yet** — go through legacy
`PATCH /data` with an `update_cell` op.

### 8.1 By row-path (index within the table)

```http
PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data
If-Match: W/"sha256:<current>"
Content-Type: application/json

{
  "rerun_strategy": "none",
  "artifact_ops": [
    {
      "op":       "update_cell",
      "row_path": [{ "artifact_id": "table_3_lg2h0", "row_index": 2, "filter": "" }],
      "column":   "input_3",
      "value":    "new value"
    }
  ]
}
```

`row_path` is a list of hops; for a top-level table it's one entry. `filter`
is reserved for scoped navigation (leave empty string for now).

### 8.2 Bulk update (many rows, one assignment set)

```json
{
  "rerun_strategy": "none",
  "artifact_ops": [
    {
      "op":          "bulk_update",
      "artifact_id": "table_3_lg2h0",
      "filter":      "$filter=input_2%20eq%20'alpha'",
      "assignments": { "input_3": "A-updated" }
    }
  ]
}
```

`filter` here is an OData query string (same syntax as `/artifacts/.../odata`)
— rows matching the filter are updated in one pass.

---

## 9. Flow 5 — Delete rows from the table (via /data-v2)

### 9.1 By row_id (preferred — the FE already has it from step 5.3)

```json
{
  "artifact_id": "table_3_lg2h0",
  "operations": [
    { "operation": "delete",
      "row_identifier": { "type": "values",
                           "match_values": { "__row_id": "r_0001" } } }
  ]
}
```

### 9.2 By index

```json
{
  "artifact_id": "table_3_lg2h0",
  "operations": [
    { "operation": "delete",
      "row_identifier": { "type": "index", "index": 3 } }
  ]
}
```

### 9.3 By condition (Polars expression)

```json
{
  "artifact_id": "table_3_lg2h0",
  "operations": [
    { "operation": "delete",
      "row_identifier": { "type": "condition",
                           "expression": "pl.col('input_2').str.contains('temp')" } }
  ]
}
```

Response shape is identical to Flow 3; `affected_rows` reflects the delete count.

---

## 10. Flow 6 — Submit the node

When the user is done editing, POST submit to flip the task to COMPLETED and
dispatch downstream work.

```http
POST /api/v1/runs/{run_id}/nodes/{node_id}/submit
Content-Type: application/json

{}
```

Optional query param: `?task_id={task_id}` to target a specific loop
iteration (not needed for a plain INPUT node).

**Response** (`SubmitNodeResponse`):

```json
{
  "task_id":               "t_xyz",
  "node_state":            "COMPLETED",
  "downstream_dispatched": true
}
```

Contract:

- The node must be in `PENDING` / `SUBMITTED` / `PENDING_RERUN`. Submitting
  a COMPLETED node is a no-op error.
- Data must already be written via PATCH /data or /data-v2 — the submit
  body does not accept inline data.
- The current working `__file_ref` becomes the task's final output; engine
  walks downstream nodes.

---

## 11. Flow 7 — Rerun the node

Two entry points. Pick based on what you want:

### 11.1 Re-run only this node after editing (inline dispatch)

Set `rerun_strategy` on **legacy** `PATCH /data`. It's the only way to
combine an edit with a rerun in a single request.

```json
{
  "rerun_strategy": "node_only",
  "scalars":        { "input": "corrected value" }
}
```

Or to reset everything downstream:

```json
{
  "rerun_strategy": "cascade",
  "artifact_ops": [ { "op": "update_cell", "row_path": [...], "column": "input_3", "value": "x" } ]
}
```

**Response** includes:

```json
"rerun": {
  "strategy":       "cascade",
  "triggered":      true,
  "degraded_from":  null,
  "reset_node_ids": ["n1", "n2", "n3"],
  "task_ids":       ["t_new_id"]
}
```

If the node is still PENDING, you'll see `"strategy": "none",
"degraded_from": "cascade"` — submit first, then rerun.

### 11.2 Re-run the whole subtree explicitly (decoupled from edit)

```http
POST /api/v1/runs/{run_id}/rerun
Content-Type: application/json

{
  "from_node_id":  "n1",
  "rerun_strategy": "cascade",
  "variables":     { "threshold": 0.7 }   // optional shallow-merge into context
}
```

**Response** (`RerunRunResponse`):

```json
{
  "reset_node_ids":      ["n1", "n2", "n3"],
  "dispatched_task_ids": ["t_new_id"]
}
```

### 11.3 What `/data-v2` does NOT do

`/data-v2` **never triggers rerun**. It only swaps the artifact's file_id
and emits `NodeDataEdited`. If downstream must re-run after a row insert
/ delete, follow up with `POST /runs/{run_id}/rerun?from_node_id=…` or use
`PATCH /data` with `rerun_strategy` for a combined request.

---

## 12. End-to-end worked example

**Goal**: a user opens the node, fills in the two scalars, adds two rows,
edits one cell, removes one row, and submits.

```
┌──────────────────────────────┬─────────────────────────────────────────────┐
│ Step                         │ Request                                     │
├──────────────────────────────┼─────────────────────────────────────────────┤
│ 1. Load schema               │ GET   /nodes/n1/schema                      │
│ 2. Load scalars + artifact   │ GET   /nodes/n1/data                        │
│    tree (capture ETag E0)    │                                             │
│ 3. Load table rows (paged)   │ GET   /nodes/n1/artifacts/table_3_lg2h0/    │
│                              │       odata?$top=50&$count=true             │
│ 4. Edit scalars              │ PATCH /nodes/n1/data                        │
│                              │   If-Match: E0                              │
│                              │   body: {"scalars": {"input":"x",           │
│                              │           "input_1":"y"}}                   │
│                              │   → response etag E1                        │
│ 5. Add row #1                │ PATCH /nodes/n1/data-v2                     │
│                              │   body: insert op (data={"input_2":"a",     │
│                              │           "input_3":"A"}, position:"after", │
│                              │           index: last_index)                │
│                              │   → response etag E2                        │
│ 6. Add row #2                │ PATCH /nodes/n1/data-v2                     │
│                              │   body: insert op …                         │
│                              │   → response etag E3                        │
│ 7. Edit cell in row #1       │ PATCH /nodes/n1/data                        │
│                              │   If-Match: E3                              │
│                              │   body: update_cell op using row_path       │
│                              │   → response etag E4                        │
│ 8. Delete a row              │ PATCH /nodes/n1/data-v2                     │
│                              │   body: delete op with match_values         │
│                              │     {"__row_id": "r_0001"}                  │
│                              │   → response etag E5                        │
│ 9. Submit                    │ POST  /nodes/n1/submit                      │
│                              │   → node_state=COMPLETED,                   │
│                              │     downstream_dispatched=true              │
└──────────────────────────────┴─────────────────────────────────────────────┘
```

Between any two steps the FE can GET `/artifacts/…/odata` again to refresh
the on-screen table without refetching the whole node.

---

## 13. Error catalog

Error codes returned by the endpoints above (envelope shape
`{"status": "error", "error": {"code", "message", "details"}}`):

| Code                           | HTTP | Where it comes from                                                    |
| :----------------------------- | :--- | :--------------------------------------------------------------------- |
| `RUN_NOT_FOUND`                | 404  | Any endpoint — `run_id` doesn't exist                                  |
| `WORKFLOW_NOT_FOUND`           | 404  | Any endpoint — run's workflow missing                                  |
| `NODE_NOT_FOUND`               | 404  | Any endpoint — `node_id` not in workflow                               |
| `ARTIFACT_NOT_FOUND`           | 404  | `/data-v2`, `update_cell`, `delete_row` — unknown `artifact_id`        |
| `ARTIFACT_EMPTY`               | 400  | `/data-v2` — artifact's `file_id` is empty (nothing to transform)      |
| `PRECONDITION_REQUIRED`        | 409  | `PATCH /data` — `If-Match` header missing                              |
| `PRECONDITION_FAILED`          | 412  | `PATCH /data` — `If-Match` stale; `details.current_etag` gives latest  |
| `VALIDATION_FAILED`            | 422  | `PATCH /data` — op payload violates node schema rules                  |
| `ROW_NOT_FOUND`                | 422  | `PATCH /data` — `row_path` points at a row that no longer exists       |
| `FILE_NOT_FOUND`               | 404  | `replace_artifact` — given `file_id` missing from file service         |
| `COLUMN_MISMATCH`              | 422  | `replace_artifact` (strict) — replacement columns don't match          |
| `STATIC_SCHEMA_IMMUTABLE`      | 422  | `add_column` / `drop_column` / `rename_column` on a design-time schema |
| `FACTORY_UNAVAILABLE`          | 500  | `/data-v2` — network/transport failure calling the data-factory        |
| `FACTORY_ERROR`                | 500  | `/data-v2` — data-factory returned `success=false`                     |
| `FACTORY_BAD_RESPONSE`         | 500  | `/data-v2` — factory returned success without a `file_id`              |
| `TASK_SUPERSEDED`              | 409  | callbacks after a reset bumped the run generation (worker only)        |

FE retry strategy:

- `PRECONDITION_FAILED` → re-GET `/data` to pick up the current ETag, then
  re-apply the edit.
- `FACTORY_UNAVAILABLE` → transient; retry with exponential backoff.
- Everything else → surface to the user; don't blind-retry.

---

## 14. Why two PATCH endpoints?

`/data-v2` is a **thin proxy** to an external data-factory service (see
`row-transform.md`). The legacy `/data` keeps the full op surface
(scalars, cell edits, column ops, schema-aware validation, rerun dispatch,
orchestration-queue serialization). The split exists so the FE can start
moving row-heavy traffic off the control plane immediately while the
data-factory surface grows to cover the rest. When parity is reached:

1. The remaining ops (`update_cell`, `bulk_update`, `add_column`, etc.)
   get proxy endpoints backed by the data-factory's column-transform API.
2. Validation + rerun dispatch move server-side on the new routes.
3. Legacy `/data` is deleted and `/data-v2` is renamed `/data`.

Until then: **row insert/delete → `/data-v2`, everything else → `/data`.**

---

## 15. Source pointers (for contributors)

- DTOs: `app/layer3_adapters/controllers/restful/v1/dtos/node_data_dtos.py`
- Routes: `app/layer3_adapters/controllers/restful/v1/run_controller.py`
- ETag: `app/layer1_domain/services/node_input_etag_calculator.py`
- Node state projection: `app/layer1_domain/services/node_data_projection.py`
- Legacy edit use case: `app/layer2_application/features/edit_node_data/use_cases/edit_node_data_use_case.py`
- Proxy use case: `app/layer2_application/features/edit_node_data_via_factory/use_cases/edit_node_data_via_factory_use_case.py`
- Factory port: `app/layer2_application/interfaces/node_data_factory_service_interface.py`
- Row-transform adapter: `app/layer4_frameworks/services/row_transform_factory_service.py`
- Submit use case: `app/layer2_application/features/submit_node/use_cases/submit_node_use_case.py`
- Rerun use case: `app/layer2_application/features/rerun_run/use_cases/rerun_run_use_case.py`
- Data-factory upstream contract: `row-transform.md` (repo root)
