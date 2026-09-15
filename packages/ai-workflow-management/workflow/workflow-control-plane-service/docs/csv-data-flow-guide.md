# CSV Data Flow Guide

All node input and output data is stored as **CSV files** in the file service. The run context holds only lightweight **file reference** dicts with an extracted cache. This document traces the exact data transformations at every boundary.

---

## Architecture Overview

```
                        FILE SERVICE
                       ┌─────────────┐
                       │  CSV files   │
                       │  (upload /   │
                       │   download)  │
                       └──────┬───────┘
                              │
  ┌───────────────────────────┼───────────────────────────┐
  │  WORKFLOW CONTROL PLANE   │                           │
  │                           │                           │
  │  ┌──────────┐  store_data │  materialize  ┌────────┐  │
  │  │DataStore │◄────────────┤──────────────►│Variable│  │
  │  │Service   │  (dict→CSV  │  (CSV→dict)   │Resolver│  │
  │  └──────────┘   →upload)  │               └────────┘  │
  │       │                   │                    │       │
  │  ┌────▼────┐         ┌───▼───┐          ┌─────▼─────┐ │
  │  │RunContext│         │  Task │          │   Task    │ │
  │  │variables │         │Handler│          │ Dispatch  │ │
  │  │(file refs)         │(input)│          │(file_id)  │ │
  │  └─────────┘         └───────┘          └───────────┘ │
  └───────────────────────┬───────────────────────────────┘
                          │ HTTP POST
                          ▼
  ┌───────────────────────────────┐
  │  WORKER EXECUTOR SERVICE      │
  │  Forwards {file_id, data_mode}│
  └───────────────┬───────────────┘
                  │ HTTP POST
                  ▼
  ┌───────────────────────────────┐
  │  WORKER                       │
  │  Receives {file_id, data_mode}│
  │  Downloads CSV from file svc  │
  │  Returns outputs dict         │
  └───────────────────────────────┘
```

---

## Phase 1: Workflow Input → CSV File

When a run starts, the workflow input is immediately stored as a CSV file.

### Code Path

```
StartRunUseCase.execute()
  → DataStoreService.store_data("input", input_data)
    → CsvConverter.dict_to_csv(input_data)          # flatten + CSV encode
    → IFileServiceClient.upload_file(csv_bytes)      # upload to file service
    → build_file_ref(file_id, extracted={...})       # build reference
  → run.context.variables["input"] = file_ref
```

### Example

**User starts run with:**
```json
{
  "url": "https://api.example.com/users",
  "method": "GET",
  "timeout_seconds": 30
}
```

**CSV file created (uploaded to file service):**
```csv
url,method,timeout_seconds
https://api.example.com/users,GET,30
```

**Stored in `run.context.variables["input"]`:**
```json
{
  "__file_ref": true,
  "file_id": "f-abc-001",
  "content_type": "text/csv",
  "row_count": 1,
  "columns": ["url", "method", "timeout_seconds"],
  "extracted": {
    "url": {"value": "https://api.example.com/users"},
    "method": {"value": "GET"},
    "timeout_seconds": {"value": 30}
  },
  "artifacts": [
    {"file_id": "...", "name": "...", "content_type": "text/csv", "artifact_type": "export", "size_bytes": 0, "created_at": "..."}
  ]
}
```

---

## Phase 2: Input Resolution (File Ref → Scalar Values)

When a TASK node needs input, the `VariableResolver` resolves expressions against the file-backed context. The `FileBackedResolverStrategy` handles file references transparently.

### Code Path

```
TaskNodeHandler.handle()
  → VariableResolver.resolve_mapping(node.input_binding, run.context.variables)
    → For each expression like "{{$input.url}}":
      → _resolve_single(parsed, context)
        → context["input"] = file_ref dict
        → FileBackedResolverStrategy.try_resolve(file_ref, ("url",))
          → Check extracted cache: extracted["url"] = {"value": "https://..."}
          → Cache HIT → return "https://api.example.com/users"
```

### Resolution Strategy Chain

```
1. FileBackedResolverStrategy  ← Handles __file_ref dicts
   - If source_data has __file_ref: true
     - Check extracted cache first (dot-joined path key)
     - On cache miss: materialize CSV → traverse → cache result
   - Otherwise: return UNRESOLVED → next strategy

2. MetadataResolverStrategy    ← Handles metadata.extracted patterns
   - For legacy data with extracted metadata cache
   - Otherwise: return UNRESOLVED → next strategy

3. TraversalResolverStrategy   ← Handles plain dicts/lists
   - Walk source_data by key/index
   - Raises VariableResolutionError if path not found
```

### Example: Cache Hit (fast, no file download)

```
Expression: "{{$input.url}}"
Source: context["input"] = {__file_ref: true, extracted: {"url": {"value": "https://..."}}}

FileBackedResolverStrategy:
  → is_file_ref? YES
  → cache_key = "url"
  → extracted["url"] = {"value": "https://api.example.com/users"}
  → return "https://api.example.com/users"

Result: "https://api.example.com/users"
```

### Example: Cache Miss (downloads CSV)

```
Expression: "{{$api_call.response_body.users[0].name}}"
Source: context["api_call"] = {__file_ref: true, file_id: "f-abc-002", extracted: {...}}

FileBackedResolverStrategy:
  → is_file_ref? YES
  → cache_key = "response_body.users.0.name"
  → extracted["response_body.users.0.name"] → NOT FOUND
  → materialize: download CSV f-abc-002 → parse → reconstruct dict
  → traverse: data["response_body"]["users"][0]["name"] = "Alice"
  → update cache: extracted["response_body.users.0.name"] = {"value": "Alice"}
  → return "Alice"
```

---

## Phase 3: Resolved Input → CSV → Worker Dispatch

After input expressions are resolved to concrete values, the resolved input dict is converted to a CSV file before dispatch.

### Code Path

```
OrchestrationEngine._exec_uniform()  (dispatched branch for TASK nodes)
  → TaskNodeHandler.handle() → resolves input_binding → resolved_input dict
  → _apply_node_definition() → merges locked_defaults + input_mapping
  → _apply_csv_data_mode() → handles csv_single/csv_multi nodes
  → DataStoreService.store_data(task_id + "-input", resolved_input)
    → CSV upload → file_ref
  → task.resolved_input = {"file_id": "f-abc-003", "data_mode": "csv_single"}
  → TaskDispatched event published with task.resolved_input
```

### Example

**Node `google-search-1` has input_binding:**
```json
{
  "url": "{{$input.url}}",
  "method": "{{$input.method}}",
  "timeout_seconds": "{{$input.timeout_seconds}}"
}
```

**After resolution:**
```json
{
  "url": "https://api.example.com/users",
  "method": "GET",
  "timeout_seconds": 30
}
```

**After CSV conversion, `task.resolved_input` becomes:**
```json
{
  "file_id": "f-abc-003",
  "data_mode": "csv_single"
}
```

**CSV file `f-abc-003` contains:**
```csv
url,method,timeout_seconds
https://api.example.com/users,GET,30
```

---

## Phase 4: Control Plane → Executor → Worker (HTTP Boundaries)

### Boundary 1: Control Plane → Worker Executor

**HTTP POST** `{WORKER_EXECUTOR_URL}/api/v1/tasks/execute`

```json
{
  "task_id": "run-001-google-search-1",
  "run_id": "run-001",
  "node_id": "google-search-1",
  "worker_type": "http-request-worker",
  "input": {
    "file_id": "f-abc-003",
    "data_mode": "csv_single"
  },
  "node_config": {
    "_node_definition": { "name": "Google Search", ... },
    "_node_internals": {
      "locked_defaults": { "auth_type": "none" },
      "output_mapping": { ... }
    }
  },
  "idempotency_key": "run-001-google-search-1-attempt-0"
}
```

**Key:** The `input` field contains `file_id` + `data_mode`, NOT the raw data.

### Boundary 2: Worker Executor → Worker

**HTTP POST** `{WORKER_URL}/api/v1/execute-async`

```json
{
  "task_id": "run-001-google-search-1",
  "action": "execute",
  "inputs": {
    "file_id": "f-abc-003",
    "data_mode": "csv_single"
  },
  "parameters": {
    "_node_definition": { ... },
    "_node_internals": { ... }
  },
  "correlation_id": "run-001",
  "callback_url": "http://executor:8004/api/v1/tasks/callback"
}
```

**Key:** Field name changes from `input` to `inputs`. Content is the same `{file_id, data_mode}`.

### Worker Receives

The worker receives `inputs.file_id` and `inputs.data_mode`. It downloads the CSV from the file service, parses the data, and executes its logic.

```python
# Inside worker execute():
inputs = request.inputs
file_id = inputs.get("file_id")
data_mode = inputs.get("data_mode")

if file_id:
    csv_bytes = file_service_client.download_file(file_id)
    # Parse CSV → get original fields
    # e.g., {"url": "https://...", "method": "GET", "timeout_seconds": 30}
```

---

## Phase 5: Worker Output → Executor → Control Plane

### Worker Returns Output

The worker executes its logic and returns results as a dict.

**HTTP-Request-Worker example output:**
```json
{
  "status_code": 200,
  "response_headers": {"content-type": "application/json"},
  "response_body": {"users": [{"id": 1, "name": "Alice"}]},
  "content_type": "application/json",
  "elapsed_ms": 156.2,
  "is_success": true,
  "final_url": "https://api.example.com/users"
}
```

### Boundary 3: Worker → Executor (Async Callback)

**HTTP POST** `{CALLBACK_URL}/api/v1/tasks/callback`

```json
{
  "task_id": "run-001-google-search-1",
  "status": "success",
  "outputs": {
    "status_code": 200,
    "response_headers": {"content-type": "application/json"},
    "response_body": {"users": [{"id": 1, "name": "Alice"}]},
    "content_type": "application/json",
    "elapsed_ms": 156.2,
    "is_success": true,
    "final_url": "https://api.example.com/users"
  },
  "error": null,
  "duration_ms": 165.8,
  "output_reference": ""
}
```

### Boundary 4: Executor → Control Plane

**HTTP POST** `{WCP_URL}/runs/run-001/tasks/run-001-google-search-1/complete`

```json
{
  "status": "COMPLETED",
  "output_reference": "",
  "output_metadata": {
    "status_code": 200,
    "response_headers": {"content-type": "application/json"},
    "response_body": {"users": [{"id": 1, "name": "Alice"}]},
    "content_type": "application/json",
    "elapsed_ms": 156.2,
    "is_success": true,
    "final_url": "https://api.example.com/users"
  }
}
```

**Key:** Field name changes from `outputs` to `output_metadata`. Content is the same worker output dict.

---

## Phase 6: Output → CSV File → Context Storage

The control plane receives the worker output and stores it as a CSV file.

### Code Path

```
CompleteTaskUseCase._complete_task()
  → Build task_output envelope:
      task_output = {
        "output": {"uri": output_reference},
        "metadata": output_metadata,
        status_code: 200,              # spread from output_metadata
        response_body: {...},          # spread from output_metadata
        ...
      }
  → Apply output_mapping (if node definition defines one)
  → Apply output_binding (if workflow node defines one)
  → Extract `_artifacts` from output_metadata and hold aside before CSV conversion
  → DataStoreService.store_data(node_id, task_output)
    → CsvConverter.dict_to_csv(task_output)   # flatten nested dict
    → Upload CSV → file_id
    → Build file_ref with extracted cache
  → Attach held `_artifacts` array to the file reference after storage
  → run.context.variables[node_id] = file_ref
  → run.context.variables[node_name] = file_ref  (alias)
```

### Example

**task_output before CSV conversion:**
```json
{
  "output": {"uri": ""},
  "metadata": {
    "status_code": 200,
    "response_body": {"users": [{"id": 1, "name": "Alice"}]},
    "is_success": true
  },
  "status_code": 200,
  "response_body": {"users": [{"id": 1, "name": "Alice"}]},
  "is_success": true,
  "elapsed_ms": 156.2,
  "content_type": "application/json",
  "final_url": "https://api.example.com/users",
  "response_headers": {"content-type": "application/json"}
}
```

**CSV file created (flattened with dot-notation):**
```csv
output.uri,metadata.status_code,metadata.response_body,metadata.is_success,status_code,response_body,is_success,elapsed_ms,content_type,final_url,response_headers
,"200","[{""id"": 1, ""name"": ""Alice""}]",True,200,"[{""id"": 1, ""name"": ""Alice""}]",True,156.2,application/json,https://api.example.com/users,"{""content-type"": ""application/json""}"
```

**Stored in `run.context.variables["google-search-1"]`:**
```json
{
  "__file_ref": true,
  "file_id": "f-abc-004",
  "content_type": "text/csv",
  "row_count": 1,
  "columns": [
    "output.uri", "metadata.status_code", "metadata.response_body",
    "metadata.is_success", "status_code", "response_body",
    "is_success", "elapsed_ms", "content_type", "final_url",
    "response_headers"
  ],
  "extracted": {
    "output.uri": {"value": ""},
    "metadata.status_code": {"value": 200},
    "metadata.response_body": {"value": [{"id": 1, "name": "Alice"}]},
    "metadata.is_success": {"value": true},
    "status_code": {"value": 200},
    "response_body": {"value": [{"id": 1, "name": "Alice"}]},
    "is_success": {"value": true},
    "elapsed_ms": {"value": 156.2},
    "content_type": {"value": "application/json"},
    "final_url": {"value": "https://api.example.com/users"},
    "response_headers": {"value": {"content-type": "application/json"}}
  }
}
```

---

## Phase 7: Downstream Node Reads Upstream Output

A downstream node's `input_binding` references the upstream node's output. The resolver reads from the extracted cache.

### Example

**Downstream node `transform-1` has input_binding:**
```json
{
  "users": "{{$google-search-1.response_body}}",
  "total_count": "{{$google-search-1.status_code}}"
}
```

**Resolution:**
```
"{{$google-search-1.response_body}}"
  → context["google-search-1"] = file_ref
  → FileBackedResolverStrategy checks extracted["response_body"]
  → Cache HIT: {"value": [{"id": 1, "name": "Alice"}]}
  → Returns: [{"id": 1, "name": "Alice"}]

"{{$google-search-1.status_code}}"
  → extracted["status_code"] = {"value": 200}
  → Returns: 200
```

**resolved_input for transform-1:**
```json
{
  "users": [{"id": 1, "name": "Alice"}],
  "total_count": 200
}
```

**This is then converted to CSV and dispatched as `{file_id, data_mode}`.**

---

## Complete End-to-End Trace

```
USER STARTS RUN
  input_data: {"url": "https://api.example.com/users", "method": "GET"}
  │
  ▼ store_data("input", input_data)
  CSV upload → file_id: "f-001"
  context.variables["input"] = {__file_ref: true, file_id: "f-001", extracted: {url: ..., method: ...}}
  │
  ▼ TaskNodeHandler resolves input_binding
  input_binding: {"url": "{{$input.url}}", "method": "{{$input.method}}"}
  FileBackedResolver → cache hit → resolved: {"url": "https://...", "method": "GET"}
  │
  ▼ store_data("task-input", resolved_input)
  CSV upload → file_id: "f-002"
  task.resolved_input = {"file_id": "f-002", "data_mode": "csv_single"}
  │
  ▼ Dispatch to executor
  POST /tasks/execute  →  input: {"file_id": "f-002", "data_mode": "csv_single"}
  │
  ▼ Executor forwards to worker
  POST /execute-async  →  inputs: {"file_id": "f-002", "data_mode": "csv_single"}
  │
  ▼ Worker downloads CSV, executes HTTP request
  Worker returns: {status_code: 200, response_body: {...}, ...}
  │
  ▼ Callback to executor
  POST /tasks/callback  →  outputs: {status_code: 200, response_body: {...}, ...}
  │
  ▼ Executor reports to control plane
  POST /runs/{id}/tasks/{id}/complete  →  output_metadata: {status_code: 200, ...}
  │
  ▼ CompleteTaskUseCase builds task_output, applies output_mapping
  task_output = {output: {uri: ""}, metadata: {...}, status_code: 200, response_body: {...}, ...}
  │
  ▼ store_data(node_id, task_output)
  CSV upload → file_id: "f-003"
  context.variables["google-search-1"] = {__file_ref: true, file_id: "f-003", extracted: {...}}
  │
  ▼ Successor node resolves expressions
  "{{$google-search-1.status_code}}" → cache hit → 200
  "{{$google-search-1.response_body}}" → cache hit → {...}
  │
  ▼ Next task dispatched with its own CSV...
```

---

## Field Name Reference

| Boundary | Direction | Field Name | Content |
|----------|-----------|------------|---------|
| Control Plane → Executor | HTTP POST `/tasks/execute` | `input` | `{file_id, data_mode}` |
| Executor → Worker | HTTP POST `/execute-async` | `inputs` | `{file_id, data_mode}` |
| Worker → Executor | HTTP POST callback | `outputs` | Worker result dict |
| Executor → Control Plane | HTTP POST `/tasks/{id}/complete` | `output_metadata` | Worker result dict |
| Context Storage | run.context.variables | `[node_id]` | `{__file_ref, file_id, extracted}` |

---

## Nested Dict Flattening Rules

When a dict is converted to CSV, nested keys are flattened with dot notation:

| Original | CSV Column Name |
|----------|-----------------|
| `{"status_code": 200}` | `status_code` |
| `{"output": {"uri": "file-1"}}` | `output.uri` |
| `{"metadata": {"type": "task"}}` | `metadata.type` |
| `{"tags": ["a", "b"]}` | `tags` (JSON-encoded: `["a", "b"]`) |
| `{"a": {"b": {"c": 1}}}` | `a.b.c` |

When CSV is parsed back, dot-notation columns are unflattened:

| CSV Column | Reconstructed |
|------------|---------------|
| `output.result` = `42` | `{"output": {"result": 42}}` |
| `tags` = `["a", "b"]` | `{"tags": ["a", "b"]}` |
| `active` = `True` | `{"active": true}` |
| `count` = `5` | `{"count": 5}` |

---

## Extracted Cache Format

The `extracted` dict on each file reference maps dot-notation paths to cached values:

```json
{
  "extracted": {
    "status_code": {"value": 200},
    "output.uri": {"value": ""},
    "response_body": {"value": [{"id": 1, "name": "Alice"}]},
    "metadata.type": {"value": "task"}
  }
}
```

The resolver builds a cache key by joining path parts with dots:
- Expression `{{$node.status_code}}` → cache key `"status_code"`
- Expression `{{$node.output.uri}}` → cache key `"output.uri"`
- Expression `{{$node.metadata.type}}` → cache key `"metadata.type"`

Cache hits avoid downloading the CSV file entirely.

`artifact_count` is included in the context summary for file-backed entries, reflecting the number of artifacts attached to the file reference.

---

## Builtin Node Handling

Builtin nodes (COMPUTE, CONDITION, LOOP, etc.) execute inline in the control plane. Their outputs are also stored as CSV files.

### COMPUTE Example

```
Node expression: "{{$google-search-1.status_code}} * 2"

Resolution:
  → FileBackedResolver: extracted["status_code"] = {"value": 200}
  → Expression becomes: "200 * 2"
  → Safe eval: 400

State update stored as CSV:
  {"output": {"result": 400}, "metadata": {"type": "compute"}}
  → CSV upload → file_ref
  → context.variables["compute-1"] = {__file_ref: true, file_id: "f-005", extracted: {"output.result": {"value": 400}}}
```

### CONDITION Example

```
Condition: "{{$google-search-1.is_success}} == true"

Resolution:
  → FileBackedResolver: extracted["is_success"] = {"value": true}
  → Evaluates: true == true → true
  → Routes to "then" branch
```

---

## Artifact Endpoints

Artifacts attached to file references can be listed and added via dedicated REST endpoints:

- **`GET /runs/{run_id}/nodes/{node_id}/artifacts`** — List all artifacts associated with the node's file reference.
- **`POST /runs/{run_id}/nodes/{node_id}/artifacts`** — Add a new artifact to the node's file reference.

These endpoints operate on the `artifacts` array within the stored file reference for the given node.

---

### LOOP Example

```
items_expression: "{{$google-search-1.response_body}}"

Resolution:
  → FileBackedResolver: extracted["response_body"] = {"value": [{"id": 1}, {"id": 2}]}
  → Returns array: [{"id": 1}, {"id": 2}]
  → Loop iterates with: {index: 0, item: {"id": 1}, total: 2}
```
