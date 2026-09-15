# Frontend Integration Guide

Complete reference for integrating with the Workflow Control Plane API. Covers the full lifecycle from building workflows to executing them and displaying results.

---

## Table of Contents

1. [API Base URL & Response Format](#1-api-base-url--response-format)
2. [Design-Time: Building Workflows](#2-design-time-building-workflows)
3. [Execution: Running Workflows](#3-execution-running-workflows)
4. [Realtime: Socket.IO Events](#4-realtime-socketio-events)
5. [Data: Reading Node Input/Output](#5-data-reading-node-inputoutput)
6. [Data: Editing & Submitting Data](#6-data-editing--submitting-data)
7. [Expressions: Variable Binding](#7-expressions-variable-binding)
8. [Files & Artifacts](#8-files--artifacts)
9. [Worker Functions](#9-worker-functions)
10. [Complete Flow Examples](#10-complete-flow-examples)

---

## 1. API Base URL & Response Format

**Base URL:** `http://<host>:8001`

All API responses are wrapped in a standard envelope:

```json
{
  "data": { ... },
  "error": null,
  "message": "Success",
  "timestamp": "2026-03-29T10:00:00Z"
}
```

Error responses:
```json
{
  "data": null,
  "error": { "code": "NOT_FOUND", "message": "Run not found" },
  "message": "Error",
  "timestamp": "2026-03-29T10:00:00Z"
}
```

---

## 2. Design-Time: Building Workflows

### 2.1 Fetch Available Node Types

```
GET /node-palette?tenant_id=system
```

Returns three categories of nodes the user can drag onto the canvas:

```json
{
  "control_flow": [
    {
      "id": "builtin:condition",
      "name": "Condition",
      "node_type": "CONDITION",
      "worker_id": "builtin:condition",
      "icon": "GitBranch",
      "color": "#8B5CF6",
      "input_schema": [...],
      "output_schema": [...],
      "ports": { "in": [...], "out": [...] }
    }
  ],
  "worker": [
    {
      "id": "http_request_worker",
      "name": "HTTP Request",
      "node_type": "TASK",
      "worker_id": "http_request_worker",
      "input_schema": [...],
      "output_schema": [...],
      "functions": [
        { "name": "test_connection", "description": "...", "input_schema": [...] }
      ]
    }
  ],
  "business": [
    {
      "id": "nd-send-email",
      "name": "Send Email",
      "node_type": "TASK",
      "node_definition_id": "nd-send-email",
      "input_schema": [...],
      "base_worker_id": "http_request_worker"
    }
  ]
}
```

### 2.2 Create a Workflow

```
POST /workflows
```

```json
{
  "name": "Order Processing",
  "description": "Process new orders",
  "nodes": [
    {
      "id": "trigger-1",
      "name": "Start",
      "node_type": "TRIGGER",
      "x": 100, "y": 200
    },
    {
      "id": "task-1",
      "name": "Fetch Order",
      "node_type": "TASK",
      "worker_type": "http_request_worker",
      "parameters": {
        "method": "GET",
        "url": "{{$input.order_url}}"
      },
      "x": 300, "y": 200
    },
    {
      "id": "cond-1",
      "name": "Check Status",
      "node_type": "CONDITION",
      "parameters": {
        "expression": "{{$task-1.status_code}} == 200"
      },
      "x": 500, "y": 200
    }
  ],
  "edges": [
    { "source_node_id": "trigger-1", "target_node_id": "task-1" },
    { "source_node_id": "task-1", "target_node_id": "cond-1", "source_port_id": "success" },
    { "source_node_id": "cond-1", "target_node_id": "...", "condition": "then" }
  ]
}
```

Response includes `id`, `test_run_id`, and the full workflow with enriched nodes.

### 2.3 Update a Workflow

```
PUT /workflows/{workflow_id}     ← full replacement
PATCH /workflows/{workflow_id}   ← partial update (only send changed fields)
```

### 2.4 Node Definitions (Business Templates)

CRUD for reusable node templates that wrap a base worker:

```
POST   /node-definitions              ← create
GET    /node-definitions?tenant_id=x  ← list
GET    /node-definitions/{id}         ← get
PATCH  /node-definitions/{id}         ← update
DELETE /node-definitions/{id}         ← delete
```

Node definitions support inheritance via `parent_definition_id`. The backend resolves the full chain and snapshots the result into the workflow at save time.

### 2.5 Validate & Dry Run

```
POST /workflows/{workflow_id}/validate   ← DAG validation
POST /workflows/{workflow_id}/dry-run    ← simulate without dispatching
```

---

## 3. Execution: Running Workflows

### 3.1 Start a Run

```
POST /runs
```

```json
{
  "workflow_id": "wf-123",
  "input_data": {
    "order_url": "https://api.example.com/orders/456",
    "customer_id": "cust-789"
  }
}
```

Response:
```json
{
  "id": "run-abc",
  "workflow_id": "wf-123",
  "status": "RUNNING",
  "started_at": "2026-03-29T10:00:00Z"
}
```

### 3.2 Monitor Run Status

```
GET /runs/{run_id}
```

Returns `status`: `PENDING` | `RUNNING` | `COMPLETED` | `FAILED` | `CANCELLED` | `PAUSED`

### 3.3 Run Lifecycle Actions

```
POST /runs/{run_id}/cancel    ← cancel a running workflow
POST /runs/{run_id}/pause     ← pause execution
POST /runs/{run_id}/resume    ← resume paused execution
```

### 3.4 List Runs

```
GET /runs?workflow_id=wf-123&status=RUNNING,COMPLETED&offset=0&limit=20
```

### 3.5 Open a Run: Bulk Node Statuses

When the user opens a run, the canvas needs the live status of every node in
one round-trip. Use the lightweight bulk endpoint — it returns just status,
timing, a short error and `child_run_id` per node (no `resolved_input`,
`output_metadata`, or task list — fetch those on-demand via 3.6).

```
GET /runs/{run_id}/nodes/status
```

Response:
```json
{
  "run_id": "run-abc",
  "workflow_id": "wf-123",
  "status": "RUNNING",
  "started_at": "2026-03-29T10:00:00Z",
  "completed_at": null,
  "nodes": [
    {
      "node_id": "task-a",
      "node_type": "TASK",
      "status": "COMPLETED",
      "started_at": "2026-03-29T10:00:01Z",
      "completed_at": "2026-03-29T10:00:30Z",
      "error": null,
      "child_run_id": null
    },
    {
      "node_id": "subflow",
      "node_type": "WORKFLOW",
      "status": "RUNNING",
      "started_at": "2026-03-29T10:00:31Z",
      "completed_at": null,
      "error": null,
      "child_run_id": "run-child-1"
    },
    {
      "node_id": "task-b",
      "node_type": "TASK",
      "status": "NOT_STARTED",
      "started_at": null,
      "completed_at": null,
      "error": null,
      "child_run_id": null
    }
  ]
}
```

Per-node status values (the canvas can rely on this closed set):

| Status | Meaning |
|--------|---------|
| `NOT_STARTED` | Node hasn't been reached yet (no tasks, run still in flight) |
| `PENDING` | Task created and waiting to be dispatched / awaiting human input |
| `RUNNING` | At least one task is executing |
| `COMPLETED` | All tasks for the node finished successfully |
| `FAILED` | At least one task failed |
| `SKIPPED` | Node was bypassed — explicitly marked skipped, *or* the run terminated (COMPLETED / FAILED / CANCELLED) without ever reaching it |

Aggregation rules (in order):
1. Node in `skipped_nodes` → `SKIPPED`
2. No tasks for the node + run is terminal → `SKIPPED` (run ended without visiting)
3. No tasks for the node + run still in flight → `NOT_STARTED`
4. Any task `FAILED` → `FAILED`
5. All tasks `COMPLETED` → `COMPLETED`
6. Any task `RUNNING` → `RUNNING`
7. Any task `PENDING` → `PENDING`
8. Otherwise → the first task's status (e.g. `READY`, `WAITING`, `RETRYING`)

Important: a `NOT_STARTED` node is **never** displayed as `RUNNING` just because the run is `RUNNING`, nor as `FAILED` just because some other node failed. Status is per-node.

`started_at` is the earliest task start; `completed_at` is set only when the node is in a terminal state (`COMPLETED` / `FAILED` / `SKIPPED`).

### 3.6 Drill into Sub-workflows (lazy)

When a node has `child_run_id != null`, expand it lazily. Two ways:

1. **Recurse the same endpoint** with the child run id (preferred — same shape):
   ```
   GET /runs/{child_run_id}/nodes/status
   ```
2. **List all direct children** of a parent run (e.g. for a tree side-panel):
   ```
   GET /runs/{run_id}/children
   ```
   Response:
   ```json
   {
     "run_id": "run-abc",
     "children": [
       {
         "parent_node_id": "subflow",
         "child_run_id": "run-child-1",
         "workflow_id": "wf-sub",
         "status": "RUNNING",
         "started_at": "2026-03-29T10:00:31Z",
         "completed_at": null,
         "error": null
       }
     ]
   }
   ```

### 3.7 Open & Resume Flow

A complete "open a paused run, inspect, resume" sequence:

1. `GET /runs/{run_id}` — header status (PENDING/RUNNING/PAUSED/…).
2. `GET /runs/{run_id}/nodes/status` — paint the canvas.
3. Subscribe to Socket.IO room `run:{run_id}` (see §4) for live deltas — the
   bulk call above is the snapshot, Socket.IO supplies the stream.
4. For any node where `child_run_id` is non-null, repeat step 2 with that id
   when the user expands it.
5. Heavy details (input/output payloads, task list) on demand via
   `GET /runs/{run_id}/nodes/{node_id}/execution`.
6. To unblock a `PAUSED` run: `POST /runs/{run_id}/resume`.

---

## 4. Realtime: Socket.IO Events

### 4.1 Connect & Subscribe

```javascript
const socket = io("http://host:8001", { path: "/socket.io/" });

// Subscribe to a specific run
socket.emit("join:run", { run_id: "run-abc" });

// Subscribe to all runs of a workflow
socket.emit("join:workflow", { workflow_id: "wf-123" });
```

### 4.2 Event Catalog

Fetch the full event catalog programmatically:
```
GET /events/config
```

### 4.3 Key Events for Frontend

**Workflow lifecycle:**

| Event | When | Key Fields | Frontend Action |
|-------|------|-----------|-----------------|
| `run.started` | Run begins | `run_id, workflow_id` | Show "running" state |
| `run.completed` | Run finishes | `run_id, output_data` | Show results |
| `run.failed` | Run fails | `run_id, error, failed_task_id` | Show error, highlight failed node |
| `run.cancelled` | Run cancelled | `run_id, reason` | Show cancelled state |

**Node execution:**

| Event | When | Key Fields | Frontend Action |
|-------|------|-----------|-----------------|
| `task.dispatched` | Task sent to worker | `task_id, node_id, input` | Animate node as "running" |
| `task.completed` | Worker finished | `task_id, node_id` | Mark node green, fetch output |
| `task.failed` | Task failed | `task_id, node_id, error_message` | Mark node red, show error + retry button |
| `task.retrying` | Retrying | `task_id, node_id, attempt` | Show retry indicator |
| `task.rerun` | Task re-executed | `task_id, node_id, previous_status` | Reset node state |
| `edge.traversed` | Execution follows edge | `edge_id, source_node_id, target_node_id` | Animate edge |

**Human interaction:**

| Event | When | Key Fields | Frontend Action |
|-------|------|-----------|-----------------|
| `input.requested` | INPUT node waits | `task_id, node_id, input_schema, instruction` | Show input form |
| `human_action.requested` | HUMAN_ACTION waits | `task_id, node_id, instruction, input_schema, confirm_label, reject_label` | Show confirm/reject dialog |
| `input.submitted` | User submitted | `task_id, node_id` | Close form |
| `human_action.completed` | User acted | `task_id, node_id, action` | Close dialog |

**Data updates:**

| Event | When | Key Fields | Frontend Action |
|-------|------|-----------|-----------------|
| `context.updated` | Node output stored | `node_id, node_name, data_type` | Refresh data panel if node selected |
| `condition.evaluated` | Condition decided | `node_id, expression, result, branch` | Highlight taken branch |
| `loop.iteration.completed` | Loop iteration done | `node_id, iteration, total` | Update progress indicator |
| `artifact.added` | File attached to node | `node_id, file_id, name, artifact_type` | Refresh artifact list |

### 4.4 Recommended Event Handling Pattern

```javascript
// On task.completed → fetch the actual output data
socket.on("task.completed", async (event) => {
  const { run_id, node_id } = event;
  markNodeCompleted(node_id);
  // Fetch actual data (event only has status, not output)
  const execution = await fetch(`/runs/${run_id}/nodes/${node_id}/execution`);
  updateNodePanel(node_id, execution.data);
});

// On context.updated → refresh data if node is selected
socket.on("context.updated", async (event) => {
  if (selectedNodeId === event.node_id) {
    const dataInfo = await fetch(`/runs/${run_id}/nodes/${node_id}/data-info`);
    refreshDataGrid(dataInfo.data);
  }
});

// On edge.traversed → animate the edge
socket.on("edge.traversed", (event) => {
  animateEdge(event.edge_id, event.source_node_id, event.target_node_id);
});
```

---

## 5. Data: Reading Node Input/Output

### 5.1 Get All Node Outputs (Context Summary)

```
GET /runs/{run_id}/context
```

Returns a lightweight summary of every node's output:
```json
{
  "run_id": "run-abc",
  "run_status": "RUNNING",
  "entries": [
    {
      "key": "node-task-1",
      "node_id": "node-task-1",
      "node_name": "Fetch Order",
      "node_type": "TASK",
      "data_type": "file_ref",
      "file_id": "csv-001",
      "summary": {
        "file_id": "csv-001",
        "content_type": "text/csv",
        "row_count": 1,
        "columns": ["status_code", "response_body"],
        "artifact_count": 0,
        "status_code": 200
      },
      "updated_at": "2026-03-29T10:01:00Z"
    }
  ]
}
```

Use this to render status badges and quick summaries without downloading full data.

### 5.2 Get Node Execution Details

```
GET /runs/{run_id}/nodes/{node_id}/execution
```

Returns full execution history including input and output:
```json
{
  "node_id": "node-task-1",
  "node_name": "Fetch Order",
  "node_type": "TASK",
  "status": "COMPLETED",
  "input_schema": [...],
  "edit_mode": "none",
  "data_mode": "csv_single",
  "executions": [
    {
      "task_id": "task-001",
      "status": "COMPLETED",
      "resolved_input": { "file_id": "input-csv-001", "data_mode": "csv_single" },
      "output": { "uri": "output-csv-002" },
      "output_metadata": { "status_code": 200, "response_body": "..." },
      "started_at": "2026-03-29T10:00:30Z",
      "completed_at": "2026-03-29T10:00:31Z"
    }
  ],
  "context_output": {
    "__file_ref": true,
    "file_id": "output-csv-002",
    "row_count": 1,
    "columns": ["status_code", "response_body"],
    "extracted": { "status_code": { "value": 200 } },
    "artifacts": []
  },
  "input_binding": { "url": "{{$input.order_url}}" },
  "output_binding": {}
}
```

### 5.3 Get Node Schema & Data Links

```
GET /runs/{run_id}/nodes/{node_id}/data-info
```

Returns schema and OData/patch links for the data grid:
```json
{
  "node_id": "node-task-1",
  "node_name": "Fetch Order",
  "input_schema": [...],
  "output_schema": [...],
  "data_mode": "csv_single",
  "edit_mode": "none",
  "file_id": "output-csv-002",
  "links": {
    "data": "/files/output-csv-002/odata",
    "patch": "/files/output-csv-002/patch",
    "upload": "/files"
  }
}
```

### 5.4 Fetch Actual Data (OData)

```
GET /files/{file_id}/odata?$top=50&$skip=0
```

Returns paginated CSV rows as JSON:
```json
{
  "file_id": "output-csv-002",
  "total_matches": 1,
  "data": [
    { "status_code": "200", "response_body": "{\"order_id\": 456, ...}" }
  ]
}
```

### 5.5 Get Single Task Details

```
GET /runs/{run_id}/tasks/{task_id}
```

Returns `resolved_input`, `output_ref`, `output_metadata`, `status`, timestamps.

---

## 6. Data: Editing & Submitting Data

### 6.1 Edit CSV Cells (Patch)

```
POST /files/{file_id}/patch
```

```json
{
  "edits": [
    { "row_index": 0, "column": "status_code", "value": "201" }
  ],
  "new_rows": [
    { "name": "New Item", "price": "29.99" }
  ],
  "delete_row_indices": [3, 5]
}
```

Returns a **new `file_id`** (immutable files — patch creates a new version).

### 6.2 Upload a File

```
POST /files
Content-Type: multipart/form-data
file: <binary>
```

Returns `{ "file_id": "new-file-abc" }`.

### 6.3 Resume a Pending Task (INPUT / HUMAN_ACTION Node)

When the workflow pauses at an INPUT or HUMAN_ACTION node, the frontend submits user data:

```
POST /runs/{run_id}/tasks/{task_id}/resume
```

```json
{
  "updated_input": {
    "approved": true,
    "notes": "Looks good"
  }
}
```

For HUMAN_ACTION nodes, include `_action`:
```json
{
  "updated_input": {
    "_action": "confirm",
    "comments": "Approved by manager"
  }
}
```

### 6.4 Rerun a Failed/Completed Task

```
POST /runs/{run_id}/tasks/{task_id}/rerun
```

```json
{
  "new_input": {
    "url": "https://corrected-endpoint.com/api",
    "method": "POST"
  }
}
```

Or omit `new_input` to rerun with the same input.

---

## 7. Expressions: Variable Binding

### 7.1 Expression Reference API

```
GET /expressions/reference
```

Returns the complete catalog of supported expression syntax, sources, meta paths, and operators. Use this to power autocomplete in the expression editor.

### 7.2 Sources

| Source | Syntax | Available |
|--------|--------|-----------|
| Workflow input | `{{$input.field_name}}` | Always |
| Node output (by ID) | `{{$node_id.field}}` | After node completes |
| Node output (by name) | `{{$Node_Name.field}}` | After node completes |
| Trigger data | `{{$trigger.type}}` | When TRIGGER node exists |
| Loop context | `{{$loop.item}}`, `{{$loop.index}}` | Inside LOOP body |
| Custom input (mapping) | `{{$custom.field}}` | Only in `input_mapping` |
| Worker output (mapping) | `{{$_output.field}}` | Only in `output_mapping` |

### 7.3 File Reference Meta Paths

When a node's output is a CSV file reference, use `__` prefix to access metadata:

| Expression | Returns |
|-----------|---------|
| `{{$node.__file_id}}` | Primary CSV file_id |
| `{{$node.__artifacts}}` | Full artifacts array |
| `{{$node.__artifacts[0].file_id}}` | First artifact's file_id |
| `{{$node.__artifact.export.csv}}` | file_id of artifact named "export.csv" |
| `{{$node.__content_type}}` | `"text/csv"` |
| `{{$node.__row_count}}` | Number of rows |
| `{{$node.__columns}}` | Column names array |
| `{{$node.__columns[0]}}` | First column name |

### 7.4 Passing Artifacts Between Nodes

To use a previous node's artifact as input for the next node:

```json
{
  "input_binding": {
    "file_id": "{{$Extract_Data.__artifacts[0].file_id}}"
  }
}
```

Or by artifact name:
```json
{
  "input_binding": {
    "file_id": "{{$Extract_Data.__artifact.cleaned_data.csv}}"
  }
}
```

### 7.5 Variable Suggestions (Context-Aware)

```
GET /runs/{run_id}/nodes/{node_id}/variable-suggestions
```

Returns variables available to this specific node based on what predecessors have completed.

### 7.6 Condition Expressions

Used in CONDITION and SWITCH nodes:

```
{{$node.status_code}} == 200
{{$node.name}} CONTAINS 'test'
{{$node.count}} > 10 AND {{$node.status}} != 'error'
```

Operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `IN`, `CONTAINS`
Logical: `AND`, `OR`

---

## 8. Files & Artifacts

### 8.1 File Lifecycle

```
Upload:   POST /files (multipart) → { file_id }
Download: GET /files/{file_id} → raw bytes
Query:    GET /files/{file_id}/odata → paginated JSON rows
Edit:     POST /files/{file_id}/patch → { new_file_id }
```

### 8.2 Node Artifacts

List artifacts attached to a node:
```
GET /runs/{run_id}/nodes/{node_id}/artifacts
```

```json
{
  "primary_file_id": "csv-001",
  "artifacts": [
    {
      "file_id": "export-002",
      "name": "summary.csv",
      "content_type": "text/csv",
      "size_bytes": 1024,
      "artifact_type": "export",
      "download_link": "/files/export-002"
    }
  ]
}
```

Add an artifact:
```
POST /runs/{run_id}/nodes/{node_id}/artifacts
```

```json
{
  "file_id": "uploaded-file-003",
  "name": "manual_data.csv",
  "content_type": "text/csv",
  "artifact_type": "data"
}
```

### 8.3 How Workers Produce Artifacts

Workers include `_artifacts` in their output. The control plane extracts and stores them:
```json
// Worker output_metadata:
{
  "status_code": 200,
  "data": [...],
  "_artifacts": [
    { "file_id": "worker-file-001", "name": "export.csv", "artifact_type": "export" }
  ]
}
```

---

## 9. Worker Functions

Workers can expose callable functions for design-time and runtime use.

### 9.1 Discover Functions

```
GET /workers/{worker_type}/functions
```

```json
[
  {
    "name": "test_connection",
    "description": "Test connectivity to a URL",
    "input_schema": [{ "key": "url", "type": "string", "required": true }],
    "output_schema": [{ "key": "reachable", "type": "boolean" }]
  }
]
```

### 9.2 Invoke a Function

```
POST /workers/{worker_type}/functions/{function_name}/invoke
```

```json
{ "params": { "url": "https://api.example.com" } }
```

Response:
```json
{ "status": "success", "result": { "reachable": true, "latency_ms": 45.2 } }
```

### 9.3 Dynamic Schema Source (Node Definition)

Node definitions can declare fields that fetch options from worker functions:

```json
{
  "key": "table_name",
  "type": "string",
  "ui_component": "dropdown",
  "dynamic_source": {
    "worker_function": "list_tables",
    "params": { "connection_id": "{{$locked.connection_id}}" },
    "value_path": "tables[*].name",
    "label_path": "tables[*].name",
    "depends_on": ["connection_id"],
    "cache_ttl_seconds": 300
  }
}
```

Frontend flow:
1. Detect `dynamic_source` on a schema field
2. Resolve `params` (substitute `{{$locked.*}}` from the node definition's `locked_defaults`)
3. Call `POST /workers/{worker_type}/functions/{function_name}/invoke`
4. Map response using `value_path`/`label_path` to populate dropdown
5. When a `depends_on` field changes, re-fetch

---

## 10. Complete Flow Examples

### 10.1 Full Workflow Execution (Happy Path)

```
Frontend                          Control Plane                    Worker
   │                                   │                              │
   ├── POST /runs ────────────────────►│                              │
   │   {workflow_id, input_data}       │                              │
   │                                   │── resolve entry nodes ──────►│
   │◄── {run_id, status: RUNNING} ────│                              │
   │                                   │                              │
   │  join Socket.IO room: run:{id}    │                              │
   │                                   │                              │
   │◄── edge.traversed ──────────────│  (trigger → task-1)          │
   │◄── task.dispatched ─────────────│──── gRPC Execute ──────────►│
   │    {node_id, input}               │                              │
   │                                   │                              │
   │                                   │◄── ExecuteResponse ─────────│
   │◄── task.completed ──────────────│                              │
   │◄── context.updated ────────────│                              │
   │◄── edge.traversed ──────────────│  (task-1 → cond-1)          │
   │◄── condition.evaluated ─────────│  (branch: "then")           │
   │◄── edge.traversed ──────────────│  (cond-1 → task-2)          │
   │◄── task.dispatched ─────────────│──── gRPC Execute ──────────►│
   │                                   │◄── ExecuteResponse ─────────│
   │◄── task.completed ──────────────│                              │
   │◄── context.updated ────────────│                              │
   │◄── run.completed ──────────────│                              │
   │    {output_data}                  │                              │
   │                                   │                              │
   ├── GET /runs/{id}/context ────────►│                              │
   │◄── {entries: [...]} ─────────────│                              │
   │                                   │                              │
   ├── GET /files/{id}/odata ─────────►│                              │
   │◄── {data: [...]} ────────────────│                              │
```

### 10.2 Human Interaction Flow (INPUT Node)

```
Frontend                          Control Plane
   │                                   │
   │◄── input.requested ──────────────│
   │    {task_id, node_id,             │
   │     input_schema, instruction}    │
   │                                   │
   │  Render form from input_schema    │
   │  User fills in data...            │
   │                                   │
   ├── POST /runs/{id}/tasks/{id}/resume ──►│
   │   {updated_input: {field1: "val"}}      │
   │                                         │
   │◄── input.submitted ───────────────────│
   │◄── edge.traversed ────────────────────│  (input → next_node)
   │◄── task.dispatched ───────────────────│  (next node starts)
```

### 10.3 Failed Task → Retry Flow

```
Frontend                          Control Plane
   │                                   │
   │◄── task.failed ──────────────────│
   │    {task_id, node_id,             │
   │     error_code, error_message}    │
   │                                   │
   │  Show error + "Rerun" button      │
   │  User clicks Rerun...            │
   │                                   │
   ├── POST /runs/{id}/tasks/{id}/rerun ──►│
   │   {new_input: {url: "fixed-url"}}       │
   │                                         │
   │◄── task.rerun ────────────────────────│
   │◄── task.dispatched ───────────────────│
   │    ... continues ...                    │
```

### 10.4 Data Display Flow (Node Selected)

```
Frontend                          Control Plane
   │                                   │
   │  User clicks on node-task-1       │
   │                                   │
   ├── GET /runs/{id}/nodes/{nid}/data-info ──►│
   │◄── {file_id, links, input_schema,         │
   │     data_mode, edit_mode}                  │
   │                                            │
   ├── GET /files/{file_id}/odata?$top=50 ────►│
   │◄── {data: [{col1: "v1", col2: "v2"}, ...]}│
   │                                            │
   │  Render data grid                          │
   │                                            │
   ├── GET /runs/{id}/nodes/{nid}/artifacts ──►│
   │◄── {artifacts: [{name, download_link}]}    │
   │                                            │
   │  Render artifact list with download links  │
```

### 10.5 Dynamic Form with Worker Functions

```
Frontend                          Control Plane         Worker
   │                                   │                   │
   │  Render node definition form      │                   │
   │  Detect dynamic_source on field   │                   │
   │                                   │                   │
   ├── POST /workers/{type}/functions/{name}/invoke ──►│──►│
   │   {params: {connection_id: "..."}}                 │   │
   │◄── {status: "success", result: {tables: [...]}} ──│◄──│
   │                                   │                   │
   │  Populate dropdown with tables    │                   │
   │  User selects a table...          │                   │
   │  depends_on triggers re-fetch     │                   │
   │                                   │                   │
   ├── POST /workers/{type}/functions/get_columns/invoke ►│
   │   {params: {table_name: "orders"}}                    │
   │◄── {result: {columns: [...]}} ────────────────────────│
   │                                   │                   │
   │  Populate column dropdown         │                   │
```

---

## API Quick Reference

| Category | Endpoint | Method | Purpose |
|----------|----------|--------|---------|
| **Workflow** | `/workflows` | POST | Create workflow |
| | `/workflows` | GET | List workflows |
| | `/workflows/{id}` | GET | Get workflow |
| | `/workflows/{id}` | PUT | Replace workflow |
| | `/workflows/{id}` | PATCH | Patch workflow |
| | `/workflows/{id}` | DELETE | Delete workflow |
| | `/workflows/{id}/validate` | POST | Validate DAG |
| | `/workflows/{id}/dry-run` | POST | Dry run |
| | `/workflows/{id}/nodes/{nid}/test` | POST | Test single node |
| | `/workflows/{id}/test/full` | POST | Test full workflow |
| | `/workflows/{id}/test/reset` | POST | Reset test run |
| **Run** | `/runs` | POST | Start run |
| | `/runs` | GET | List runs (filterable) |
| | `/runs/{id}` | GET | Get run status |
| | `/runs/{id}/cancel` | POST | Cancel |
| | `/runs/{id}/pause` | POST | Pause |
| | `/runs/{id}/resume` | POST | Resume |
| | `/runs/{id}/nodes/status` | GET | **Bulk per-node status (lightweight, for canvas)** |
| | `/runs/{id}/children` | GET | **List sub-workflow runs** |
| | `/runs/{id}/context` | GET | All node outputs summary |
| | `/runs/{id}/nodes/{nid}/execution` | GET | Node execution history |
| | `/runs/{id}/nodes/{nid}/data-info` | GET | Schema + data links |
| | `/runs/{id}/nodes/{nid}/variable-suggestions` | GET | Available variables |
| | `/runs/{id}/nodes/{nid}/artifacts` | GET | List artifacts |
| | `/runs/{id}/nodes/{nid}/artifacts` | POST | Add artifact |
| | `/runs/{id}/tasks/{tid}` | GET | Task details |
| | `/runs/{id}/tasks/{tid}/complete` | POST | Complete task |
| | `/runs/{id}/tasks/{tid}/fail` | POST | Fail task |
| | `/runs/{id}/tasks/{tid}/rerun` | POST | Rerun task |
| | `/runs/{id}/tasks/{tid}/resume` | POST | Resume pending task |
| **File** | `/files` | POST | Upload file |
| | `/files/{id}` | GET | Download file |
| | `/files/{id}/odata` | GET | Query CSV data |
| | `/files/{id}/patch` | POST | Edit CSV cells |
| **Config** | `/node-palette` | GET | Available node types |
| | `/node-definitions` | CRUD | Node definition templates |
| | `/events/config` | GET | Socket.IO event catalog |
| | `/expressions/reference` | GET | Expression syntax reference |
| | `/workers/{type}/functions` | GET | Worker functions |
| | `/workers/{type}/functions/{name}/invoke` | POST | Call worker function |
| | `/health` | GET | Health check |
