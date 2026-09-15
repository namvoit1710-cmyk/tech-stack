# Edit Node Input API

`PATCH /runs/{run_id}/nodes/{node_id}/input`

Edit the input data of a single node on a run. Supports two independent edit
*targets* (the scalar main CSV or one array-field artifact) and four mutually
exclusive *operations* (full-file swap, file-service patch, data-factory row
ops, data-factory column transforms).

Sibling read endpoint: `GET /runs/{run_id}/nodes/{node_id}/input-data` —
returns the same file/artifact structure so the client knows what to address.

Implementation: [edit_node_input_use_case.py](../app/layer2_application/features/edit_node_input/use_cases/edit_node_input_use_case.py),
controller binding in [run_controller.py](../app/layer3_adapters/controllers/restful/v1/run_controller.py).

---

## Data model recap

After an INPUT node with a complex form schema is submitted once, its data
lives in `run.context.variables[node_id]` as a `__file_ref`:

```jsonc
{
  "__file_ref": true,
  "file_id": "<scalar-main-csv>",      // one-row CSV of scalar fields
  "row_count": 1,
  "columns": ["input1"],
  "extracted": { "input1": { "value": "hello" } },
  "artifacts": [
    {
      "file_id": "<table-csv>",
      "name": "table_2_ugn6u",          // matches the array field's `key`
      "artifact_type": "array_field",
      "row_count": 2,
      "columns": ["input2", "input_13"]
    }
  ]
}
```

Schema example that produces this shape:

```json
[
  { "key": "input1", "outputType": "string", "fieldConfig": { "fieldControl": "InputControl" } },
  {
    "key": "table_2_ugn6u", "outputType": "array",
    "fieldConfig": { "fieldWrapper": "TableWrapper" },
    "fields": [
      { "key": "table_2_ugn6u_row", "outputType": "object",
        "fields": [
          { "key": "input2",  "outputType": "string" },
          { "key": "input_13", "outputType": "string" }
        ]
      }
    ]
  }
]
```

`DataStoreService` splits the payload at the top level:

- Scalar fields → main CSV (single row, columns = field keys).
- Each top-level array-of-objects field → a separate CSV stored as an
  `array_field` artifact whose `name` equals the field `key`. The inner row
  wrapper (`table_2_ugn6u_row`) and inner fields (`input2`, `input_13`) become
  the CSV's columns.
- Nesting *deeper* than one level of array-of-objects is not separately
  addressable — every array-of-objects collapses to one CSV at its `key`.

---

## Request body

```python
class EditNodeInputRequest(BaseModel):
    target_field: str = ""              # "" → main CSV; "<key>" → artifact
    file_id: str | None = None          # full-file swap
    edits: list[dict] = []              # cell patches
    new_rows: list[dict[str, str]] = [] # appended rows
    delete_row_indices: list[int] = []  # row deletions by current index
    row_operations: list[dict] = []     # data-factory row ops
    transform_rules: list[dict] = []    # data-factory column transforms
    auto_rerun: bool = False
```

### Routing priority

Evaluated top-down; first non-empty field wins. The others are ignored.

| Priority | Field | Backend |
|---|---|---|
| 1 | `file_id` | direct swap, no service call |
| 2 | `row_operations` | `data_factory_client.transform_rows` |
| 3 | `transform_rules` | `data_factory_client.transform_data` |
| 4 | `edits` ∪ `new_rows` ∪ `delete_row_indices` | `file_service_client.patch_file_data` |
| — | none of the above | error `NOTHING_TO_EDIT` |

### `target_field` semantics

| Value | What gets edited |
|---|---|
| `""` (default) | scalar main CSV. Updates `task.resolved_input.file_id`, propagates to `run.context.variables[node_id].file_id`, clears stale `extracted` cache. |
| `"<artifact_name>"` | the matching `artifact_type=="array_field"` artifact. Updates only `artifact.file_id`, then refreshes the artifact's `row_count`/`columns` from the new file. Main CSV and `task.resolved_input` are untouched. |
| `"<unknown>"` | error `ARTIFACT_NOT_FOUND`. |

---

## Operations — payload examples

The examples below assume `run_id=r1`, `node_id=n1`, and the complex form above.

### 1. Full file swap (`file_id`)

Client uploads a new CSV to the file service first, then references the
returned id here. This is a *two-step* flow — the PATCH route does not accept
multipart.

```bash
# Step 1: POST <file-service>/files (multipart) → { file_id: "new-uuid" }
# Step 2:
curl -X PATCH .../runs/r1/nodes/n1/input \
  -H 'content-type: application/json' \
  -d '{ "target_field": "table_2_ugn6u", "file_id": "new-uuid" }'
```

| Without `target_field` | scalar main CSV is replaced wholesale. |
| With `target_field` | the named artifact's `file_id` is replaced and its `row_count`/`columns` are refreshed. |

### 2. Cell edits (`edits`)

Row index is into the file's *current* row order. Column must already exist.

```jsonc
// Edit the scalar field — main CSV always has row_index 0
{ "edits": [{ "row_index": 0, "column": "input1", "value": "new_hello" }] }

// Edit table cells
{
  "target_field": "table_2_ugn6u",
  "edits": [
    { "row_index": 0, "column": "input2",  "value": "patched_a1" },
    { "row_index": 1, "column": "input_13", "value": "patched_b2" }
  ]
}
```

### 3. Append rows (`new_rows`)

Only meaningful on a table artifact (the scalar CSV is single-row by design).
Missing columns are filled with `""`.

```jsonc
{
  "target_field": "table_2_ugn6u",
  "new_rows": [
    { "input2": "a3", "input_13": "b3" },
    { "input2": "a4", "input_13": "b4" }
  ]
}
```

### 4. Delete rows (`delete_row_indices`)

Indices into the file's current order. May be combined with `edits` and
`new_rows` in one call — all three are sent to `patch_file_data` together.

```jsonc
{
  "target_field": "table_2_ugn6u",
  "delete_row_indices": [0, 2]
}
```

### 5. Row operations (`row_operations`)

Use when index-based deletion or naive append isn't enough — e.g. inserting
at a position relative to another row, deleting by value match. Requires the
data-factory client to be wired.

```jsonc
{
  "target_field": "table_2_ugn6u",
  "row_operations": [
    {
      "operation": "insert",
      "row_identifier": { "type": "index", "index": 0 },
      "data": { "input2": "head", "input_13": "row" },
      "position": "before"
    },
    {
      "operation": "delete",
      "row_identifier": { "type": "values", "match_values": { "input2": "a1" } }
    }
  ]
}
```

### 6. Column transforms (`transform_rules`)

Filter / map / aggregate over the file as a whole. Also data-factory-backed.

```jsonc
{
  "target_field": "table_2_ugn6u",
  "transform_rules": [
    {
      "rule_name": "keep_a_rows",
      "type": "filter",
      "params": { "expression": "pl.col('input2').str.starts_with('a')" }
    }
  ]
}
```

---

## `auto_rerun`

Set `"auto_rerun": true` to flip a COMPLETED/FAILED task back to RUNNING after
the edit so the orchestration engine picks it up again. PENDING tasks ignore
the flag (they're already waiting). For DATA_EDIT nodes, `output_metadata` is
preserved across the rerun so the edit form keeps its schema.

---

## Response

```jsonc
{
  "node_id": "n1",
  "task_id": "task-...",
  "previous_file_id": "<id before>",  // artifact id when target_field set, else scalar id
  "new_file_id":      "<id after>",
  "task_status": "PENDING|RUNNING|COMPLETED|FAILED",
  "rerun_triggered": true|false
}
```

For artifact swaps, `previous_file_id`/`new_file_id` are the *artifact's* ids,
not the scalar main CSV's. The published `node_input.edited` event carries the
same pair.

---

## Errors

| `error_code` | HTTP | When |
|---|---|---|
| `RUN_NOT_FOUND` | 404 | `run_id` doesn't exist |
| `NODE_NOT_FOUND` | 404 | `node_id` not in root workflow or any descendant sub-workflow |
| `TASK_NOT_FOUND` | 404 | node exists but no task has been created for it on the effective run |
| `ARTIFACT_NOT_FOUND` | 404 | `target_field` doesn't match an `array_field` artifact |
| `INVALID_TASK_STATUS` | 400 | task is not in `{PENDING, COMPLETED, FAILED}` (e.g. RUNNING) |
| `NOTHING_TO_EDIT` | 400 | request body has no `file_id` and no operations |
| `FILE_OPERATION_FAILED` | 400 | no `previous_file_id` to operate on, or file-service rejected the patch |
| `SERVICE_NOT_CONFIGURED` | 500 | `row_operations`/`transform_rules` requested but `data_factory_client` is unset; or file-service unset for `edits` |
| `DATA_OPERATION_FAILED` | 400 | data-factory returned `success=false` or didn't return a new `file_id` |

`error_details` carries context (`run_id`, `node_id`, `task_id`, `field_name`,
`current_status`/`allowed`, etc.) — use it for client-side messages.

---

## What this endpoint is *not* for

- **Initial submission of an INPUT/HUMAN_ACTION node.** The PATCH route
  requires `previous_file_id`, which only exists after the node has been
  resumed at least once. For the first submission use
  `POST /runs/{run_id}/nodes/{node_id}/resume-input` with the full
  `updated_data` payload — `DataStoreService` runs there and creates the
  file_ref + artifacts.
- **Editing nested arrays inside a row.** Only top-level array-of-objects are
  addressable via `target_field`.
- **Cross-node edits.** Each call targets a single node's input on a single
  run. Walk descendants by passing the *root* `run_id`; the use case resolves
  the node in the nested run automatically.

---

## End-to-end flow on a complex form

```text
1. Client POSTs to /runs (StartRun) or hits an INPUT node mid-run.
2. Client GETs /runs/{run_id}/nodes/{node_id}/input-data
     → schema + main_file_id + arrays[{ field_name, file_id, ... }]
3. Client renders the form. User edits.
4a. Whole-file replace?           → upload new CSV → PATCH /input
                                     { target_field, file_id }
4b. Cell-level edits / row insert → PATCH /input
                                     { target_field, edits | new_rows | delete_row_indices }
4c. Insert-at-position / by-value → PATCH /input
                                     { target_field, row_operations }
4d. Filter / column transform     → PATCH /input
                                     { target_field, transform_rules }
5. (Optional) auto_rerun=true to re-execute downstream nodes.
6. Subscribers consume node_input.edited (previous_file_id, new_file_id).
```

For the contract that drives the form itself (schema resolution, table_data
detection, scalar unflattening), see
[input-data-api.md](./input-data-api.md).
