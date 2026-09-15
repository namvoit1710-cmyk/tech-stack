# Frontend Guide: Interactive Node Types & Input Data API

## Interactive Node Types

The workflow engine has **4 interactive (PENDING) node types** that pause execution and wait for user input:

| Node Type | Icon | Color | Output Ports | Purpose |
|-----------|------|-------|--------------|---------|
| **INPUT** | `FormInput` | `#8B5CF6` | `out_default` | Collect structured form input from user |
| **HUMAN_ACTION** | `UserCheck` | `#F59E0B` | `confirm`, `reject` | User confirms or rejects an action (branching) |
| **DATA_EDIT** | `TableProperties` | `#10B981` | `out_default` | User reviews and edits upstream tabular data (CSV) |
| **FILE_UPLOAD** | `Upload` | `#3B82F6` | `out_default` | User uploads a file |

---

### INPUT

Collects structured form data. Supports text, number, boolean, select, date, textarea fields, and dynamic table schemas from upstream CSV files.

**Config**:

| Field | Type | Description |
|-------|------|-------------|
| `input_schema` | `list[object]` | Form field definitions (AutoForm format: `key`, `outputType`, `fieldConfig`) |
| `input_types` | `dict[str, str]` | Field key -> coercion type. Falls back to `input_schema.outputType` |
| `instruction` | `string` | User-facing instruction text |
| `timeout_minutes` | `number` | Optional timeout |
| `file_id` | `string` | Expression to upstream file for dynamic table generation |
| `dynamic_table` | `boolean` | Auto-generate table schema from `file_id` headers |

**Input binding** prefills form fields:
```json
{ "input_binding": { "username": "{{$input.default_user}}", "age": "{{$prev_node.user_age}}" } }
```

**Task `output_metadata`** (what backend returns when node is PENDING):
```json
{
  "node_type": "INPUT",
  "input_schema": [{ "key": "username", "outputType": "string", "fieldConfig": { "..." : "..." } }],
  "instruction": "Please provide your details",
  "prefilled": { "username": "john_doe" },
  "timeout_minutes": 30
}
```

**Socket.IO events**: `input.requested` (paused), `input.submitted` (resumed)

---

### HUMAN_ACTION

Displays context data and two action buttons (confirm/reject). The chosen action determines which branch the workflow follows. Optionally includes a form.

**Config**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `input_schema` | `list[object]` | `[]` | Optional form fields alongside action buttons |
| `instruction` | `string` | `""` | Instruction text |
| `confirm_label` | `string` | `"Confirm"` | Confirm button label |
| `reject_label` | `string` | `"Reject"` | Reject button label |
| `input_types` | `dict[str, str]` | `{}` | Type coercion for optional form fields |

**Input binding** provides read-only context to display:
```json
{ "input_binding": { "order_details": "{{$fetch_order.details}}", "total": "{{$calculator.total}}" } }
```

**Task `output_metadata`**:
```json
{
  "node_type": "HUMAN_ACTION",
  "instruction": "Review and approve or reject.",
  "input_schema": [],
  "confirm_label": "Approve",
  "reject_label": "Reject",
  "prefilled": { "order_details": { "id": "ORD-123", "total": 599.99 } }
}
```

**Branching**: Confirm matches ports `confirm`/`success`/`true_branch`. Reject matches `reject`/`failure`/`false_branch`. Downstream nodes on the not-taken branch are skipped.

**Socket.IO events**: `human_action.requested` (paused), `human_action.completed` (resumed, includes `action` field)

---

### DATA_EDIT

Loads an upstream CSV file into an editable table. The backend auto-generates the table schema from file headers and pre-fills with existing rows.

**Config**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `instruction` | `string` | `""` | Instruction text |
| `data_mode` | `string` | `"csv_single"` | `csv_single`, `csv_multi`, or `json` |
| `timeout_minutes` | `number` | — | Optional timeout |
| `artifact_key` | `string` | `""` | Named key for edited artifact (`{{$artifacts.<key>}}`) |

**Input binding** — `file_id` is required to reference the upstream file:
```json
{ "input_binding": { "file_id": "{{$extract_data.__file_id}}" } }
```

**Task `output_metadata`**:
```json
{
  "node_type": "DATA_EDIT",
  "input_schema": [{
    "key": "table_data",
    "outputType": "array",
    "fieldConfig": {
      "fieldControl": "TableArrayFieldControl",
      "controlProps": {
        "columns": [{ "key": "name", "label": "Name" }, { "key": "email", "label": "Email" }],
        "value": [{ "name": "John", "email": "john@test.com" }]
      }
    }
  }],
  "instruction": "Review and correct the data",
  "prefilled": {},
  "file_id": "abc-123",
  "source_file_id": "abc-123",
  "source_node_id": "extract_node",
  "source_node_name": "Extract Data",
  "source_columns": ["name", "email"],
  "source_row_count": 150,
  "artifact_key": "edited_data"
}
```

**Socket.IO events**: `data_edit.requested` (paused, includes source info), `data_edit.completed` (resumed)

---

### FILE_UPLOAD

Renders a single file upload control. After upload, the file metadata becomes the node output and the file is attached as a named artifact.

**Config**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `instruction` | `string` | `""` | Instruction text |
| `artifact_key` | `string` | `""` | Named key for uploaded artifact (`{{$artifacts.<key>}}`) |

**Input binding**: typically empty.

**Task `output_metadata`**:
```json
{
  "node_type": "FILE_UPLOAD",
  "input_schema": [{
    "key": "file",
    "outputType": "string",
    "fieldConfig": { "fieldControl": "UploadControl", "wrapperProps": { "label": "Upload File" } }
  }],
  "instruction": "Upload the customer data CSV file",
  "prefilled": {},
  "artifact_key": "customer_data"
}
```

**Node output after resume**:
```json
{
  "file_id": "uploaded-uuid", "filename": "customers.csv", "content_type": "text/csv",
  "size_bytes": 12345, "row_count": 100, "columns": ["name", "email", "phone"], "col_count": 3
}
```

**Socket.IO events**: `input.requested` (paused), `input.submitted` (resumed)

---

## Input Data API

### Get Node Input Data

```
GET /runs/{run_id}/nodes/{node_id}/input-data?page_size=20
```

Returns the current input data for an interactive node: schema, scalar values, and first page of each array artifact. Optimized for rendering edit forms.

#### Response

```json
{
  "run_id": "run-uuid",
  "node_id": "node-uuid",
  "node_name": "My Input Node",
  "node_type": "INPUT",
  "status": "PENDING",
  "input_schema": [
    { "key": "username", "outputType": "string", "fieldConfig": { "..." : "..." } }
  ],
  "edit_mode": "before_dispatch",
  "data_mode": "json",
  "scalars": {
    "username": "john_doe",
    "age": 30
  },
  "arrays": [
    {
      "field_name": "items",
      "file_id": "array-file-uuid",
      "row_count": 50,
      "columns": ["name", "quantity"],
      "preview_rows": [{ "name": "Widget A", "quantity": 10 }],
      "page_size": 20,
      "links": {
        "data": "/files/array-file-uuid/odata",
        "patch": "/files/array-file-uuid/patch"
      }
    }
  ],
  "main_file_id": "file-uuid",
  "main_links": {
    "data": "/files/file-uuid/odata",
    "patch": "/files/file-uuid/patch"
  }
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `node_type` | `string` | `INPUT`, `HUMAN_ACTION`, `DATA_EDIT`, or `FILE_UPLOAD` |
| `status` | `string` | `PENDING`, `COMPLETED`, or `SKIPPED` |
| `input_schema` | `list[object]` | Form field definitions for rendering the UI |
| `edit_mode` | `string` | `"none"` (read-only) or `"before_dispatch"` (editable). Interactive nodes default to `"before_dispatch"` |
| `data_mode` | `string` | `"json"`, `"csv_single"`, or `"csv_multi"` |
| `scalars` | `dict` | Flat key-value pairs of scalar field values |
| `arrays` | `list[ArrayFieldData]` | Array field artifacts with paginated preview rows and OData links |
| `main_file_id` | `string\|null` | File ID of the main data file (when file-backed) |
| `main_links` | `dict` | `data` (OData endpoint) and `patch` (edit endpoint) for the main file |

#### Schema Resolution Order

The `input_schema` in the response is resolved with this fallback chain:

1. `node.config._node_definition.input_schema` (from NodeDefinition template)
2. `node.config.input_schema` (direct node config, for INPUT/HUMAN_ACTION)
3. `task.output_metadata.input_schema` (runtime-generated, for FILE_UPLOAD/DATA_EDIT)

#### How It Works Per Node Type

| Node Type | `scalars` contains | `arrays` contains | `input_schema` source |
|-----------|--------------------|-------------------|-----------------------|
| **INPUT** | Submitted form values | Array fields from form submission | Node config `input_schema` |
| **HUMAN_ACTION** | Submitted form + `_action` field | Array fields if any | Node config `input_schema` |
| **DATA_EDIT** | Scalar fields from edited CSV | Edited table data artifacts | Runtime-generated table schema |
| **FILE_UPLOAD** | Upload metadata (file_id, filename, etc.) | — | Runtime-generated upload schema |
