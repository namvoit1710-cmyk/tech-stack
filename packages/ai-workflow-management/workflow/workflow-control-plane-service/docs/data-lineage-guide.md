# Data Lineage Guide — How Data Flows Through Workflows

This document traces exactly how data moves from workflow input, through nodes and workers, and back into the run context for downstream consumption. Every transformation step is documented with the code location and a concrete example.

---

## Table of Contents

1. [Overview](#overview)
2. [The Run Context — Single Source of Truth](#the-run-context--single-source-of-truth)
3. [Phase 1: Workflow Input](#phase-1-workflow-input)
4. [Phase 2: INPUT Node (User Form)](#phase-2-input-node-user-form)
5. [Phase 3: TASK Node — Input Resolution](#phase-3-task-node--input-resolution)
6. [Phase 4: Dispatch to Worker](#phase-4-dispatch-to-worker)
7. [Phase 5: Worker Returns Output](#phase-5-worker-returns-output)
8. [Phase 6: Successor Nodes Access Data](#phase-6-successor-nodes-access-data)
9. [Phase 7: Run Completion](#phase-7-run-completion)
10. [Expression Syntax Reference](#expression-syntax-reference)
11. [Complete Example: Google Search Workflow](#complete-example-google-search-workflow)
12. [Context Variables — Growth Over Time](#context-variables--growth-over-time)
13. [Key Code Locations](#key-code-locations)

---

## Overview

```
 Workflow Input                                                  Run Output
 ══════════════                                                  ══════════
 { "search_term":     ┌──────────┐   ┌──────────────┐   ┌──────────┐
   "weather today" }──►│  INPUT   │──►│ Google Search │──►│ CONDITION│──► ...
                       │  Node    │   │  (TASK Node)  │   │  Node    │
                       └────┬─────┘   └──────┬────────┘   └──────────┘
                            │                │
                            ▼                ▼
                     context.variables  context.variables
                     ["input-1"] =     ["google-search-1"] =
                     { "output":       { "search_result": "<html>...",
                       { "search_      "status_code": 200,
                          term":       "is_success": true }
                         "weather
                          today" }}
```

Data flows through a single dict: **`run.context.variables`**. Each node reads from it (via expressions like `{{$input-1.output.search_term}}`), processes data, and writes its output back. This is the only mechanism for passing data between nodes.

---

## The Run Context — Single Source of Truth

Every workflow run carries a `RunContext` object with a `variables` dict. This dict is the **only** place nodes read from and write to.

```python
# From workflow_run.py
@dataclass
class RunContext:
    variables: dict[str, Any] = field(default_factory=dict)   # <-- THE data store
    loop_state: dict[str, Any] = field(default_factory=dict)
    completed_nodes: set[str] = field(default_factory=set)
    skipped_nodes: set[str] = field(default_factory=set)
    ...
```

**Key rules:**
- Each node's output is stored under **both** its `node_id` and its `name` (if different).
- The initial workflow input is stored under the key `"input"`.
- Expressions like `{{$node_id.field}}` resolve by looking up `context.variables["node_id"]["field"]`.

---

## Phase 1: Workflow Input

**Code:** `start_run_use_case.py:92`

When a run starts, the user-provided `input_data` is stored in the context:

```python
context = RunContext(
    variables={"input": req.input_data},
    dag_info=dag_info,
)
```

**Example:**

```
POST /runs
{ "workflow_id": "wf-1", "input_data": { "api_key": "sk-xxx" } }
```

Context after start:
```json
{
  "input": { "api_key": "sk-xxx" }
}
```

Any node can reference this via `{{$input.api_key}}`.

---

## Phase 2: INPUT Node (User Form)

**Code:** `resume_pending_task_use_case.py:169-194`

INPUT nodes pause the workflow and wait for user submission. When the user submits, their data is merged and stored:

```python
task.resolved_input.update(command.updated_input)

run.context.variables[node.id] = {
    "output": submitted_data,
    "metadata": {"type": node.node_type.value},
}
if node.name and node.name != node.id:
    run.context.variables[node.name] = run.context.variables[node.id]
```

**Example:**

User submits `{ "search_term": "weather today" }` to INPUT node `input-1` (name: "User Input"):

Context after submission:
```json
{
  "input": { "api_key": "sk-xxx" },
  "input-1": {
    "output": { "search_term": "weather today" },
    "metadata": { "type": "INPUT" }
  },
  "User Input": { /* same as input-1 */ }
}
```

Downstream nodes reference via `{{$input-1.output.search_term}}` or `{{$User Input.output.search_term}}`.

---

## Phase 3: TASK Node — Input Resolution

This is the most complex phase. Three transformations happen in sequence before the worker receives data.

### Step 3a: Resolve input_binding

**Code:** `task_handler.py:33-42`

The node's `input_binding` maps expressions to field names. The VariableResolver evaluates each expression against `run.context.variables`.

```python
resolved_input = self._resolver.resolve_mapping(
    node.input_binding,          # { "query": "{{$input-1.output.search_term}}" }
    run.context.variables,       # { "input": {...}, "input-1": {...} }
)
```

**Example:**

```
input_binding: { "query": "{{$input-1.output.search_term}}" }
```

Result:
```json
{ "query": "weather today" }
```

### Step 3b: Apply input_mapping (from node definition)

**Code:** `task_handler.py:71-92`

If the node has a `node_definition_id`, the snapshotted `_node_internals.input_mapping` transforms user-facing fields into worker-facing fields. The context includes both the run variables AND the resolved input under `$custom`.

```python
mapping_context = {**run.context.variables, "custom": resolved_input}
mapped = self._resolver.resolve_mapping(input_mapping, mapping_context)
result = {**resolved_input, **mapped, **locked_defaults}
```

**Example:**

```
input_mapping: {
    "query_params": [
        { "param_name": "q", "param_value": "{{$custom.query}}" }
    ]
}
```

`$custom` = the resolved input from step 3a = `{ "query": "weather today" }`

Mapped result:
```json
{
    "query_params": [
        { "param_name": "q", "param_value": "weather today" }
    ]
}
```

### Step 3c: Merge with locked_defaults

**Code:** `task_handler.py:89`

Locked defaults are injected last and **always win**:

```python
result = {**resolved_input, **mapped, **locked_defaults}
```

**Example:**

```
locked_defaults: {
    "method": "GET",
    "url": "https://www.google.com/search",
    "timeout_seconds": 30
}
```

Final merged payload sent to worker:
```json
{
    "query": "weather today",
    "query_params": [{ "param_name": "q", "param_value": "weather today" }],
    "method": "GET",
    "url": "https://www.google.com/search",
    "timeout_seconds": 30
}
```

### Merge Priority (highest wins)

```
locked_defaults     ← ALWAYS wins, user cannot override
    ▲
input_mapping       ← Transforms user fields to worker fields
    ▲
resolved_input      ← Raw user input from input_binding
```

If `locked_defaults` has `method: "GET"` and `input_mapping` tries to set `method: "POST"`, the worker receives `method: "GET"`.

---

## Phase 4: Dispatch to Worker

**Code:** `orchestration_engine.py:204-218`, `http_task_dispatcher.py:39-64`

The merged payload is sent as a `TaskDispatched` event, which the `HttpTaskDispatcher` picks up and POSTs to the worker executor:

```python
# orchestration_engine.py
self._event_publisher.publish(
    "task.dispatched",
    TaskDispatched(
        task_id=task.id,
        run_id=task.run_id,
        node_id=task.node_id,
        worker_type=task.worker_type,
        input=task.resolved_input or {},     # <-- The merged payload
        node_config=node.config,
    ),
)
```

```python
# http_task_dispatcher.py
POST {worker_execution_url}/api/v1/tasks/execute
{
    "task_id": "run-1-google-search-1",
    "run_id": "run-1",
    "node_id": "google-search-1",
    "worker_type": "http-request-worker",
    "input": {
        "method": "GET",
        "url": "https://www.google.com/search",
        "query_params": [{"param_name": "q", "param_value": "weather today"}],
        "timeout_seconds": 30
    }
}
```

The worker executor routes this to the registered `http-request-worker`, which makes the actual HTTP request.

---

## Phase 5: Worker Returns Output

**Code:** `complete_task_use_case.py:92-136`

When the worker finishes, its raw output arrives as `output_metadata`. Two transformations happen before the data is stored in context.

### Step 5a: Build task_output

```python
task_output = {}
task_output["output"] = {"uri": req.output_reference}
task_output["metadata"] = req.output_metadata or {}
task_output.update(req.output_metadata)   # Spread worker keys at top level
```

For `http-request-worker`, the raw output is:
```json
{
    "status_code": 200,
    "response_body": "<html>...",
    "is_success": true,
    "content_type": "text/html",
    "elapsed_ms": 450,
    "response_headers": { "..." },
    "final_url": "https://www.google.com/search?q=weather+today"
}
```

After building:
```json
{
    "output": { "uri": "" },
    "metadata": { "status_code": 200, "response_body": "...", ... },
    "status_code": 200,
    "response_body": "<html>...",
    "is_success": true,
    "content_type": "text/html",
    "elapsed_ms": 450,
    "response_headers": { "..." },
    "final_url": "https://www.google.com/search?q=weather+today"
}
```

Worker keys are spread at the top level for direct access via `{{$_output.response_body}}`.

Workers can include `_artifacts` in `output_metadata`. These are extracted before CSV conversion and stored in the file reference's `artifacts` array.

### Step 5b: Apply output_mapping (from node definition)

**Code:** `complete_task_use_case.py:106-119`

The node definition's `output_mapping` transforms the raw worker output into business-meaningful fields:

```python
task_output = self._variable_resolver.resolve_mapping(
    output_mapping,              # { "search_result": "{{$_output.response_body}}", ... }
    {"_output": task_output}     # Context: raw worker output
)
```

**Example:**

```
output_mapping: {
    "search_result": "{{$_output.response_body}}",
    "status_code": "{{$_output.status_code}}",
    "is_success": "{{$_output.is_success}}"
}
```

Result:
```json
{
    "search_result": "<html>...",
    "status_code": 200,
    "is_success": true
}
```

### Step 5c: Apply output_binding (from workflow node)

**Code:** `complete_task_use_case.py:122-131`

The workflow node's `output_binding` applies an additional transformation (optional, configured in the workflow editor):

```python
task_output = self._variable_resolver.resolve_mapping(
    node.output_binding,         # { "result": "{{$_output.search_result}}" }
    {"_output": task_output}     # Context: output_mapping result
)
```

### Step 5d: Store in context

**Code:** `complete_task_use_case.py:133-135`

```python
run.context.variables[task.node_id] = task_output
if node.name and node.name != task.node_id:
    run.context.variables[node.name] = task_output
```

Context after Google Search completes:
```json
{
    "input": { "api_key": "sk-xxx" },
    "input-1": { "output": { "search_term": "weather today" }, ... },
    "google-search-1": {
        "search_result": "<html>...",
        "status_code": 200,
        "is_success": true
    },
    "Google Search": { /* same as google-search-1 */ }
}
```

### output_mapping vs output_binding

| | output_mapping | output_binding |
|---|---|---|
| **Defined in** | Node Definition (`_node_internals`) | Workflow Node (`node.output_binding`) |
| **Purpose** | Transform raw worker output to business fields | Reshape for specific workflow needs |
| **Applied** | First | Second (on top of mapping result) |
| **Context** | `{"_output": raw_worker_output}` | `{"_output": mapped_output}` |
| **Typical use** | `response_body` → `search_result` | `search_result` → `result` |

---

## Phase 6: Successor Nodes Access Data

**Code:** `successor_processor.py:84-132`

After a task completes, the engine processes successor nodes. Each successor can read the completed node's output from context.

An `edge.traversed` event is emitted for each edge followed during successor processing, enabling frontend edge animation.

### CONDITION Node

**Code:** `condition_handler.py:34-48`

```python
condition_result = self._evaluator.evaluate(
    expression,                    # "{{$google-search-1.is_success}} == true"
    run.context.variables          # Full context with all completed nodes
)
```

Expressions can use any completed node's output:
```
{{$google-search-1.is_success}} == true
{{$google-search-1.status_code}} >= 200 AND {{$google-search-1.status_code}} < 300
```

### Port-Based Routing (TASK nodes)

**Code:** `successor_processor.py:87-97`

TASK nodes route to `success` or `failure` output port based on task status:

```python
expected_port = (
    "success" if completed_task.status == TaskStatus.COMPLETED
    else "failure"
)
outgoing_edges = [
    e for e in outgoing_edges
    if not e.source_port_id or e.source_port_id == expected_port
]
```

Only edges matching the result port are followed.

A `task.failed` Socket.IO event is now emitted when a task fails, providing `error_code`, `error_message`, and `attempt` to the frontend.

### Next TASK Node

The next TASK node's `input_binding` reads from the previous node:

```json
{
    "input_binding": {
        "data": "{{$google-search-1.search_result}}",
        "was_successful": "{{$google-search-1.is_success}}"
    }
}
```

---

## Phase 7: Run Completion

**Code:** `orchestration_engine.py:441-458`

When all leaf nodes complete, the run's `output_data` is built from context:

```python
@staticmethod
def _build_output_data(run, workflow):
    node_ids = {n.id for n in workflow.nodes}
    special_keys = {"input"}
    canonical_keys = node_ids | special_keys
    return {
        k: v
        for k, v in run.context.variables.items()
        if k in canonical_keys          # Only node IDs + "input", no name aliases
    }
```

The final `output_data` contains every node's output keyed by node ID.

---

## Expression Syntax Reference

### Basic Format

```
{{$<source>.<path>}}
```

### Available Sources

| Source | Description | When Available |
|--------|-------------|----------------|
| `$input` | Workflow `input_data` | Always (from run start) |
| `$<node_id>` | Node output by ID | After node completes |
| `$<node_name>` | Node output by name | After node completes |
| `$custom` | Resolved user input | Only inside `input_mapping` |
| `$_output` | Raw worker output | Only inside `output_mapping` / `output_binding` |
| `$loop` | Current loop iteration | Inside LOOP body |

### Meta-Path Expressions

Meta-path expressions (`__file_id`, `__artifacts`, `__artifact.<name>`, `__content_type`, `__row_count`, `__columns`) access file reference metadata directly, bypassing CSV content resolution.

### Path Traversal

```
{{$node1.field}}                →  variables["node1"]["field"]
{{$node1.output.data}}          →  variables["node1"]["output"]["data"]
{{$node1.output.items[0]}}      →  variables["node1"]["output"]["items"][0]
{{$node1.output.items[0].name}} →  variables["node1"]["output"]["items"][0]["name"]
```

### String Interpolation

When an expression is the ENTIRE value, the resolved type is preserved:
```json
{ "count": "{{$node1.total}}" }     →  { "count": 42 }        // number preserved
```

When embedded in a string, everything is coerced to string:
```json
{ "msg": "Found {{$node1.total}} items" }  →  { "msg": "Found 42 items" }
```

### Condition Expressions

```
{{$node1.status_code}} == 200
{{$node1.is_success}} == true
{{$node1.total}} > 0 AND {{$node1.total}} < 100
{{$node1.category}} IN "electronics,books"
{{$node1.name}} CONTAINS "test"
```

Operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `IN`, `CONTAINS`, `AND`, `OR`

---

## Complete Example: Google Search Workflow

A 3-node workflow: INPUT → Google Search (TASK) → CONDITION

### Node Definition (created beforehand)

```json
{
    "name": "Google Search",
    "base_worker_id": "http-request-worker",
    "locked_defaults": {
        "method": "GET",
        "url": "https://www.google.com/search",
        "timeout_seconds": 30
    },
    "input_schema": [
        { "key": "query", "outputType": "string", ... }
    ],
    "input_mapping": {
        "query_params": [
            { "param_name": "q", "param_value": "{{$custom.query}}" }
        ]
    },
    "output_mapping": {
        "search_result": "{{$_output.response_body}}",
        "status_code": "{{$_output.status_code}}",
        "is_success": "{{$_output.is_success}}"
    }
}
```

### Workflow Definition

```json
{
    "nodes": [
        {
            "id": "input-1",
            "name": "User Input",
            "node_type": "INPUT",
            "config": {
                "input_schema": [
                    { "key": "search_term", ... }
                ]
            }
        },
        {
            "id": "google-search-1",
            "name": "Google Search",
            "node_type": "TASK",
            "node_definition_id": "nd-google-search",
            "input_binding": {
                "query": "{{$input-1.output.search_term}}"
            }
        },
        {
            "id": "check-success",
            "name": "Check Success",
            "node_type": "CONDITION",
            "config": {
                "expression": "{{$google-search-1.is_success}} == true"
            }
        }
    ],
    "edges": [
        { "source": "input-1", "target": "google-search-1" },
        { "source": "google-search-1", "source_port": "success", "target": "check-success" }
    ]
}
```

### Execution Trace

#### T0: Run Starts

```
POST /runs  { "workflow_id": "wf-1", "input_data": {} }
```

```
context.variables = {
    "input": {}
}
```

#### T1: INPUT Node Pauses (waiting for user)

```
context.variables = {
    "input": {}
}
```

Run status: `RUNNING` (paused at input-1)

#### T2: User Submits Form

```
POST /runs/{id}/tasks/{task_id}/resume
{ "updated_input": { "search_term": "weather today" } }
```

```
context.variables = {
    "input": {},
    "input-1": {
        "output": { "search_term": "weather today" },
        "metadata": { "type": "INPUT" }
    },
    "User Input": { /* same */ }
}
```

#### T3: Google Search Node — Input Resolution

**3a. input_binding resolves:**
```
input_binding: { "query": "{{$input-1.output.search_term}}" }
   ↓ resolve against context.variables
resolved_input = { "query": "weather today" }
```

**3b. input_mapping transforms:**
```
input_mapping context = { ...context.variables, "custom": { "query": "weather today" } }

input_mapping: { "query_params": [{ "param_name": "q", "param_value": "{{$custom.query}}" }] }
   ↓ resolve
mapped = { "query_params": [{ "param_name": "q", "param_value": "weather today" }] }
```

**3c. Merge:**
```
final = {
    "query": "weather today",           ← from resolved_input
    "query_params": [                    ← from input_mapping
        { "param_name": "q", "param_value": "weather today" }
    ],
    "method": "GET",                     ← from locked_defaults
    "url": "https://www.google.com/search",
    "timeout_seconds": 30
}
```

#### T4: Worker Executes HTTP Request

```
HTTP GET https://www.google.com/search?q=weather+today
```

Worker returns:
```json
{
    "status_code": 200,
    "response_body": "<html><!doctype html>...",
    "is_success": true,
    "content_type": "text/html",
    "elapsed_ms": 450,
    "response_headers": { "content-type": "text/html" },
    "final_url": "https://www.google.com/search?q=weather+today"
}
```

#### T5: Complete Task — Output Processing

**5a. Build task_output (spread worker keys):**
```json
{
    "output": { "uri": "" },
    "metadata": { "status_code": 200, "response_body": "...", ... },
    "status_code": 200,
    "response_body": "<html>...",
    "is_success": true,
    "content_type": "text/html",
    "elapsed_ms": 450,
    "response_headers": { "..." },
    "final_url": "https://www.google.com/search?q=weather+today"
}
```

**5b. output_mapping transforms:**
```
output_mapping: {
    "search_result": "{{$_output.response_body}}",
    "status_code": "{{$_output.status_code}}",
    "is_success": "{{$_output.is_success}}"
}
   ↓ resolve against {"_output": task_output}
task_output = {
    "search_result": "<html>...",
    "status_code": 200,
    "is_success": true
}
```

**5c. Store in context:**
```
context.variables = {
    "input": {},
    "input-1": { "output": { "search_term": "weather today" }, ... },
    "User Input": { /* same */ },
    "google-search-1": {
        "search_result": "<html>...",
        "status_code": 200,
        "is_success": true
    },
    "Google Search": { /* same */ }
}
```

#### T6: CONDITION Node Evaluates

```
expression: "{{$google-search-1.is_success}} == true"
   ↓ resolve
"true == true"
   ↓ evaluate
result = True  →  follows "then" branch
```

---

## Context Variables — Growth Over Time

```
T0  Run Start
    ┌──────────────────────────────────────────┐
    │ "input": { "api_key": "sk-xxx" }         │
    └──────────────────────────────────────────┘

T2  INPUT Node Completes
    ┌──────────────────────────────────────────┐
    │ "input": { "api_key": "sk-xxx" }         │
    │ "input-1": {                             │
    │     "output": { "search_term": "..." }   │
    │ }                                        │
    └──────────────────────────────────────────┘

T5  Google Search Completes
    ┌──────────────────────────────────────────┐
    │ "input": { "api_key": "sk-xxx" }         │
    │ "input-1": {                             │
    │     "output": { "search_term": "..." }   │
    │ }                                        │
    │ "google-search-1": {                     │
    │     "search_result": "<html>...",        │
    │     "status_code": 200,                  │
    │     "is_success": true                   │
    │ }                                        │
    └──────────────────────────────────────────┘

T6  Condition Evaluates (reads, no write)
    ┌──────────────────────────────────────────┐
    │ "input": { ... }                         │
    │ "input-1": { ... }                       │
    │ "google-search-1": { ... }               │
    │ "check-success": {                       │
    │     "result": true,                      │
    │     "branch": "then"                     │
    │ }                                        │
    └──────────────────────────────────────────┘

    Each node ADDS to the context. Nothing is removed.
    Downstream nodes can read ANY upstream node's output.
```

---

## Key Code Locations

| Step | File | Lines | What Happens |
|------|------|-------|--------------|
| Run start | `start_run_use_case.py` | 92 | `variables = {"input": input_data}` |
| INPUT submit | `resume_pending_task_use_case.py` | 169-194 | User data → `variables[node_id]` |
| input_binding | `task_handler.py` | 33-42 | Expressions resolved from context |
| input_mapping | `task_handler.py` | 71-92 | `$custom` → worker fields |
| Merge | `task_handler.py` | 89 | `{resolved, mapped, locked_defaults}` |
| Dispatch | `orchestration_engine.py` | 204-218 | `task.resolved_input` → event |
| HTTP POST | `http_task_dispatcher.py` | 56-64 | Event → worker executor |
| Output build | `complete_task_use_case.py` | 92-99 | Worker output → task_output |
| output_mapping | `complete_task_use_case.py` | 106-119 | `$_output` → business fields |
| output_binding | `complete_task_use_case.py` | 122-131 | Final reshape |
| Context store | `complete_task_use_case.py` | 133-135 | `variables[node_id] = result` |
| Successor | `successor_processor.py` | 84-132 | Trigger next nodes |
| Condition eval | `condition_handler.py` | 34-48 | Expression against context |
| Run output | `orchestration_engine.py` | 441-458 | `output_data` = canonical keys |
