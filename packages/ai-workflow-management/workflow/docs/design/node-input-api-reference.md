# Node Input / Edit API — Interface Reference

> Companion to `node-input-review-edit-api.md`. Compact reference for the frontend team: every endpoint, every DTO field, every enum value.
>
> **Out of scope**: the **auto-form schema** (`fieldWrapper`, `fieldControl`, conditions, validation-rule vocabulary, etc.) is authored and owned by the frontend. The backend stores and returns the schema object unchanged via `GET /schema` — it does not interpret rendering or control keys.

---

## 1. Endpoint Index

| Method | Path | Purpose | Cache | Concurrency |
|---|---|---|---|---|
| `GET`   | `/runs/{run_id}/nodes/{node_id}/schema` | FE-owned auto-form schema (opaque to BE) | ETag (weak) | `If-None-Match` → 304 |
| `GET`   | `/runs/{run_id}/nodes/{node_id}/data` | Scalars + descriptor tree of artifacts | ETag (weak) | `If-None-Match` → 304 |
| `GET`   | `/runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata` | Scoped OData query → file service | — | — |
| `PATCH` | `/runs/{run_id}/nodes/{node_id}/data` | Atomic batch edit (scalars + artifact ops) | — | `If-Match` required |
| `POST`  | `/runs/{run_id}/nodes/{node_id}/submit` | Finalize a pending node | — | — |
| `GET`   | `/runs/{run_id}/artifacts` | Run-level `$artifacts` registry | ETag (weak) | `If-None-Match` → 304 |
| `POST`  | `/runs/{run_id}/resolve` | Batched variable-path evaluator (builder preview) | — | — |
| `POST`  | `/runs/{run_id}/rerun` | Run-level rerun | — | — |
| `GET`   | `/runs/{run_id}/nodes/{node_id}/variable-suggestions` | Design-time path picker (walker updated — see design doc §17) | — | — |

Node-scoped endpoints accept `?task_id={task_id}` to target a specific loop iteration; without it, the latest task for the node is used.

---

## 2. Common Headers

| Header | Direction | Endpoints | Notes |
|---|---|---|---|
| `ETag` | response | `GET /schema`, `GET /data`, `PATCH /data` | Weak: `W/"sha256:..."`. Sent on 200. |
| `If-None-Match` | request | `GET /schema`, `GET /data` | Match → 304 empty body. |
| `If-Match` | request | `PATCH /data` | **Required.** Missing → 409. Stale → 412. |
| `Cache-Control` | response | `GET /schema` | `private, max-age=30` |

---

## 3. Common Envelope

```ts
type ApiEnvelope<T> =
  | { status: "ok"; data: T }
  | { status: "error"; error: ApiError };

interface ApiError {
  code: ApiErrorCode;
  message: string;
  details?: Record<string, unknown>;
}
```

---

## 4. Enums

### 4.1 `ApiErrorCode`

| Code | HTTP | Meaning | `details` shape |
|---|---|---|---|
| `RUN_NOT_READY` | 409 | Run exists but not ready for the action | — |
| `NODE_NOT_FOUND` | 404 | `node_id` unknown in this run | — |
| `ARTIFACT_NOT_FOUND` | 404 | `artifact_id` not in the node's tree | `{artifact_id}` |
| `FORBIDDEN` | 403 | Artifact / file outside this run+node+task | — |
| `PRECONDITION_REQUIRED` | 409 | `If-Match` missing on PATCH | — |
| `PRECONDITION_FAILED` | 412 | Stale `If-Match` | `{current_etag}` |
| `INPUT_EVICTED` | 410 | `file_id`s no longer resolvable | `{missing_file_ids: string[]}` |
| `VALIDATION_FAILED` | 422 | Validation rejected one or more ops | `{ops: ValidationOpError[]}` |
| `STATIC_SCHEMA_IMMUTABLE` | 422 | Column op attempted on static (design_time) schema | `{op_index, artifact_id}` |
| `ROW_PATH_AMBIGUOUS` | 422 | `delete_row` without terminal hop, or `delete_rows` with empty filter | `{op_index}` |
| `NO_INPUT_REFERENCE` | 422 | `reset_to_input` / `reset_artifact` on a node without input/output separation | — |
| `FILE_NOT_FOUND` | 422 | `replace_artifact.file_id` not found in file service | `{op_index, file_id}` |
| `MISSING_SYNTHETIC_COLUMNS` | 422 | Replacement file lacks `__row_id` (or `__parent_row_id` for nested) | `{op_index, missing: string[]}` |
| `COLUMN_MISMATCH` | 422 | Replacement columns differ from artifact's declared columns under `strict` | `{op_index, expected: string[], actual: string[]}` |
| `PARENT_ROW_ID_MISMATCH` | 422 | Nested `replace_artifact` `__parent_row_id` values don't match resolved parent | `{op_index, expected_parent_row_id, observed: string[]}` |
| `NODE_STATE_INVALID` | 423 | Node not editable | `{current_state: NodeState}` |
| `ROW_NOT_FOUND` | 422 | `row_path` resolved no row for some hop | `{op_index, hop_index}` |
| `BATCH_TOO_LARGE` | 413 | Too many ops or payload bytes | `{limit_ops, limit_bytes}` |

```ts
interface ValidationOpError {
  op_index: number;    // -1 for scalars
  field: string;       // column or scalar key
  rule: string;        // validation rule method name
  message: string;
  value?: unknown;
}
```

### 4.2 `NodeKind`

| Value | Meaning |
|---|---|
| `INPUT` | User-designed input form (pending). |
| `HUMAN_ACTION` | Human review / approval (pending). |
| `DATA_EDIT` | Edit large upstream dataset (pending). |
| `FILE_UPLOAD` | Upload binary + optional mapping (pending). |

### 4.3 `NodeState`

| Value | Meaning | Editable? |
|---|---|---|
| `PENDING` | Waiting for user input | ✅ PATCH + SUBMIT |
| `SUBMITTED` | Submitted, downstream dispatched | ⚠️ PATCH only with rerun |
| `RUNNING` | Task executing | ❌ |
| `COMPLETED` | Task finished | ⚠️ PATCH only with rerun |
| `FAILED` | Task errored | ⚠️ PATCH + rerun |
| `SKIPPED` | Skipped by DAG | ❌ |
| `PENDING_RERUN` | Reset by upstream edit; awaiting dispatch | ❌ |

### 4.4 `RerunStrategy`

| Value | Behaviour |
|---|---|
| `none` | Pointer swap only; no re-execution. |
| `node_only` | Re-run the edited node. |
| `cascade` | Re-run the edited node + BFS-reset of all downstream nodes. |

### 4.5 `OutputType` — wire shape FE serializes (not a UI hint)

| Value | FE sends | Storage |
|---|---|---|
| `string` | JSON string (incl. ISO dates, enum strings, file_id strings) | Scalar cell |
| `number` | JSON number | Scalar cell |
| `boolean` | JSON boolean | Scalar cell |
| `object` | JSON object | Scalar cell (flattened or JSON-serialized) |
| `array` — scalar | JSON array of scalars (e.g., multi-select tags) | Scalar cell (JSON-serialized) |
| `array` — artifact-bound | **Never inline.** FE mutates via PATCH `artifact_ops`. | Separate file-backed artifact |

"Artifact-bound" = schema carries `wrapperProps.artifact_binding = "<dotted.path>"`. The exact FE rendering rules (wrappers, controls) are not backend concerns.

### 4.5b `artifact_binding` — when the FE must opt into the nested data layer

`wrapperProps.artifact_binding` on a schema field is the **sole signal** that tells the backend "store this field's data in its own file-backed artifact, not as an inline scalar." It is a FE-authored attribute; the backend does not infer it.

**Routing rule** (applies to every `outputType: "array"` field wrapped in `TableWrapper`):

| Schema has `wrapperProps.artifact_binding`? | FE submits data via | Backend storage |
|---|---|---|
| **Yes** — e.g. `"artifact_binding": "orders"` | `PATCH /data` → `artifact_ops` (`insert_rows`, `update_cell`, …) | Separate CSV file; row-granular edits via OData; `GET /data` returns a child descriptor |
| **No** | `PATCH /data` → `scalars` (the whole array as the field's value) | Main scalar CSV; JSON-serialized into a single cell; no pagination |

**When the FE must add it**:

1. **Any table that might grow beyond ~1,000 rows**. Without `artifact_binding`, the whole array is stored in one CSV cell and returned in full on every `GET /data`. Past a few thousand rows that's unusably slow.
2. **Any table that needs per-row edits by the user** (clicking a cell and changing one value). Without `artifact_binding` the FE must rewrite the whole array on every keystroke; with it, one `update_cell` op mutates exactly one row.
3. **Any table that needs filter/sort/pagination in the grid**. OData pagination only works against artifact-backed tables.
4. **Nested tables** (a table inside a row of another table). Each level needs its own `artifact_binding` — dotted to reflect the tree, e.g., `orders` → `orders.line_items` → `orders.line_items.attrs`.

**When the FE can skip it** (legitimate inline `array` / `object` cases):

- Multi-select chips producing a short string list: `outputType: "array"` + `MultipleComboboxControl` + **no** `TableWrapper`. The array rides inline in scalars as JSON.
- Nested object field (e.g., an address block): `outputType: "object"` + `DivWrapper`/`FragmentWrapper`. No `artifact_binding` needed; object rides inline in scalars as JSON.

Neither of these use `TableWrapper`; the backend never expects `artifact_binding` on them.

**Naming convention**:

- Simple table: `artifact_binding` equals the field's `key`.
  ```json
  { "key": "orders",
    "fieldConfig": {
      "fieldWrapper": "TableWrapper",
      "wrapperProps": { "artifact_binding": "orders" }
    } }
  ```
- Nested table: dotted path reflecting the tree.
  ```json
  { "key": "line_items",
    "fieldConfig": {
      "fieldWrapper": "TableWrapper",
      "wrapperProps": { "artifact_binding": "orders.line_items" }
    } }
  ```
  The dotted path must be unique across the whole schema — the backend uses it as the key when locating the artifact in the `__file_ref` tree.

**What happens if FE forgets `artifact_binding` on a `TableWrapper` field**:

- The backend doesn't reject the schema — it doesn't own the schema shape.
- The field is treated as an inline scalar `array`. Rows land in the main CSV as a JSON blob in one cell.
- First symptom: a large form becomes slow; `GET /data` payload grows by the full table content every request.
- There is no OData drilldown available, because no artifact exists to point at.

**Server-side guard**: if FE *does* declare `artifact_binding` but mistakenly POSTs the array via `scalars`, the backend rejects with `422 VALIDATION_FAILED` and `ops[].rule = "scalar_is_artifact_bound"`. That error surfaces an incorrect FE routing, not a schema bug.

**FE builder recommendation**: auto-set `artifact_binding = fieldKey` in the schema builder whenever the designer picks `TableWrapper` as the wrapper. Allow override for nested tables (where the dotted path isn't inferable from the local `key` alone). Warn (or block) schema save if a `TableWrapper` field ends up without `artifact_binding`.

### 4.6 Artifact `artifact_type`

| Value | Meaning | `content_type` |
|---|---|---|
| `array_field` | Top-level table artifact | `text/csv` |
| `nested_array_field` | Child table (has `parent_row_key_column`) | `text/csv` |

Files uploaded via FE components (`UploadControl`, etc.) are **not** separate artifact types. They are represented either:

- **As scalars** — the field's value is a JSON object `{ file_id, filename, size_bytes, content_type }` sitting inline in `data.scalars`.
- **As rows in a normal CSV table** — when the schema expresses a collection of files as a `TableWrapper`-bound array; each row carries `file_id, filename, size_bytes, content_type` as regular columns.

The actual bytes live in the file service. The control-plane persists only the metadata and never proxies file bytes — FE uploads and downloads directly against the file service using `file_id`. See §4.8.

### 4.7 `ArtifactOp.op`

| Value | Scope | Targeting | Typical FE trigger |
|---|---|---|---|
| `update_cell` | 1 cell | `row_path` + `column` | Inline cell edit |
| `bulk_update` | N rows | `filter` (+ optional parent `row_path`) | Mass update under a filtered view |
| `insert_rows` | Append | — (server assigns `__row_id`) | Add rows via grid / paste |
| `delete_row` | 1 row | `row_path` (terminal hop = target) | User clicks delete on a specific row in a grid |
| `delete_rows` | N rows | `filter` (non-empty) (+ optional parent `row_path`) | Bulk delete under a filtered view |
| `reset_artifact` | 1 artifact | `artifact_id` only | User clicks "Reset to original" on a table |
| `replace_artifact` | 1 artifact | `artifact_id` + external `file_id` | External service swaps in a pre-uploaded file (ETL / enrichment / AI) |
| `add_column` | Schema + data | `artifact_id` + `column` + `output_type` + optional `default_value` | User adds a column (DATA_EDIT) |
| `drop_column` | Schema + data | `artifact_id` + `column` | User removes a column (DATA_EDIT) |
| `rename_column` | Schema + data | `artifact_id` + `old_name` + `new_name` | User renames a column (DATA_EDIT) |

**Op constraints**:

- `reset_artifact`, `reset_to_input`: only valid on nodes with input/output separation (DATA_EDIT, etc.). On INPUT / HUMAN_ACTION / FILE_UPLOAD → `422 NO_INPUT_REFERENCE`.
- `replace_artifact`: the `file_id` must already exist in the file service. Replacement file must carry `__row_id` (and `__parent_row_id` for nested). Under `schema_compatibility: "strict"` (default) columns must match the artifact's declared columns; under `"drift_ok"` (DATA_EDIT only) the runtime schema updates to match. Static schemas with drifting columns → `422 STATIC_SCHEMA_IMMUTABLE`.
- `add_column` / `drop_column` / `rename_column`: only valid when the node's `schema_source == "runtime"` (DATA_EDIT). On static schemas → `422 STATIC_SCHEMA_IMMUTABLE`. These ops bump the **schema ETag**; FE must refetch `GET /schema` after success.
- Reserved columns `__row_id` / `__parent_row_id` cannot be `drop_column`'d or `rename_column`'d.
- `delete_rows.filter` must be non-empty; empty → `422 ROW_PATH_AMBIGUOUS`.
- `delete_row` requires a `row_path` whose terminal hop identifies exactly one row; missing terminal hop → `422 ROW_PATH_AMBIGUOUS`.

### 4.7b Input/output separation (DATA_EDIT model)

DATA_EDIT (and any node that edits upstream data) maintains two data references:

| `side=` | Source | Mutable? | Purpose |
|---|---|---|---|
| `input` | `task.resolved_input` | **No** — frozen at PENDING time | Original upstream data |
| `output` | `run.context.variables[node_id]` | **Yes** — PATCH target | Working copy |

**Initialization (copy-on-write)**: when the node reaches PENDING, both point to the same `file_id`s. No data is duplicated. On first PATCH, only the modified artifact gets a new `file_id`; unmodified artifacts share the original.

**Diff**: FE compares `file_id` at each artifact level between `data` (input) and `output`. Same = untouched. Different = user edited.

**Reset**: `{ "reset_to_input": true }` on PATCH discards all edits; `{ "op": "reset_artifact", "artifact_id": "..." }` reverts one artifact.

For INPUT / HUMAN_ACTION / FILE_UPLOAD: `side=input` and `side=output` return the same data (no separate original). `reset_to_input` and `reset_artifact` → 422.

### 4.7c `schema_source` — static vs dynamic schemas

| Value | Meaning | Node kinds |
|---|---|---|
| `design_time` | Schema authored by FE in the workflow builder | INPUT, HUMAN_ACTION, FILE_UPLOAD |
| `runtime` | Schema computed by the node handler at execution time (e.g., columns derived from upstream CSV) | DATA_EDIT |

Returned in `GET /schema` response. FE uses this to decide whether to show a "Refresh schema" affordance. Schema regeneration happens automatically on `rerun_strategy=node_only` or `cascade`.

### 4.8 File Upload / Download — metadata in scalars, bytes in file service

File-upload components in the FE auto-form (e.g., `UploadControl`) produce a scalar `outputType: "object"` field whose value is a **file metadata object**:

```ts
interface FileRef {
  file_id:      string;   // opaque handle assigned by the file service
  filename:     string;   // original user filename
  size_bytes:   number;
  content_type: string;   // MIME type, e.g. "application/pdf"
}
```

The control-plane stores this metadata alongside other scalars. It never handles file bytes.

**Upload flow** (FE-driven):

```
1. User picks file in the FE upload component.
2. FE → FileService: POST upload → returns { file_id }.
3. FE → ControlPlane: PATCH /data
     If-Match: <etag>
     { scalars: { evidence: { file_id, filename, size_bytes, content_type } } }
4. ControlPlane: validate → persist → bump ETag → return new ETag.
```

**Download / preview flow** (FE-driven):

```
1. FE → ControlPlane: GET /data → reads { evidence: { file_id, filename, ... } }
   directly from data.scalars (no extra round-trip).
2. FE → FileService: GET bytes by file_id when user opens/downloads.
```

**Collection of files (a "documents" grid).** Model the field as a normal artifact-bound `array` with `TableWrapper`; each row is a file's metadata:

```
documents.csv columns: __row_id | file_id | filename | size_bytes | content_type
```

FE reads rows via `GET /artifacts/documents/odata?...` like any other table; mutations use the normal `artifact_ops` on that artifact. When inserting a new file row:

```json
{ "op": "insert_rows", "artifact_id": "documents",
  "rows": [ { "file_id": "f_new", "filename": "x.pdf", "size_bytes": 1234, "content_type": "application/pdf" } ] }
```

**What the control-plane does NOT do**:

- Never accepts file bytes in request bodies.
- Never serves file bytes.
- Never issues presigned URLs on behalf of the file service.

The boundary is strict: control-plane owns metadata + identity; file service owns bytes. FE glues the two together.

---

### 4.9 `$artifacts` scope (run-level registry)

Cross-node map populated whenever a node's output carries named artifacts. Lives on `run.context.variables["artifacts"]`. One registry per run. Shape:

```ts
interface ArtifactRef {
  file_id:          string;
  filename?:        string;
  content_type:     string;
  size_bytes:       number;
  produced_by_node: string;
  produced_by_task: string;
  registered_at:    string;  // ISO-8601
}
// Registry:
type ArtifactsRegistry = Record<string, ArtifactRef>;
```

Resolver:

| Expression | Result |
|---|---|
| `{{$artifacts.invoice_pdf}}` | Full `ArtifactRef` object |
| `{{$artifacts.invoice_pdf.file_id}}` | Opaque file_id (for file-service download) |
| `{{$artifacts.invoice_pdf.filename}}` | Display name |
| `{{$artifacts.invoice_pdf.content_type}}` | MIME |
| `{{$artifacts.invoice_pdf.size_bytes}}` | Bytes |

Nodes that declare `artifact_key` in their config (today: `FILE_UPLOAD`, `DATA_EDIT`) contribute a name into this registry when they complete. Other nodes can contribute too — any node output that is a file-ref with named entries is registered.

To read the full registry, FE calls `GET /runs/{run_id}/artifacts` (§6.7).

### 4.10 `PathKind` (variable-suggestion + resolve results)

| Value | Meaning |
|---|---|
| `scalar` | Plain value (string/number/boolean) |
| `file_ref` | File-metadata object (`{file_id, filename, ...}`) |
| `artifact` | Opaque table reference (`__file_ref`) |
| `artifact_row` | Table row iterator (`$.node.table[*]`) |
| `artifact_column` | Column extractor (`$.node.table[*].col`) |

---

## 5. Interfaces

### 5.1 `ArtifactDescriptor`, `NodeData`

**`ArtifactDescriptor`**

| Field | Type | Req | Description |
|---|---|---|---|
| `artifact_id` | `string` | ✅ | `"__root__"` at the top; dotted path otherwise. |
| `file_id` | `string` | ✅ | Opaque handle for the file service. |
| `columns` | `string[]` | ✅ | Includes synthetic `__row_id`; includes `__parent_row_id` on nested children. |
| `parent_row_key_column` | `string \| null` | ✅ | `"__parent_row_id"` on nested; `null` at top level. |
| `children` | `ArtifactDescriptor[]` | ✅ | `[]` at leaves. |
| `artifact_type` | `"array_field" \| "nested_array_field"` | — | Only CSV-backed artifacts. Uploaded files are not artifacts — their metadata lives in scalars or as columns in a normal table artifact (see §4.8). |
| `content_type` | `string` | — | Always `"text/csv"` for artifacts in this design. |

**`NodeData`**

| Field | Type | Req |
|---|---|---|
| `etag` | `string` | ✅ |
| `version` | `number` | ✅ |
| `scalars` | `Record<string, unknown>` | ✅ |
| `root` | `ArtifactDescriptor` | ✅ |

### 5.2 Endpoint DTOs

#### `GetSchemaResponse`

| Field | Type | Notes |
|---|---|---|
| `schema` | `object` | **Opaque auto-form schema.** Static = FE-authored; dynamic = handler-computed. See §4.7c. |
| `schema_version` | `string` | Content-based hash; changes when schema changes. |
| `schema_source` | `"design_time" \| "runtime"` | See §4.7c. FE uses this to show "Refresh schema" on dynamic schemas. |
| `node_kind` | `NodeKind` | — |
| `node_state` | `NodeState` | — |

#### `GetDataResponse`

| Field | Type | Req | Notes |
|---|---|---|---|
| `run_id` | `string` | ✅ | |
| `node_id` | `string` | ✅ | |
| `task_id` | `string` | ✅ | Resolved (latest or `?task_id=`). |
| `node_kind` | `NodeKind` | ✅ | |
| `node_state` | `NodeState` | ✅ | |
| `schema_ref` | `{url: string; etag: string}` | ✅ | Pointer to `/schema`. |
| `data` | `NodeData` | ⚠️ | Present when `side` ∈ `{"input","both"}`. |
| `output` | `NodeData` | ⚠️ | Present when `side` ∈ `{"output","both"}`. |

Query: `side` (`input`|`output`|`both`, default `input`), `task_id?`.

#### `SubmitNodeResponse`

| Field | Type |
|---|---|
| `task_id` | `string` |
| `node_state` | `NodeState` (= `SUBMITTED`) |
| `downstream_dispatched` | `boolean` |

#### `RerunRunRequest` / `RerunRunResponse`

| Field | Type | Direction |
|---|---|---|
| `from_node_id?` | `string` | request |
| `rerun_strategy?` | `RerunStrategy` (default `cascade`) | request |
| `variables?` | `Record<string, unknown>` | request |
| `reset_node_ids` | `string[]` | response |
| `dispatched_task_ids` | `string[]` | response |

#### Scoped OData response

| Field | Type | Notes |
|---|---|---|
| `@odata.count?` | `number` | Present only with `$count=true`. |
| `value` | `Row[]` | Each row has `__row_id`; nested rows also have `__parent_row_id`. |

#### `GetRunArtifactsResponse` (§6.7)

| Field | Type |
|---|---|
| `artifacts` | `Record<string, ArtifactRef>` (see §4.9) |

#### `ResolvePathsRequest` / `ResolvePathsResponse` (§6.8)

| Field | Type | Direction | Notes |
|---|---|---|---|
| `paths` | `string[]` | request | Variable expressions, e.g. `$.node.sku` |
| `task_id?` | `string` | request | Target a specific loop iteration |
| `results` | `ResolveResult[]` | response | Per-path outcome; errors don't fail the batch |

```ts
interface ResolveResult {
  path:   string;
  status: "ok" | "error";
  kind?:  PathKind;          // set when status="ok"; see §4.10
  value?: unknown;           // set when status="ok"
  error?: ApiError;          // set when status="error"
}
```

#### `VariablePath` (in `/variable-suggestions` — design-time endpoint)

| Field | Type | Notes |
|---|---|---|
| `path` | `string` | e.g. `$.node.orders[*].sku` or `$artifacts.invoice_pdf.filename` |
| `kind` | `PathKind` | See §4.10 |
| `output_type` | `OutputType` | Wire shape |
| `sample?` | `unknown` | Example value (when available) |

### 5.3 PATCH /data DTOs — see §7 for full integration guide

```ts
type RerunStrategy = "none" | "node_only" | "cascade";

interface RowPathHop {
  artifact_id: string;
  row_index:   number;   // 0-based index under filter+orderby
  filter:      string;   // full OData query; "" if none
}

interface PatchDataRequest {
  rerun_strategy: RerunStrategy;
  reset_to_input?: boolean;         // true = discard all edits; scalars/artifact_ops ignored
  scalars?:        Record<string, unknown>;
  artifact_ops?:   ArtifactOp[];
}

type ArtifactOp =
  | UpdateCellOp | BulkUpdateOp
  | InsertRowsOp | DeleteRowOp | DeleteRowsOp
  | ResetArtifactOp | ReplaceArtifactOp
  | AddColumnOp | DropColumnOp | RenameColumnOp;

interface UpdateCellOp {
  op: "update_cell";
  row_path: RowPathHop[];
  column:   string;
  value:    unknown;
}

interface BulkUpdateOp {
  op:          "bulk_update";
  artifact_id: string;
  row_path?:   RowPathHop[];            // parent chain for nested artifacts
  filter:      string;
  assignments: Record<string, unknown>;
}

interface InsertRowsOp {
  op:          "insert_rows";
  artifact_id: string;
  row_path?:   RowPathHop[];
  rows:        Array<Record<string, unknown>>;
}

interface DeleteRowsOp {
  op:          "delete_rows";
  artifact_id: string;
  row_path?:   RowPathHop[];
  filter:      string;
}

interface PatchDataResponse {
  etag:         string;
  version:      number;
  file_lineage: Record<string, string>; // old_file_id → new_file_id (informational)
  results:      OpResult[];
  rerun: {
    strategy:       RerunStrategy;
    triggered:      boolean;
    degraded_from?: RerunStrategy;   // present when server downgraded (e.g., cascade on PENDING)
    reset_node_ids: string[];
    task_ids:       string[];
  };
}

interface DeleteRowOp {
  op:       "delete_row";
  row_path: RowPathHop[];     // root → leaf; last hop identifies the target row
}

interface ResetArtifactOp {
  op:          "reset_artifact";
  artifact_id: string;             // revert this artifact to task.resolved_input
}

interface ReplaceArtifactOp {
  op:                     "replace_artifact";
  artifact_id:            string;
  file_id:                string;              // must already exist in file service
  row_path?:              RowPathHop[];        // parent chain; omit for root artifacts
  schema_compatibility?:  "strict" | "drift_ok";   // default "strict"
  expected_columns?:      string[];            // optional caller-asserted column list
}

interface AddColumnOp {
  op:            "add_column";
  artifact_id:   string;
  column:        string;            // new column name; must be unique + not reserved
  output_type:   "string" | "number" | "boolean";
  default_value?: unknown;          // applied to all existing rows (null if omitted)
  rules?:        Array<{ method: string; value?: unknown; message?: string }>;
}

interface DropColumnOp {
  op:          "drop_column";
  artifact_id: string;
  column:      string;              // cannot be __row_id or __parent_row_id
}

interface RenameColumnOp {
  op:          "rename_column";
  artifact_id: string;
  old_name:    string;
  new_name:    string;              // must be unique + not reserved
}

interface OpResult {
  op_index:      number;
  status:        "ok" | "failed";
  rows_affected?: number;
  row_ids?:       string[];   // __row_id of inserted / updated rows
  error?:         ApiError;
}
```

---

## 6. Endpoints — concise detail

### 6.1 `GET /schema`

| | |
|---|---|
| Query | `task_id?` |
| Req headers | `If-None-Match?` |
| Res headers | `ETag`, `Cache-Control: private, max-age=30` |
| 200 | `GetSchemaResponse` |
| 304 | empty |
| Errors | 404 `NODE_NOT_FOUND`, 409 `RUN_NOT_READY` |

### 6.2 `GET /data`

| | |
|---|---|
| Query | `side?` (default `input`), `task_id?` |
| Req headers | `If-None-Match?` |
| Res headers | `ETag` |
| 200 | `GetDataResponse` |
| 304 | empty |
| Errors | 404 `NODE_NOT_FOUND`, 410 `INPUT_EVICTED` |

Row counts: call `GET /artifacts/{artifact_id}/odata?$top=0&$count=true`.

### 6.3 `GET /artifacts/{artifact_id}/odata`

| | |
|---|---|
| Query | `task_id?` + any OData (`$filter`, `$orderby`, `$skip`, `$top`, `$count`, `$select`, `$expand`) |
| 200 | `{ "@odata.count"?: number, value: Row[] }` |
| Errors | 404 `ARTIFACT_NOT_FOUND`, 403 `FORBIDDEN` |

Nested drilldown: `?$filter=__parent_row_id eq '<parent_row_id>'`.

### 6.4 `PATCH /data`

See §7 for the full integration guide.

### 6.5 `POST /submit`

| | |
|---|---|
| Query | `task_id?` |
| Body | none |
| 200 | `SubmitNodeResponse` |
| Errors | 422, 423, 404 |

### 6.6 `POST /runs/{run_id}/rerun`

| | |
|---|---|
| Body | `RerunRunRequest` |
| 200 | `RerunRunResponse` |
| Errors | 423, 404 |

### 6.7 `GET /runs/{run_id}/artifacts`

Returns the run-level `$artifacts` registry (see §4.9).

| | |
|---|---|
| Query | `task_id?` (accepted, ignored — registry is run-scoped) |
| Req headers | `If-None-Match?` |
| Res headers | `ETag` |
| 200 | `GetRunArtifactsResponse` |
| 304 | empty |
| Errors | 404 `RUN_NOT_FOUND` |

Typical use: FE reviewer panel "Files produced by this run"; builder's artifact explorer.

### 6.8 `POST /runs/{run_id}/resolve`

Batched variable-path evaluator. Wraps the server-side `VariableResolver`; read-only.

| | |
|---|---|
| Body | `ResolvePathsRequest` |
| 200 | `ResolvePathsResponse` |
| Errors | 400 `INVALID_PATH_SYNTAX`, 404 `RUN_NOT_FOUND`, 413 `BATCH_TOO_LARGE` |

**Limits**: up to 100 paths per call; each path subject to the 16-hop nesting cap.

**Per-path failures** are reported in `results[i].error` without failing the whole request. Only syntax/tenancy/batch-size issues are request-level errors.

**Primary consumer**: the workflow builder during test runs — evaluates a variable chip's concrete value without requiring FE to walk `/data` + OData drilldowns itself.

---

## 7. `PATCH /data` — Integration Guide

This section is intended for the frontend integrator. It covers intent, request shape, addressing, per-op semantics, validation, concurrency, response interpretation, error recovery, limits, and worked examples.

### 7.1 What it is

`PATCH /data` is the **single write path** for node input/output data. It **always targets the working copy** (`variables[node_id]`), never the frozen input. It applies an atomic batch of changes to the node's `__file_ref` tree:

- optional changes to root **scalars** (fields stored in the main CSV), and/or
- zero or more **artifact operations** against any artifact in the tree (including deeply nested ones), and/or
- a full **reset to input** (`reset_to_input: true`) or per-artifact reset (`reset_artifact` op).

On success the backend produces a new `__file_ref` tree (new `file_id`s for every mutated artifact) and bumps the node's `ETag` and `version`. On any failure the old tree remains authoritative — there is **no partial commit**.

### 7.2 Request shape at a glance

```http
PATCH /runs/{run_id}/nodes/{node_id}/data?task_id={optional_task_id}
If-Match: W/"sha256:<etag-you-read>"
Content-Type: application/json

{
  "rerun_strategy": "none" | "node_only" | "cascade",
  "reset_to_input": false,         // optional; true = discard all edits, revert to original input
  "scalars": { ... },              // optional
  "artifact_ops": [ ... ]          // optional
}
```

At least one of `scalars` / `artifact_ops` / `reset_to_input` must be present.

### 7.3 ETag workflow (concurrency)

```
FE: GET /data           →  200  ETag: W/"v12"   (store v12)
FE: PATCH /data         →  If-Match: W/"v12"
     ├─ 200 ETag: W/"v13"    store v13 and move on
     ├─ 412 PRECONDITION_FAILED  →  refetch GET /data, reconcile UI, retry with fresh ETag
     └─ 409 PRECONDITION_REQUIRED  →  FE bug: If-Match header missing
```

Rules:

- `If-Match` is **required** on every PATCH. Missing → `409 PRECONDITION_REQUIRED`.
- Any PATCH anywhere in the subtree bumps the node ETag. Concurrent writers will collide.
- On `412`, the server returns `details.current_etag`. **Do not automatically retry** — the underlying data has changed; the FE must re-render (visible OData pages may be stale) before retrying.
- Worker completion of the same node also bumps the ETag. Expect `412`s during races with automated state changes; handle them identically.

### 7.4 `rerun_strategy`

| Value | When to pick | What BE does |
|---|---|---|
| `none` | Silent correction. User is still editing; no re-execution needed. | Just swap `__file_ref`. |
| `node_only` | User wants the edited node to re-run with the new input, but not downstream. | Reset and re-dispatch only the edited node. |
| `cascade` | User wants the whole downstream subtree to reflect the edit. Default for "apply and rerun". | Reset the node + every downstream node via BFS; dispatch from the edited node. |

Pending nodes (state `PENDING`) should almost always use `none` — the node hasn't run yet, so there's nothing to re-run. Completed/failed nodes typically use `cascade`.

### 7.5 `scalars` — merge, not replace

The `scalars` object is a **partial merge**: only the keys you send are updated. Keys not present remain unchanged. Setting a key to `null` clears it (stores `null`, honouring validation). To remove a key, set it to `null`; there is no separate delete operation for scalars.

Keys in `scalars` must match scalar field keys in the schema. Attempting to set an artifact-bound field (e.g., `"orders"`) via `scalars` is rejected with `422 VALIDATION_FAILED` — use `artifact_ops` instead.

### 7.6 `row_path` — the walk-down protocol

`row_path` is how the FE tells the backend *which exact row* in a nested tree it is targeting, given that the FE only knows view-level coordinates (index inside a filtered, sorted grid). The backend uses it to resolve `row_index → __row_id` against the file service, chaining down the tree.

Shape:

```ts
type RowPathHop = {
  artifact_id: string;   // e.g., "orders" or "orders.line_items"
  row_index:   number;   // 0-based index inside the filter+orderby view
  filter:      string;   // full OData query string; "" if no filter/order
};
type RowPath = RowPathHop[];  // root → leaf hops, in order
```

Rules:

- **Hops are ordered root-to-leaf.** Hop 0 addresses a row in a root artifact; hop 1 addresses a row inside that row's child artifact; and so on.
- **`filter`** carries the **exact** OData query the FE had in effect when the user targeted that row. It may contain any of `$filter`, `$orderby`, `$select`, `$top`, `$skip` — but typically only `$filter` and `$orderby` are relevant for targeting. An empty string means "unfiltered, default order".
- **`row_index`** is the 0-based position of the target row **after** applying `filter`+`$orderby`. If the grid displays rows 50–99 (page 2, page size 50), the visible top row has `row_index = 50`, not `0`.
- The backend resolves each hop by issuing a scoped query `<filter> & $skip=row_index & $top=1 & $select=__row_id` to the file service.
- **For nested hops**, the backend automatically prepends `$filter=__parent_row_id eq '<resolved_parent_row_id>'` to whatever `filter` the FE provided. The FE must NOT include `__parent_row_id` in its filter — the backend manages parent scoping.
- If the row can't be resolved (index out of range, `__row_id` missing, file service error), the op fails with `ROW_NOT_FOUND` in `details.ops[]` and the whole batch rolls back.

When is `row_path` used?

| Op | `row_path` |
|---|---|
| `update_cell` | Required — walks to the artifact containing the target row; the **final hop** addresses the row itself. |
| `bulk_update` | Only the **parent chain** — hops leading up to but NOT including the target artifact. For root-level artifacts, omit entirely. |
| `insert_rows` | Same as `bulk_update`: parent chain only. For root artifacts, omit. |
| `delete_rows` | Same as `bulk_update`: parent chain only. For root artifacts, omit. |

Put another way: `update_cell` is the only op where the last hop names the row; all other ops name rows by `filter` at the artifact level.

### 7.7 Per-op semantics

#### 7.7.1 `update_cell` — single cell in a single row

```ts
{
  op: "update_cell",
  row_path: RowPathHop[],  // root → leaf (inclusive of target row)
  column:   string,
  value:    unknown        // must match column's outputType
}
```

Behaviour:

- Resolves the `__row_id` of the last hop's row, then writes `column = value` into that row.
- `column` must be a real column of the target artifact (see `ArtifactDescriptor.columns`). Attempting to write to `__row_id` or `__parent_row_id` is rejected.
- `value` must validate against any schema rules on that column (see §7.9).
- If the row no longer exists (concurrent delete), op fails with `ROW_NOT_FOUND` and the batch rolls back.

Example — set the `qty` on the 4th row of `orders` (as displayed under APAC filter sorted by sku), where the `orders` artifact is at the root:

```json
{
  "op": "update_cell",
  "row_path": [
    { "artifact_id": "orders", "row_index": 3,
      "filter": "$filter=region eq 'APAC'&$orderby=sku asc" }
  ],
  "column": "qty",
  "value": 150
}
```

Example — set `price` on a deeply nested row (the 2nd line_item of the 4th order):

```json
{
  "op": "update_cell",
  "row_path": [
    { "artifact_id": "orders",            "row_index": 3, "filter": "$filter=region eq 'APAC'" },
    { "artifact_id": "orders.line_items", "row_index": 1, "filter": "" }
  ],
  "column": "price",
  "value": 9.5
}
```

#### 7.7.2 `bulk_update` — filter-scoped mass update

```ts
{
  op:          "bulk_update",
  artifact_id: string,
  row_path?:   RowPathHop[],   // parent chain; omit for root artifacts
  filter:      string,         // OData $filter (and optional $orderby) scoping the rows
  assignments: Record<string, unknown>
}
```

Behaviour:

- Updates every row in `artifact_id` matching the combined filter (parent `__parent_row_id` scoping, if any, is added automatically from `row_path`).
- `assignments` is a column → value map. Every column in `assignments` must exist on the artifact; every value must validate against schema rules for that column.
- `__row_id` and `__parent_row_id` are **not allowed** in `assignments`.
- A `filter` of `""` means "all rows in this artifact (under the scoped parent, if any)" — use sparingly; usually you want at least `$filter=...`.
- The number of rows actually updated is returned in `results[i].rows_affected`.

Example — in every order with region EU, set status to "review":

```json
{
  "op": "bulk_update",
  "artifact_id": "orders",
  "filter": "$filter=region eq 'EU'",
  "assignments": { "status": "review" }
}
```

Example — in line_items of the 4th order (APAC-filtered), set `status=review` for those with `price < 10`:

```json
{
  "op": "bulk_update",
  "artifact_id": "orders.line_items",
  "row_path": [
    { "artifact_id": "orders", "row_index": 3, "filter": "$filter=region eq 'APAC'" }
  ],
  "filter": "$filter=price lt 10",
  "assignments": { "status": "review" }
}
```

#### 7.7.3 `insert_rows` — append new rows

```ts
{
  op:          "insert_rows",
  artifact_id: string,
  row_path?:   RowPathHop[],    // parent chain for nested inserts; omit for root
  rows:        Array<Record<string, unknown>>
}
```

Behaviour:

- Appends each element of `rows` to `artifact_id`.
- **The server assigns `__row_id`.** FE must NOT include `__row_id` in the row objects; doing so is rejected with 422.
- For nested artifacts, the server also assigns `__parent_row_id` from the last `row_path` hop's resolved row. FE must NOT include `__parent_row_id` either.
- Missing columns in a row default to `null` (still subject to `required` validation). Unknown columns are rejected with 422.
- `results[i].row_ids[]` returns the `__row_id`s of the inserted rows in order.

Example — add a line_item to the 4th order:

```json
{
  "op": "insert_rows",
  "artifact_id": "orders.line_items",
  "row_path": [
    { "artifact_id": "orders", "row_index": 3, "filter": "" }
  ],
  "rows": [
    { "part_no": "X-1", "price": 9.5 },
    { "part_no": "X-2", "price": 11.0 }
  ]
}
```

#### 7.7.3b `delete_row` — single-row delete (precise)

```ts
{
  op:       "delete_row",
  row_path: RowPathHop[]      // root → leaf; terminal hop identifies the row
}
```

Behaviour:

- Uses the **same addressing model as `update_cell`** — the last `row_path` hop's `{artifact_id, row_index, filter}` resolves to exactly one `__row_id` via a bounded file-service query.
- Use this when the user clicks "delete" on a specific row in the grid. The FE's current filter goes into the terminal hop so the index resolution is correct, but only the **one row at `row_index`** is deleted — not every row matching the filter.
- Cascade on nested children applies (child artifact rows with matching `__parent_row_id` are deleted too).
- `row_path` missing or empty → `422 ROW_PATH_AMBIGUOUS`.
- Row doesn't exist at that index (e.g., another user deleted it first) → `422 ROW_NOT_FOUND`, whole batch rolls back — FE refetches visible page and retries.

Example — user clicks delete on the row displayed at index 7 in the APAC-filtered `orders` grid:

```json
{
  "op": "delete_row",
  "row_path": [
    { "artifact_id": "orders", "row_index": 7, "filter": "$filter=region eq 'APAC'&$orderby=sku asc" }
  ]
}
```

Example — delete a specific line_item of a specific order:

```json
{
  "op": "delete_row",
  "row_path": [
    { "artifact_id": "orders",            "row_index": 3, "filter": "$filter=region eq 'APAC'" },
    { "artifact_id": "orders.line_items", "row_index": 2, "filter": "" }
  ]
}
```

**Choosing between `delete_row` and `delete_rows`**:

| FE action | Op |
|---|---|
| User clicks trash icon on one row | `delete_row` |
| User multi-selects N specific rows → delete | N × `delete_row` in one batch |
| "Delete all matching filter" toolbar | `delete_rows` |

#### 7.7.4 `delete_rows` — filter-scoped delete

```ts
{
  op:          "delete_rows",
  artifact_id: string,
  row_path?:   RowPathHop[],
  filter:      string
}
```

Behaviour:

- Deletes every row in `artifact_id` matching `filter` (under the scoped parent, if any).
- Deleting a row in a parent artifact also deletes all its descendants in nested artifacts (cascade). `rows_affected` reports only the rows in the target artifact; cascaded descendant counts are not itemized.
- `filter` MUST be non-empty — empty filter would delete all rows, which is a strong foot-gun. To wipe everything, pass `"$filter=true eq true"` explicitly.

Example — delete all EU orders:

```json
{
  "op": "delete_rows",
  "artifact_id": "orders",
  "filter": "$filter=region eq 'EU'"
}
```

#### 7.7.5 `reset_artifact` — revert one artifact to original input

```ts
{
  op:          "reset_artifact",
  artifact_id: string     // the artifact to revert
}
```

Behaviour:

- Only valid for nodes with input/output separation (DATA_EDIT, etc.). On INPUT/HUMAN_ACTION/FILE_UPLOAD → `422 NO_INPUT_REFERENCE`.
- Reads `task.resolved_input` (the frozen original), finds the artifact with matching `artifact_id`, and swaps its `file_id` back into the working copy's tree.
- Only this artifact reverts; other edits are preserved.
- Other ops in the same batch execute against the **post-reset** state.
- `results[i].rows_affected` is omitted; `results[i].status = "ok"` means the artifact was reset.
- **Schema revert on drift**: if a prior `replace_artifact` with `drift_ok` changed this artifact's columns, the runtime-schema entry for the artifact is also rebuilt from the input file's columns. Both data ETag and schema ETag bump; FE should refetch `GET /schema`.

Example — revert the `orders.line_items` table to original but keep other edits:

```json
{ "op": "reset_artifact", "artifact_id": "orders.line_items" }
```

#### 7.7.6 `reset_to_input` — discard all edits (top-level flag)

Not an op — a top-level boolean on `PatchDataRequest`:

```json
{ "rerun_strategy": "none", "reset_to_input": true }
```

When `true`: replaces `variables[node_id]` entirely with a fresh copy-on-write reference to `task.resolved_input`. **All `scalars` and `artifact_ops` in the same request are ignored** — reset wins. Returns the input's ETag as the new ETag.

Only valid for nodes with input/output separation; returns `422 NO_INPUT_REFERENCE` otherwise.

#### 7.7.6b `replace_artifact` — swap in an externally-uploaded file

Primary audience: **external services** (ETL, enrichment, AI, data-factory) that have already produced a new version of an artifact and want to adopt it wholesale without cell-by-cell PATCHes.

```ts
{
  op:                    "replace_artifact",
  artifact_id:           string,
  file_id:               string,                // must exist in file service (HEAD-verified)
  row_path?:             RowPathHop[],          // parent chain; omit for root artifacts
  schema_compatibility?: "strict" | "drift_ok", // default "strict"
  expected_columns?:     string[]               // optional caller-asserted column list
}
```

Behaviour:

- Backend calls the file service with `HEAD file_id` — if not found → `422 FILE_NOT_FOUND`.
- Verifies the file has `__row_id` (and `__parent_row_id` for nested) — missing → `422 MISSING_SYNTHETIC_COLUMNS`.
- Checks column compatibility:
  - `strict` (default): replacement columns must equal the artifact's declared columns (order-agnostic; `__row_id`/`__parent_row_id` always required). Mismatch → `422 COLUMN_MISMATCH` with `details.expected` / `details.actual`.
  - `drift_ok` (DATA_EDIT only): columns may differ; the node's runtime schema (`task.output_metadata["input_schema"]`) is rebuilt from the replacement's columns. Bumps schema ETag. On static schemas under `drift_ok` → `422 STATIC_SCHEMA_IMMUTABLE`.
- If `expected_columns` is supplied, server additionally checks the replacement file's columns match the caller's assertion (sanity check). Mismatch → `422 COLUMN_MISMATCH`.
- **Root replacement** (`row_path` omitted): swaps the artifact's `file_id` in the working-copy tree. Any nested children of this artifact remain — their `__parent_row_id` references must still be valid (replacement file should retain the same `__row_id`s if children exist, or the caller should replace nested artifacts in the same batch).
- **Nested replacement** (`row_path` supplied): resolves the parent's `__row_id` via `RowPathResolver`. Every row in the replacement file must have `__parent_row_id` equal to that resolved value, else `422 PARENT_ROW_ID_MISMATCH`. Backend removes existing nested rows with that `__parent_row_id` from the current child CSV, then appends the replacement's rows.
- Bumps the node's data ETag. Bumps schema ETag only when schema actually drifted.

Example — ETL service replaces the whole `orders` table with an enriched version:

```http
POST <file-service>/upload              (direct; not through control-plane)
 → { file_id: "f_enriched_v2" }

PATCH /runs/r_abc/nodes/n_review/data
If-Match: W/"sha256:v12"
Body:
{
  "rerun_strategy": "cascade",
  "artifact_ops": [
    {
      "op": "replace_artifact",
      "artifact_id": "orders",
      "file_id": "f_enriched_v2",
      "schema_compatibility": "strict"
    }
  ]
}
 → 200 OK
   { "etag": "W/\"sha256:v13\"", "version": 13,
     "file_lineage": { "f_orders_v1": "f_enriched_v2" },
     "results": [{ "op_index": 0, "status": "ok" }],
     "rerun": { "strategy": "cascade", "triggered": true,
                "reset_node_ids": ["n_review","n_scoring"], "task_ids": ["t_next_1"] } }
```

Example — enrichment service replaces `orders.line_items` under one specific order:

```json
{
  "op": "replace_artifact",
  "artifact_id": "orders.line_items",
  "file_id": "f_li_v2",
  "row_path": [ { "artifact_id": "orders", "row_index": 3, "filter": "" } ],
  "schema_compatibility": "drift_ok"
}
```

**Combining with other ops**: `replace_artifact` can appear in the same batch as scalar edits, other artifact ops, or rerun strategies — they all commit atomically.

**Not** for FE use. FE uses the granular ops (`update_cell`, `bulk_update`, etc.) to reflect user gestures. `replace_artifact` is an **integration primitive** for backends.

#### 7.7.7 `add_column` — extend the artifact's schema + data (DATA_EDIT only)

```ts
{
  op:            "add_column",
  artifact_id:   string,
  column:        string,                     // new column name; unique, non-reserved
  output_type:   "string" | "number" | "boolean",
  default_value?: unknown,                   // applied to all existing rows; null if omitted
  rules?:         ValidationRule[]           // passed through to runtime schema
}
```

Behaviour:

- **DATA_EDIT only.** Node must have `schema_source == "runtime"`; otherwise → `422 STATIC_SCHEMA_IMMUTABLE`.
- Adds the column to the CSV header and fills every existing row with `default_value` (or `null`).
- Adds a corresponding field to `task.output_metadata["input_schema"]` — the runtime schema gains the new field.
- Bumps **schema ETag** + data ETag. FE must refetch `GET /schema` to see the new field definition.
- `column` name must not exist on the artifact already (else `VALIDATION_FAILED`, rule `"duplicate_column"`) and must not be `__row_id` / `__parent_row_id` (rule `"reserved_column"`).

Example:

```json
{
  "op": "add_column",
  "artifact_id": "orders",
  "column": "flagged",
  "output_type": "boolean",
  "default_value": false
}
```

#### 7.7.8 `drop_column` — remove a column (DATA_EDIT only)

```ts
{
  op:          "drop_column",
  artifact_id: string,
  column:      string                        // cannot be __row_id / __parent_row_id
}
```

Behaviour:

- **DATA_EDIT only.** Static schema → `422 STATIC_SCHEMA_IMMUTABLE`.
- Removes the column from the CSV header and strips its values from every row.
- Removes the field from `task.output_metadata["input_schema"]`.
- Bumps schema + data ETag.
- Data loss is permanent on commit; if recovery is needed, use `reset_artifact` (only if the input still has the column).

#### 7.7.9 `rename_column` — rename a column (DATA_EDIT only)

```ts
{
  op:          "rename_column",
  artifact_id: string,
  old_name:    string,
  new_name:    string                        // unique, non-reserved
}
```

Behaviour:

- **DATA_EDIT only.**
- Atomic: CSV header and runtime-schema field key are updated together in the same swap.
- `new_name` must not collide with an existing column (`duplicate_column`) and must not be reserved.
- Existing row values are preserved under the new name.
- Any `rules` attached to the schema field follow the rename; mappings in downstream nodes referencing the old name must be updated separately (out of scope — caller's responsibility).

### 7.8 Reserved / forbidden columns

In **any** op payload (`assignments`, `rows`, or `value`):

- `__row_id` — **forbidden**. Server-assigned; immutable.
- `__parent_row_id` — **forbidden**. Server-managed; determined by `row_path`.

Attempting to set either is rejected with `422 VALIDATION_FAILED`, rule `"reserved_column"`.

### 7.9 Validation

The backend runs a generic validator against every proposed change using the rules carried in the FE-authored schema:

- For `scalars` and `update_cell`: the new value is checked against the field's `rules[]` (e.g., `required`, `min`, `max`, `pattern`, etc.). Rule semantics are FE-defined; the backend is a generic dispatcher.
- For `bulk_update`: the assignment value is checked against the column's rules once (not per affected row).
- For `insert_rows`: each row is checked column-by-column, including rules that require presence.
- For `delete_rows`: no value validation, but `filter` must be non-empty (`ROW_PATH_AMBIGUOUS`).
- For `delete_row`: validates that `row_path` has a terminal hop (`ROW_PATH_AMBIGUOUS` if missing) and that it resolves to a real row (`ROW_NOT_FOUND` if not).
- For `add_column` / `drop_column` / `rename_column`: validates `schema_source == "runtime"` (`STATIC_SCHEMA_IMMUTABLE` if not), column-name uniqueness (`duplicate_column`), reserved-name restriction (`reserved_column`), and `output_type` membership in the allowed set.
- For `replace_artifact`: validates file existence via file-service HEAD (`FILE_NOT_FOUND`), synthetic columns (`MISSING_SYNTHETIC_COLUMNS`), column compatibility per mode (`COLUMN_MISMATCH`), and — for nested replacement — `__parent_row_id` consistency (`PARENT_ROW_ID_MISMATCH`).

Validation happens BEFORE any file-service write. Failure yields `422 VALIDATION_FAILED` with per-op errors and no data mutation.

### 7.10 Execution model

Ops execute **in the order given**. The full algorithm:

1. BE acquires a per-run lock.
2. BE reads the current `__file_ref`; computes current ETag; checks `If-Match`.
3. BE validates every op (§7.9); on any failure → 422, lock released, no writes.
4. BE resolves every `row_path` via file-service queries (§7.6).
5. BE applies ops one by one: download → mutate → upload → capture new `file_id`s. This stage is not observable to readers; the old tree still points to old `file_id`s.
6. On any op failure at this stage → abort: orphan new `file_id`s are best-effort garbage collected; the old tree stays authoritative. Response is an error with `results[]` showing which op failed.
7. BE rebuilds the `__file_ref` tree, bumps `version`, computes new ETag, and swaps the pointer on `run.context.variables[node_id]` — this is the **commit**.
8. Readers after the swap see the new tree; readers in flight see the old tree.
9. BE fires rerun dispatch per `rerun_strategy`.
10. BE releases the lock and responds.

Implications for FE:

- All-or-nothing. Any single op failing cancels the whole batch; no partial application.
- Ops aren't independently retriable — if one fails, FE must retry the whole batch (with the current ETag).
- Order matters for dependencies between ops in the same batch (e.g., `insert_rows` then `update_cell` on one of the inserted rows) — but you'd normally split those into two PATCH round-trips because you don't know the inserted `__row_id`s until the first response.

### 7.11 Response interpretation

Success body:

```json
{
  "etag": "W/\"sha256:...\"",
  "version": 13,
  "file_lineage": { "f_orders_xyz": "f_orders_xyz_v2" },
  "results": [
    { "op_index": 0, "status": "ok", "rows_affected": 1, "row_ids": ["r_ab12"] },
    { "op_index": 1, "status": "ok", "rows_affected": 27 }
  ],
  "rerun": {
    "strategy": "cascade",
    "triggered": true,
    "reset_node_ids": ["n_scoring", "n_notify"],
    "task_ids": ["t_new_1"]
  }
}
```

What FE does with each piece:

- **`etag`** — store it; use on the next PATCH / conditional GET.
- **`version`** — informational; monotonically increasing.
- **`file_lineage`** — informational. If FE cached any OData results keyed by `file_id`, it should invalidate entries whose keys appear in `file_lineage`.
- **`results[].row_ids`** — critical for `insert_rows` so FE can pair UI rows with server IDs.
- **`rerun.triggered`** — if `true`, FE should reflect the downstream state change (refetch the run state, the affected nodes' data, etc.). If `false` with `strategy != none`, inspect `reset_node_ids`.
- **`rerun.reset_node_ids`** — every node in this list had its state reset; FE UIs showing those nodes should refetch.
- **`rerun.task_ids`** — the newly dispatched tasks (useful if subscribed to run events via Socket.IO).

### 7.12 Error recovery recipes

| Status | Code | Recovery |
|---|---|---|
| 409 | `PRECONDITION_REQUIRED` | FE bug. Ensure `If-Match` header present. |
| 412 | `PRECONDITION_FAILED` | Refetch `GET /data`, refetch any visible OData pages, reconcile UI state, ask user to re-apply or auto-merge, then retry PATCH with new ETag. |
| 410 | `INPUT_EVICTED` | Input files have been evicted (e.g., retention policy). Hard refresh; the node's data is gone. |
| 422 | `VALIDATION_FAILED` | `details.ops[]` lists which op + field + rule + message failed. Highlight in UI; ask user to fix. |
| 422 | `VALIDATION_FAILED` with rule=`reserved_column` | FE is sending `__row_id` / `__parent_row_id` in `rows` or `assignments` — remove them. |
| 422 | `ROW_NOT_FOUND` | Row resolved from `row_path` doesn't exist (concurrent delete or stale index). Refetch the visible page and retry. |
| 423 | `NODE_STATE_INVALID` | Node not editable. Likely run cancelled/completed. Show read-only. |
| 413 | `BATCH_TOO_LARGE` | Too many ops or too much JSON. Split into multiple PATCHes (each atomic, sequential). |
| 500 | — | Transient. Retry after brief backoff, but only if safe (e.g., the user hasn't moved on). |

### 7.13 Limits

| Limit | Value (indicative) | Behaviour |
|---|---|---|
| `artifact_ops[].length` | 1000 per request | Exceed → 413 `BATCH_TOO_LARGE` |
| `insert_rows.rows[].length` | 10_000 per op | Exceed → split across multiple ops |
| Request body size | ~10 MB | Exceed → 413 |
| `row_path.length` (max nesting depth) | 16 | Exceed → 422 |

(Exact values finalized at implementation; these are the targets.)

### 7.14 OData filter grammar

The `filter` string in `row_path` and in `bulk_update`/`delete_rows` is the exact OData v4 query string understood by the file service. FE passes whatever query the grid currently applies. The backend does not parse or rewrite it beyond prepending `__parent_row_id` scoping for nested hops.

Typical forms the FE will produce:

- `""` — no filter, no order
- `"$filter=region eq 'APAC'"` — equality
- `"$filter=price lt 10 and status eq 'draft'"` — compound
- `"$filter=region eq 'APAC'&$orderby=sku asc"` — with order
- `"$filter=contains(name,'acme')"` — string contains

FE should URL-encode when constructing the request URL but **not** inside the JSON body — inside JSON the string is literal.

### 7.15 Worked example — combined batch

Scenario: on a completed "Review Orders" node, the user:

1. Changes their overall comment (scalar)
2. In the APAC-filtered `orders` grid, marks row index 3's `qty` as 150 (cell edit)
3. For the same order's `line_items`, bulk-sets `status="review"` where `price < 10`
4. Adds two new line items to that order
5. Deletes all EU orders
6. Wants a full downstream cascade

Request:

```http
PATCH /runs/r_abc/nodes/n_review/data
If-Match: W/"sha256:v12"

{
  "rerun_strategy": "cascade",
  "scalars": {
    "reviewer_comment": "Approved with adjustments."
  },
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
      "row_path": [
        { "artifact_id": "orders", "row_index": 3, "filter": "$filter=region eq 'APAC'&$orderby=sku asc" }
      ],
      "filter": "$filter=price lt 10",
      "assignments": { "status": "review" }
    },
    {
      "op": "insert_rows",
      "artifact_id": "orders.line_items",
      "row_path": [
        { "artifact_id": "orders", "row_index": 3, "filter": "$filter=region eq 'APAC'&$orderby=sku asc" }
      ],
      "rows": [
        { "part_no": "X-1", "price": 9.5 },
        { "part_no": "X-2", "price": 11.0 }
      ]
    },
    {
      "op": "delete_rows",
      "artifact_id": "orders",
      "filter": "$filter=region eq 'EU'"
    }
  ]
}
```

Response 200:

```json
{
  "etag": "W/\"sha256:v13\"",
  "version": 13,
  "file_lineage": {
    "f_orders_v1":      "f_orders_v2",
    "f_line_items_v1":  "f_line_items_v2"
  },
  "results": [
    { "op_index": 0, "status": "ok", "rows_affected": 1, "row_ids": ["r_q3k1"] },
    { "op_index": 1, "status": "ok", "rows_affected": 27 },
    { "op_index": 2, "status": "ok", "rows_affected": 2, "row_ids": ["r_L01","r_L02"] },
    { "op_index": 3, "status": "ok", "rows_affected": 5 }
  ],
  "rerun": {
    "strategy": "cascade",
    "triggered": true,
    "reset_node_ids": ["n_review", "n_scoring", "n_notify"],
    "task_ids": ["t_next_1"]
  }
}
```

FE post-processing checklist:

1. Store `etag` → `W/"sha256:v13"` (replace previous).
2. Invalidate any OData caches keyed on `f_orders_v1` or `f_line_items_v1`.
3. Pair the two new inserted rows in the UI with `r_L01`, `r_L02`.
4. Mark `n_scoring` and `n_notify` as "re-running" in the run view; subscribe to `t_next_1` events.

### 7.15b Worked example — external service integration (replace_artifact)

Audience: backend ETL / enrichment / AI / data-factory services. Not for FE use.

**Scenario**: an enrichment service reads the `orders` artifact of a `DATA_EDIT` node, produces a version with an extra `risk_score` column, and adopts it wholesale. It wants downstream scoring + notification nodes to re-run automatically.

**Preconditions**:

- Service has a trusted client identity that grants it PATCH rights on the target run.
- Node's `schema_source == "runtime"` (DATA_EDIT). The service plans to use `drift_ok` because it's adding a new column.

**Flow**:

```
Step 1 — Service reads current state to learn the working-copy ETag + artifact's file_id + columns:

  GET /runs/r_abc/nodes/n_review/data?side=output
   ← { data: { etag: "W/\"sha256:v12\"",
               root: { artifact_id: "__root__", ...,
                       children: [{ artifact_id: "orders", file_id: "f_orders_v1",
                                    columns: ["__row_id","sku","qty","region"] }] } } }

Step 2 — Service reads the actual rows via OData (to preserve __row_ids while enriching):

  GET /runs/r_abc/nodes/n_review/artifacts/orders/odata?$top=0&$count=true
   ← { "@odata.count": 5000, "value": [] }

  GET /runs/r_abc/nodes/n_review/artifacts/orders/odata?$top=5000
   ← { value: [ {__row_id:"r_1", sku:"...", qty:10, region:"APAC"}, ... ] }

Step 3 — Service produces enriched rows (adds risk_score column, keeps __row_id):

  [ { __row_id:"r_1", sku:"...", qty:10, region:"APAC", risk_score:0.82 }, ... ]

Step 4 — Service uploads the new file directly to the file service:

  POST <file-service>/upload
       body: CSV with columns [__row_id, sku, qty, region, risk_score]
   ← { file_id: "f_orders_enriched_v1" }

Step 5 — Service submits the replacement via PATCH /data:

  PATCH /runs/r_abc/nodes/n_review/data
  If-Match: W/"sha256:v12"
  {
    "rerun_strategy": "cascade",
    "artifact_ops": [
      {
        "op": "replace_artifact",
        "artifact_id": "orders",
        "file_id": "f_orders_enriched_v1",
        "schema_compatibility": "drift_ok",
        "expected_columns": ["__row_id","sku","qty","region","risk_score"]
      }
    ]
  }
   ← 200 OK
     {
       "etag": "W/\"sha256:v13\"",
       "version": 13,
       "file_lineage": { "f_orders_v1": "f_orders_enriched_v1" },
       "results": [{ "op_index": 0, "status": "ok" }],
       "rerun": { "strategy": "cascade", "triggered": true,
                  "reset_node_ids": ["n_review","n_scoring","n_notify"],
                  "task_ids": ["t_next_1"] }
     }
```

**What the service must check post-response**:

- `results[0].status == "ok"` — replace succeeded.
- `file_lineage` — record old→new mapping for audit.
- `rerun.reset_node_ids` — downstream nodes that will re-execute.
- The **schema ETag** has also bumped (because `drift_ok` + new column). The service does NOT need to refetch schema (it's FE's concern).

**Error handling — common cases**:

| Error | Meaning | Recovery |
|---|---|---|
| `412 PRECONDITION_FAILED` | Run state changed since `GET /data` (another edit, cascade reset). | Refetch `GET /data`, re-upload replacement file if needed, retry with new ETag. |
| `422 FILE_NOT_FOUND` | Service's uploaded file_id not reachable — wrong tenant or upload failed. | Re-upload, check tenant scope. |
| `422 MISSING_SYNTHETIC_COLUMNS` | Service omitted `__row_id` on rows. | Re-generate the file preserving `__row_id` from the original rows. |
| `422 COLUMN_MISMATCH` under `strict` | Service added / removed a column but used strict mode. | Switch to `drift_ok` (if the node permits) or fix the file. |
| `422 STATIC_SCHEMA_IMMUTABLE` | Tried `drift_ok` on a non-DATA_EDIT node. | Either use `strict` (no column changes) or accept that column changes aren't allowed on this node. |
| `422 PARENT_ROW_ID_MISMATCH` | Nested replace's rows reference the wrong parent `__row_id`. | Resolve parent's `__row_id` first (via OData on the parent artifact), regenerate file. |

**Idempotency**: same as any PATCH — `If-Match` makes retries safe as long as the first success hasn't been observed. If the service retries after a 200, it gets a 412 on the second attempt.

**Lifecycle of the replacement file_id**: the external service owns the file's lifecycle pre-commit. After a successful replace, the file_id becomes part of the node's `__file_ref` tree and is managed like any other artifact file (reset / rerun / GC semantics apply).

### 7.16 Idempotency

PATCH is **not** idempotent in the HTTP sense — retrying the same body will produce a new ETag and new `file_id`s each time. However, `If-Match` gives effective idempotency for retries that happen before any successful write: the first success bumps the ETag, so a retry with the same `If-Match` will 412.

Safe retry window: only while the server still returns 5xx / network error without 200. Once you've seen a 200, the operation is committed — don't retry.

### 7.17 What PATCH does NOT do

- It does **not** submit the node (that's `POST /submit`).
- It does **not** update the schema (schemas are node-definition-level; changing them requires a separate workflow edit path, out of scope here).
- It does **not** return row data — if the FE needs the post-edit grid view, re-query via `GET /artifacts/{artifact_id}/odata` with the new ETag.
- It does **not** accept `__row_id` or `__parent_row_id` in payloads.
