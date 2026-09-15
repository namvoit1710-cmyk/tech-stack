# Human-in-the-Loop

These four nodes **pause** a run and wait for a person before resuming. All have the **pending** runtime: when the engine reaches one, the run parks (status `PAUSED`, node status `PENDING`) and a real-time event is emitted so a UI can prompt the user. The run resumes when you submit through the REST API — see [Running a Workflow → If the run pauses](/guide/running-a-workflow#step-4-if-the-run-pauses-for-a-human).

## INPUT

**Pauses workflow to collect structured input from a user.** Runtime: **pending**.

- **Input ports:** `in_default`
- **Output ports:** `out_default`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `instruction` | string | | Prompt shown to the user (textarea). |
| `input_schema` | object | | The schema describing the fields to collect (object editor). |
| `timeout_minutes` | number | | Auto-timeout for the pause. *(internal)* |
| `file_id` | string | ✅ | Backing file id for table-style input. *(internal)* |
| `dynamic_table` | boolean | | Collect tabular data. *(internal)* |
| `runThrough` | boolean | | Pass-through behavior flag. *(internal)* |

### Output

| Field | Type | Description |
|---|---|---|
| `submitted_data` | object | The data the user submitted. |

### Resume

```bash
POST /runs/{run_id}/nodes/{node_id}/submit
{ "node_id": "input-1", "output_data": { "approved_amount": 500 } }
```

Downstream reads `{{$input-1.output.submitted_data.approved_amount}}`.

---

## HUMAN_ACTION

**Pauses workflow for a human to perform an action and confirm or reject.** Runtime: **pending**. This is the approval node — it routes differently depending on the human's decision.

- **Input ports:** `in_default`
- **Output ports:** `confirm`, `reject`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `instruction` | string | | **Required.** What to review and decide (textarea). |
| `input_schema` | array | | Optional fields to collect alongside the decision. Each item: `key`, `type` (`string`/`number`/`boolean`/`file`), `label`, `required`. |
| `confirm_label` | string | | Confirm button text. Default `Confirm`. |
| `reject_label` | string | | Reject button text. Default `Reject`. |

### Output

| Field | Type | Description |
|---|---|---|
| `action` | string | The chosen action (`confirm` / `reject`). |
| `submitted_data` | object | Any fields collected with the decision. |

### Example & routing

```json
{
  "id": "approve-refund",
  "type": "HUMAN_ACTION",
  "config": {
    "instruction": "Approve this refund?",
    "confirm_label": "Approve",
    "reject_label": "Deny",
    "input_schema": [ { "key": "comments", "type": "string", "label": "Comments" } ]
  }
}
```

Wire the `confirm` port to the approved path and the `reject` port to the rejection path. Both branches receive `submitted_data`.

---

## DATA_EDIT

**Pauses workflow to let a user review and edit upstream artifact data.** Runtime: **pending**. Surfaces a table (or single row) of data for in-place editing before the run continues.

- **Input ports:** `in_default`
- **Output ports:** `out_default`

### Config

Fields are grouped into two tabs in the editor: **Configuration** (`main`) and **Settings** (`settings_tab`).

| Field | Tab | Type | Expr | Notes |
|---|---|---|---|---|
| `file_id` | main | string | ✅ | **Required.** Source data, e.g. `{{$nodeA.__file_id}}`. |
| `artifact_key` | main | string | | Artifact key to load, e.g. `uploaded_file`. |
| `instruction` | main | string | | Guidance for the user (textarea). |
| `data_mode` | main | string | | `csv_single` (single row) / `csv_multi` (multiple rows). |
| `query` | main | string | | OData query to pre-filter, e.g. `$filter=qty gt 5&$top=10`. |
| `actions` | settings | json | | Row-action definitions (object editor). |
| `settings` | settings | json | | Extra settings (object editor). |
| `readonly` | settings | boolean | | Show data without allowing edits. |
| `custom_data_update` | settings | boolean | | Use a custom update path. |

### Output

| Field | Type | Description |
|---|---|---|
| `file_id` | string | The edited data's file id. |
| `data_mode` | string | The mode used. |

### Resume

`DATA_EDIT` is resumed by pushing the edited rows:

```bash
PATCH /runs/{run_id}/nodes/{node_id}/data
{ "node_id": "edit-1", "working_data": { ... }, "if_match": "<etag>" }
```

Pass the current `if_match` etag (from `GET .../data`) so a stale edit is rejected with `412` instead of silently overwriting.

---

## FILE_UPLOAD

**Pauses workflow to allow a user to upload a file.** Runtime: **pending**.

- **Input ports:** `in_default`
- **Output ports:** `out_default`

### Config

| Field | Type | Expr | Notes |
|---|---|---|---|
| `instruction` | string | | Prompt shown to the user (textarea). |
| `artifact_key` | string | | Key to store the uploaded file under, e.g. `uploaded_file`. |
| `use_agent` | string | | **Required.** Which agent(s) handle the upload, e.g. `agent-1, agent-2`. |

### Output

| Field | Type | Description |
|---|---|---|
| `file_id` | string | The uploaded file's id. |
| `file_name` | string | Original filename. |
| `file_size` | number | Size in bytes. |
| `content_type` | string | MIME type. |

### Resume

The run waits until a file is uploaded and registered, then resumes with the file metadata as the node's output. Reference the uploaded file downstream via `{{$upload-1.output.file_id}}` — for example as the `file_id` of a following `DATA_EDIT` node.
