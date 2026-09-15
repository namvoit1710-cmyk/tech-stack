# CSV Business Data Round-Trip: How INPUT/HUMAN_ACTION Nodes Handle Data

## Purpose

This is an **explanation document** — no code changes. It describes how data flows when a user fills an auto-form at an INPUT or HUMAN_ACTION node, how the backend receives that data, and how the CSV convention ("business data -> CSV for agent/user collaboration") applies.

---

## 1. The Complete Round-Trip (Step by Step)

### Step 1: Workflow Pauses at INPUT Node -> Frontend Gets Form Schema

When the workflow engine reaches an INPUT node, `InputNodeHandler.handle()` runs:

**File**: `workflow-control-plane-service/app/layer1_domain/services/node_handlers/input_handler.py`

```
1. Resolves input_binding expressions -> prefilled dict
   e.g. node.input_binding: {"order_id": "{{$trigger.output.order_id}}"}
   -> prefilled = {"order_id": "ORD-123"}

2. Creates a PENDING task:
   task.resolved_input = prefilled          <- pre-filled values
   task.output_metadata = {
     "node_type": "INPUT",
     "input_schema": [                      <- form field definitions
       {"key": "name", "type": "string", "label": "Full Name", "required": true},
       {"key": "items", "type": "array", "label": "Order Items"}
     ],
     "instruction": "Please review and fill in the details.",
     "prefilled": {"order_id": "ORD-123"}, <- pre-filled values for the form
     "timeout_minutes": 60
   }

3. Publishes InputRequested event -> Socket.IO broadcasts to frontend
```

The frontend receives the event, calls `GET /runs/{run_id}/tasks/{task_id}` to get `output_metadata`, and renders the auto-form using the `Builder` component from `ldc-auto-form`.

### Step 2: User Fills the Form and Submits

The frontend sends:

```
POST /api/v1/runs/{run_id}/tasks/{task_id}/resume
{
  "updated_input": {
    "name": "Alice Johnson",
    "email": "alice@example.com",
    "items": [
      {"product": "Widget", "qty": 5},
      {"product": "Gadget", "qty": 3}
    ]
  }
}
```

### Step 3: Backend Receives and Stores the Data

**File**: `workflow-control-plane-service/app/layer2_application/features/resume_pending_task/use_cases/resume_pending_task_use_case.py`

The use case routes INPUT/HUMAN_ACTION nodes to `_resume_human_interaction_task()`:

```
1. Merges updated_input into task.resolved_input:
   task.resolved_input = {"order_id":"ORD-123", "name":"Alice", "email":"...", "items":[...]}
                          (prefilled)            (user-submitted)

2. Marks task COMPLETED (no worker -- INPUT nodes don't dispatch to workers)

3. Stores submitted data in run context variables:
   run.context.variables["input-node-1"] = {
     "output": {
       "order_id": "ORD-123",
       "name": "Alice Johnson",
       "email": "alice@example.com",
       "items": [{"product":"Widget","qty":5}, {"product":"Gadget","qty":3}]
     },
     "metadata": {"type": "INPUT", "action": "confirm"}
   }

4. Also aliases by node.name if it differs from node.id

5. Adds node to completed_nodes, processes successor nodes immediately

6. Publishes InputSubmitted event -> Socket.IO
```

**Key point**: At this stage, the data is stored as **raw JSON** in `run.context.variables`. No CSV conversion has happened yet.

### Step 4: Downstream TASK Node Picks Up the Data -> CSV Conversion Happens

When the next TASK node in the workflow has `data_mode: csv_single` or `csv_multi`, CSV conversion occurs at dispatch time:

**File**: `workflow-control-plane-service/app/layer1_domain/services/orchestration_engine.py` -> `_apply_csv_data_mode()` (line 253)

```
1. TASK node's input_binding resolves expressions:
   input_binding: {
     "name": "{{$input-node-1.output.name}}",
     "items": "{{$input-node-1.output.items}}"
   }
   -> resolved_input = {"name":"Alice", "items":[{"product":"Widget","qty":5}, ...]}

2. _apply_csv_data_mode() checks node's _node_definition snapshot:
   data_mode = "csv_multi"
   locked_defaults = {"method": "POST", "url": "https://api/process"}

3. CsvConverter.determine_csv_input():
   |-- Separates locked_defaults from CSV-eligible fields
   |-- Finds "items" (list of dicts) -> converts to multi-row CSV:
   |     product,qty
   |     Widget,5
   |     Gadget,3
   '-- Returns (csv_bytes, "items.csv")

4. file_service_client.upload_file(csv_bytes, "items.csv") -> file_id = "file-abc-123"

5. task.resolved_input becomes:
   {"file_id": "file-abc-123", "data_mode": "csv_multi", "method": "POST", "url": "..."}

6. If edit_mode == "before_dispatch":
   -> task.status = PENDING (user reviews CSV in CsvDataGrid before dispatch)
   Else:
   -> task dispatches to worker immediately
```

### Step 5: Worker Receives CSV

The worker (e.g., http-request-worker) receives:

```json
{
  "task_id": "task-xyz",
  "input": {
    "file_id": "file-abc-123",
    "data_mode": "csv_multi",
    "method": "POST",
    "url": "https://api/process"
  }
}
```

The worker downloads the CSV file using `file_id`, processes it, and reports results back.

---

## 2. Where CSV Conversion Actually Happens

```
INPUT node         TASK node (csv_multi)        Worker
    |                      |                       |
    |  raw JSON            |                       |
    |---------------->     |                       |
    |  {name, items}       |                       |
    |                      |                       |
    |          variables   |  input_binding        |
    |  ------------------> |  resolves from        |
    |                      |  variables            |
    |                      |                       |
    |                      |  _apply_csv_data_mode  |
    |                      |-- CsvConverter --->CSV |
    |                      |-- upload_file ---->    |
    |                      |   file_id             |
    |                      |                       |
    |                      |-- dispatch ---------->|
    |                      |   {file_id, ...}      |
    |                      |                       |  downloads CSV
    |                      |                       |  processes rows
```

**The CSV conversion is a TASK-node-level concern, not an INPUT-node concern.** The INPUT node is purely a data-collection point. The TASK node (with its `_node_definition.data_mode`) decides whether to package data as CSV.

This is intentional:
- **INPUT nodes** collect structured data from users -> stored as JSON
- **TASK nodes** package that data for workers -> converted to CSV if `data_mode` requires it
- The same INPUT data can feed multiple downstream TASK nodes, each with different data modes

---

## 3. The CSV Convention: "Business Data -> CSV for Agent/User Collaboration"

The convention works through **NodeDefinition** settings on TASK nodes:

**File**: `workflow-control-plane-service/app/layer1_domain/entities/node_definition.py`

| Setting | Values | Purpose |
|---------|--------|---------|
| `data_mode` | `"json"`, `"csv_single"`, `"csv_multi"` | How to package data for the worker |
| `edit_mode` | `"none"`, `"before_dispatch"`, `"after_dispatch"` | Whether human reviews CSV before/after dispatch |
| `locked_defaults` | `{"method":"POST", ...}` | Immutable fields excluded from CSV (passed as-is) |

### csv_single vs csv_multi

**csv_single** -- flat form fields become a 1-row CSV:
```
Form: {name: "Alice", email: "alice@ex.com"}
CSV:  name,email
      Alice,alice@ex.com
```

**csv_multi** -- first list-of-dicts field becomes multi-row CSV:
```
Form: {items: [{product:"Widget",qty:5}, {product:"Gadget",qty:3}]}
CSV:  product,qty
      Widget,5
      Gadget,3
```

### before_dispatch (Human-In-The-Loop CSV Review)

When `edit_mode: "before_dispatch"`:
1. CSV is generated and uploaded
2. Task is set to PENDING (not dispatched yet)
3. Frontend renders `CsvDataGrid` (editable spreadsheet UI)
4. User reviews, edits rows, adds/deletes rows
5. Frontend saves edited CSV -> uploads -> gets new `file_id`
6. Frontend calls `POST /resume` with `{updated_input: {file_id: "new-file-id"}}`
7. Backend dispatches task to worker with the reviewed CSV

**File**: `apps/frontend/apps/smdg-ai/src/features/node-builder/components/csv-data-grid.tsx`
- Downloads CSV via `file_id` -> parses -> renders editable table
- On save: converts rows back to CSV -> uploads -> returns new `file_id`

**File**: `apps/frontend/apps/smdg-ai/src/features/node-builder/file.service.ts`
- `downloadFile(fileId)` -> GET /api/v1/files/{fileId}
- `uploadCsvFile(blob, filename)` -> POST /api/v1/files
- `parseCsvBlob(blob)` -> {columns, rows}

---

## 4. CsvConverter Details

**File**: `workflow-control-plane-service/app/layer1_domain/services/csv_converter.py`

Three static methods:

```python
CsvConverter.single_row_to_csv({"name":"Alice","age":"30"})
# -> b"name,age\nAlice,30\n"

CsvConverter.multi_row_to_csv(
    [{"name":"Alice","age":25}, {"name":"Bob","age":30}]
)
# -> b"name,age\nAlice,25\nBob,30\n"

CsvConverter.determine_csv_input(
    resolved_input={"name":"Alice","method":"POST"},
    locked_defaults={"method":"POST"},
    data_mode="csv_single"
)
# -> (b"name\nAlice\n", "form_data.csv")
# "method" excluded from CSV (it's a locked default)
```

`determine_csv_input` is the key method -- it:
1. Separates `locked_defaults` keys from CSV-eligible fields
2. For `csv_multi`: finds first list-of-dicts value, converts to multi-row CSV
3. For `csv_single`: converts remaining fields to single-row CSV
4. Returns `(csv_bytes, filename)`

---

## 5. Variable Resolution: How Data Flows Between Nodes

**File**: `workflow-control-plane-service/app/layer1_domain/services/variable_resolver.py`

Nodes reference predecessor outputs using n8n-style expressions:

```
$input-node-1.output.name       -> "Alice Johnson"
$input-node-1.output.items[0]   -> {"product":"Widget","qty":5}
$input-node-1.output.items      -> [{...},{...}]  (full array)
$trigger.output.order_id        -> "ORD-123"
$input                          -> workflow input_data
```

A TASK node's `input_binding` maps these expressions to its resolved_input:

```json
{
  "customer_name": "{{$input-node-1.output.name}}",
  "order_items": "{{$input-node-1.output.items}}",
  "reference": "{{$trigger.output.order_id}}"
}
```

After resolution:
```json
{
  "customer_name": "Alice Johnson",
  "order_items": [{"product":"Widget","qty":5}, {"product":"Gadget","qty":3}],
  "reference": "ORD-123"
}
```

Then `_apply_csv_data_mode()` converts this to CSV if needed.

---

## 6. HUMAN_ACTION: Same Flow with Branching

HUMAN_ACTION works identically to INPUT for data collection, with two additions:

1. **Confirm/Reject branching**: The `_action` field in `updated_input` determines which downstream branch executes
2. **Instruction display**: `output_metadata.instruction` tells the user what to do

```
POST /resume with: {"updated_input": {"_action": "confirm", "comments": "Approved"}}

-> Backend: action = "confirm"
   (extracted via: action = resolved_input.pop("_action", "confirm"))
-> Confirm-branch successor nodes execute
-> Reject-branch nodes are skipped (BFS traversal of skipped nodes)

-> run.context.variables["ha-node-1"] = {
    "output": {"comments": "Approved", "_action": "confirm"},
    "metadata": {"type": "HUMAN_ACTION", "action": "confirm"}
  }
```

The branching logic (lines 200-220 of `resume_pending_task_use_case.py`):
- Iterates outgoing edges from the HUMAN_ACTION node
- For `action == "confirm"`: takes edges with condition `confirm/true/then/""`, skips `reject/false/else`
- For `action == "reject"`: vice versa
- Uses BFS (`_collect_downstream_nodes`) to skip all transitive descendants of the not-taken branch
- Only skips a downstream node if **all** its predecessors are already skipped (handles merge points correctly)

Downstream nodes can check the action:
```
{{$ha-node-1.metadata.action}}  -> "confirm"
{{$ha-node-1.output.comments}}  -> "Approved"
```

---

## 7. Summary: The Full Picture

```
+---------------------------------------------------------------------+
|                        DESIGN TIME                                  |
|                                                                     |
|  Node Builder creates:                                              |
|  |-- INPUT node with: input_schema, input_binding                   |
|  '-- TASK node with: NodeDefinition (data_mode, locked_defaults)    |
+-----------------------------+---------------------------------------+
                              | workflow saved
                              v
+---------------------------------------------------------------------+
|                        RUNTIME                                      |
|                                                                     |
|  1. INPUT node handler -> PENDING task                              |
|     output_metadata: {input_schema, prefilled}                      |
|                                                                     |
|  2. Frontend renders auto-form (Builder component)                  |
|     User fills in fields                                            |
|                                                                     |
|  3. POST /resume -> {updated_input: {form fields as JSON}}          |
|                                                                     |
|  4. Backend stores raw JSON in run.context.variables[node.id]       |
|     <- NO CSV here, this is intentional                             |
|                                                                     |
|  5. Downstream TASK node resolves input_binding from variables      |
|     -> resolved_input = {JSON fields from INPUT + other expressions}|
|                                                                     |
|  6. _apply_csv_data_mode() on the TASK node:                        |
|     IF data_mode == csv_*:                                          |
|       CsvConverter -> csv_bytes -> upload -> file_id                |
|       IF edit_mode == before_dispatch:                              |
|         PENDING -> user reviews CSV in CsvDataGrid -> resume        |
|       ELSE:                                                         |
|         dispatch to worker with file_id                             |
|     ELSE (json):                                                    |
|       dispatch to worker with raw JSON input                        |
|                                                                     |
|  7. Worker processes data, reports results                          |
|     output stored in run.context.variables[task_node.id]            |
+---------------------------------------------------------------------+
```

**Key insight**: The CSV conversion is **not** the INPUT node's responsibility. The INPUT node collects raw form data as JSON. The TASK node's `NodeDefinition.data_mode` setting determines whether that data gets packaged as CSV before going to a worker. This separation means:

- The same INPUT data can feed a JSON worker AND a CSV worker
- CSV formatting details (locked_defaults, column selection) are owned by the TASK's NodeDefinition, not the INPUT node
- The `before_dispatch` edit mode gives users a second chance to review the CSV table before it goes to the worker

---

## 8. Future Enhancement (Not Implemented Yet)

If we later want INPUT/HUMAN_ACTION nodes to **also** produce CSV at submit time (so that `run.context.variables[node.id]` includes a `file_id`), the recommended approach is:

- **Backend converts** -- frontend always submits JSON form fields, backend checks `data_mode` on the node and converts server-side
- Add `csv_converter` + `file_service_client` as optional deps to `ResumePendingTaskUseCase`
- Store both raw JSON (`output`) and `file_id` in variables, so downstream nodes can use either

This is not needed today because the TASK-node-level CSV conversion handles the agent/worker use case.
