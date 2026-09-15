# Input-Data API Reference

## 1. Overview

The **input-data API** is the primary endpoint the frontend uses to fetch current data and schema for a workflow node, so it can render an edit form (for INPUT/HUMAN_ACTION nodes) or a data table (for DATA_EDIT nodes).

```
GET /api/v1/runs/{run_id}/nodes/{node_id}/input-data?page_size=20
```

**Source file:** `app/layer2_application/features/get_node_input_data/use_cases/get_node_input_data_use_case.py`

**What it returns:**
- The node's **input schema** (field definitions for form rendering)
- **Scalars** — the flat/nested key-value data (single-row fields)
- **Arrays** — paginated preview of tabular array fields (multi-row fields)
- **Links** — OData and patch URLs for deep pagination and editing
- **Status** — whether the node is PENDING, COMPLETED, or SKIPPED

---

## 2. Response Shape

### `GetNodeInputDataResult`

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | `string` | The workflow run ID |
| `node_id` | `string` | The node ID within the workflow |
| `node_name` | `string` | Human-readable node name (e.g., `"User Input"`) |
| `node_type` | `string` | Node type enum value: `"INPUT"`, `"HUMAN_ACTION"`, `"DATA_EDIT"`, `"FILE_UPLOAD"`, `"TASK"`, etc. |
| `status` | `string` | One of `"PENDING"`, `"COMPLETED"`, `"SKIPPED"` |
| `input_schema` | `list[dict]` | Auto-form field definitions for UI rendering |
| `edit_mode` | `string` | `"none"`, `"before_dispatch"`, or `"after_dispatch"` |
| `data_mode` | `string` | `"json"`, `"csv_single"`, or `"csv_multi"` |
| **`scalars`** | **`dict`** | **Single-row key-value data (nested dict, unflattened from CSV)** |
| **`arrays`** | **`list[ArrayFieldData]`** | **Array field previews (each backed by a separate CSV file)** |
| **`main_file_id`** | **`string \| null`** | **File ID of the main CSV (scalar data). `null` if legacy inline or no data** |
| `main_links` | `dict` | `{"data": "/files/{id}/odata", "patch": "/files/{id}/patch"}` or `{}` |
| `error` | `string \| null` | Error message if request failed |
| `error_code` | `string` | Machine-readable error code (e.g., `"RUN_NOT_FOUND"`) |
| `error_details` | `dict` | Additional error context |

### `ArrayFieldData`

Each entry in the `arrays` list represents one array-of-objects field stored as a separate CSV artifact:

| Field | Type | Description |
|-------|------|-------------|
| `field_name` | `string` | Original field name from the node output (e.g., `"orders"`) |
| `file_id` | `string` | File ID of this artifact's CSV |
| `row_count` | `int` | Total number of rows in the CSV |
| `columns` | `list[string]` | Column names, in CSV header order (i.e. the order they appear in the source file) |
| `preview_rows` | `list[dict]` | First `page_size` rows (preview for the frontend) |
| `page_size` | `int` | Number of preview rows requested |
| `links` | `dict` | `{"data": "/files/{id}/odata", "patch": "/files/{id}/patch"}` |

---

## 3. How Data Gets Stored (Background)

Before understanding the API response, you need to understand how node data is stored. When a node produces output or a user submits input, `DataStoreService.store_data()` converts it to CSV files:

```
Original node output (Python dict)
         │
         ▼
 DataStoreService.store_data()
         │
         ├── Scalar fields ──────► Main CSV (single row)
         │   (strings, numbers,       file_id: "main-abc"
         │    booleans, nested         row_count: 1
         │    objects)                 columns: ["name", "address.city", ...]
         │
         └── Array fields ──────► Artifact CSV (multi-row)
             (list[dict])              file_id: "art-xyz"
                                       artifact_type: "array_field"
                                       row_count: N
```

The result is stored in `run.context.variables[node_id]` as a **file reference dict**:

```json
{
  "__file_ref": true,
  "file_id": "main-abc",
  "content_type": "text/csv",
  "row_count": 1,
  "columns": ["name", "address.city", "address.zip"],
  "extracted": {
    "name": { "value": "Alice" },
    "address.city": { "value": "Berlin" },
    "address.zip": { "value": "10115" }
  },
  "artifacts": [
    {
      "file_id": "art-xyz",
      "name": "orders",
      "artifact_type": "array_field",
      "content_type": "text/csv",
      "row_count": 150,
      "size_bytes": 4096,
      "created_at": "2026-04-08T10:00:00Z"
    }
  ]
}
```

**Key design decisions:**
- **Separation**: Array-of-objects fields get their own CSV files so the frontend can paginate them independently via OData without loading the entire dataset.
- **Extracted cache**: The `extracted` dict pre-populates scalar values at storage time so downstream variable resolution (`{{$node_id.name}}`) can resolve without downloading the CSV.
- **`__file_ref: true` marker**: Distinguishes file-backed data from legacy inline dicts.

---

## 4. The Four Data Scenarios

This is the most critical part: understanding when `scalars`, `arrays`, and `main_file_id` are populated.

### Scenario A: File-Backed with Scalars + Arrays

**When it happens:** The node output contained both scalar fields AND at least one array-of-objects field.

**Example original data:**
```json
{
  "customer_name": "Alice",
  "customer_email": "alice@example.com",
  "address": {
    "city": "Berlin",
    "zip": "10115"
  },
  "orders": [
    { "order_id": 1, "amount": 100, "product": "Widget" },
    { "order_id": 2, "amount": 250, "product": "Gadget" },
    { "order_id": 3, "amount": 75,  "product": "Doohickey" }
  ]
}
```

`DataStoreService.store_data()` splits this into:
- **Main CSV** (`file_id: "main-1"`): One row with columns `customer_name`, `customer_email`, `address.city`, `address.zip`
- **Artifact CSV** (`file_id: "art-orders"`): Three rows with columns `order_id`, `amount`, `product`

**API response:**
```json
{
  "run_id": "r1",
  "node_id": "n1",
  "node_name": "Customer Data",
  "node_type": "INPUT",
  "status": "COMPLETED",
  "input_schema": [...],
  "edit_mode": "before_dispatch",
  "data_mode": "json",

  "main_file_id": "main-1",
  "main_links": {
    "data": "/files/main-1/odata",
    "patch": "/files/main-1/patch"
  },
  "scalars": {
    "customer_name": "Alice",
    "customer_email": "alice@example.com",
    "address": {
      "city": "Berlin",
      "zip": "10115"
    }
  },
  "arrays": [
    {
      "field_name": "orders",
      "file_id": "art-orders",
      "row_count": 3,
      "columns": ["order_id", "amount", "product"],
      "preview_rows": [
        { "order_id": 1, "amount": 100, "product": "Widget" },
        { "order_id": 2, "amount": 250, "product": "Gadget" }
      ],
      "page_size": 2,
      "links": {
        "data": "/files/art-orders/odata",
        "patch": "/files/art-orders/patch"
      }
    }
  ]
}
```

**What the frontend should do:**
- Render `scalars` as an editable form (key-value pairs, nested objects as grouped fields)
- Render each entry in `arrays` as a paginated data table
- Use `arrays[].links.data` with OData query params (`$top`, `$skip`, `$filter`) to paginate beyond the preview
- Use `main_links.patch` or `arrays[].links.patch` to save edits

---

### Scenario B: File-Backed, Scalars Only (No Array Fields)

**When it happens:** The node output was a dict with only scalar/nested-object fields, no array-of-objects.

**Example original data:**
```json
{
  "name": "Alice",
  "age": 30,
  "address": {
    "city": "Berlin",
    "zip": "10115"
  }
}
```

**What's stored:** A single main CSV. No artifacts.

**API response:**
```json
{
  "status": "COMPLETED",
  "main_file_id": "main-1",
  "main_links": {
    "data": "/files/main-1/odata",
    "patch": "/files/main-1/patch"
  },
  "scalars": {
    "name": "Alice",
    "age": 30,
    "address": {
      "city": "Berlin",
      "zip": "10115"
    }
  },
  "arrays": []
}
```

**Key point:** `main_file_id` is present and `main_links` are provided. `arrays` is empty because there were no array-of-objects fields in the original data.

---

### Scenario C: Legacy Inline Dict (No File Backing)

**When it happens:** The context value is a plain Python dict that was stored directly in `run.context.variables[node_id]` without going through `DataStoreService`. This is a legacy path for backward compatibility.

**API response:**
```json
{
  "status": "COMPLETED",
  "main_file_id": null,
  "main_links": {},
  "scalars": {
    "name": "Alice",
    "age": 30
  },
  "arrays": []
}
```

**Key difference from Scenario B:** `main_file_id` is `null` and `main_links` is empty. The frontend cannot use OData pagination or patch — the data is all in `scalars` directly.

---

### Scenario D: No Data Yet (PENDING)

**When it happens:** The node hasn't been submitted yet. `run.context.variables[node_id]` is `None`.

**API response:**
```json
{
  "status": "PENDING",
  "main_file_id": null,
  "main_links": {},
  "scalars": {},
  "arrays": [],
  "input_schema": [
    { "key": "name", "type": "string", "label": "Full Name", "required": true },
    { "key": "age", "type": "number", "label": "Age" }
  ],
  "edit_mode": "before_dispatch",
  "data_mode": "json"
}
```

**What the frontend should do:** Use `input_schema` to render an empty form for the user to fill in. The form will be submitted via the resume-pending-task endpoint (`POST /runs/{run_id}/tasks/{task_id}/resume`).

---

### Decision Flowchart

```
run.context.variables[node_id]
        │
        ├── None ───────────────────► Scenario D: PENDING
        │                              scalars={}, arrays=[], main_file_id=null
        │
        ├── dict with __file_ref=true
        │       │
        │       ├── has artifacts ──► Scenario A: Scalars + Arrays
        │       │   (array_field)      scalars={...}, arrays=[...], main_file_id="xxx"
        │       │
        │       └── no artifacts ───► Scenario B: Scalars Only
        │                              scalars={...}, arrays=[], main_file_id="xxx"
        │
        └── plain dict ────────────► Scenario C: Legacy Inline
                                       scalars={...}, arrays=[], main_file_id=null
```

---

## 5. What Are Scalars — Deep Explanation

**Scalars** represent all non-array-of-objects fields from the original node output. Despite the name "scalar", this includes:

| Included in scalars | Example |
|---------------------|---------|
| Strings | `"Alice"` |
| Numbers | `42`, `3.14` |
| Booleans | `true`, `false` |
| Nested objects | `{"city": "Berlin", "zip": "10115"}` |
| Small lists | `["tag1", "tag2"]` (stored as JSON string in CSV) |
| Null values | `null` (stored as empty string in CSV) |

**NOT included in scalars** (separated into arrays instead):

| Excluded from scalars | Example |
|----------------------|---------|
| Array of objects | `[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]` |

### How Scalars Are Stored and Retrieved

**Storage (flatten):** Nested objects are flattened to dot-notation CSV columns.

```
Python dict:                          CSV columns:
{                                     customer_name | address.city | address.zip
  "customer_name": "Alice",           Alice         | Berlin       | 10115
  "address": {
    "city": "Berlin",
    "zip": "10115"
  }
}
```

**Retrieval (unflatten):** The API reads the first row from the main CSV (`$top=1`), strips the synthetic `_row_index` column, and unflattens dot-notation columns back into a nested dict.

```
CSV row:                              Unflattened result:
{                                     {
  "customer_name": "Alice",    ──►      "customer_name": "Alice",
  "address.city": "Berlin",              "address": {
  "address.zip": "10115"                   "city": "Berlin",
}                                          "zip": "10115"
                                         }
                                       }
```

**Deep nesting works too:**

```
CSV column: "a.b.c" = "deep"
            "a.b.d" = "sibling"
            "x"     = "root"

Unflattened:
{
  "a": {
    "b": {
      "c": "deep",
      "d": "sibling"
    }
  },
  "x": "root"
}
```

### Type Recovery

Values stored as CSV strings are automatically parsed back to their original types:

| CSV string | Recovered type | Value |
|-----------|----------------|-------|
| `"42"` | `int` | `42` |
| `"3.14"` | `float` | `3.14` |
| `"true"` | `bool` | `True` |
| `"false"` | `bool` | `False` |
| `""` | `None` | `None` |
| `"[1, 2, 3]"` | `list` | `[1, 2, 3]` |
| `'{"a": 1}'` | `dict` | `{"a": 1}` |
| `"hello"` | `str` | `"hello"` |

---

## 6. What Are Arrays — Deep Explanation

**Arrays** represent fields whose original value was a **non-empty list of dicts** (tabular data). These are automatically separated from scalars during storage because:

1. **Scalars = single row, Arrays = many rows** — they need different CSV files
2. **Pagination** — arrays can have hundreds/thousands of rows; the frontend needs OData `$top`/`$skip` support
3. **Independent editing** — each array field is editable via its own patch endpoint

### How Arrays Are Identified

During `DataStoreService.store_data()`, each field is checked:

```python
def _is_array_of_dicts(value):
    return (
        isinstance(value, list)
        and len(value) > 0
        and isinstance(value[0], dict)
    )
```

If a field passes this check, it becomes an artifact with `artifact_type: "array_field"`.

### How Arrays Are Stored

Each array field is stored as a **separate CSV file** (artifact), appended to the file reference's `artifacts` list:

```
Original data:
{
  "status": "active",          ◄── scalar → main CSV
  "orders": [                  ◄── array  → artifact CSV "orders"
    {"id": 1, "amount": 100},
    {"id": 2, "amount": 250}
  ],
  "line_items": [              ◄── array  → artifact CSV "line_items"
    {"sku": "A1", "qty": 5},
    {"sku": "B2", "qty": 3}
  ]
}

Result:
  Main CSV (file_id: "main-1"):     status
                                    active

  Artifact CSV (file_id: "art-1"):  id  | amount
                                    1   | 100
                                    2   | 250

  Artifact CSV (file_id: "art-2"):  sku | qty
                                    A1  | 5
                                    B2  | 3
```

### How the API Returns Array Previews

The `_fetch_artifact_previews()` method:

1. Iterates over the file reference's `artifacts` list
2. **Skips non-array artifacts** — only includes entries with `artifact_type: "array_field"` (e.g., attachments with `artifact_type: "attachment"` are excluded)
3. Queries the file service with `$top=page_size` to get the first page of rows
4. Returns each artifact as an `ArrayFieldData` with:
   - `file_id` — for the frontend to reference in subsequent OData/patch calls
   - `preview_rows` — the first page of data
   - `row_count` — total rows in the artifact (for pagination UI)
   - `columns` — column names in CSV header order (the order they appear in the source file)
   - `links` — OData query and patch URLs

### Multiple Arrays in One Response

A node can produce multiple array fields. Each becomes a separate entry in the `arrays` list:

```json
{
  "scalars": { "status": "active" },
  "arrays": [
    {
      "field_name": "orders",
      "file_id": "art-1",
      "row_count": 150,
      "preview_rows": [...]
    },
    {
      "field_name": "line_items",
      "file_id": "art-2",
      "row_count": 45,
      "preview_rows": [...]
    }
  ]
}
```

---

## 7. Schema Resolution

`edit_mode` and `data_mode` use a **template-first** chain; `input_schema` uses a
**runtime-first** chain. They are resolved independently because modes are
compile-time contracts while the schema carries runtime state (resolved
expressions, dynamic table rows, baked-in default values).

### `edit_mode` / `data_mode` — template-first

#### Priority 1: Node Definition Template

If the node has `config["_node_definition"]`, those values are **authoritative**:

```json
{
  "_node_definition": {
    "edit_mode": "before_dispatch",
    "data_mode": "csv_single"
  }
}
```

#### Priority 2: Direct Node Config

If no template is attached, check `node.config["edit_mode"]` and `node.config["data_mode"]` for per-node overrides.

#### Priority 3: Node Type Inference

If neither template nor direct config provides values:
- `edit_mode`: INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD nodes default to `"before_dispatch"`. All other node types default to `"none"`.
- `data_mode`: Always defaults to `"json"`.

### `input_schema` — runtime-first

The `input_schema` resolution chain prefers the runtime-resolved schema over
the design-time definition so the frontend can render forms dynamically:

1. **`latest_task.output_metadata["input_schema"]`** — handler-resolved schema.
   Wins whenever a task exists for the node (PENDING or COMPLETED) and its
   schema is non-empty. This is the only source that carries:
   - `{{…}}` expressions in `default` / `value` / `fieldConfig.controlProps.*`
     evaluated to concrete values (via `InputNodeHandler._resolve_schema_defaults`)
   - `dynamic_table` field `controlProps.value` rows materialized from the
     upstream CSV headers (via `InputNodeHandler._build_dynamic_table_schema`)
   - `prefilled` init values baked into the field defaults
2. **`node.config["_node_definition"]["input_schema"]`** — design-time template.
   Used when no task has run yet (e.g., the user peeks at a downstream node).
3. **`node.config["input_schema"]`** — raw per-node schema. May be a list or a
   JSON string (auto-parsed). Used when neither template nor task is available.

**Empty-list fall-through:** an empty list in `task.output_metadata["input_schema"]`
is treated as "not set" and falls through to the next precedence level, so a
runtime-misconfigured task can't mask the design-time schema.

---

## 8. Status Determination

The node's status is determined by checking the run context:

```
node_id in run.context.completed_nodes?  ──► "COMPLETED"
node_id in run.context.skipped_nodes?    ──► "SKIPPED"
run.context.variables[node_id] exists?   ──► "COMPLETED"
otherwise                                ──► "PENDING"
```

Note: a context value existing (even without the node being in `completed_nodes`) marks the node as COMPLETED, because the data has been submitted.

---

## 9. Frontend Integration Guide

### Rendering the UI

```
if status == "PENDING":
    Render empty form using input_schema
elif status == "COMPLETED":
    if scalars is not empty:
        Render form pre-filled with scalars
    for each array in arrays:
        Render data table with array.preview_rows
        Show pagination (total: array.row_count, page_size: array.page_size)
elif status == "SKIPPED":
    Show "Node was skipped" message
```

### OData Pagination for Arrays

When the user paginates beyond the preview, use the array's `links.data`:

```
GET /files/{array.file_id}/odata?$top=20&$skip=40
```

Supported OData query parameters:
- `$top` — number of rows to return
- `$skip` — number of rows to skip (for offset-based pagination)
- `$filter` — row filtering
- `$orderby` — sorting

### Editing Data

**Scalars:** Use `main_links.patch` to update scalar values:
```
PATCH /files/{main_file_id}/patch
Content-Type: application/json

{ "row_index": 0, "updates": { "name": "Bob", "address.city": "Munich" } }
```

**Arrays:** Use `arrays[].links.patch` to update specific rows:
```
PATCH /files/{array.file_id}/patch
Content-Type: application/json

{ "row_index": 5, "updates": { "amount": 300 } }
```

### Submitting PENDING Node Data

After the user fills in the form, submit via the resume-pending-task endpoint:

```
POST /api/v1/runs/{run_id}/tasks/{task_id}/resume
Content-Type: application/json

{
  "updated_input": {
    "name": "Alice",
    "age": 30,
    "orders": [
      {"order_id": 1, "amount": 100}
    ]
  }
}
```

This stores the data (separating scalars and arrays), marks the task as COMPLETED, and resumes the workflow.

---

## 10. Error Handling & Resilience

The API is designed to degrade gracefully:

| Scenario | Behavior |
|----------|----------|
| Run not found | Returns `error: "Run not found"`, `error_code: "RUN_NOT_FOUND"` |
| Node not found | Returns `error: "Node not found"`, `error_code: "NODE_NOT_FOUND"` |
| File service unavailable | `main_file_id` and `main_links` still returned; `scalars` will be `{}`; `preview_rows` will be `[]` |
| File client not configured | Same as above — metadata (links, row_count) provided without data preview |
| Single artifact fetch fails | Other artifacts still returned; failed artifact has `preview_rows: []` with `links` and `row_count` intact |

The frontend should always check for `main_links` availability before attempting OData/patch operations. When `main_file_id` is `null`, the data in `scalars` is all that's available (legacy inline mode).

---

## Summary: Quick Reference

| Question | Answer |
|----------|--------|
| When is `main_file_id` set? | When node data is file-backed (`__file_ref: true` in context) |
| When is `main_file_id` null? | Legacy inline dict or no data yet (PENDING) |
| What are `scalars`? | All non-array fields from node output, stored as single CSV row, unflattened to nested dict |
| When do `arrays` appear? | When the original data had array-of-objects fields (e.g., `"orders": [{...}, {...}]`) |
| Can you have `main_file_id` with empty `arrays`? | Yes — when original data had only scalar/object fields, no array-of-objects |
| Can you have `arrays` without `main_file_id`? | No — if there are arrays, there's always a file reference, so `main_file_id` is set (may be empty string if scalar part was empty) |
| How are nested objects handled? | Flattened to dot-notation in CSV (`address.city`), unflattened back on read |
| How are large arrays paginated? | First `page_size` rows in `preview_rows`; use `links.data` OData endpoint for more |
