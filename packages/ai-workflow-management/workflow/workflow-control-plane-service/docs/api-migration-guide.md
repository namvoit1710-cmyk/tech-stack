# API Migration Guide — Upgrading to the Current `/api/v1` Surface

For external integrators who previously consumed the legacy unprefixed surface (`POST /runs`, `GET /runs/{run_id}/nodes/{node_id}/data-info`, `POST /runs/{run_id}/nodes/{node_id}/resume-input`, …). Maps every legacy endpoint to its current `/api/v1/*` successor with side-by-side examples.

> The "current" surface in this doc is the one in source code today (`workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/`). The "legacy" surface is the simpler one external systems were integrated against previously and is no longer served.

> Companion docs: [Frontend Integration Guide](./frontend-integration-guide.md), [Frontend Interactive Nodes Guide](./frontend-interactive-nodes-guide.md), [Input Data API](./input-data-api.md).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Base URL, Versioning, Response Envelope](#2-base-url-versioning-response-envelope)
3. [Endpoint Map (At-a-Glance)](#3-endpoint-map-at-a-glance)
4. [Migration Highlights](#4-migration-highlights)
5. [Endpoint Reference](#5-endpoint-reference)
6. [Migration Patterns (Recipes)](#6-migration-patterns-recipes)
7. [Error Codes](#7-error-codes)
8. [Appendix](#8-appendix)

---

## 1. Overview

### 1.1 What changed and why

Four shifts separate the legacy surface from the current `/api/v1/*` API:

- **Explicit version prefix.** All routes now sit under `/api/v1/`. This makes the contract version visible in the URL and gives the server room to evolve without silently breaking integrators.
- **Submit becomes two-step.** The single-shot `POST /resume-input` is replaced by `PATCH /data` (with optimistic concurrency via `If-Match`) and `POST /submit`. The split exists because `DATA_EDIT` workflows need autosave UX (write rows over time without resuming the node), and atomic batched operations need an etag round-trip to detect concurrent writers.
- **Unified `/data` path replaces `/data-info` and `/input`.** GET reads descriptors, PATCH edits rows. Verb determines semantics; `?side=input|output|both` selects which side of the node you're inspecting.
- **Per-node output folds into `/data?side=output`.** Same `ArtifactDescriptor` shape as input data, one endpoint to learn, one client method to maintain.

### 1.2 Migration model

**Clean cutover.** The legacy unprefixed surface is no longer served. Update your client end-to-end before pointing at a v1-only deployment.

### 1.3 Audience & scope

This doc is for **external integrators** who built clients against the legacy surface and need to upgrade to the current `/api/v1/*` API. Internal callers (other services in this repo) are out of scope.

---

## 2. Base URL, Versioning, Response Envelope

### 2.1 Base URL & prefix

The current surface is **prefixed with `/api/v1/`**. All examples below assume the host base — e.g. `https://workflow.example.com/api/v1/runs`. The legacy unprefixed paths are gone.

### 2.2 Standard response envelope

Every response — success or error — is wrapped:

```json
{
  "data": { ... },
  "error": null,
  "message": "Success",
  "timestamp": "2026-04-29T10:00:00Z"
}
```

Error variant:

```json
{
  "data": null,
  "error": { "code": "RUN_NOT_FOUND", "message": "Run not found" },
  "message": "Error",
  "timestamp": "2026-04-29T10:00:00Z"
}
```

JSON examples in §5 show only the contents of `data` for brevity.

### 2.3 Auth & tenancy headers

| Header | Purpose | Notes |
|---|---|---|
| `Authorization: Bearer <token>` | Caller identity | Required on every call. |
| `X-Tenant-Id` | Tenant scope | Required for multi-tenant endpoints; falls back to `system` for single-tenant deployments. |
| `Idempotency-Key` | Replay safety | Recommended on `POST /api/v1/runs` and the resume flow to deduplicate accidental retries. |

### 2.4 Optimistic concurrency

The current `PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data` (§5.8) gates writes on `If-Match: "<etag>"` — fetch the etag from the matching `GET .../data` response and echo it back. The legacy `POST /resume-input` had no etag check; once you migrate, every write needs an etag round-trip.

`If-None-Match` is supported on the GET side for 304 caching.

---

## 3. Endpoint Map (At-a-Glance)

| # | Capability | Legacy (old) | Current `/api/v1/*` (new) | Change class |
|---|---|---|---|---|
| 5.1 | trigger_workflow | `POST /runs` | `POST /api/v1/runs` | path-only |
| 5.2 | get_run_state | `GET /runs/{run_id}` | `GET /api/v1/runs/{run_id}` | path-only |
| 5.3 | get_task_details | `GET /runs/{run_id}/tasks/{task_id}` | `GET /api/v1/runs/{run_id}/tasks/{task_id}` | path-only |
| 5.4 | get_workflow_definition | `GET /workflows/{workflow_id}` | `GET /api/v1/workflows/{workflow_id}` | path-only |
| 5.5 | get_workflow_node_tree | `GET /workflows/{workflow_id}/node-tree` | `GET /api/v1/workflows/{workflow_id}/node-tree` | path-only |
| 5.6 | get_node_palette | `GET /node-palette` | `GET /api/v1/node-palette` | path-only |
| 5.7 | get_node_data_info | `GET /runs/{run_id}/nodes/{node_id}/data-info` | `GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=input` | renamed |
| 5.8 | update_node_input | `PATCH /runs/{run_id}/nodes/{node_id}/input` | `PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data` (with `If-Match`) | renamed + concurrency added |
| 5.9 | update_field_data | `POST /runs/{run_id}/nodes/{node_id}/resume-input` | `PATCH .../data` + `POST .../submit` (two-step) | **split** |
| 5.10 | get_node_execution | `GET /runs/{run_id}/nodes/{node_id}/execution` | `GET /api/v1/runs/{run_id}/nodes/{node_id}/execution` | path-only |
| 5.11 | get_node_output | `GET /runs/{run_id}/nodes/{node_id}/output` | `GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=output` | **folded** |

---

## 4. Migration Highlights

### 4.1 Add the `/api/v1` prefix

Every endpoint gains the prefix. If your client uses a base path constant, change it once. There is no behaviour change for any "path-only" row in §3.

### 4.2 `update_field_data`: one-shot → two-step

The biggest semantic shift. The legacy `POST /resume-input` carried both data and resume-the-node intent in one call. The current API splits them: `PATCH /data` writes artifact rows under an `If-Match` etag (any number of times, the node stays `PENDING`), and `POST /submit` advances the node out of `PENDING` with no body. See [§5.9](#59-update_field_data--patch--post-submit-two-step) for the split and [§6.1](#61-submitting-an-input-node) for the migration recipe.

The split is what enables autosave UX (write rows progressively without committing) and concurrency control (etag rejects stale writes from multi-tab clients).

### 4.3 `get_node_output`: dedicated endpoint → folded into `/data?side=output`

The legacy `GET .../output` returned the per-node output payload. The current API serves the same payload from the unified data endpoint with `?side=output`. The response shape is the same — just the URL and query param differ. See [§5.11](#511-get_node_output--get-runsrun_idnodesnode_iddatasideoutput).

### 4.4 `/data-info` and `/input` unified under `/data`

The legacy surface used two URLs to disambiguate read from write:
- `GET .../data-info` → read descriptors
- `PATCH .../input` → write rows

The current API unifies both under `/data`, with HTTP verb selecting semantics. `GET /data` returns descriptors (identity-only — no inline rows). `PATCH /data` writes rows under `If-Match`.

---

## 5. Endpoint Reference

Each entry follows the same template: **Old / New / Change class / Semantic diff / Request and Response (old & new) / Notes**. JSON shows only `data` envelope contents.

---

### 5.1 trigger_workflow — POST /api/v1/runs

**Old:** `POST /runs`
**New:** `POST /api/v1/runs`
**Change class:** path-only

**Semantic diff** — none. Body and response shapes unchanged; only the URL changes.

**Request — old & new (identical body)**
```json
{
  "workflow_id": "wf-order-processing",
  "input_data": { "order_id": "ord-42" },
  "tenant_id": "system",
  "correlation_id": "corr-abc",
  "callback_topic": "",
  "predefined_run_id": null,
  "skip_validation": true,
  "execution_mode": "auto"
}
```

`execution_mode` is `"auto"` (default — engine drives execution) or `"manual"` (run created in `SUSPENDED` state and waits for explicit `POST /api/v1/runs/{run_id}/nodes/{node_id}/trigger` calls).

**Response — old & new**
```json
{
  "id": "run-123",
  "workflow_id": "wf-order-processing",
  "status": "RUNNING",
  "started_at": "2026-04-29T10:00:00Z",
  "completed_at": null,
  "error": null,
  "output_data": null,
  "execution_mode": "auto"
}
```

**Notes**
- Send `Idempotency-Key` to deduplicate accidental retries.
- 400 `BAD_REQUEST` if `workflow_id` is missing or unknown.

---

### 5.2 get_run_state — GET /api/v1/runs/{run_id}

**Old:** `GET /runs/{run_id}`
**New:** `GET /api/v1/runs/{run_id}`
**Change class:** path-only

**Semantic diff** — none.

**Response — old & new**
```json
{
  "id": "run-123",
  "workflow_id": "wf-order-processing",
  "status": "COMPLETED",
  "started_at": "2026-04-29T10:00:00Z",
  "completed_at": "2026-04-29T10:02:14Z",
  "error": null,
  "output_data": null,
  "execution_mode": "auto"
}
```

**Notes**
- `status` values: `RUNNING`, `SUSPENDED`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`.
- 404 `RUN_NOT_FOUND` if the run does not exist.

---

### 5.3 get_task_details — GET /api/v1/runs/{run_id}/tasks/{task_id}

**Old:** `GET /runs/{run_id}/tasks/{task_id}`
**New:** `GET /api/v1/runs/{run_id}/tasks/{task_id}`
**Change class:** path-only

**Semantic diff** — none.

**Response — old & new**
```json
{
  "id": "task-abc",
  "run_id": "run-123",
  "node_id": "node-fetch-order",
  "status": "COMPLETED",
  "worker_type": "http_request_worker",
  "resolved_input": { "url": "https://api.example.com/orders/ord-42" },
  "output_ref": "file://outputs/run-123/task-abc.json",
  "output_metadata": { "size_bytes": 1234 },
  "retry_count": 0,
  "max_retries": 3,
  "error": null,
  "created_at": "2026-04-29T10:00:01Z",
  "started_at": "2026-04-29T10:00:02Z",
  "completed_at": "2026-04-29T10:00:05Z"
}
```

**Notes**
- 404 `TASK_NOT_FOUND` if the task is unknown.
- 404 `TASK_RUN_MISMATCH` if the task exists but does not belong to `run_id`.

---

### 5.4 get_workflow_definition — GET /api/v1/workflows/{workflow_id}

**Old:** `GET /workflows/{workflow_id}`
**New:** `GET /api/v1/workflows/{workflow_id}`
**Change class:** path-only

**Semantic diff** — none.

**Response — old & new (data envelope contents)**
```json
{
  "id": "wf-order-processing",
  "name": "Order Processing",
  "description": "Process new orders",
  "nodes": [ /* WorkflowNode list */ ],
  "edges": [ /* WorkflowEdge list */ ],
  "schema": { /* form schema */ },
  "main_flow": true,
  "created_at": "2026-04-01T08:00:00Z",
  "updated_at": "2026-04-15T14:30:00Z"
}
```

**Notes**
- 404 `WORKFLOW_NOT_FOUND` if the workflow is unknown.
- For tenant-scoped lists, use `GET /api/v1/workflows?$top=50` (OData pagination — see [api-list-workflows-and-runs.md](./api-list-workflows-and-runs.md)).

---

### 5.5 get_workflow_node_tree — GET /api/v1/workflows/{workflow_id}/node-tree

**Old:** `GET /workflows/{workflow_id}/node-tree`
**New:** `GET /api/v1/workflows/{workflow_id}/node-tree`
**Change class:** path-only

**Semantic diff** — none. Recursively resolves `WORKFLOW`-typed nodes (sub-workflows) up to `max_depth` (default 10).

**Query params** — `max_depth=<int>` (cap 10), `include_edges=<bool>`.

**Response — old & new**
```json
{
  "workflow_id": "wf-order-processing",
  "workflow_name": "Order Processing",
  "nodes": [
    {
      "id": "node-trigger",
      "name": "Start",
      "node_type": "TRIGGER",
      "depth": 0,
      "parent_workflow_id": "wf-order-processing",
      "children": null,
      "ports": { "in": [], "out": [{ "id": "default", "label": "" }] }
    },
    {
      "id": "node-sub",
      "name": "Run Validation",
      "node_type": "WORKFLOW",
      "depth": 0,
      "parent_workflow_id": "wf-order-processing",
      "children": {
        "workflow_id": "wf-validate",
        "workflow_name": "Order Validation",
        "nodes": [ /* nested nodes at depth=1 */ ],
        "edges": []
      }
    }
  ],
  "edges": [],
  "max_depth_reached": false,
  "total_nodes": 7,
  "total_workflows_resolved": 1
}
```

**Notes**
- Cycles are detected via the `visited` set — a child workflow that points back to an ancestor is not re-expanded.
- `max_depth_reached: true` indicates one or more sub-workflow expansions were truncated.

---

### 5.6 get_node_palette — GET /api/v1/node-palette

**Old:** `GET /node-palette`
**New:** `GET /api/v1/node-palette`
**Change class:** path-only

**Semantic diff** — none. Returns the three categories of nodes a designer can drag onto the canvas.

**Query params** — `tenant_id=<string>` (default `system`); business nodes are scoped to the tenant.

**Response — old & new**
```json
{
  "categories": {
    "control_flow": [
      {
        "id": "builtin:condition",
        "name": "Condition",
        "node_type": "CONDITION",
        "node_class": "CONTROL_FLOW",
        "icon": "GitBranch",
        "color": "#8B5CF6",
        "input_schema": [...],
        "output_schema": [...],
        "ports": { "in": [...], "out": [...] }
      }
    ],
    "worker": [
      {
        "id": "worker:http_request_worker",
        "name": "HTTP Request",
        "node_type": "TASK",
        "worker_id": "http_request_worker",
        "input_schema": [...],
        "output_schema": [...],
        "capabilities": [{ "domain": "http", "action": "request" }]
      }
    ],
    "business": [
      {
        "id": "business:nd-send-email",
        "node_definition_id": "nd-send-email",
        "name": "Send Email",
        "node_type": "TASK",
        "node_class": "BUSINESS",
        "input_schema": [...]
      }
    ]
  },
  "total": 21
}
```

**Notes**
- Worker nodes are filtered to those reporting `health_status` in `("healthy", "active")` or unset.
- A v2 catalog endpoint exists internally (`/api/v2/node-catalog`, gated on `ENABLE_NODE_CATALOG_V2`) — not part of this migration; the legacy `/node-palette` external integration maps to `/api/v1/node-palette`.

---

### 5.7 get_node_data_info — GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=input

**Old:** `GET /runs/{run_id}/nodes/{node_id}/data-info`
**New:** `GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=input`
**Change class:** renamed (URL change only, response shape unchanged)

**Semantic diff** — same response, unified URL. Returns `scalars` and a recursive artifact descriptor tree (identity only — `file_id`s, columns, no inline rows). Fetch table rows via the per-artifact OData endpoint using the returned `file_id`.

**Query params** — `side=input|output|both` (default `input`), `task_id=<id>` (loop iteration targeting), `If-None-Match: "<etag>"` for 304 caching.

**Response — old & new**
```json
{
  "run_id": "run-123",
  "node_id": "node-input-1",
  "task_id": "task-abc",
  "node_kind": "INPUT",
  "node_state": "PENDING",
  "schema_ref": { "schema_version": "v3", "schema_source": "design_time" },
  "data": {
    "etag": "v7",
    "version": 7,
    "root": {
      "artifact_id": "__root__",
      "file_id": "file-root-abc",
      "columns": ["username", "email"],
      "artifact_type": "object_field",
      "children": [
        {
          "artifact_id": "table_orders",
          "file_id": "file-orders-def",
          "columns": ["sku", "qty"],
          "artifact_type": "array_field",
          "children": []
        }
      ]
    },
    "output_port": null,
    "taken_edge_ports": null
  },
  "output": null,
  "produced_artifacts": [],
  "etag": "v7"
}
```

**Notes**
- 304 if `If-None-Match` matches the current etag.
- Use `?side=output` after the node completes to read its produced descriptors (§5.11).

---

### 5.8 update_node_input — PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data

**Old:** `PATCH /runs/{run_id}/nodes/{node_id}/input`
**New:** `PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data` (with `If-Match`)
**Change class:** renamed + optimistic concurrency added

**Semantic diff** — same body shape; URL changed and `If-Match` is now required. Apply a batch of row-level operations to a node's working-copy artifact tree. Use this for autosave / mid-edit writes that should **not** advance the node out of `PENDING`. To submit and resume the node, follow with `POST /submit` (§5.9).

**Headers** — `If-Match: "<etag>"` is required. 409 if missing, 412 if stale. Fetch the current etag via `GET /data?side=input` (§5.7) before each PATCH.

**Query params** — `task_id=<id>` (loop iteration targeting).

**Request — old & new (identical body)**
```json
{
  "artifacts": [
    {
      "artifact_id": "__root__",
      "operations": [
        { "operation": "edit", "row_id": "r1", "data": { "username": "alice", "email": "alice@example.com" } }
      ]
    },
    {
      "artifact_id": "table_orders",
      "operations": [
        { "operation": "insert", "row_id": null, "data": { "sku": "SKU-1", "qty": 3 } },
        { "operation": "delete", "row_id": "r-17" },
        { "operation": "add_column", "column": "region", "default_value": "US" }
      ]
    }
  ]
}
```

Operations: `insert` / `edit` / `delete` for rows; `add_column` / `drop_column` / `rename_column` for schema. Object-typed artifacts (the root scalar bag) accept only `insert` and treat it as upsert-row-1.

**Response — old & new**
```json
{
  "etag": "v8",
  "version": 8,
  "file_lineage": { "file-root-abc": "file-root-xyz" },
  "results": [
    { "artifact_id": "__root__", "op_index": 0, "status": "ok", "rows_affected": 1, "row_ids": ["r1"], "error": null },
    { "artifact_id": "table_orders", "op_index": 0, "status": "ok", "rows_affected": 1, "row_ids": ["r-new-1"], "error": null },
    { "artifact_id": "table_orders", "op_index": 1, "status": "ok", "rows_affected": 1, "row_ids": ["r-17"], "error": null },
    { "artifact_id": "table_orders", "op_index": 2, "status": "ok", "rows_affected": null, "row_ids": null, "error": null }
  ]
}
```

**Notes**
- Whole batch is atomic — any failure rolls back all writes and any newly-uploaded files are GC'd.
- Status codes: 400, 403, 409 (etag missing), 412 (etag stale), 422, 423.
- Migration: existing clients that called `PATCH /input` must now thread the etag through every call. Pattern: GET → keep etag → PATCH with `If-Match` → use the returned etag for the next PATCH.

---

### 5.9 update_field_data — PATCH .../data + POST .../submit (two-step)

**Old:** `POST /runs/{run_id}/nodes/{node_id}/resume-input` (single call)
**New:** `PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data` (with `If-Match`) → `POST /api/v1/runs/{run_id}/nodes/{node_id}/submit`
**Change class:** **split** (one-shot → two-step)

**Semantic diff**
The legacy `POST /resume-input` was a single call: it carried artifact operations and resumed the node atomically. The current API splits those concerns. `PATCH /data` writes artifact rows under `If-Match` (any number of times — node stays `PENDING`); `POST /submit` advances the node out of `PENDING` with no body. Use the split for `INPUT`, `HUMAN_ACTION`, `DATA_EDIT`, and `FILE_UPLOAD` nodes.

The split exists because:
- **Autosave UX** — `DATA_EDIT` clients want to persist edits progressively without committing.
- **Optimistic concurrency** — `If-Match` rejects stale writes from a second tab/integrator.
- **Atomic batched operations** — the PATCH body can contain dozens of row ops across multiple artifacts; batching them server-side is cheaper than chaining one-shots.

**Request — old (single call)**
```json
{
  "task_id": "task-abc",
  "artifacts": [
    {
      "artifact_id": "__root__",
      "operations": [
        { "operation": "edit", "row_id": "r1", "data": { "username": "alice" } }
      ]
    }
  ],
  "action": null
}
```

**Request — new (step 1: PATCH .../data?task_id=task-abc)**
Headers: `If-Match: "v7"`
```json
{
  "artifacts": [
    {
      "artifact_id": "__root__",
      "operations": [
        { "operation": "edit", "row_id": "r1", "data": { "username": "alice" } }
      ]
    }
  ]
}
```

**Request — new (step 2: POST .../submit?task_id=task-abc)**
Empty body.

**Response — old (single call)**
```json
{
  "run_id": "run-123",
  "node_id": "node-input-1",
  "task_id": "task-abc",
  "node_state": "RUNNING",
  "etag": "v8",
  "version": 8,
  "results": [
    { "artifact_id": "__root__", "op_index": 0, "status": "ok", "rows_affected": 1, "row_ids": ["r1"], "error": null }
  ]
}
```

**Response — new (PATCH result)**
```json
{
  "etag": "v8",
  "version": 8,
  "file_lineage": {},
  "results": [
    { "artifact_id": "__root__", "op_index": 0, "status": "ok", "rows_affected": 1, "row_ids": ["r1"], "error": null }
  ]
}
```

**Response — new (submit result)**
```json
{
  "run_id": "run-123",
  "node_id": "node-input-1",
  "task_id": "task-abc",
  "node_state": "RUNNING"
}
```

**Notes**
- **HUMAN_ACTION's `action` field.** The legacy body's `"action": "confirm" | "reject"` no longer rides on the resume call. Pass it as a `?action=confirm` query param on `POST /submit` (or per the `submit` endpoint's documented contract).
- 409 (etag missing) / 412 (etag stale) on PATCH.
- 422 on submit if the node is not `PENDING` (already resumed by another client).
- See recipes [§6.1](#61-submitting-an-input-node) and [§6.2](#62-editing-a-data_edit-artifact-then-submitting).

---

### 5.10 get_node_execution — GET /api/v1/runs/{run_id}/nodes/{node_id}/execution

**Old:** `GET /runs/{run_id}/nodes/{node_id}/execution`
**New:** `GET /api/v1/runs/{run_id}/nodes/{node_id}/execution`
**Change class:** path-only

**Semantic diff** — none. Returns the node's execution view: aggregate status across all task iterations, the input schema, edit-mode metadata, and a per-iteration `executions[]` array.

**Response — old & new**
```json
{
  "run_id": "run-123",
  "node_id": "node-fetch-order",
  "node_name": "Fetch Order",
  "node_type": "TASK",
  "status": "COMPLETED",
  "skipped": false,
  "input_schema": [{ "name": "url", "type": "string" }],
  "edit_mode": "none",
  "data_mode": "json",
  "executions": [
    {
      "task_id": "task-abc",
      "status": "COMPLETED",
      "iteration_index": 0,
      "resolved_input": { "url": "https://api.example.com/orders/ord-42" },
      "output": { "uri": "file://outputs/run-123/task-abc.json" },
      "output_metadata": { "size_bytes": 1234 },
      "error": null,
      "editable": false,
      "started_at": "2026-04-29T10:00:02Z",
      "completed_at": "2026-04-29T10:00:05Z"
    }
  ],
  "context_output": { "order_id": "ord-42", "total": 99.99 },
  "input_binding": { "url": "{{$workflow.input.api_url}}" },
  "output_binding": {}
}
```

**Notes**
- Aggregate `status` collapses task iterations: any `FAILED` → `FAILED`, all `COMPLETED` → `COMPLETED`, etc.
- `executions[].editable` is `true` only for `PENDING` tasks.

---

### 5.11 get_node_output — GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=output

**Old:** `GET /runs/{run_id}/nodes/{node_id}/output`
**New:** `GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=output`
**Change class:** **folded** (dedicated endpoint → query param on `/data`)

**Semantic diff** — same payload, served from the unified data endpoint. The `output` field of `GetNodeDataResponse` carries the per-node output `{etag, version, root: ArtifactDescriptor, output_port, taken_edge_ports}`. When `side=output`, the `data` (input) field is null and `output` is populated.

**Query params** — `side=output` (required to flip the response from input to output), `task_id=<id>` (loop iteration targeting), `If-None-Match: "<etag>"` for 304 caching.

**Request — old**
```
GET /runs/run-123/nodes/node-fetch-order/output
```

**Response — old**
```json
{
  "run_id": "run-123",
  "node_id": "node-fetch-order",
  "task_id": "task-abc",
  "etag": "v3",
  "version": 3,
  "root": {
    "artifact_id": "__root__",
    "file_id": "file-output-xyz",
    "columns": ["order_id", "total"],
    "children": []
  },
  "output_port": "success"
}
```

**Request — new**
```
GET /api/v1/runs/run-123/nodes/node-fetch-order/data?side=output
```

**Response — new**
```json
{
  "run_id": "run-123",
  "node_id": "node-fetch-order",
  "task_id": "task-abc",
  "node_kind": "TASK",
  "node_state": "COMPLETED",
  "schema_ref": { "schema_version": "v1", "schema_source": "runtime" },
  "data": null,
  "output": {
    "etag": "v3",
    "version": 3,
    "root": {
      "artifact_id": "__root__",
      "file_id": "file-output-xyz",
      "columns": ["order_id", "total"],
      "artifact_type": "object_field",
      "children": []
    },
    "output_port": "success",
    "taken_edge_ports": {}
  },
  "produced_artifacts": ["file-output-xyz"],
  "etag": "v3"
}
```

**Notes**
- `output.output_port` carries the port the last `NodeOutputProduced` emission took — `"success"` / `"failure"` for `TASK`, branch id for `SWITCH`.
- For `CONDITION` / `SWITCH` nodes, `taken_edge_ports` is `{edge_id: port_id}`; empty for other types.
- Use `?side=both` if you need input descriptors and output descriptors in one call (saves a round-trip).
- A run-level alternative `GET /api/v1/outputs/{run_id}` also exists in the current surface but is not the per-node successor — see [§8.2](#82-related-current-endpoints-not-part-of-the-migration).

---

## 6. Migration Patterns (Recipes)

### 6.1 Submitting an INPUT node

**Old (one-shot):**
```
POST /runs/{run_id}/nodes/{node_id}/resume-input
body: {
  "task_id": "task-abc",
  "artifacts": [{ "artifact_id": "__root__", "operations": [...] }],
  "action": null
}
```

**New (two-step with etag):**
```
1. GET   /api/v1/runs/{run_id}/nodes/{node_id}/data?side=input         → read etag "v7"
2. PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data?task_id=task-abc    If-Match: "v7"
   body: { "artifacts": [{ "artifact_id": "__root__", "operations": [...] }] }
   → response carries new etag "v8"
3. POST  /api/v1/runs/{run_id}/nodes/{node_id}/submit?task_id=task-abc
```

> **Loop iterations.** If the node is inside a `LOOP`, it can be paused multiple times concurrently. `task_id` is **required** in that case. Find active task ids via `GET /api/v1/runs/{run_id}/nodes/{node_id}/execution` (§5.10) — `executions[].task_id` where `status == "PENDING"`.

### 6.2 Editing a DATA_EDIT artifact then submitting

`DATA_EDIT` nodes typically need autosave UX — the user edits rows over time and only commits at the end. The two-step shines here:

```
# autosave (any number of times — node stays PENDING)
GET   /api/v1/runs/{run_id}/nodes/{node_id}/data?side=input    → etag "v7"
PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data               If-Match: "v7"
body: { "artifacts": [{ "artifact_id": "table_orders", "operations": [{...}] }] }
→ etag "v8"  (use this for the next PATCH)

# final commit (resumes the node)
POST  /api/v1/runs/{run_id}/nodes/{node_id}/submit
→ node_state: "RUNNING"
```

Track the latest etag in your client's per-node state. Every PATCH requires the **most recent** etag returned by either GET or a prior PATCH.

### 6.3 Reading a node's output

**Old:**
```
GET /runs/{run_id}/nodes/{node_id}/output
```

**New (single endpoint, side flag):**
```
GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=output
```

If your UI needs both the input that ran and the output produced (typical for a "node detail" panel), use `?side=both` and pull `data` and `output` from a single response.

---

## 7. Error Codes

The codes external clients typically branch on. All errors land in `error.code` of the response envelope.

| Code | HTTP | When | Client action |
|---|---|---|---|
| `RUN_NOT_FOUND` | 404 | `run_id` unknown | Navigate back; show "run not found". |
| `WORKFLOW_NOT_FOUND` | 404 | `workflow_id` unknown | Navigate back. |
| `NODE_NOT_FOUND` | 404 | `node_id` unknown for this run/workflow | Refresh the workflow definition. |
| `TASK_NOT_FOUND` | 404 | `task_id` unknown | Reload the run's task list. |
| `TASK_RUN_MISMATCH` | 404 | task exists but belongs to a different run | Treat as not-found. |
| `BAD_REQUEST` | 400 | malformed body / bad query param | Show validation error inline. |
| `CONFLICT` (etag missing) | 409 | `If-Match` header missing on `PATCH /data` | Re-fetch via `GET /data?side=input` to grab etag, retry. |
| `PRECONDITION_FAILED` | 412 | etag stale on `PATCH /data` | Re-fetch, surface "data changed", let the user re-confirm. |
| `UNPROCESSABLE_ENTITY` | 422 | node not in expected state (e.g. submit on non-PENDING) | Refresh node state; another client may have submitted. |
| `LOCKED` | 423 | node currently being edited by another writer | Surface "in use", retry after delay. |

The legacy surface had no `If-Match` semantics — 409 / 412 are new failure modes external clients must handle after migration.

---

## 8. Appendix

### 8.1 DTO reference

The JSON shapes above are derived from these source-of-truth Pydantic models in the current codebase:

- `StartRunRequest` — [`run_controller.py:50`](../app/layer3_adapters/controllers/restful/v1/run_controller.py)
- `RunOutputDto` — [`run_controller.py:80`](../app/layer3_adapters/controllers/restful/v1/run_controller.py)
- `PatchNodeDataRequest` — [`dtos/node_data_dtos.py:84`](../app/layer3_adapters/controllers/restful/v1/dtos/node_data_dtos.py) (carries worked examples in `json_schema_extra`)
- `GetNodeDataResponse` / `NodeDataPayload` / `ArtifactDescriptor` — [`dtos/node_data_dtos.py:206-242`](../app/layer3_adapters/controllers/restful/v1/dtos/node_data_dtos.py)
- `PatchNodeDataResponse` / `OpResult` — [`dtos/node_data_dtos.py:244-258`](../app/layer3_adapters/controllers/restful/v1/dtos/node_data_dtos.py)
- `GetTaskResult` — [`get_task/use_cases/get_task_use_case.py:18`](../app/layer2_application/features/get_task/use_cases/get_task_use_case.py)
- `GetNodeExecutionResult` / `ExecutionEntry` — [`get_node_execution/use_cases/get_node_execution_use_case.py:21,37`](../app/layer2_application/features/get_node_execution/use_cases/get_node_execution_use_case.py)
- `GetWorkflowNodeTreeResult` — [`get_workflow_node_tree/use_cases/get_workflow_node_tree_use_case.py:21`](../app/layer2_application/features/get_workflow_node_tree/use_cases/get_workflow_node_tree_use_case.py)
- `ListNodePaletteResult` — [`list_node_palette/use_cases/list_node_palette_use_case.py:67`](../app/layer2_application/features/list_node_palette/use_cases/list_node_palette_use_case.py)

### 8.2 Related current endpoints not part of the migration

These endpoints exist in the current surface but have no legacy counterpart in the external integration:

| Current endpoint | Purpose |
|---|---|
| `GET /api/v1/runs/{run_id}/nodes/{node_id}/schema` | Auto-form schema + version + source for a node (with ETag caching). |
| `GET /api/v1/runs/{run_id}/artifacts` | Run-level `$artifacts` registry (named artifacts). |
| `GET /api/v1/runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata` | Per-artifact OData rows query. |
| `GET /api/v1/runs/{run_id}/nodes/{node_id}/variable-suggestions` | Reachable variable paths for designer UX. |
| `POST /api/v1/runs/{run_id}/resolve` | Batch evaluate `{{$expressions}}`. |
| `GET /api/v1/outputs/{run_id}` | Run-level output blob (legacy/internal — prefer per-node `GET /data?side=output`). |
| `POST /api/v1/runs/{run_id}/cancel` / `pause` / `resume` | Run lifecycle controls. |
| `POST /api/v1/runs/{run_id}/nodes/{node_id}/trigger` | Manual-mode node trigger. |

### 8.3 Removed endpoints

The following legacy endpoints have **no current successor**. If your client uses them, the call site needs to be redesigned per the linked recipe.

| Legacy endpoint | Replacement strategy |
|---|---|
| `POST /runs/{run_id}/nodes/{node_id}/resume-input` (one-shot) | Two-step: `PATCH /data` + `POST /submit` — see [§5.9](#59-update_field_data--patch--post-submit-two-step) and [§6.1](#61-submitting-an-input-node). |
| `GET /runs/{run_id}/nodes/{node_id}/data-info` | Renamed to `GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=input` — see [§5.7](#57-get_node_data_info--get-apiv1runsrun_idnodesnode_iddatasideinput). |
| `PATCH /runs/{run_id}/nodes/{node_id}/input` | Renamed to `PATCH /api/v1/runs/{run_id}/nodes/{node_id}/data` with required `If-Match` — see [§5.8](#58-update_node_input--patch-apiv1runsrun_idnodesnode_iddata). |
| `GET /runs/{run_id}/nodes/{node_id}/output` | Folded into `GET /api/v1/runs/{run_id}/nodes/{node_id}/data?side=output` — see [§5.11](#511-get_node_output--get-apiv1runsrun_idnodesnode_iddatasideoutput). |

### 8.4 Changelog

- **2026-04-29** — Initial publication of this migration guide.
