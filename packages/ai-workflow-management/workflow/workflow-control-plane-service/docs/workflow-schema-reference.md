# Workflow Schema Reference — Frontend Integration Guide

> Supported schema contracts for frontend integration with the Workflow Control Plane API.
> All endpoints return JSON with envelope `{ "status": "ok", "data": ... }` on success
> and `{ "status": "error", "error": { "code": "...", "message": "..." } }` on failure.

**Base URL:** `http://localhost:8001` (configurable via API Gateway)

---

## Table of Contents

1. [Response Envelope](#1-response-envelope)
2. [Enums & Constants](#2-enums--constants)
3. [Core Schemas](#3-core-schemas)
4. [Auto-Form Field Schema](#4-auto-form-field-schema)
5. [Port Schema](#5-port-schema)
6. [Node Type Reference](#6-node-type-reference)
7. [API Endpoints](#7-api-endpoints)
8. [Node Palette API](#8-node-palette-api)
9. [Node Definition (Business Node) API](#9-node-definition-business-node-api)
10. [Workflow Run API](#10-workflow-run-api)
11. [Expression & Variable Syntax](#11-expression--variable-syntax)
12. [Socket.IO Events](#12-socketio-events)
13. [TypeScript Interfaces](#13-typescript-interfaces)
14. [Integration Examples](#14-integration-examples)

---

## 1. Response Envelope

All API responses follow a consistent envelope:

```jsonc
// Success
{
  "status": "ok",
  "data": { /* payload */ }
}

// Error
{
  "status": "error",
  "error": {
    "code": "WORKFLOW_NOT_FOUND",
    "message": "Workflow with id 'abc' not found"
  }
}
```

---

## 2. Enums & Constants

### WorkflowStatus

| Value | Description |
|-------|-------------|
| `DRAFT` | Initial state, editable |
| `VALID` | Passed validation |
| `DEPLOYED` | Active in production |
| `ARCHIVED` | Soft-deleted |

### NodeType (12 types)

| Value | Category | Description |
|-------|----------|-------------|
| `TRIGGER` | Control Flow | Workflow entry point |
| `CONDITION` | Control Flow | If/else branching |
| `SWITCH` | Control Flow | Multi-way branching |
| `LOOP` | Control Flow | Iteration over collection or count |
| `PARALLEL` | Control Flow | Fan-out parallel execution |
| `MERGE` | Control Flow | Fan-in join point |
| `COMPUTE` | Control Flow | Inline expression evaluation |
| `WORKFLOW` | Control Flow | Sub-workflow invocation |
| `INPUT` | Control Flow | Pause for user-provided data |
| `HUMAN_ACTION` | Control Flow | Pause for confirm/reject decision |
| `TASK` | Worker | Dispatched to external worker |

### NodeClass

| Value | Description |
|-------|-------------|
| `TECHNICAL` | Control-flow / infrastructure nodes |
| `BUSINESS` | Domain-specific nodes backed by a `NodeDefinition` |

### Builtin `worker_type` Convention

Control-flow nodes use the `builtin:` prefix for their `worker_type` identifier. These are handled by the engine internally and are never dispatched to an external worker.

| worker_type | Node Type |
|-------------|-----------|
| `builtin:condition` | CONDITION |
| `builtin:switch` | SWITCH |
| `builtin:loop` | LOOP |
| `builtin:parallel` | PARALLEL |
| `builtin:merge` | MERGE |
| `builtin:trigger` | TRIGGER |
| `builtin:workflow` | WORKFLOW |
| `builtin:compute` | COMPUTE |
| `builtin:input` | INPUT |
| `builtin:human_action` | HUMAN_ACTION |

Only `TASK` nodes use an external `worker_type` (e.g., `http-request`, `wait`). The node palette API returns `builtin:*` identifiers in the `id` field for control-flow palette items.

### RunStatus

| Value | Description |
|-------|-------------|
| `PENDING` | Created, not yet started |
| `RUNNING` | Actively executing |
| `COMPLETED` | Finished successfully |
| `FAILED` | Terminated with error |
| `CANCELLED` | Manually cancelled |
| `PAUSED` | Waiting for external input |

### TaskStatus

| Value | Description |
|-------|-------------|
| `PENDING` | Waiting for external input (INPUT / HUMAN_ACTION) |
| `WAITING` | Queued |
| `READY` | Ready to dispatch |
| `RUNNING` | Being processed by worker |
| `COMPLETED` | Finished |
| `FAILED` | Error |
| `RETRYING` | Auto-retry in progress |

---

## 3. Core Schemas

### 3a. Workflow

```jsonc
{
  "id": "uuid-v4",                     // auto-generated
  "name": "string",                    // required
  "version": "1",                      // string, default "1"
  "description": "",                   // optional
  "status": "DRAFT",                   // WorkflowStatus
  "input_schema": [],                  // auto-form fields (see §4)
  "output_schema": [],                 // auto-form fields (see §4)
  "nodes": [],                         // WorkflowNode[]
  "edges": [],                         // WorkflowEdge[]
  "purpose": "",                       // business purpose text
  "metadata": {},                      // arbitrary key-value
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z",
  "schema_version": "1.0.0"
}
```

### 3b. WorkflowNode

```jsonc
{
  "id": "uuid-v4",                     // unique node ID
  "name": "My Node",                   // display name
  "type": "TASK",                      // NodeType enum
  "worker_id": "http-request" | null,  // required for TASK nodes
  "parameters": {},                    // unified config from frontend (split by backend)
  "config": {},                        // engine-specific config (backend-managed)
  "input_binding": {},                 // variable mapping: { "url": "${nodes.trigger_1.output.url}" }
  "output_binding": {},                // output transformation
  "input_require": [],                 // list of required input keys
  "x": 100,                           // canvas X position (nullable)
  "y": 200,                           // canvas Y position (nullable)
  "node_class": "TECHNICAL",           // "TECHNICAL" | "BUSINESS"
  "description": "",                   // node description
  "instruction": "",                   // user-facing instruction (INPUT, HUMAN_ACTION)
  "icon": "Cog",                       // Lucide icon name
  "color": "#3B82F6",                  // hex color
  "tags": [],                          // string tags
  "ports": {                           // connection points
    "in": [{ "id": "in_default", "label": "Input", "data_type": "any", "required": true, "description": "" }],
    "out": [{ "id": "success", "label": "Success", "data_type": "any", "required": true, "description": "" }]
  },
  "node_definition_id": null           // references NodeDefinition for BUSINESS nodes
}
```

### 3c. WorkflowEdge

```jsonc
{
  "id": "uuid-v4",
  "source_node_id": "node-1-uuid",    // origin node ID
  "target_node_id": "node-2-uuid",    // destination node ID
  "source_port_id": "success",        // must match a port id in source node's out ports
  "target_port_id": "in_default",     // must match a port id in target node's in ports
  "condition": "then" | null,         // edge label for conditional routing
  "label": "",                        // display label
  "description": ""                   // optional description
}
```

---

## 4. Auto-Form Field Schema

Used in `input_schema`, `output_schema` (on Workflow, NodeDefinition, and node palette items).
Frontend should render dynamic forms from this schema.

```jsonc
{
  "key": "customer_id",                // unique field key (used as data key)
  "outputType": "string",             // "string" | "number" | "boolean" | "object" | "array"
  "fieldConfig": {
    "label": "Customer ID",            // display label
    "description": "Enter customer ID",// help text
    "fieldControl": "Input",           // UI control type (see table below)
    "fieldWrapper": "FormItem",        // wrapper component (always "FormItem" or null)
    "controlProps": {                  // props passed to the control
      "placeholder": "e.g. CUST-001",
      "options": [                     // only for Select
        { "label": "Option A", "value": "a" },
        { "label": "Option B", "value": "b" }
      ]
    }
  },
  "rules": {
    "isRequired": true                 // validation rule
  },
  "default": "CUST-001",              // optional default value
  "show_when": {                       // conditional visibility (optional)
    "field": "mode",                   // key of the field to watch
    "operator": "eq",                  // "eq" | "neq" | "contains"
    "value": "advanced"                // value to match
  }
}
```

### Supported `fieldControl` Types

| Value | Renders As | Notes |
|-------|-----------|-------|
| `Input` | Single-line text input | Default for strings |
| `Textarea` | Multi-line text area | For long text, JSON, expressions |
| `Select` | Dropdown | Requires `controlProps.options` |
| `Switch` | Boolean toggle | For boolean fields |
| `None` | Read-only display | Output-only fields, no user input |

---

## 5. Port Schema

Ports define the connection points on each node for the visual editor.

```jsonc
{
  "id": "success",            // unique within node, used in edge source_port_id / target_port_id
  "label": "Success",         // display label
  "data_type": "any",         // "any" | "string" | "number" | "boolean" | "object" | "array"
  "required": true,           // whether a connection is required
  "description": ""           // tooltip/help text
}
```

---

## 6. Node Type Reference

### Default Ports & Visual Config per Node Type

| Node Type | Icon | Color | In Ports | Out Ports |
|-----------|------|-------|----------|-----------|
| **TRIGGER** | `Zap` | `#F59E0B` | _(none)_ | `out_default` |
| **CONDITION** | `GitBranch` | `#8B5CF6` | `in_default` | `true_branch`, `false_branch` |
| **SWITCH** | `Shuffle` | `#6366F1` | `in_default` | `default` _(dynamic, add per case)_ |
| **PARALLEL** | `LayoutGrid` | `#0EA5E9` | `in_default` | _(dynamic, one per branch)_ |
| **MERGE** | `Merge` | `#10B981` | _(dynamic, one per incoming branch)_ | `out_default` |
| **LOOP** | `Repeat` | `#F97316` | `in_default` | `loop_body`, `loop_complete` |
| **TASK** | `Cog` | `#3B82F6` | `in_default` | `success`, `failure` |
| **COMPUTE** | `Code2` | `#64748B` | `in_default` | `success`, `failure` |
| **WORKFLOW** | `Workflow` | `#6366F1` | `in_default` | `success`, `failure` |
| **INPUT** | `FormInput` | `#8B5CF6` | `in_default` | `out_default` |
| **HUMAN_ACTION** | `UserCheck` | `#F59E0B` | `in_default` | `confirm`, `reject` |

### Node `parameters` Keys (Frontend → Backend Split)

When the frontend sends `parameters`, the backend splits them into `config` (engine settings) and `input_binding` (variable mappings). The frontend can send everything in `parameters` — the backend handles the split.

| Node Type | Config Keys (engine behavior) |
|-----------|-------------------------------|
| **CONDITION** | `expression` |
| **COMPUTE** | `expression`, `result_key` |
| **SWITCH** | `expression`, `cases`, `default` |
| **PARALLEL** | `branches`, `fail_strategy`, `max_concurrency` |
| **MERGE** | `wait_strategy`, `merge_strategy`, `n_required`, `inputs`, `defaults`, `on_branch_failure` |
| **LOOP** | `items_expression`, `mode`, `max_iterations`, `parallelism` |
| **TRIGGER** | `trigger_type`, `event_topic` |
| **WORKFLOW** | `workflow_id`, `input_binding`, `output_binding`, `max_depth`, `on_failure`, `timeout_ms` |
| **TASK** | `input_types` |
| **INPUT** | `input_schema`, `instruction`, `input_types`, `timeout_minutes` |
| **HUMAN_ACTION** | `instruction`, `input_schema`, `confirm_label`, `reject_label`, `input_types` |

### Node Configuration Detail

#### CONDITION

```jsonc
{
  "parameters": {
    "expression": "${nodes.prev_node.output.amount} > 1000"  // required, evaluates to boolean
  }
}
// Edges: true_branch → then path, false_branch → else path
```

#### SWITCH

```jsonc
{
  "parameters": {
    "expression": "${nodes.input_1.output.region}",           // required
    "cases": {
      "US": "us_branch",       // value → port_id
      "EU": "eu_branch"
    },
    "default": "default_branch"
  }
}
```

#### LOOP

```jsonc
{
  "parameters": {
    "items_expression": "${nodes.fetch_data.output.items}",   // collection to iterate
    "mode": "sequential",       // "sequential" | "parallel"
    "max_iterations": 100,
    "parallelism": 5            // only when mode = "parallel"
  }
}
// Edges: loop_body → iteration subgraph, loop_complete → after loop
```

#### PARALLEL

```jsonc
{
  "parameters": {
    "branches": ["branch_a", "branch_b"],
    "fail_strategy": "fail_fast",   // "fail_fast" | "wait_all"
    "max_concurrency": 5
  }
}
```

#### MERGE

```jsonc
{
  "parameters": {
    "wait_strategy": "all",          // "all" | "any" | "n_of"
    "merge_strategy": "combine",     // how to merge results
    "n_required": 2,                 // only when wait_strategy = "n_of"
    "on_branch_failure": "skip"      // "skip" | "fail"
  }
}
```

#### WORKFLOW (Sub-workflow)

```jsonc
{
  "parameters": {
    "workflow_id": "uuid-of-sub-workflow",        // required
    "input_binding": { "param1": "${nodes.x.output.val}" },
    "output_binding": { "result": "sub_output.data" },
    "max_depth": 10,
    "timeout_ms": 60000,
    "on_failure": "fail"             // "fail" | "skip"
  }
}
```

#### INPUT (Pause for user data)

```jsonc
{
  "parameters": {
    "instruction": "Please provide the approval details",
    "input_schema": [/* auto-form fields */],
    "timeout_minutes": 60
  }
}
```

#### HUMAN_ACTION (Confirm/Reject)

```jsonc
{
  "parameters": {
    "instruction": "Review and approve this order",
    "input_schema": [/* optional additional fields */],
    "confirm_label": "Approve",
    "reject_label": "Reject"
  }
}
// Edges: confirm → approved path, reject → rejected path
```

#### COMPUTE

```jsonc
{
  "parameters": {
    "expression": "${nodes.a.output.price} * ${nodes.a.output.quantity}",
    "result_key": "total"            // output stored as { "total": <result> }
  }
}
```

#### TRIGGER

```jsonc
{
  "parameters": {
    "trigger_type": "manual"         // "manual" | "schedule" | "event" | "webhook"
  }
}
```

---

## 7. API Endpoints

### Workflow CRUD

#### Create Workflow

```
POST /workflows
```

```jsonc
// Request body
{
  "name": "Order Processing",                       // required
  "description": "Handles incoming orders",
  "input_schema": [/* auto-form fields */],
  "output_schema": [/* auto-form fields */],
  "nodes": [/* WorkflowNode[] */],
  "edges": [/* WorkflowEdge[] */],
  "metadata": {},
  "schema_version": "1.0.0"
}
```

```jsonc
// Response
{
  "status": "ok",
  "data": {
    "id": "wf-uuid",
    "name": "Order Processing",
    "version": "1",
    "description": "Handles incoming orders",
    "status": "DRAFT",
    "nodes": [/* ... */],
    "edges": [/* ... */],
    "input_schema": [/* ... */],
    "output_schema": [/* ... */],
    "metadata": {},
    "created_at": "2026-01-01T00:00:00Z",
    "schema_version": "1.0.0"
  }
}
```

#### Get Workflow

```
GET /workflows/{workflow_id}
```

Response: same shape as create response `data`.

#### List Workflows

```
GET /workflows
```

```jsonc
// Response
{
  "status": "ok",
  "data": {
    "workflows": [/* WorkflowOutputDto[] */]
  }
}
```

#### Update Workflow (Full Replace)

```
PUT /workflows/{workflow_id}
```

Request body: same as create. All fields replaced.

#### Patch Workflow (Partial Update)

```
PATCH /workflows/{workflow_id}
```

Request body: any subset of create fields. Only provided fields are updated.

#### Delete Workflow

```
DELETE /workflows/{workflow_id}
```

#### Validate Workflow

```
POST /workflows/{workflow_id}/validate
```

```jsonc
// Response
{
  "status": "ok",
  "data": {
    "is_valid": true,
    "errors": []       // list of validation error strings if invalid
  }
}
```

#### Socket.IO Event Catalog

```
GET /events/config
```

Returns the full catalog of Socket.IO event names and their payload schemas. Useful for frontend auto-subscription and developer tooling.

#### List Worker Functions

```
GET /workers/{worker_type}/functions
```

Returns the list of callable functions exposed by a registered worker type (e.g., `http-request`).

#### Invoke Worker Function

```
POST /workers/{worker_type}/functions/{function_name}/invoke
```

Directly invoke a specific function on a worker outside of a workflow run. Useful for testing and ad-hoc execution.

#### Expression Syntax Reference

```
GET /expressions/reference
```

Returns the supported expression syntax, operators, and built-in functions available for use in node expressions and bindings.

#### List Node Artifacts

```
GET /runs/{run_id}/nodes/{node_id}/artifacts
```

Returns all artifacts (files, data snapshots) produced by a specific node during a run.

#### Add Node Artifact

```
POST /runs/{run_id}/nodes/{node_id}/artifacts
```

Attach a new artifact (file reference, data snapshot) to a node within a run.

---

## 8. Node Palette API

The node palette provides the complete catalog of available nodes for the workflow editor's drag-and-drop sidebar.

```
GET /node-palette?tenant_id={tenant_id}
```

```jsonc
// Response
{
  "status": "ok",
  "data": {
    "categories": {
      "control_flow": [
        {
          "id": "builtin:condition",
          "name": "Condition",
          "node_type": "CONDITION",
          "category": "control_flow",
          "description": "Evaluate an expression and branch",
          "worker_id": null,
          "capabilities": [],
          "input_schema": [/* auto-form fields for config */],
          "output_schema": [/* auto-form fields for output */],
          "node_class": "TECHNICAL",
          "icon": "GitBranch",
          "color": "#8B5CF6",
          "tags": ["branching"],
          "ports": {
            "in": [{ "id": "in_default", "label": "Input", "data_type": "any", "required": true }],
            "out": [
              { "id": "true_branch", "label": "True", "data_type": "any", "required": true },
              { "id": "false_branch", "label": "False", "data_type": "any", "required": true }
            ]
          }
        }
        // ... other control flow nodes
      ],
      "worker": [
        {
          "id": "worker:http-request",
          "name": "HTTP Request",
          "node_type": "TASK",
          "category": "worker",
          "description": "Make HTTP requests",
          "worker_id": "http-request",
          "worker_type": "http-request",
          "version": "1.0.0",
          "capabilities": [{ "domain": "http", "action": "request" }],
          "input_schema": [/* worker-specific auto-form fields */],
          "output_schema": [/* ... */],
          "config_hints": {},
          "node_class": "TECHNICAL",
          "icon": "Globe",
          "color": "#3B82F6",
          "tags": [],
          "ports": {
            "in": [{ "id": "in_default", "label": "Input", "data_type": "any", "required": true }],
            "out": [
              { "id": "success", "label": "Success", "data_type": "any", "required": true },
              { "id": "failure", "label": "Failure", "data_type": "any", "required": true }
            ]
          }
        }
      ],
      "business": [
        {
          "id": "business:node-def-uuid",
          "node_definition_id": "node-def-uuid",
          "name": "Fetch Customer Orders",
          "node_type": "TASK",
          "category": "business",
          "description": "Pre-configured HTTP call for customer orders",
          "worker_id": "http-request",
          "input_schema": [/* custom form fields */],
          "output_schema": [/* ... */],
          "icon": "ShoppingCart",
          "color": "#10B981",
          "tags": ["orders", "customer"]
        }
      ]
    },
    "total": 15,
    "node_types": [
      {
        "node_type": "TRIGGER",
        "node_class": "TECHNICAL",
        "icon": "Zap",
        "color": "#F59E0B",
        "ports": {
          "in": [],
          "out": [{ "id": "out_default", "label": "Output", "data_type": "any", "required": true }]
        }
      }
      // ... all 12 node types with defaults
    ]
  }
}
```

### Frontend Usage

1. **Fetch palette on editor load** — `GET /node-palette?tenant_id=xxx`
2. **Render sidebar** — group items by `categories` keys (`control_flow`, `worker`, `business`)
3. **On drag-drop** — create a node using the palette item's `node_type`, `icon`, `color`, `ports`, `worker_id`, and `node_definition_id`
4. **Render config panel** — use `input_schema` to render auto-form fields for the node's parameters

---

## 9. Node Definition (Business Node) API

Business nodes are reusable domain-specific templates that wrap a base worker with pre-configured defaults.

#### Create Node Definition

```
POST /node-definitions
```

```jsonc
{
  "tenant_id": "tenant-1",                           // required
  "name": "Fetch Customer Orders",                    // required
  "description": "Pre-configured HTTP call",
  "parent_definition_id": null,                       // for inheritance (optional)
  "base_type": "TASK",                                // underlying NodeType
  "base_worker_id": "http-request",                   // required when base_type = TASK
  "locked_defaults": {                                // hidden from user, injected at runtime
    "method": "GET",
    "base_url": "https://api.example.com"
  },
  "input_schema": [                                   // form fields shown to user
    {
      "key": "customer_id",
      "outputType": "string",
      "fieldConfig": { "label": "Customer ID", "fieldControl": "Input" },
      "rules": { "isRequired": true }
    }
  ],
  "output_schema": [
    {
      "key": "orders",
      "outputType": "array",
      "fieldConfig": { "label": "Orders", "fieldControl": "None" }
    }
  ],
  "input_mapping": {                                  // transform user fields → worker fields
    "url": "'/customers/' + customer_id + '/orders'"
  },
  "output_mapping": {                                 // extract from worker output
    "orders": "response.body.data"
  },
  "icon": "ShoppingCart",
  "color": "#10B981",
  "tags": ["orders", "customer"],
  "data_mode": "json",                                // "json" | "csv_single" | "csv_multi"
  "edit_mode": "none"                                 // "none" | "before_dispatch" | "after_dispatch"
}
```

#### List Node Definitions

```
GET /node-definitions?tenant_id={tenant_id}
```

#### Get / Patch / Delete

```
GET    /node-definitions/{id}
PATCH  /node-definitions/{id}    // partial update, same fields as create
DELETE /node-definitions/{id}
```

### Inheritance

Set `parent_definition_id` to extend another definition. At workflow save time, the backend resolves the full inheritance chain and merges:

- `locked_defaults`: child overrides parent
- `input_schema`: child fields appended after parent fields
- `output_schema`: child fields appended after parent fields
- `input_mapping` / `output_mapping`: child overrides parent keys

---

## 10. Workflow Run API

#### Start Run

```
POST /runs
```

```jsonc
// Request
{
  "workflow_id": "wf-uuid",          // required
  "input_data": { "key": "value" },  // matches workflow's input_schema
  "tenant_id": "tenant-1",
  "correlation_id": "req-123",
  "callback_topic": ""
}
```

```jsonc
// Response
{
  "status": "ok",
  "data": {
    "id": "run-uuid",
    "workflow_id": "wf-uuid",
    "status": "RUNNING",
    "started_at": "2026-01-01T00:00:00Z",
    "completed_at": null,
    "error": null,
    "output_data": null
  }
}
```

#### Get Run Status

```
GET /runs/{run_id}
```

Response: same shape as start run response `data`.

#### Run Lifecycle Actions

```
POST /runs/{run_id}/cancel
POST /runs/{run_id}/pause
POST /runs/{run_id}/resume
```

#### Complete a Pending Task (INPUT / HUMAN_ACTION)

When a run is `PAUSED` waiting for user input, submit data via:

```
POST /runs/{run_id}/tasks/{task_id}/complete
```

```jsonc
{
  "status": "COMPLETED",
  "output_reference": "",
  "output_metadata": {
    "submitted_data": { "approval_note": "Looks good" },
    "action": "confirm"              // for HUMAN_ACTION: "confirm" | "reject"
  },
  "idempotency_key": "unique-key"
}
```

#### Fail a Task

```
POST /runs/{run_id}/tasks/{task_id}/fail
```

```jsonc
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input provided",
    "details": {}
  }
}
```

#### Get Task Details

```
GET /runs/{run_id}/tasks/{task_id}
```

---

## 11. Expression & Variable Syntax

The engine supports variable interpolation in `input_binding`, `expression`, and mapping fields.

### Variable References

```
${nodes.<node_id>.output.<field>}     — Access another node's output
${input.<field>}                       — Access workflow-level input
${context.variables.<key>}             — Access runtime context variable
${loop.item}                           — Current loop item
${loop.index}                          — Current loop iteration index
```

### File & Artifact Meta Paths

Nodes that produce files (CSV workers, extract-data, modify-file) expose additional meta paths using the `__` prefix convention:

```
{{$node.__file_id}}                    — Primary CSV file_id produced by the node
{{$node.__artifacts}}                  — Full artifacts array for the node
{{$node.__artifacts[0].file_id}}       — First artifact's file_id
{{$node.__artifact.filename.csv}}      — Lookup artifact by name (e.g., "filename.csv")
{{$node.__content_type}}               — Content type of the primary output (e.g., "text/csv")
{{$node.__row_count}}                  — Row count of the primary CSV output
{{$node.__columns}}                    — Column names array of the primary CSV output
```

These meta paths are resolved at runtime from the node's output metadata and artifact store. They are particularly useful for chaining file-processing nodes where downstream nodes need to reference upstream file identifiers.

### Examples

```jsonc
// input_binding — map values from other nodes
{
  "url": "${nodes.trigger_1.output.webhook_url}",
  "body": "${nodes.compute_1.output.total}",
  "customer": "${input.customer_id}"
}

// CONDITION expression
"expression": "${nodes.compute_1.output.total} > 1000"

// COMPUTE expression
"expression": "${nodes.a.output.price} * ${nodes.a.output.quantity}"

// LOOP items_expression
"items_expression": "${nodes.fetch_data.output.items}"
```

---

## 12. Socket.IO Events

The control plane pushes real-time events over Socket.IO. Connect to `ws://localhost:8001` and join the run's room to receive events. Use `GET /events/config` for the full machine-readable catalog.

### Event Reference

| Event Name | Description | Payload Fields |
|-----------|-------------|----------------|
| `run.started` | Workflow run has begun | `run_id`, `workflow_id`, `started_at` |
| `run.completed` | Workflow run finished | `run_id`, `workflow_id`, `output_data`, `completed_at` |
| `run.failed` | Workflow run failed | `run_id`, `workflow_id`, `error` |
| `run.paused` | Run paused (INPUT/HUMAN_ACTION) | `run_id`, `workflow_id`, `task_id`, `node_id` |
| `node.started` | Node execution began | `run_id`, `workflow_id`, `node_id`, `node_type` |
| `node.completed` | Node execution finished | `run_id`, `workflow_id`, `node_id`, `output_data` |
| `node.failed` | Node execution failed | `run_id`, `workflow_id`, `node_id`, `error` |
| `task.dispatched` | Task sent to worker | `task_id`, `run_id`, `workflow_id`, `node_id`, `worker_type` |
| `task.completed` | Worker finished task | `task_id`, `run_id`, `workflow_id`, `node_id`, `output_data` |
| `task.failed` | Worker task failed | `task_id`, `run_id`, `workflow_id`, `node_id`, `error_code`, `error_message`, `attempt` |
| `edge.traversed` | An edge was followed during execution | `run_id`, `workflow_id`, `edge_id`, `source_node_id`, `target_node_id`, `source_port_id` |
| `artifact.added` | An artifact was attached to a node | `run_id`, `workflow_id`, `node_id`, `file_id`, `name`, `content_type`, `artifact_type` |

### Subscribing (Frontend)

```typescript
import { io } from "socket.io-client";

const socket = io("ws://localhost:8001");

// Join a run's room to receive its events
socket.emit("join_run", { run_id: "run-uuid" });

// Listen for events
socket.on("node.completed", (payload) => {
  console.log(`Node ${payload.node_id} completed`, payload.output_data);
});

socket.on("task.failed", (payload) => {
  console.error(`Task ${payload.task_id} failed: [${payload.error_code}] ${payload.error_message} (attempt ${payload.attempt})`);
});

socket.on("edge.traversed", (payload) => {
  console.log(`Edge ${payload.edge_id}: ${payload.source_node_id} -> ${payload.target_node_id}`);
});

socket.on("artifact.added", (payload) => {
  console.log(`Artifact "${payload.name}" (${payload.content_type}) added to node ${payload.node_id}`);
});
```

---

## 13. TypeScript Interfaces

Copy-paste ready interfaces for frontend development:

```typescript
// ─── Enums ───────────────────────────────────────────

type NodeType =
  | "TRIGGER" | "CONDITION" | "SWITCH" | "LOOP"
  | "PARALLEL" | "MERGE" | "COMPUTE" | "WORKFLOW"
  | "INPUT" | "HUMAN_ACTION" | "TASK";

type NodeClass = "TECHNICAL" | "BUSINESS";

type WorkflowStatus = "DRAFT" | "VALID" | "DEPLOYED" | "ARCHIVED";

type RunStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED" | "PAUSED";

type TaskStatus = "PENDING" | "WAITING" | "READY" | "RUNNING" | "COMPLETED" | "FAILED" | "RETRYING";

type FieldControl = "Input" | "Textarea" | "Select" | "Switch" | "None";

type DataType = "any" | "string" | "number" | "boolean" | "object" | "array";

// ─── Port ────────────────────────────────────────────

interface Port {
  id: string;
  label: string;
  data_type: DataType;
  required: boolean;
  description?: string;
}

// ─── Auto-Form Field ────────────────────────────────

interface AutoFormField {
  key: string;
  outputType: "string" | "number" | "boolean" | "object" | "array";
  fieldConfig: {
    label: string;
    description?: string;
    fieldControl: FieldControl;
    fieldWrapper?: "FormItem" | null;
    controlProps?: {
      placeholder?: string;
      options?: Array<{ label: string; value: string }>;
    };
  };
  rules?: {
    isRequired?: boolean;
  };
  default?: unknown;
  show_when?: {
    field: string;
    operator: "eq" | "neq" | "contains";
    value: unknown;
  };
}

// ─── Node ────────────────────────────────────────────

interface WorkflowNode {
  id: string;
  name: string;
  node_type: NodeType;
  worker_id?: string | null;
  parameters: Record<string, unknown>;
  config?: Record<string, unknown>;
  input_binding?: Record<string, unknown>;
  output_binding?: Record<string, unknown>;
  input_require?: string[];
  x?: number | null;
  y?: number | null;
  node_class: NodeClass;
  description?: string;
  instruction?: string;
  icon?: string;
  color?: string;
  tags?: string[];
  ports: {
    in: Port[];
    out: Port[];
  };
  node_definition_id?: string | null;
}

// ─── Edge ────────────────────────────────────────────

interface WorkflowEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  source_port_id: string;
  target_port_id: string;
  condition?: string | null;
  label?: string;
  description?: string;
}

// ─── Workflow ────────────────────────────────────────

interface Workflow {
  id: string;
  name: string;
  version: string;
  description: string;
  status: WorkflowStatus;
  input_schema: AutoFormField[];
  output_schema: AutoFormField[];
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  purpose?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
  schema_version: string;
}

// ─── Run ─────────────────────────────────────────────

interface WorkflowRun {
  id: string;
  workflow_id: string;
  status: RunStatus;
  started_at: string;
  completed_at?: string | null;
  error?: string | null;
  output_data?: Record<string, unknown> | null;
}

// ─── Node Palette ────────────────────────────────────

interface NodePaletteItem {
  id: string;
  name: string;
  node_type: NodeType;
  category: "control_flow" | "worker" | "business";
  description: string;
  worker_id?: string | null;
  node_definition_id?: string;
  capabilities?: Array<{ domain: string; action: string }>;
  input_schema: AutoFormField[];
  output_schema: AutoFormField[];
  node_class: NodeClass;
  icon: string;
  color: string;
  tags: string[];
  ports?: {
    in: Port[];
    out: Port[];
  };
}

interface NodePaletteResponse {
  categories: {
    control_flow: NodePaletteItem[];
    worker: NodePaletteItem[];
    business: NodePaletteItem[];
  };
  total: number;
  node_types: Array<{
    node_type: NodeType;
    node_class: NodeClass;
    icon: string;
    color: string;
    ports: { in: Port[]; out: Port[] };
  }>;
}

// ─── Node Definition ────────────────────────────────

interface NodeDefinition {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  parent_definition_id?: string | null;
  base_type: NodeType;
  base_worker_id?: string | null;
  locked_defaults: Record<string, unknown>;
  input_schema: AutoFormField[];
  output_schema: AutoFormField[];
  input_mapping: Record<string, unknown>;
  output_mapping: Record<string, unknown>;
  icon: string;
  color: string;
  tags: string[];
  data_mode: "json" | "csv_single" | "csv_multi";
  edit_mode: "none" | "before_dispatch" | "after_dispatch";
  created_at: string;
  updated_at: string;
}

// ─── API Response Envelope ──────────────────────────

interface ApiSuccess<T> {
  status: "ok";
  data: T;
}

interface ApiError {
  status: "error";
  error: {
    code: string;
    message: string;
  };
}

type ApiResponse<T> = ApiSuccess<T> | ApiError;
```

---

## 14. Integration Examples

### Example: Create a Simple Workflow

A workflow with TRIGGER → TASK → CONDITION → two end paths:

```jsonc
{
  "name": "Order Processing",
  "description": "Process incoming orders with approval logic",
  "input_schema": [
    {
      "key": "order_id",
      "outputType": "string",
      "fieldConfig": { "label": "Order ID", "fieldControl": "Input" },
      "rules": { "isRequired": true }
    }
  ],
  "nodes": [
    {
      "id": "trigger-1",
      "name": "Start",
      "type": "TRIGGER",
      "parameters": { "trigger_type": "manual" },
      "node_class": "TECHNICAL",
      "icon": "Zap",
      "color": "#F59E0B",
      "x": 100, "y": 200,
      "ports": {
        "in": [],
        "out": [{ "id": "out_default", "label": "Output", "data_type": "any", "required": true }]
      }
    },
    {
      "id": "task-1",
      "name": "Fetch Order",
      "type": "TASK",
      "worker_id": "http-request",
      "parameters": {},
      "input_binding": {
        "url": "https://api.example.com/orders/${input.order_id}"
      },
      "node_class": "TECHNICAL",
      "icon": "Cog",
      "color": "#3B82F6",
      "x": 300, "y": 200,
      "ports": {
        "in": [{ "id": "in_default", "label": "Input", "data_type": "any", "required": true }],
        "out": [
          { "id": "success", "label": "Success", "data_type": "any", "required": true },
          { "id": "failure", "label": "Failure", "data_type": "any", "required": true }
        ]
      }
    },
    {
      "id": "condition-1",
      "name": "Check Amount",
      "type": "CONDITION",
      "parameters": {
        "expression": "${nodes.task-1.output.amount} > 1000"
      },
      "node_class": "TECHNICAL",
      "icon": "GitBranch",
      "color": "#8B5CF6",
      "x": 500, "y": 200,
      "ports": {
        "in": [{ "id": "in_default", "label": "Input", "data_type": "any", "required": true }],
        "out": [
          { "id": "true_branch", "label": "True", "data_type": "any", "required": true },
          { "id": "false_branch", "label": "False", "data_type": "any", "required": true }
        ]
      }
    },
    {
      "id": "human-1",
      "name": "Manager Approval",
      "type": "HUMAN_ACTION",
      "parameters": {
        "instruction": "Order exceeds $1000. Please review and approve.",
        "confirm_label": "Approve",
        "reject_label": "Reject"
      },
      "node_class": "TECHNICAL",
      "icon": "UserCheck",
      "color": "#F59E0B",
      "x": 700, "y": 100,
      "ports": {
        "in": [{ "id": "in_default", "label": "Input", "data_type": "any", "required": true }],
        "out": [
          { "id": "confirm", "label": "Confirm", "data_type": "any", "required": true },
          { "id": "reject", "label": "Reject", "data_type": "any", "required": true }
        ]
      }
    }
  ],
  "edges": [
    {
      "id": "edge-1",
      "source_node_id": "trigger-1",
      "target_node_id": "task-1",
      "source_port_id": "out_default",
      "target_port_id": "in_default"
    },
    {
      "id": "edge-2",
      "source_node_id": "task-1",
      "target_node_id": "condition-1",
      "source_port_id": "success",
      "target_port_id": "in_default"
    },
    {
      "id": "edge-3",
      "source_node_id": "condition-1",
      "target_node_id": "human-1",
      "source_port_id": "true_branch",
      "target_port_id": "in_default",
      "condition": "then"
    }
  ]
}
```

### Example: Start a Run and Handle HUMAN_ACTION

```typescript
// 1. Start the workflow run
const runRes = await api.post("/runs", {
  workflow_id: "wf-uuid",
  input_data: { order_id: "ORD-42" },
  tenant_id: "tenant-1",
});
const runId = runRes.data.data.id;

// 2. Poll for status (or use Socket.IO for realtime updates — see §12)
const status = await api.get(`/runs/${runId}`);
// status.data.data.status === "PAUSED" when HUMAN_ACTION is waiting

// 3. Get the pending task
const task = await api.get(`/runs/${runId}/tasks/${taskId}`);

// 4. Submit human decision
await api.post(`/runs/${runId}/tasks/${taskId}/complete`, {
  status: "COMPLETED",
  output_metadata: {
    action: "confirm",            // or "reject"
    submitted_data: {
      approval_note: "Approved by manager"
    }
  }
});
```
