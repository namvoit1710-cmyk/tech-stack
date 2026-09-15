# REST API Reference (New Engine)

Single lookup for every endpoint the frontend uses. Grouped by resource. All paths are relative to the service base URL. Every response body is wrapped in the `{ status, data, error }` envelope (see [`00-index.md`](00-index.md#3-response-envelope)).

Legend:
- **Auth**: none is enforced in code today; tenancy is query/body driven via `tenant_id` (defaults to `"system"`).
- **ETag**: weak ETags on `/schema`, `/data`, `/artifacts`.

---

## Workflows — `/workflows`

| Method | Path | Purpose |
|---|---|---|
| GET | `/workflows` | List workflows. OData: `$filter` (status/tenant_id/contains), `$skip`, `$top`, `$orderby`. |
| POST | `/workflows` | Create. Body: `CreateWorkflowRequest { name, description?, input_schema?, output_schema?, nodes[], edges[], metadata?, schema_version? }`. |
| GET | `/workflows/{workflow_id}` | Fetch full definition. |
| PUT | `/workflows/{workflow_id}` | Full replace. Body: `ReplaceWorkflowRequest`. |
| PATCH | `/workflows/{workflow_id}` | Partial update. Body: `PatchWorkflowRequest` (only provided fields applied; supports `routing_path`, `main_flow`). |
| DELETE | `/workflows/{workflow_id}` | Delete. |
| GET | `/workflows/by-path/{routing_path:path}` | Look up workflow by its webhook routing path. |
| GET | `/workflows/{workflow_id}/node-tree` | Hierarchical tree for graph UI. Query: `max_depth`, `include_edges`. |
| GET | `/workflows/main` | List workflows marked as main-flow entry points. |
| PATCH | `/workflows/{workflow_id}/main-flow` | Mark as main flow. Body: `{ main_flow: bool }`. |
| POST | `/workflows/{workflow_id}/validate` | Validate structure (no run). |

Design-time execution endpoints are in [`09-design-time-testing.md`](09-design-time-testing.md).

---

## Runs — lifecycle

| Method | Path | Purpose |
|---|---|---|
| GET | `/runs` | List runs. Filters: `workflow_id`, `status` (comma-separated), `tenant_id`, `is_test`. OData: `$filter` / `$skip` / `$top`. Status can be comma-separated. |
| POST | `/runs` | Start a run. Body: `StartRunRequest { workflow_id, input_data?, tenant_id?, correlation_id?, callback_topic?, predefined_run_id?, skip_validation? }`. Returns `RunOutputDto`. |
| GET | `/runs/{run_id}` | Status snapshot. Returns `RunOutputDto { id, workflow_id, status, started_at, completed_at?, error?, output_data? }`. |
| POST | `/runs/{run_id}/cancel` | Cancel. |
| POST | `/runs/{run_id}/pause` | Pause at next checkpoint. |
| POST | `/runs/{run_id}/resume` | Resume from pause. |

See [`08-rerun-and-cancel.md`](08-rerun-and-cancel.md) for cancel/pause/resume semantics and rerun.

---

## Runs — tasks

| Method | Path | Purpose |
|---|---|---|
| GET | `/runs/{run_id}/tasks/{task_id}` | Get a single task's state (status, inputs, outputs, timing). |
| POST | `/runs/{run_id}/tasks/{task_id}/complete` | Externally complete a task. Body: `CompleteTaskRequest { status?, output_reference?, output_metadata?, idempotency_key? }`. |
| POST | `/runs/{run_id}/tasks/{task_id}/fail` | Fail a task. Body: `FailTaskRequest { error, outputs }`. |
| POST | `/runs/{run_id}/tasks/{task_id}/rerun` | Rerun one task. Body: `RerunTaskRequest { new_input? }`. |
| GET | `/runs/{run_id}/nodes/{node_id}/execution` | All task invocations for a node (loop iterations, reruns). |

---

## Runs — node data (read)

| Method | Path | Purpose |
|---|---|---|
| GET | `/runs/{run_id}/context` | Full run context (`variables`, `artifacts`, upstream nodes). Useful for debugging. |
| GET | `/runs/{run_id}/nodes/{node_id}/variable-suggestions` | Reachable variable paths for wiring a node's inputs. See [`03-input-panel-drag-drop.md`](03-input-panel-drag-drop.md). |
| GET | `/runs/{run_id}/nodes/{node_id}/schema` | Auto-form schema + version + source. Supports `If-None-Match` → 304. Query: `task_id?`. |
| GET | `/runs/{run_id}/nodes/{node_id}/data` | Scalars + recursive `ArtifactDescriptor` tree. Query: `side=input\|output\|both` (default `input`), `task_id?`. Supports `If-None-Match` → 304. |
| GET | `/runs/{run_id}/artifacts` | Run-level `$artifacts` registry. Supports `If-None-Match` → 304. |
| GET | `/runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata` | Scoped OData query against a node-owned artifact. Query: any OData (`$filter`, `$orderby`, `$skip`, `$top`, `$count`, `$select`, `$expand`), plus `task_id?`. Control plane forwards to the file service after tenancy check. |

### `GetNodeDataResponse` shape (for `/nodes/{id}/data`)

```jsonc
{
  "run_id": "…",
  "node_id": "…",
  "task_id": "…",
  "node_kind": "TASK",
  "node_state": "completed",
  "schema_ref": { "version": "…", "source": "runtime" },
  "data":   { "etag": "…", "version": 3, "scalars": { … }, "root": ArtifactDescriptor | null },
  "output": { "etag": "…", "version": 3, "scalars": { … }, "root": ArtifactDescriptor | null },
  "produced_artifacts": ["orders", "invoice_pdf"],
  "etag": "…"
}
```

`data` is present when `side` is `input` or `both`; `output` is present when `side` is `output` or `both`. Each `ArtifactDescriptor` recurses via `children[]`:

```jsonc
{
  "artifact_id": "orders",
  "file_id": "f_abc…",
  "columns": ["sku", "qty", "region"],
  "parent_row_key_column": null,
  "children": [ /* nested ArtifactDescriptor entries for array-of-dicts columns */ ],
  "artifact_type": "array_field" | "nested_array_field" | null,
  "content_type": "text/csv"
}
```

### `GetRunArtifactsResponse` shape

```jsonc
{
  "artifacts": {
    "invoice_pdf": {
      "file_id": "f_123",
      "filename": "invoice.pdf",
      "content_type": "application/pdf",
      "size_bytes": 12345,
      "produced_by_node": "n_gen_invoice",
      "produced_by_task": "t_abc",
      "registered_at": "2026-04-16T10:12:00Z"
    }
  },
  "etag": "…"
}
```

---

## Runs — node data (write)

| Method | Path | Purpose |
|---|---|---|
| PATCH | `/runs/{run_id}/nodes/{node_id}/data` | Atomic batch of scalar / table / schema ops. Requires `If-Match`. See [`07-data-editing.md`](07-data-editing.md). |
| POST | `/runs/{run_id}/nodes/{node_id}/submit` | Finalize a pending node. **No body.** Data must already be in the working copy via a prior PATCH. Query: `task_id?`. Returns `SubmitNodeResponse { task_id, node_state, downstream_dispatched }`. |

---

## Runs — batch / lifecycle

| Method | Path | Purpose |
|---|---|---|
| POST | `/runs/{run_id}/rerun` | Run-level rerun. Body: `RerunRunRequestBody { from_node_id?, rerun_strategy? = "cascade", variables? }`. Returns `{ reset_node_ids[], dispatched_task_ids[] }`. |
| POST | `/runs/{run_id}/resolve` | Batch-evaluate up to 100 variable expressions against the run context. Body: `ResolvePathsRequestBody { paths: string[], task_id? }`. Returns `{ results: [{ path, status: "ok"\|"error", kind?, value?, error? }] }`. |

---

## Outputs — `/outputs`

| Method | Path | Purpose |
|---|---|---|
| POST | `/outputs` | Store a run's final output (externally computed). Body: `{ run_id, data, reference }`. |
| GET | `/outputs/{run_id}` | Fetch stored output. |

In most cases the FE reads `output_data` directly off `GET /runs/{run_id}` or the `run.completed` event; `/outputs` is a secondary stream for externally-produced results.

---

## Node palette — `/node-palette`

| Method | Path | Purpose |
|---|---|---|
| GET | `/node-palette?tenant_id=…` | List available node types for the canvas drawer. |

---

## Node definitions — `/node-definitions`

| Method | Path | Purpose |
|---|---|---|
| POST | `/node-definitions` | Create a reusable node type (supports inheritance via `parent_definition_id`). |
| GET | `/node-definitions` | List (OData + `tenant_id`). |
| GET | `/node-definitions/{id}` | Fetch one. |
| PATCH | `/node-definitions/{id}` | Partial update. |
| DELETE | `/node-definitions/{id}` | Delete. |
| POST | `/node-definitions/{id}/test` | Design-time test. |

See [`10-node-palette-and-definitions.md`](10-node-palette-and-definitions.md) for form generation from `input_schema` + `controlProps`.

---

## Expressions — `/expressions`

| Method | Path | Purpose |
|---|---|---|
| GET | `/expressions/reference` | Operators, functions, examples for the expression-builder helper. |

---

## Events (Socket.IO config) — `/events`

| Method | Path | Purpose |
|---|---|---|
| GET | `/events` | Event catalog + default rooms / priority lanes. |

Full event list in [`02-realtime-events.md`](02-realtime-events.md).

---

## Workers (external function invocation) — `/workers`

| Method | Path | Purpose |
|---|---|---|
| GET | `/workers/{worker_type}/functions` | List functions registered by a worker type. |
| POST | `/workers/{worker_type}/functions/{function_name}/invoke` | Invoke a worker function (used for dynamic dropdowns). Body: `{ params: {} }`. |

503 if no worker registry is configured.

---

## Webhooks (inbound gateway) — `/webhooks`

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhooks/{webhook_path:path}` | Smart-link: looks up workflow by `routing_path`, starts a run with the JSON body as `input_data`. |

---

## Webhook management — `/webhook-management`

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhook-management` | Create outbound webhook subscription. |
| GET | `/webhook-management` | List. OData + `workflow_id` + `tenant_id`. |
| GET | `/webhook-management/{webhook_id}` | Fetch one. |
| PATCH | `/webhook-management/{webhook_id}` | Update. |
| DELETE | `/webhook-management/{webhook_id}` | Delete. |
| GET | `/webhook-management/{webhook_id}/deliveries` | Delivery log for debugging. |

---

## Health — `/health`

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Basic health. |
| GET | `/health/eventmesh` | Event mesh broker state. |

---

## OData conventions

Endpoints that return lists support:
- `$filter=name eq 'foo' and status eq 'active'` — only `eq` and `contains` are parsed by the control plane; anything else is either forwarded (artifact OData pass-through) or ignored.
- `$skip=N`, `$top=N` — pagination (defaults: skip 0, top 50 on most endpoints; check per endpoint).
- `$orderby=field asc`.
- `$count=true` — returns `@odata.count` alongside `value`.

The scoped artifact endpoint (`/runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata`) forwards *all* OData params to the file service unmodified — `$select`, `$expand`, complex `$filter`, etc., are supported by the file service directly.

---

## ETag semantics

| Header | Applies to | Behavior |
|---|---|---|
| `If-None-Match` | `GET /schema`, `GET /data`, `GET /artifacts` | Weak match → `304 Not Modified`. |
| `If-Match` | `PATCH /data` | Required. Missing → `409 PRECONDITION_REQUIRED`. Stale → `412 PRECONDITION_FAILED`. |

ETags are weak (`W/"…"`). Store the raw token (or the whole `W/"…"` string) and pass it back verbatim.

---

## Error codes

All errors arrive in the envelope as `{ status: "error", error: { code, message, details? } }`. HTTP status mirrors the code:

| Code | HTTP | When |
|---|---|---|
| `BAD_REQUEST` | 400 | Client payload invalid. |
| `FORBIDDEN` | 403 | Tenancy / ownership violation (e.g. artifact not in node tree). |
| `NOT_FOUND` | 404 | Run / workflow / node / task / artifact missing. |
| `PRECONDITION_REQUIRED` | 409 | PATCH without `If-Match`. Also used for state conflicts (e.g. submit on a non-pending node). |
| `GONE` | 410 | Artifact deleted / replaced under your feet. |
| `PRECONDITION_FAILED` | 412 | Stale `If-Match`. Re-GET then retry. |
| `PAYLOAD_TOO_LARGE` | 413 | Too many rows / paths / ops (`resolve` caps at 100, `artifact_ops` at 1000, `insert_rows.rows` at 10 000). |
| `UNPROCESSABLE_ENTITY` | 422 | Schema violation (e.g. typed column got a non-coercible value). |
| `LOCKED` | 423 | Node is in a state that forbids the op (e.g. another submit in flight). |
| `INTERNAL_ERROR` | 500 | Unhandled. |

---

## Source of truth

Controllers live under `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/`. DTOs for the node-data surface are in `dtos/node_data_dtos.py`. If this doc drifts from code, code wins — grep the router file for the path, then follow the DTO imports.
