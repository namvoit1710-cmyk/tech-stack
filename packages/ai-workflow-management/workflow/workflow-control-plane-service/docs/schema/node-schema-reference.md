# Workflow Node Schema Reference

> Authoritative schema reference for the AI Workflow Management control plane.
> Covers all 12 node types, entity schemas, expression syntax, and runtime behaviors.

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [Core Entities](#2-core-entities)
3. [Enums / Value Objects](#3-enums--value-objects)
4. [Node Type Reference](#4-node-type-reference)
5. [Expression & Variable Syntax](#5-expression--variable-syntax)
6. [Business Node Features](#6-business-node-features)
7. [Input/Output Schema Format](#7-inputoutput-schema-format)
8. [Complete Example](#8-complete-example)

---

## 1. Overview & Architecture

### DAG Execution Model

The workflow control plane executes workflows as **directed acyclic graphs (DAGs)**. Each workflow is a collection of **nodes** connected by **edges**. At runtime, a `WorkflowRun` tracks execution state, and the orchestration engine advances through the DAG by:

1. Starting at **entry nodes** (nodes with no incoming edges, typically a TRIGGER).
2. Executing each node via its registered **NodeHandler**.
3. Using the **SuccessorProcessor** to advance to downstream nodes once predecessors complete.
4. Checking **all predecessor completion** before activating a node (join semantics).
5. Marking the run as COMPLETED when all leaf nodes are covered (completed or skipped).

### Node Processing Categories

Every node type falls into one of three processing categories:

| Category | Behavior | Node Types |
|----------|----------|------------|
| **BUILTIN** | Executed inline by the engine. Completes immediately and auto-advances to successors. | TRIGGER, CONDITION, SWITCH, LOOP, PARALLEL, MERGE, COMPUTE, WORKFLOW |
| **PENDING** | Creates a task with status `PENDING`. Workflow pauses until external input resumes the task. | INPUT, HUMAN_ACTION |
| **TASK** | Dispatches to an external worker via the worker-executor service. Workflow pauses until the worker reports a result. | TASK |

### Node Class

Nodes are classified by their `node_class` field:

| Class | Description |
|-------|-------------|
| **TECHNICAL** | Control-flow or infrastructure nodes. Defined entirely by `type` + `config`. |
| **BUSINESS** | Domain-specific nodes backed by a `NodeDefinition` template. Feature locked defaults, input/output schemas, data modes (CSV/JSON), and edit modes (HITL pause). |

### Recursion & Depth Limits

- **Successor processing** max recursion depth: `100`
- **Workflow (sub-workflow) nesting** max depth: configurable via `config.max_depth` (default `10`)
- **Loop max iterations**: configurable via `config.max_iterations` (default `10000`)

---

## 2. Core Entities

### 2a. Workflow

The top-level workflow definition. Source: `layer1_domain/entities/workflow.py`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `string` | UUID v4 | Unique workflow identifier |
| `name` | `string` | `""` | Human-readable name (required for `create()`) |
| `version` | `string` | `"1"` | Version string |
| `description` | `string` | `""` | Workflow description |
| `status` | `WorkflowStatus` | `DRAFT` | Lifecycle status |
| `input_schema` | `list[dict]` | `[]` | Auto-form field list defining workflow input (see [Section 7](#7-inputoutput-schema-format)) |
| `output_schema` | `list[dict]` | `[]` | Auto-form field list defining workflow output |
| `nodes` | `list[WorkflowNode]` | `[]` | All nodes in the workflow |
| `edges` | `list[WorkflowEdge]` | `[]` | All edges connecting nodes |
| `purpose` | `string` | `""` | Business purpose description |
| `metadata` | `dict` | `{}` | Arbitrary metadata |
| `created_at` | `string` | ISO 8601 UTC | Creation timestamp |
| `updated_at` | `string` | ISO 8601 UTC | Last update timestamp |
| `schema_version` | `string` | `"1.0.0"` | Schema version for forward compatibility |

**Helper methods:**
- `get_node(node_id) -> WorkflowNode | None`
- `get_outgoing_edges(node_id) -> list[WorkflowEdge]`
- `get_incoming_edges(node_id) -> list[WorkflowEdge]`
- `Workflow.create(name, description, **kwargs) -> Workflow` (validates name is non-empty)

**Schema normalization:** The `normalize_schema()` function converts legacy schema formats:
- `None` -> `[]`
- `list` -> returned as-is
- `dict` with `"fields"` key -> returns `fields` list
- Empty or other -> `[]`

### 2b. WorkflowNode

A single node in the workflow DAG. Source: `layer1_domain/entities/workflow.py`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `string` | UUID v4 | Unique node identifier |
| `name` | `string` | `""` | Human-readable node name. Used as an alias key in `run.context.variables` for expression resolution (not included in `output_data`) |
| `type` | `NodeType` | `TASK` | One of 12 node types (see [Section 3](#3-enums--value-objects)) |
| `worker_id` | `string \| null` | `null` | Worker type for TASK nodes (e.g. `"http-request-worker"`) |
| `input_binding` | `dict` | `{}` | Expression map resolving input data from context (e.g. `{"url": "{{$trigger.body.url}}"}`) |
| `output_binding` | `dict` | `{}` | Expression map for post-processing task output |
| `input_require` | `list[string]` | `[]` | Keys that must be present and non-null in resolved input |
| `config` | `dict` | `{}` | Node-type-specific configuration (see [Section 4](#4-node-type-reference)) |
| `x` | `float \| null` | `null` | Canvas X position |
| `y` | `float \| null` | `null` | Canvas Y position |
| `node_class` | `string` | `""` | `"TECHNICAL"` or `"BUSINESS"` |
| `description` | `string` | `""` | Node description |
| `instruction` | `string` | `""` | Human-readable instruction for the node |
| `icon` | `string` | `""` | Icon identifier for the frontend |
| `color` | `string` | `""` | Hex color for the frontend |
| `tags` | `list[string]` | `[]` | Arbitrary tags |
| `ports` | `object` | `{"in": [], "out": []}` | Port definitions (see [Port](#2d-port)). Contains `in` (input ports) and `out` (output ports) arrays. |
| `node_definition_id` | `string \| null` | `null` | Reference to a NodeDefinition template (business nodes) |

**Post-init normalization:**
- If `type` is a string, it is converted to `NodeType` enum.
- If `input_require` is a comma-separated string, it is split into a list.
- **Ports**: Both `ports: {in, out}` and legacy `in_ports`/`out_ports` are accepted. The `ports` object is always kept in sync. This means the same `ports` format is used for both API request (create/update) and API response (get), so the frontend does not need to transform between different formats.

### 2c. WorkflowEdge

A directed connection between two nodes. Source: `layer1_domain/entities/workflow.py`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `string` | UUID v4 | Unique edge identifier |
| `source_node_id` | `string` | `""` | ID of the source node |
| `target_node_id` | `string` | `""` | ID of the target node |
| `condition` | `string \| null` | `null` | Edge condition label (e.g. `"then"`, `"else"`, `"body"`, `"exit"`, `"default"`, `"case:value"`) |
| `source_port_id` | `string` | `""` | Source port ID |
| `target_port_id` | `string` | `""` | Target port ID |
| `label` | `string` | `""` | Display label |
| `description` | `string` | `""` | Edge description |

### 2d. Port

Input/output port definition. Source: `layer1_domain/value_objects/port.py`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `string` | `""` | Port identifier |
| `label` | `string` | `""` | Display label |
| `data_type` | `string` | `"any"` | Data type: `"any"`, `"string"`, `"number"`, `"boolean"`, `"object"`, `"array"` |
| `required` | `bool` | `true` | Whether a connection is required |
| `description` | `string` | `""` | Port description |

**Methods:**
- `to_dict() -> dict` — serializes to plain dict
- `Port.from_dict(data) -> Port` — deserializes from dict

### 2e. NodeDefinition (Business Node Template)

A reusable template that wraps a base worker/control-flow node into a business-level node. Source: `layer1_domain/entities/node_definition.py`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `string` | UUID v4 | Unique definition identifier |
| `tenant_id` | `string` | `""` | Owning tenant (required) |
| `name` | `string` | `""` | Human-readable name (required) |
| `description` | `string` | `""` | Template description |
| `base_type` | `NodeType` | `TASK` | Underlying node type |
| `base_worker_id` | `string \| null` | `null` | Worker for TASK base types (required when `base_type == TASK`) |
| `locked_defaults` | `dict` | `{}` | Config values merged at dispatch time, not editable by users |
| `input_schema` | `list[dict]` | `[]` | Simplified schema fields for the input form |
| `output_schema` | `list[dict]` | `[]` | Simplified schema fields for the output |
| `input_mapping` | `dict` | `{}` | Expression-based input transformation |
| `output_mapping` | `dict` | `{}` | Expression-based output transformation |
| `icon` | `string` | `""` | Icon identifier |
| `color` | `string` | `""` | Hex color |
| `tags` | `list[string]` | `[]` | Arbitrary tags |
| `data_mode` | `string` | `"json"` | Data conversion mode |
| `edit_mode` | `string` | `"before_dispatch"` | HITL edit mode |
| `created_at` | `string` | ISO 8601 UTC | Creation timestamp |
| `updated_at` | `string` | ISO 8601 UTC | Last update timestamp |

**Valid data modes:** `"json"`, `"csv_single"`, `"csv_multi"`

**Valid edit modes:** `"none"`, `"before_dispatch"`, `"after_dispatch"`

**Validation rules** (`validate() -> list[str]`):
- `name` must be non-empty
- `tenant_id` must be non-empty
- `base_worker_id` is required when `base_type == TASK`
- `data_mode` must be one of `VALID_DATA_MODES`
- `edit_mode` must be one of `VALID_EDIT_MODES`

### 2f. Task (Runtime)

A runtime task dispatched during workflow execution. Source: `layer1_domain/entities/task.py`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `string` | UUID v4 | Task ID (format: `{run_id}-{node_id}[-iter-{index}]`) |
| `run_id` | `string` | `""` | Parent workflow run |
| `node_id` | `string` | `""` | Node this task executes |
| `status` | `TaskStatus` | `READY` | Current task status |
| `worker_id` | `string \| null` | `null` | Target worker type |
| `resolved_input` | `dict \| null` | `null` | Fully resolved input data sent to the worker |
| `output_ref` | `string \| null` | `null` | Reference to output (e.g. file ID) |
| `output_metadata` | `dict \| null` | `null` | Output metadata / PENDING node frontend data |
| `loop_context` | `dict \| null` | `null` | Loop iteration context: `{index, item, total}` |
| `iteration_index` | `int \| null` | `null` | Loop iteration index |
| `retry_count` | `int` | `0` | Current retry attempt |
| `max_retries` | `int` | `3` | Maximum retry attempts |
| `error` | `string \| null` | `null` | Error message on failure |
| `idempotency_key` | `string` | `""` | Idempotency key (format: `{task_id}-attempt-{n}`) |
| `created_at` | `string` | ISO 8601 UTC | Creation timestamp |
| `started_at` | `string \| null` | `null` | Execution start timestamp |
| `completed_at` | `string \| null` | `null` | Completion timestamp |

### TaskResult

DTO returned by workers to report task completion. Source: `layer1_domain/entities/task.py`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `task_id` | `string` | `""` | Task identifier |
| `run_id` | `string` | `""` | Run identifier |
| `node_id` | `string` | `""` | Node identifier |
| `status` | `string` | `"COMPLETED"` | Result status: `"COMPLETED"` or `"FAILED"` |
| `output_reference` | `string` | `""` | Reference to output artifact |
| `output_metadata` | `dict` | `{}` | Structured output metadata |
| `execution_time_ms` | `int` | `0` | Execution duration in milliseconds |
| `error` | `dict \| null` | `null` | Error details on failure |
| `idempotency_key` | `string` | `""` | Idempotency key echoed back |

### 2g. WorkflowRun + RunContext + DAGInfo

Runtime state for a single workflow execution. Source: `layer1_domain/entities/workflow_run.py`

#### WorkflowRun

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `string` | UUID v4 | Run identifier |
| `workflow_id` | `string` | `""` | Parent workflow ID |
| `workflow_version` | `string` | `"1"` | Workflow version at run time |
| `status` | `RunStatus` | `PENDING` | Current run status |
| `input_data` | `dict` | `{}` | Workflow input data |
| `output_data` | `dict \| null` | `null` | Workflow output (set on completion; contains only canonical keys — node IDs and `input` — not name aliases or the `trigger` convenience key) |
| `context` | `RunContext` | *(empty)* | Mutable execution context |
| `parent_run_id` | `string \| null` | `null` | Parent run ID (for sub-workflows) |
| `parent_node_id` | `string \| null` | `null` | Parent node ID (for sub-workflows) |
| `depth_level` | `int` | `0` | Nesting depth (0 = top-level) |
| `error` | `string \| null` | `null` | Error message on failure |
| `started_at` | `string` | ISO 8601 UTC | Start timestamp |
| `completed_at` | `string \| null` | `null` | Completion timestamp |
| `tenant_id` | `string` | `""` | Tenant identifier |
| `correlation_id` | `string` | `""` | External correlation ID (for callbacks) |
| `callback_topic` | `string` | `""` | Messaging topic for completion callback |

#### RunContext

Mutable state that evolves during execution.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `variables` | `dict[string, Any]` | `{}` | Node outputs keyed by node ID (and aliased by node name). The primary data store for expression resolution. |
| `loop_state` | `dict[string, Any]` | `{}` | Active loop state keyed by loop node ID |
| `completed_nodes` | `set[string]` | `set()` | Node IDs that have completed |
| `current_node` | `string` | `""` | Currently executing node ID |
| `skipped_nodes` | `set[string]` | `set()` | Node IDs skipped by branching (condition/switch) |
| `child_runs` | `dict[string, string]` | `{}` | Map of node_id -> child_run_id for WORKFLOW nodes |
| `dag_info` | `DAGInfo` | *(empty)* | Pre-computed DAG structure |

#### DAGInfo

Pre-computed graph topology for efficient traversal.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `adjacency` | `dict[string, list[string]]` | `{}` | node_id -> list of successor node_ids |
| `predecessors` | `dict[string, list[string]]` | `{}` | node_id -> list of predecessor node_ids |
| `edge_map` | `dict[string, list[WorkflowEdge]]` | `{}` | node_id -> outgoing edges |
| `entry_nodes` | `list[string]` | `[]` | Node IDs with no incoming edges |
| `leaf_nodes` | `list[string]` | `[]` | Node IDs with no outgoing edges |

---

## 3. Enums / Value Objects

### NodeType

12 workflow node types. Source: `layer1_domain/value_objects/node_type.py`

| Value | Category | Description |
|-------|----------|-------------|
| `TASK` | TASK-dispatch | Dispatches work to an external worker |
| `PARALLEL` | BUILTIN | Fan-out: dispatches multiple branches simultaneously |
| `CONDITION` | BUILTIN | Binary branching based on boolean expression |
| `LOOP` | BUILTIN | Iterates over an array, executing body nodes per item |
| `SWITCH` | BUILTIN | Multi-way routing based on expression value matching |
| `MERGE` | BUILTIN | Fan-in: waits for multiple branches to complete |
| `TRIGGER` | BUILTIN | Workflow entry point |
| `WORKFLOW` | BUILTIN | Invokes another workflow as a child sub-workflow |
| `COMPUTE` | BUILTIN | Safe inline math/expression evaluator |
| `INPUT` | PENDING | Pauses workflow to collect structured input from a user |
| `HUMAN_ACTION` | PENDING | Pauses workflow for a human to perform an action and confirm/reject |

### Builtin `worker_type` Convention

When the engine dispatches a node it assigns a `worker_type` string. Builtin node types use a `builtin:` prefix; TASK nodes carry the worker type from the node definition.

| Node Type | worker_type |
|-----------|-------------|
| CONDITION | `builtin:condition` |
| SWITCH | `builtin:switch` |
| LOOP | `builtin:loop` |
| PARALLEL | `builtin:parallel` |
| MERGE | `builtin:merge` |
| TRIGGER | `builtin:trigger` |
| WORKFLOW | `builtin:workflow` |
| COMPUTE | `builtin:compute` |
| INPUT | `builtin:input` |
| HUMAN_ACTION | `builtin:human_action` |
| TASK | from node definition (e.g. `http_request_worker`) |

### NodeClass

| Value | Description |
|-------|-------------|
| `TECHNICAL` | Control-flow or infrastructure node |
| `BUSINESS` | Domain-specific node backed by a NodeDefinition |

### TaskStatus

| Value | Description |
|-------|-------------|
| `PENDING` | Created but waiting for user input (INPUT/HUMAN_ACTION) or HITL review |
| `WAITING` | Waiting for a prerequisite |
| `READY` | Ready to be dispatched |
| `RUNNING` | Currently being executed by a worker |
| `COMPLETED` | Successfully completed |
| `FAILED` | Execution failed |
| `RETRYING` | Being retried after failure |

### RunStatus

| Value | Description |
|-------|-------------|
| `PENDING` | Run created but not yet started |
| `RUNNING` | Actively executing |
| `COMPLETED` | All leaf nodes covered, no active tasks |
| `FAILED` | A task failed (non-skipped) |
| `CANCELLED` | Run was cancelled |
| `PAUSED` | Run is paused (e.g. awaiting INPUT/HUMAN_ACTION) |

### WorkflowStatus

| Value | Description |
|-------|-------------|
| `DRAFT` | Being edited, not executable |
| `VALID` | Validated, ready for deployment |
| `DEPLOYED` | Live and executable |
| `ARCHIVED` | Retired, no longer executable |

---

## 4. Node Type Reference

### 4.1. TRIGGER

**Category:** BUILTIN
**Purpose:** Entry point for workflow execution. Captures the trigger event and passes input data downstream.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `trigger_type` | `string` | `"manual"` | Type of trigger: `"manual"`, `"event"`, `"schedule"`, etc. |
| `event_topic` | `string` | — | Event topic name (for event-based triggers) |

#### Edge Conditions

No special edge conditions. All outgoing edges are followed unconditionally.

#### Port Conventions

| `ports` key | Typical Ports |
|-------------|---------------|
| `in` | *(none — entry node)* |
| `out` | `out_default` |

#### State Output

Written to `run.context.variables`:

```json
{
  "<node_id>": {
    "output": {
      "type": "<trigger_type>",
      "body": { /* run.input_data */ }
    },
    "metadata": {
      "type": "trigger",
      "trigger_type": "<trigger_type>"
    }
  },
  "trigger": {
    "type": "<trigger_type>",
    "body": { /* run.input_data */ }
  }
}
```

The special `"trigger"` key provides a convenient alias for expressions like `{{$trigger.body.order_id}}`. It is only available during expression resolution and is **not** included in the run's `output_data` (only the `<node_id>` entry appears there).

#### Events Emitted

*(none)*

#### JSON Example

```json
{
  "id": "trigger-1",
  "name": "Order Received",
  "type": "TRIGGER",
  "node_class": "TECHNICAL",
  "config": {
    "trigger_type": "event",
    "event_topic": "orders.created"
  },
  "ports": {
    "in": [],
    "out": [{ "id": "out_default", "label": "Output", "data_type": "any" }]
  }
}
```

---

### 4.2. TASK

**Category:** TASK-dispatch
**Purpose:** Resolves input bindings and dispatches work to an external worker via the worker-executor service. Supports both technical (raw) and business (NodeDefinition-backed) modes.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `input_types` | `dict[string, string]` | — | Optional type coercion map for resolved input keys |
| `_node_definition` | `dict` | — | *(Business nodes only)* Snapshot of NodeDefinition user-facing config |
| `_node_internals` | `dict` | — | *(Business nodes only)* Snapshot of NodeDefinition runtime config (locked_defaults, input_mapping, output_mapping) |

#### Processing Flow

1. **Resolve `input_binding`**: Evaluate all expressions against `run.context.variables`.
2. **Check `input_require`**: Verify all required keys are present and non-null.
3. **Apply NodeDefinition** (if `_node_internals` exists):
   - Resolve `input_mapping` with context + custom (resolved input).
   - Merge: `resolved_input` + mapped values + `locked_defaults` (locked defaults win).
4. **CSV data mode** (if `_node_definition.data_mode` is `csv_single` or `csv_multi`):
   - Separate CSV-eligible fields from locked defaults.
   - Convert to CSV, upload to file-service, replace input with `{file_id, data_mode, ...locked_defaults}`.
5. **HITL pause** (if `edit_mode == "before_dispatch"`):
   - Set task status to `PENDING` instead of dispatching. Workflow pauses.
6. **Dispatch**: Set task to `RUNNING`, publish `task.dispatch` event.

#### Edge Conditions

No special edge conditions. Successors are processed by the SuccessorProcessor after task completion.

#### Port Conventions

| `ports` key | Typical Ports |
|-------------|---------------|
| `in` | `in_default` |
| `out` | `success`, `failure` |

#### State Output

Written to `run.context.variables` after task completion (by the successor processor):

```json
{
  "<node_id>": {
    "output": { /* task output_metadata */ },
    "metadata": { /* task metadata */ }
  }
}
```

#### Events Emitted

- `TaskDispatched(task_id, run_id, node_id, worker_id, source)`

#### JSON Example (Technical)

```json
{
  "id": "fetch-order",
  "name": "Fetch Order Details",
  "type": "TASK",
  "node_class": "TECHNICAL",
  "worker_id": "http-request-worker",
  "input_binding": {
    "url": "https://api.example.com/orders/{{$trigger.body.order_id}}",
    "method": "GET"
  },
  "config": {}
}
```

#### JSON Example (Business)

```json
{
  "id": "charge-payment",
  "name": "Charge Payment",
  "type": "TASK",
  "node_class": "BUSINESS",
  "worker_id": "http-request-worker",
  "node_definition_id": "nd-charge-payment",
  "input_binding": {
    "order_id": "{{$fetch-order.response_body.order_id}}",
    "amount": "{{$compute-total.output.result}}"
  },
  "config": {
    "_node_definition": {
      "data_mode": "csv_single",
      "edit_mode": "before_dispatch",
      "input_schema": [
        { "key": "order_id", "type": "string", "label": "Order ID", "required": true },
        { "key": "amount", "type": "number", "label": "Amount (USD)", "required": true }
      ]
    },
    "_node_internals": {
      "locked_defaults": { "method": "POST", "url": "https://api.example.com/payments/charge" },
      "input_mapping": {},
      "output_mapping": { "transaction_id": "{{$_output.response_body.transaction_id}}" },
      "base_worker_id": "http-request-worker"
    }
  }
}
```

---

### 4.3. CONDITION

**Category:** BUILTIN
**Purpose:** Binary branching based on a boolean expression. Routes to `then` or `else` branch and skips downstream nodes on the untaken branch.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `expression` | `string` | `""` | Boolean expression to evaluate (see [Section 5](#5-expression--variable-syntax)) |

#### Edge Conditions

| Condition Value | When Taken |
|-----------------|------------|
| `"then"`, `"true"`, `""` (empty) | Expression evaluates to `true` |
| `"else"`, `"false"` | Expression evaluates to `false` |

#### Downstream Skip Logic

Nodes on the untaken branch are recursively marked as **skipped**. A downstream node is skipped only if **all** its predecessors are skipped (prevents skipping nodes reachable from both branches).

#### Port Conventions

| `ports` key | Typical Ports |
|-------------|---------------|
| `in` | `in_default` |
| `out` | `true_branch`, `false_branch` |

#### State Output

```json
{
  "<node_id>": {
    "output": {},
    "metadata": { "type": "condition" }
  }
}
```

#### Events Emitted

- `ConditionEvaluated(run_id, node_id, expression, result, branch, source)`

#### JSON Example

```json
{
  "id": "check-amount",
  "name": "High Value?",
  "type": "CONDITION",
  "config": {
    "expression": "{{$compute-total.output.result}} > 500"
  },
  "ports": {
    "in": [{ "id": "in_default", "label": "Input" }],
    "out": [
      { "id": "true_branch", "label": "True" },
      { "id": "false_branch", "label": "False" }
    ]
  }
}
```

**Edges:**
```json
{ "source_node_id": "check-amount", "target_node_id": "approval-gate", "condition": "then" },
{ "source_node_id": "check-amount", "target_node_id": "merge-approval", "condition": "else" }
```

---

### 4.4. SWITCH

**Category:** BUILTIN
**Purpose:** Multi-way routing based on expression value matching. Evaluates an expression and routes to the matching case target.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `expression` | `string` | `""` | Expression to resolve to a value |
| `cases` | `list[dict]` | `[]` | Case definitions (see below) |
| `default` | `string` | — | Default target node ID if no case matches |

**Case definition:**

| Key | Type | Description |
|-----|------|-------------|
| `value` | `any` | Single value to match (string comparison) |
| `values` | `list[any]` | Multiple values to match (any match wins) |
| `target` | `string` | Target node ID for this case |

#### Edge Conditions

| Condition Value | When Taken |
|-----------------|------------|
| *(target match)* | Edge target matches the selected case target |
| `"case:<value>"` | Edge condition matches `case:{resolved_value}` |
| `"default"` | No case matched; fallback to default |

#### Downstream Skip Logic

Targets not taken are recursively marked as skipped (all downstream nodes via BFS).

#### State Output

```json
{
  "<node_id>": {
    "output": {},
    "metadata": { "type": "switch" }
  }
}
```

#### Events Emitted

*(none)*

#### JSON Example

```json
{
  "id": "route-by-region",
  "type": "SWITCH",
  "config": {
    "expression": "{{$input.region}}",
    "cases": [
      { "value": "US", "target": "us-handler" },
      { "value": "EU", "target": "eu-handler" },
      { "values": ["APAC", "JP"], "target": "apac-handler" }
    ],
    "default": "fallback-handler"
  }
}
```

---

### 4.5. LOOP

**Category:** BUILTIN
**Purpose:** Iterates over an array, executing body nodes for each item. Supports sequential and parallel execution modes.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `items_expression` | `string` | `""` | Expression resolving to an array to iterate over |
| `max_iterations` | `int` | `10000` | Maximum number of iterations (truncates array) |
| `mode` | `string` | `"sequential"` | `"sequential"` or `"parallel"` |
| `parallelism` | `int` | `len(items)` | Max concurrent iterations (parallel mode only) |

#### Edge Conditions

| Condition Value | Meaning |
|-----------------|---------|
| `"body"` | Entry point for the loop body |
| `"exit"` | Exit edge after all iterations complete |
| `""` (empty, non-exit) | Treated as body entry if no explicit `"body"` edge exists |

#### Loop Variable

During iteration, a `"loop"` key is written to `run.context.variables`:

```json
{
  "loop": {
    "index": 0,
    "item": { /* current array element */ },
    "total": 10,
    "first": true,
    "last": false,
    "depth": 0
  }
}
```

Access in expressions: `{{$loop.index}}`, `{{$loop.item.name}}`, `{{$loop.total}}`

#### Task Loop Context

Tasks created inside a loop carry `loop_context` and `iteration_index`:
- `loop_context`: `{"index": <n>, "item": <value>, "total": <total>}`
- `iteration_index`: integer index
- Task ID suffix: `-iter-{index}`

#### State Output

```json
{
  "<node_id>": {
    "output": {},
    "metadata": { "type": "loop", "total": 10, "mode": "sequential" }
  }
}
```

**Loop state** (in `run.context.loop_state`):

```json
{
  "<node_id>": {
    "items": [/* array */],
    "current_index": 3,
    "total": 10,
    "completed_iterations": [0, 1, 2],
    "failed_iterations": [],
    "mode": "sequential"
  }
}
```

#### Events Emitted

- `LoopIterationCompleted(run_id, node_id, iteration, total, source)` — emitted after each iteration

#### JSON Example

```json
{
  "id": "process-items",
  "type": "LOOP",
  "config": {
    "items_expression": "{{$fetch-order.response_body.items}}",
    "mode": "sequential",
    "max_iterations": 100
  }
}
```

**Edges:**
```json
{ "source_node_id": "process-items", "target_node_id": "process-single-item", "condition": "body" },
{ "source_node_id": "process-items", "target_node_id": "after-loop", "condition": "exit" }
```

---

### 4.6. PARALLEL

**Category:** BUILTIN
**Purpose:** Fan-out — dispatches multiple branches simultaneously. Can use explicit branch config or implicit edges.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `branches` | `list[dict]` | `[]` | Explicit branch definitions (see below) |
| `max_concurrency` | `int` | `0` | Max concurrent branches (0 = unlimited) |
| `fail_strategy` | `string` | — | `"fail_fast"` or other strategies |

**Branch definition:**

| Key | Type | Description |
|-----|------|-------------|
| `condition` | `string` | Optional expression; branch is skipped if false |
| `nodes` | `list[string]` | Node IDs in this branch (first = entry) |

#### Behavior

- **With `branches` config:** Each branch entry is dispatched. Conditional branches are evaluated; skipped branches have their nodes added to `skipped_node_ids`.
- **Without `branches` config:** All outgoing edges are dispatched as parallel branches.

#### Edge Conditions

No special edge conditions. All outgoing edges are followed.

#### Port Conventions

| `ports` key | Typical Ports |
|-------------|---------------|
| `in` | `in_default` |
| `out` | `branch_*` (one per branch) |

#### State Output

```json
{
  "<node_id>": {
    "output": {},
    "metadata": { "type": "parallel" }
  }
}
```

#### Events Emitted

*(none)*

#### JSON Example

```json
{
  "id": "parallel-fulfill",
  "type": "PARALLEL",
  "config": {
    "max_concurrency": 0,
    "fail_strategy": "fail_fast"
  },
  "ports": {
    "in": [{ "id": "in_default", "label": "Input" }],
    "out": [
      { "id": "branch_payment", "label": "Payment" },
      { "id": "branch_shipping", "label": "Shipping" }
    ]
  }
}
```

---

### 4.7. MERGE

**Category:** BUILTIN
**Purpose:** Fan-in — waits for multiple branches to complete before proceeding. Aggregates predecessor outputs.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `wait_strategy` | `string` | `"all"` | When to proceed: `"all"`, `"any"`, `"n_of_m"` |
| `n_required` | `int` | `0` | Number of predecessors required (for `n_of_m`) |
| `on_branch_failure` | `string` | `"fail"` | `"fail"` or `"partial"` (use defaults for failed branches) |
| `defaults` | `dict` | `{}` | Default values for missing/skipped branches |
| `inputs` | `list[dict]` | `[]` | Explicit input source config (see below) |

**Input spec (for `inputs` config):**

| Key | Type | Description |
|-----|------|-------------|
| `source` | `string` | Source node ID |
| `key` | `string` | Key name in merged output (defaults to source ID) |
| `required` | `bool` | Whether source must be completed |

#### Wait Strategies

| Strategy | Proceeds When |
|----------|---------------|
| `"all"` | All predecessors are completed or skipped |
| `"any"` | At least one predecessor is completed or skipped |
| `"n_of_m"` | At least `n_required` predecessors are done |

#### Behavior

- **With `inputs` config:** Merges specified sources into named keys. Skipped/missing sources use `defaults`.
- **Without `inputs` config:** Auto-merges all predecessor outputs keyed by predecessor node ID.

#### Port Conventions

| `ports` key | Typical Ports |
|-------------|---------------|
| `in` | `in_*` (one per branch) |
| `out` | `out_default` |

#### State Output

```json
{
  "<node_id>": {
    "output": {
      "payment_result": { /* predecessor output */ },
      "shipping_result": { /* predecessor output */ }
    },
    "metadata": {
      "type": "merge",
      "input_count": 2,
      "wait_strategy": "all"
    }
  }
}
```

#### Events Emitted

*(none)*

#### JSON Example

```json
{
  "id": "merge-fulfill",
  "type": "MERGE",
  "config": {
    "wait_strategy": "all"
  },
  "ports": {
    "in": [
      { "id": "in_payment", "label": "Payment" },
      { "id": "in_shipping", "label": "Shipping" }
    ],
    "out": [{ "id": "out_default", "label": "Output" }]
  }
}
```

---

### 4.8. COMPUTE

**Category:** BUILTIN
**Purpose:** Safe inline expression evaluator. Resolves variables, then evaluates the result as a mathematical/logical expression.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `expression` | `string` | `""` | Expression with variable references, resolved then evaluated |
| `result_key` | `string` | — | Optional top-level key to store the computed value in context |

#### Safe Expression Evaluator

The compute handler uses a sandboxed AST evaluator that supports:

**Operators:**
- Arithmetic: `+`, `-`, `*`, `/`, `//` (floor div), `%`, `**` (power)
- Comparison: `==`, `!=`, `<`, `<=`, `>`, `>=`
- Boolean: `and`, `or`
- Unary: `+`, `-`
- Ternary: `x if condition else y`

**Allowed functions:**
`abs`, `round`, `min`, `max`, `sum`, `len`, `int`, `float`, `str`

**Allowed literals:**
`int`, `float`, `str`, `bool`, `None`, `list`, `tuple`

**NOT allowed:** arbitrary name references, attribute access, imports, lambda, or any function not in the safe builtins list.

#### Processing Flow

1. Resolve variable references in the expression string (e.g. `{{$node.output.value}}` becomes `42`).
2. Evaluate the resolved string as a mathematical expression.
3. Store result in state output.

#### State Output

```json
{
  "<node_id>": {
    "output": { "result": 42 },
    "metadata": {
      "type": "compute",
      "expression": "{{$fetch-order.response_body.total}}",
      "resolved": "42"
    }
  }
}
```

If `result_key` is set, also: `run.context.variables[result_key] = computed_value`

#### Events Emitted

*(none)*

#### JSON Example

```json
{
  "id": "compute-total",
  "type": "COMPUTE",
  "config": {
    "expression": "{{$fetch-order.response_body.total}} * 1.1",
    "result_key": "order_total_with_tax"
  }
}
```

---

### 4.9. WORKFLOW

**Category:** BUILTIN
**Purpose:** Invokes another workflow as a child sub-workflow. Supports input mapping, output binding, failure handling, and timeout.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `workflow_id` | `string` | *(required)* | ID of the child workflow to invoke |
| `input_binding` | `dict` | `{}` | Expression map for child workflow input |
| `output_binding` | `dict` | `{}` | Expression map for extracting child output |
| `max_depth` | `int` | `10` | Maximum nesting depth |
| `on_failure` | `string` | `"fail"` | Failure strategy: `"fail"`, `"skip"`, etc. |
| `timeout_ms` | `int \| null` | `null` | Timeout in milliseconds |

#### Behavior

1. Checks nesting depth against `max_depth`. Raises `MaxDepthExceededError` if exceeded.
2. Resolves `input_binding` to produce child workflow input.
3. Creates a tracking task and stores child workflow metadata in state output.
4. The orchestration engine creates a child `WorkflowRun` with incremented `depth_level`.

#### State Output

```json
{
  "<node_id>": {
    "output": {},
    "metadata": {
      "type": "workflow",
      "child_workflow_id": "wf-sub-process",
      "child_input": { /* resolved input */ },
      "tracking_task_id": "<task_id>",
      "on_failure": "fail",
      "output_binding": {},
      "timeout_ms": null
    }
  }
}
```

#### Events Emitted

*(none — events emitted by child run)*

#### JSON Example

```json
{
  "id": "sub-process",
  "type": "WORKFLOW",
  "config": {
    "workflow_id": "wf-approval-flow",
    "input_binding": {
      "request_id": "{{$trigger.body.id}}",
      "amount": "{{$compute-total.output.result}}"
    },
    "output_binding": {
      "approved": "{{$_output.decision}}"
    },
    "max_depth": 5,
    "on_failure": "fail"
  }
}
```

---

### 4.10. INPUT

**Category:** PENDING
**Purpose:** Pauses the workflow to collect structured input from a user via a form. Resumes when the user submits data.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `input_schema` | `list[dict]` | `[]` | Schema for the input form (see [Section 7](#7-inputoutput-schema-format)) |
| `instruction` | `string` | `""` | Instruction text shown to the user |
| `input_types` | `dict` | — | Type coercion map for resolved input |
| `timeout_minutes` | `int \| null` | `null` | Timeout for user input |

#### Behavior

1. Resolves `input_binding` to pre-fill form fields.
2. Creates a task with `status = PENDING` and `worker_id = None`.
3. Stores form metadata in `task.output_metadata` for the frontend:
   ```json
   {
     "node_type": "INPUT",
     "input_schema": [...],
     "instruction": "...",
     "prefilled": { /* resolved input_binding values */ },
     "timeout_minutes": null
   }
   ```
4. Workflow pauses until the task is completed externally (user submits data).

#### Port Conventions

| `ports` key | Typical Ports |
|-------------|---------------|
| `in` | `in_default` |
| `out` | `out_default` |

#### Events Emitted

- `InputRequested(task_id, run_id, node_id, input_schema, source)`

#### JSON Example

```json
{
  "id": "collect-details",
  "type": "INPUT",
  "node_class": "TECHNICAL",
  "config": {
    "input_schema": [
      { "key": "customer_name", "outputType": "string", "fieldConfig": { "label": "Customer Name" }, "rules": { "isRequired": true } },
      { "key": "priority", "outputType": "string", "fieldConfig": { "label": "Priority", "fieldControl": "Select", "options": ["low", "medium", "high"] } }
    ],
    "instruction": "Please enter the customer details to proceed."
  },
  "input_binding": {
    "customer_name": "{{$fetch-customer.output.name}}"
  }
}
```

---

### 4.11. HUMAN_ACTION

**Category:** PENDING
**Purpose:** Pauses the workflow for a human to perform an action and confirm or reject. Similar to INPUT but with explicit confirm/reject semantics.

#### Config Fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `instruction` | `string` | `""` | Instruction describing the action to perform |
| `input_schema` | `list[dict]` | `[]` | Optional schema for data the human can fill in |
| `confirm_label` | `string` | `"Confirm"` | Label for the confirm button |
| `reject_label` | `string` | `"Reject"` | Label for the reject button |
| `input_types` | `dict` | — | Type coercion map for resolved input |

#### Behavior

1. Resolves `input_binding` to pre-fill context data.
2. Creates a task with `status = PENDING` and `worker_id = None`.
3. Stores action metadata in `task.output_metadata`:
   ```json
   {
     "node_type": "HUMAN_ACTION",
     "instruction": "Review and approve the order",
     "input_schema": [...],
     "confirm_label": "Approve",
     "reject_label": "Reject",
     "prefilled": { /* resolved input_binding values */ }
   }
   ```
4. Workflow pauses until the human confirms or rejects.

#### Port Conventions

| `ports` key | Typical Ports |
|-------------|---------------|
| `in` | `in_default` |
| `out` | `out_default` |

#### Events Emitted

- `HumanActionRequested(task_id, run_id, node_id, instruction, source)`

#### JSON Example

```json
{
  "id": "manager-review",
  "type": "HUMAN_ACTION",
  "node_class": "TECHNICAL",
  "config": {
    "instruction": "Review the order details and approve or reject.",
    "confirm_label": "Approve",
    "reject_label": "Send Back",
    "input_schema": [
      { "key": "notes", "outputType": "string", "fieldConfig": { "label": "Review Notes" } }
    ]
  },
  "input_binding": {
    "order_summary": "{{$compute-total.output.result}}",
    "customer": "{{$fetch-order.response_body.customer_email}}"
  }
}
```

---

### 4.12. Runtime Events (Cross-Cutting)

In addition to the per-node events listed above, the engine emits the following cross-cutting runtime events:

| Event | When Emitted | Payload |
|-------|-------------|---------|
| `task.failed` | A task transitions to FAILED status (after exhausting retries). | `{task_id, run_id, node_id, error, retry_count, source}` |
| `edge.traversed` | The engine follows an edge from one node to another during DAG traversal. | `{run_id, source_node_id, target_node_id, edge_id, condition, source}` |
| `artifact.added` | An artifact is attached to a task output (e.g. file produced by a worker). | `{task_id, run_id, node_id, artifact_file_id, artifact_name, artifact_type, source}` |

---

## 5. Expression & Variable Syntax

### Template Pattern

Expressions use double-brace syntax with a `$` prefix to reference data sources:

```
{{$<source>.<path>[<index>].<key>}}
```

**Regex:** `\{\{\$([a-zA-Z0-9_-]+)((?:\.[a-zA-Z0-9_]+|\[\d+\])*)\}\}`

### Available Sources

| Source | Resolves To | In `output_data`? | Description |
|--------|-------------|-------------------|-------------|
| `$<node_id>` | `run.context.variables[node_id]` | Yes | Output of a specific node (by ID) |
| `$<node_name>` | `run.context.variables[node_name]` | No (alias) | Output of a specific node (by display name — expression-only alias) |
| `$trigger` | `run.context.variables["trigger"]` | No (alias) | Trigger event data (`{type, body}`) — expression-only alias |
| `$input` | `run.context.variables["input"]` | Yes | Workflow input data |
| `$loop` | `run.context.variables["loop"]` | No (transient) | Current loop iteration context |
| `$_output` | *(context-dependent)* | N/A | Task output reference (used in output_mapping) |

### Path Traversal

Dot notation and array indexing are supported:

| Expression | Resolves To |
|------------|-------------|
| `{{$trigger.body}}` | `run.context.variables["trigger"]["body"]` |
| `{{$trigger.body.order_id}}` | Nested key access |
| `{{$node1.items[0].name}}` | Array index + key |
| `{{$loop.index}}` | Current loop index |
| `{{$node1}}` | Entire node output (empty path) |

### Meta-Path Expressions

Nodes that produce file-reference outputs (see [File Reference Format](#file-reference-format)) expose additional meta-path segments prefixed with double underscores. These paths let downstream nodes reference file metadata without fetching the file contents.

| Expression | Resolves To |
|------------|-------------|
| `{{$node.__file_id}}` | Primary `file_id` from the file reference |
| `{{$node.__artifacts[0].file_id}}` | `file_id` of the first artifact |
| `{{$node.__artifact.name.csv}}` | First artifact whose `name` ends with `.csv` |
| `{{$node.__content_type}}` | MIME content type of the primary file |
| `{{$node.__row_count}}` | Number of data rows (CSV outputs) |
| `{{$node.__columns}}` | List of column headers (CSV outputs) |

### String Interpolation

When an expression is embedded in a larger string, resolved values are interpolated:

```
"https://api.example.com/orders/{{$trigger.body.order_id}}"
```

Multiple expressions in one string are each resolved and substituted.

### Resolution Strategies

The VariableResolver uses a chain of strategies:

1. **MetadataResolverStrategy**: Checks `source_data.metadata.extracted` for pre-extracted values with JSON path keys.
2. **TraversalResolverStrategy**: Standard dot-notation and index traversal through nested dicts and lists.

If all strategies return `UNRESOLVED`, a `VariableResolutionError` is raised.

### Condition Expression Syntax

Used by CONDITION nodes. Supports boolean logic and comparison operators.

#### Logical Operators

| Operator | Behavior |
|----------|----------|
| `AND` | All sub-expressions must be true |
| `OR` | Any sub-expression must be true |

Parsed by splitting on ` AND ` and ` OR ` (case-sensitive, space-delimited). `OR` is evaluated first (at the top level), then `AND`.

#### Comparison Operators

| Operator | Description |
|----------|-------------|
| `==` | Equality |
| `!=` | Inequality |
| `>` | Greater than (numeric) |
| `>=` | Greater than or equal (numeric) |
| `<` | Less than (numeric) |
| `<=` | Less than or equal (numeric) |
| `IN` | Left value is contained in right (string or collection) |
| `CONTAINS` | Left string contains right substring |

#### Type Coercion Rules

When resolving condition values:

| Input | Coerced To |
|-------|------------|
| `"true"` | `True` (bool) |
| `"false"` | `False` (bool) |
| `"null"`, `"none"` | `None` |
| Quoted strings (`"..."`, `'...'`) | String (quotes stripped) |
| Integer-like | `int` |
| Float-like | `float` |
| Everything else | `string` |

#### Variable Resolver Type Coercion

The `coerce_value(value, target_type)` method supports:

| Target Type | Conversion |
|-------------|------------|
| `"string"` | `str(value)` |
| `"number"` | Parse as float/int |
| `"boolean"` | `true/1/yes` -> True, `false/0/no/""` -> False |
| `"object"` | Must already be `dict` |
| `"array"` | Must already be `list` |
| `"any"` | No conversion |

#### Example Conditions

```
{{$compute-total.output.result}} > 500
{{$fetch-order.response_body.status}} == "approved"
{{$node1.output.count}} >= 10 AND {{$node2.output.ready}} == true
{{$input.region}} IN "US,EU,APAC"
```

---

## 6. Business Node Features

### NodeDefinition Template Model

Business nodes are TASK nodes backed by a **NodeDefinition** template. The template provides:

- **Locked defaults**: Config values (e.g. HTTP method, URL) that are always merged into the task input. Users cannot override these.
- **Input/output schemas**: Simplified field definitions for auto-generated forms.
- **Input/output mappings**: Expression-based transformations applied at dispatch/completion time.
- **Data mode**: Controls whether form data is sent as JSON or converted to CSV.
- **Edit mode**: Controls whether the workflow pauses for user review before dispatching.

### Config Enrichment at Save Time

When a business node is saved to a workflow, its config is enriched with two special keys:

#### `_node_definition` (User-Facing)

Contains fields the frontend needs to render the node:

```json
{
  "_node_definition": {
    "data_mode": "csv_multi",
    "edit_mode": "before_dispatch",
    "input_schema": [
      { "key": "item_name", "type": "string", "label": "Item Name", "required": true },
      { "key": "quantity", "type": "number", "label": "Quantity", "required": true }
    ]
  }
}
```

#### `_node_internals` (Runtime)

Contains fields used by the engine at dispatch time:

```json
{
  "_node_internals": {
    "locked_defaults": { "method": "POST", "url": "https://api.example.com/endpoint" },
    "input_mapping": {},
    "output_mapping": { "result": "{{$_output.response_body}}" },
    "base_worker_id": "http-request-worker"
  }
}
```

### Data Modes

| Mode | Description | CSV Conversion |
|------|-------------|----------------|
| `json` | Standard JSON passthrough. No conversion. | None |
| `csv_single` | Form fields converted to a single-row CSV file. | Keys = headers, values = one row. Uploaded to file-service. |
| `csv_multi` | List of dicts converted to a multi-row CSV file. | Each dict = one row. Uploaded to file-service. |

#### CSV Conversion Flow

1. Separate `resolved_input` into CSV-eligible fields and `locked_defaults`.
2. For `csv_single`: Convert non-locked fields to single-row CSV bytes.
3. For `csv_multi`: Find the first key with a `list[dict]` value; convert to multi-row CSV.
4. Upload CSV bytes to file-service, get `file_id`.
5. Replace `resolved_input` with `{file_id, data_mode, ...locked_defaults}`.

#### File Reference Format

Task outputs that refer to uploaded files use a standard file-reference envelope. The `artifacts` array carries any secondary files produced alongside the primary output.

```json
{
  "__file_ref": true,
  "file_id": "...",
  "artifacts": [
    {"file_id": "...", "name": "...", "content_type": "...", "artifact_type": "...", "size_bytes": 0, "created_at": "..."}
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `__file_ref` | `bool` | Always `true`. Sentinel that marks the object as a file reference. |
| `file_id` | `string` | Primary file ID in the file-service. |
| `artifacts` | `list[dict]` | Zero or more secondary artifacts attached to the output. |
| `artifacts[].file_id` | `string` | Artifact file ID. |
| `artifacts[].name` | `string` | Human-readable file name. |
| `artifacts[].content_type` | `string` | MIME type (e.g. `"text/csv"`, `"application/pdf"`). |
| `artifacts[].artifact_type` | `string` | Logical artifact type (e.g. `"report"`, `"log"`). |
| `artifacts[].size_bytes` | `int` | File size in bytes. |
| `artifacts[].created_at` | `string` | ISO 8601 UTC creation timestamp. |

### Edit Modes

| Mode | Behavior |
|------|----------|
| `none` | Auto-dispatch. No user interaction. |
| `before_dispatch` | Workflow pauses (task set to `PENDING`). User reviews/edits data (CSV grid or form). On confirm, task dispatches to worker. |
| `after_dispatch` | Task dispatches immediately. Data remains viewable/editable in the run view for auditing or correction. |

### Input/Output Mapping Pipeline

For a business TASK node at dispatch time:

1. **Resolve `input_binding`** -> `resolved_input`
2. **Resolve `input_mapping`** (from `_node_internals`) with context = `{...run_variables, "custom": resolved_input}`
3. **Merge**: `resolved_input` + mapped values + `locked_defaults` (locked defaults take precedence)
4. **CSV conversion** (if data_mode is csv_single or csv_multi)
5. **HITL check** (if edit_mode is before_dispatch -> set task to PENDING)
6. **Dispatch** to worker

---

## 7. Input/Output Schema Format

### Auto-Form Field List Format

Used by `Workflow.input_schema`, `Workflow.output_schema`, and `INPUT` node config. This format supports auto-generation of forms by the frontend.

```json
[
  {
    "key": "order_id",
    "outputType": "string",
    "fieldConfig": {
      "label": "Order ID",
      "description": "Unique order identifier.",
      "fieldControl": "Input",
      "fieldWrapper": "FormItem",
      "options": ["option1", "option2"]
    },
    "rules": {
      "isRequired": true
    }
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `key` | `string` | Field identifier / form field name |
| `outputType` | `string` | Data type: `"string"`, `"number"`, `"boolean"`, `"object"`, `"array"`, `"file"` |
| `fieldConfig` | `dict` | UI configuration |
| `fieldConfig.label` | `string` | Display label |
| `fieldConfig.description` | `string` | Help text |
| `fieldConfig.fieldControl` | `string` | UI control type: `"Input"`, `"Select"`, `"Textarea"`, `"None"`, etc. |
| `fieldConfig.fieldWrapper` | `string` | Wrapper component: `"FormItem"`, etc. |
| `fieldConfig.options` | `list` | Options for Select controls |
| `rules` | `dict` | Validation rules |
| `rules.isRequired` | `bool` | Whether the field is required |

### Simplified CSV Schema Format

Used by `NodeDefinition.input_schema` and `NodeDefinition.output_schema`. A simpler format used for business node templates and CSV data grid columns.

```json
[
  { "key": "item_name", "type": "string", "label": "Item Name", "required": true },
  { "key": "quantity", "type": "number", "label": "Quantity", "required": true },
  { "key": "unit_price", "type": "number", "label": "Unit Price" },
  { "key": "invoice", "type": "file", "label": "Invoice PDF" }
]
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `string` | — | Field identifier / CSV column name |
| `type` | `string` | — | Data type: `"string"`, `"number"`, `"file"`, etc. |
| `label` | `string` | — | Display label |
| `required` | `bool` | `false` | Whether the field is required |

### Dynamic Source for Input Schema Fields

Schema fields can declare a `dynamic_source` object to populate their allowed values at runtime by calling a worker function. This is useful for dropdowns that need live data (e.g. listing database tables or available connections).

```json
{
  "key": "table_name",
  "type": "string",
  "dynamic_source": {
    "worker_function": "list_tables",
    "params": {"connection_id": "{{$locked.connection_id}}"},
    "value_path": "tables[*].name",
    "depends_on": ["connection_id"],
    "cache_ttl_seconds": 300
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `dynamic_source.worker_function` | `string` | Name of the worker function to invoke for option values. |
| `dynamic_source.params` | `dict` | Parameters passed to the worker function. Supports expression syntax (e.g. `{{$locked.connection_id}}`). |
| `dynamic_source.value_path` | `string` | JSONPath-style path into the worker response to extract the list of values. |
| `dynamic_source.depends_on` | `list[string]` | Other field keys whose values must be resolved before this source can be called. The frontend re-fetches when any dependency changes. |
| `dynamic_source.cache_ttl_seconds` | `int` | How long (in seconds) to cache the fetched values. `0` disables caching. |

---

## 8. Complete Example

See [`workflow-example.json`](./workflow-example.json) for a complete annotated Order Processing workflow demonstrating:

- **TRIGGER** (`trigger-1`): Event-based entry point for `orders.created`.
- **TASK (technical)** (`fetch-order`): Raw HTTP GET to fetch order details.
- **TASK (business, csv_multi)** (`review-items`): HITL node — order items displayed as editable CSV grid. Workflow pauses until user confirms.
- **COMPUTE** (`compute-total`): Recalculates order total from reviewed items.
- **CONDITION** (`check-amount`): Routes high-value orders (>$500) through approval.
- **TASK (technical)** (`approval-gate`): Wait-worker for manager approval.
- **MERGE** (`merge-approval`): Converges approval and direct paths.
- **PARALLEL** (`parallel-fulfill`): Fans out to payment and shipping.
- **TASK (business, csv_single)** (`charge-payment`): Form data as single-row CSV.
- **TASK (business, json)** (`create-shipment`): Standard JSON passthrough.
- **MERGE** (`merge-fulfill`): Waits for both payment and shipping.
- **TASK (business, csv_single)** (`notify-customer`): Sends confirmation email.

### Workflow DAG Topology

```
trigger-1 -> fetch-order -> review-items -> compute-total -> check-amount
                                                              |         |
                                                             then     else
                                                              |         |
                                                        approval-gate   |
                                                              |         |
                                                        merge-approval <+
                                                              |
                                                        parallel-fulfill
                                                          |           |
                                                    charge-payment  create-shipment
                                                          |           |
                                                        merge-fulfill
                                                              |
                                                        notify-customer
```
