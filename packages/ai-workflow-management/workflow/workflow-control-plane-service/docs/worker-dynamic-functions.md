# Worker Dynamic Functions — Design Document

## Problem

Today workers are **single-purpose executors**: they register with one capability (`action: "execute"`), receive a task, and return a result. All configuration schemas (`input_schema`, `output_schema`) are declared statically at registration time.

This creates two gaps:

1. **Design-time gap**: The workflow builder UI cannot populate dynamic dropdowns, validate connections, or preview data. For example, a Database Worker cannot expose `list_tables()` or `get_columns(table)` for the frontend to build a form dynamically.

2. **Runtime gap**: Node definitions use `input_mapping` with expressions like `{{$custom.field}}`, but they cannot call worker functions to transform, validate, or enrich data mid-execution. The node definition resolver is purely structural — it merges schemas and defaults but cannot invoke worker logic.

## Current Architecture (Baseline)

```
Worker Registration:
  worker_type: "database_worker"
  capabilities: [{domain: "database_worker", action: "execute"}]   <-- single action
  input_schema: {...static schema...}
  output_schema: {...static schema...}

Execution:
  Control Plane → TaskDispatched → Worker Executor → Worker.execute(input) → result
```

Node definitions support nested inheritance (parent → child chains resolved by `NodeDefinitionResolver`), but the resolved `input_schema`, `locked_defaults`, and mappings are all static values set at definition time.

## Proposed Design: Worker Functions

### Concept

Workers declare **named functions** alongside their existing `execute` capability. Each function has its own input/output schema and can be invoked independently of task execution.

```
Worker Registration:
  worker_type: "database_worker"
  capabilities: [{domain: "database_worker", action: "execute"}]
  functions:
    - name: "list_tables"
      description: "List available database tables"
      input_schema: {connection_id: string}
      output_schema: {tables: [{name: string, row_count: int}]}

    - name: "get_columns"
      description: "Get columns for a specific table"
      input_schema: {connection_id: string, table_name: string}
      output_schema: {columns: [{name: string, type: string, nullable: bool}]}

    - name: "preview_query"
      description: "Preview query results (limited rows)"
      input_schema: {connection_id: string, query: string, limit: int}
      output_schema: {rows: [...], columns: [...], total_count: int}
```

### Call Flow

```
                    Design-time (UI building workflow)
                    ┌─────────────────────────────────────┐
                    │                                     │
Frontend ──REST──> Control Plane ──gRPC──> Worker Executor ──gRPC──> Worker
  GET /workers/{worker_type}/          InvokeFunction()        InvokeFunction()
      functions/{name}?params=...

                    Runtime (during workflow execution)
                    ┌─────────────────────────────────────┐
                    │                                     │
Node Definition ──> Control Plane (enrichment / mapping)
  input_schema uses   calls worker function to resolve
  dynamic_source:     dropdown options or validate input
    worker_function: "list_tables"
    params: {connection_id: "{{$locked.connection_id}}"}
```

## Implementation Layers

### Layer 1: Worker SDK — Function Declaration & Execution

#### 1.1 Function Registry (Worker Side)

Workers declare functions in their bootstrap/configuration:

```python
# In worker's bootstrap.py
from worker_sdk import WorkerFunction

functions = [
    WorkerFunction(
        name="list_tables",
        description="List available database tables",
        input_schema=[{"key": "connection_id", "type": "string", "required": True}],
        output_schema=[{"key": "tables", "type": "array"}],
        handler=list_tables_handler,   # async callable
    ),
    WorkerFunction(
        name="get_columns",
        description="Get columns for a table",
        input_schema=[
            {"key": "connection_id", "type": "string", "required": True},
            {"key": "table_name", "type": "string", "required": True},
        ],
        output_schema=[{"key": "columns", "type": "array"}],
        handler=get_columns_handler,
    ),
]
```

#### 1.2 Registration Extension

The `WorkerRegistration` entity gains a `functions` field:

```python
@dataclass
class WorkerFunctionDefinition:
    name: str
    description: str = ""
    input_schema: list[dict] = field(default_factory=list)
    output_schema: list[dict] = field(default_factory=list)

@dataclass
class WorkerRegistration:
    # ... existing fields ...
    functions: list[WorkerFunctionDefinition] = field(default_factory=list)
```

#### 1.3 gRPC Service Extension

Add `InvokeFunction` to `WorkerExecutionService`:

```protobuf
// worker_execution.proto
service WorkerExecutionService {
    rpc Execute(ExecuteRequest) returns (ExecuteResponse);
    rpc ExecuteWithProgress(ExecuteRequest) returns (stream ExecutionEvent);
    rpc GetInfo(GetInfoRequest) returns (WorkerInfoResponse);
    rpc InvokeFunction(InvokeFunctionRequest) returns (InvokeFunctionResponse);  // NEW
}

message InvokeFunctionRequest {
    string function_name = 1;
    google.protobuf.Struct params = 2;
}

message InvokeFunctionResponse {
    string status = 1;              // "success" | "error"
    google.protobuf.Struct result = 2;
    string error = 3;
}
```

The worker SDK servicer routes to the registered handler:

```python
async def InvokeFunction(self, request, context):
    handler = self._function_registry.get(request.function_name)
    if not handler:
        return InvokeFunctionResponse(status="error", error=f"Unknown function: {request.function_name}")
    result = await handler(dict(request.params))
    return InvokeFunctionResponse(status="success", result=result)
```

### Layer 2: Worker Executor — Function Routing

#### 2.1 Registry Extension

Store function definitions alongside worker registration:

```protobuf
// worker_registry.proto
message WorkerFunctionDef {
    string name = 1;
    string description = 2;
    google.protobuf.Struct input_schema = 3;
    google.protobuf.Struct output_schema = 4;
}

message RegisterWorkerRequest {
    // ... existing fields ...
    repeated WorkerFunctionDef functions = 15;  // NEW
}
```

#### 2.2 Function Invocation Use Case

New feature: `invoke_worker_function`

```python
@dataclass
class InvokeWorkerFunctionInput:
    worker_type: str
    function_name: str
    params: dict[str, Any]

@dataclass
class InvokeWorkerFunctionResult:
    status: str          # "success" | "error"
    result: dict[str, Any]
    error: str = ""

class InvokeWorkerFunctionUseCase:
    async def execute(self, command: InvokeWorkerFunctionInput) -> InvokeWorkerFunctionResult:
        # 1. Look up worker by worker_type
        # 2. Validate function_name exists in worker's declared functions
        # 3. Route to worker via gRPC InvokeFunction
        # 4. Return result
```

#### 2.3 gRPC Service Extension

```protobuf
// task_dispatch.proto  (or new worker_functions.proto)
service TaskDispatchService {
    rpc DispatchTask(DispatchTaskRequest) returns (DispatchTaskResponse);
    rpc InvokeWorkerFunction(InvokeWorkerFunctionRequest) returns (InvokeWorkerFunctionResponse);  // NEW
}

message InvokeWorkerFunctionRequest {
    string worker_type = 1;
    string function_name = 2;
    google.protobuf.Struct params = 3;
}

message InvokeWorkerFunctionResponse {
    string status = 1;
    google.protobuf.Struct result = 2;
    string error = 3;
}
```

### Layer 3: Control Plane — API & Node Definition Integration

#### 3.1 REST API for Frontend (Design-Time)

New endpoints on the Control Plane (proxied through Worker Executor):

```
GET  /workers/{worker_type}/functions
     → List all functions declared by this worker

POST /workers/{worker_type}/functions/{function_name}/invoke
     Body: { "params": { "connection_id": "conn-1" } }
     → Invoke function and return result
```

These endpoints allow the frontend to:
- Discover what functions a worker supports
- Call functions to populate dropdowns, validate input, preview data

#### 3.2 Node Definition — Dynamic Schema Source

Extend `input_schema` fields with a `dynamic_source` option:

```python
# Node Definition input_schema field with dynamic source
{
    "key": "table_name",
    "type": "string",
    "label": "Table",
    "ui_component": "dropdown",
    "dynamic_source": {
        "worker_function": "list_tables",
        "params": {
            "connection_id": "{{$locked.connection_id}}"
        },
        "value_path": "tables[*].name",       # JSONPath to extract option values
        "label_path": "tables[*].name",       # JSONPath to extract option labels
        "cache_ttl_seconds": 300              # Optional: cache results for 5 minutes
    }
}
```

When the frontend renders this field:
1. Reads `dynamic_source.worker_function` = `"list_tables"`
2. Resolves `params` (substituting locked defaults like `connection_id`)
3. Calls `POST /workers/{worker_type}/functions/list_tables/invoke`
4. Maps response using `value_path` / `label_path` to populate dropdown options

#### 3.3 Node Definition — Cascading Dynamic Sources

Functions can depend on other field values (cascading dropdowns):

```python
# Parent field
{
    "key": "table_name",
    "type": "string",
    "dynamic_source": {
        "worker_function": "list_tables",
        "params": {"connection_id": "{{$locked.connection_id}}"}
    }
}

# Child field — depends on table_name selection
{
    "key": "column_name",
    "type": "string",
    "dynamic_source": {
        "worker_function": "get_columns",
        "params": {
            "connection_id": "{{$locked.connection_id}}",
            "table_name": "{{$field.table_name}}"           # References sibling field value
        },
        "depends_on": ["table_name"],                        # Re-fetch when table_name changes
        "value_path": "columns[*].name",
        "label_path": "columns[*].name"
    }
}
```

#### 3.4 Runtime Function Calls (During Execution)

For runtime use, `input_mapping` in node definitions can reference worker functions:

```python
# Node Definition with runtime function call in input_mapping
{
    "input_mapping": {
        "schema_info": {
            "_function_call": {
                "worker_function": "get_columns",
                "params": {
                    "connection_id": "{{$custom.connection_id}}",
                    "table_name": "{{$custom.table_name}}"
                }
            }
        }
    }
}
```

During task dispatch, the `TaskNodeHandler._apply_node_definition()` detects `_function_call` markers in mapping values and invokes them before dispatching to the worker.

## Node Definition Resolver Integration

The existing `NodeDefinitionResolver` inheritance chain works naturally with functions:

```
Root Definition (base worker = "database_worker")
  ├─ locked_defaults: {connection_id: "prod-db-1"}
  ├─ input_schema: [{key: "table_name", dynamic_source: {worker_function: "list_tables", ...}}]
  │
  └─ Child Definition (inherits database_worker)
      ├─ locked_defaults: {connection_id: "staging-db-2"}   ← overrides parent
      ├─ input_schema: inherited (uses same dynamic_source)
      └─ The function call uses the CHILD's locked connection_id
```

The resolver merges `locked_defaults` (child wins), so `dynamic_source.params` that reference `{{$locked.connection_id}}` automatically pick up the correct value at each level.

## Caching Strategy

Worker function results should be cached to avoid excessive calls:

| Context | Cache Location | TTL | Invalidation |
|---------|---------------|-----|-------------|
| Design-time (UI) | Frontend (in-memory) | From `cache_ttl_seconds` in schema | On field dependency change |
| Design-time (API) | Control Plane (Redis/memory) | 60-300s configurable | On worker re-registration |
| Runtime (mapping) | Per-run context | Duration of the run | Automatic (run-scoped) |

## Summary of Changes by Service

### Worker SDK (`worker/worker-sdk/`)
- New entity: `WorkerFunction` (name, handler, schemas)
- New: Function registry in bootstrap
- Extend `WorkerRegistration` with `functions` field
- Extend gRPC servicer with `InvokeFunction` RPC
- Extend HTTP routes with `/api/v1/functions/{name}/invoke`

### Proto (`proto/workflow/v1/`)
- `worker_execution.proto`: Add `InvokeFunction` RPC + messages
- `worker_registry.proto`: Add `WorkerFunctionDef` to registration

### Worker Executor (`worker-executor-service/`)
- Extend registry storage for function definitions
- New feature: `invoke_worker_function` use case
- Extend gRPC servicer for function invocation routing
- New REST endpoint: `POST /workers/{type}/functions/{name}/invoke`

### Control Plane (`workflow-control-plane-service/`)
- New REST endpoints: proxy function discovery and invocation
- Extend `input_schema` field spec to support `dynamic_source`
- Extend `TaskNodeHandler` to resolve `_function_call` in input_mapping at runtime
- Optional: caching layer for function results

### Frontend
- Detect `dynamic_source` in input_schema fields
- Call function invoke API to populate options
- Handle `depends_on` for cascading field updates
- Cache results per `cache_ttl_seconds`
