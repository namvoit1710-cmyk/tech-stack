# Frontend Integration — Master Guide

Entry point for the frontend team building on top of the **new data API** exposed by `workflow-control-plane-service`.

Read this page first, then jump into the topic-specific docs below.

---

## 1. What this service is

The control plane owns workflow definitions, runs, tasks, node state, and the artifact registry. It exposes two synchronous surfaces (REST + OData query pass-through) and one asynchronous surface (Socket.IO). Row-level data lives in a separate **file service**; the control plane only hands out identity (`file_id`, artifact descriptors).

```
┌────────────┐   REST   ┌──────────────────────────┐   dispatch   ┌─────────────────┐
│  Frontend  │ ───────▶ │   Control plane  :8001   │ ───────────▶ │ Worker executor │
│            │ ◀ — ——— │                          │              │     :8004       │
│            │ Socket.IO│  · workflow CRUD         │              └─────────────────┘
│            │          │  · run / task lifecycle  │                      │
│            │          │  · node data + artifacts │                      ▼
│            │ OData ───▶   · realtime events      │              ┌─────────────────┐
│            │          │                          │  identity    │   Workers       │
│            │          │                          │ ───────────▶ │ (http / agent   │
└────────────┘          └──────────────────────────┘              │  wait / mapping)│
       │                                                          └─────────────────┘
       │                      direct (file_id)
       └─────────────────────────────────────────▶   File service
                                                     (rows / blobs / OData)
```

**You talk to two services**:
- Control plane → identity, descriptors, lifecycle, events
- File service → row data, downloads, uploads

The control plane never inlines rows. Row fetches are either:
- scoped via `GET /runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata` (control plane tenancy-enforces, then forwards OData unmodified), or
- direct against the file service using a `file_id` you got from a control-plane response.

---

## 2. Mental model

| Concept | One-line |
|---|---|
| **workflow** | Immutable DAG definition: `nodes[]` + `edges[]` + `input_schema` + `output_schema`. |
| **node** | A unit of work in the DAG. 13 types: TASK, CONDITION, SWITCH, LOOP, PARALLEL, MERGE, TRIGGER, WORKFLOW, COMPUTE, INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD. |
| **run** | One execution of a workflow. Has a `status` (running/completed/failed/cancelled/paused) and a context. |
| **task** | One invocation of a node within a run. Loops + reruns create multiple tasks per node. |
| **port** | A labeled exit from a node (`out_default`, `then`, `else`, `confirm`, `reject`, `case_X`, …). Edges attach to source ports; the port taken determines which branch fires. |
| **expression** | `{{$source.path}}` — how a node references upstream data. Sources: node ids, `$input`, `$artifacts`, `$loop`, `$metadata`. |
| **scalars** | The inline primitive values a node emits (strings, numbers, booleans). Stored in the main CSV. |
| **artifact** | A table-backed output (array-of-dicts field or an uploaded file). Referenced by `artifact_id` + `file_id`. Can nest children. |
| **ArtifactDescriptor** | `{ artifact_id, file_id, columns, children[], artifact_type, parent_row_key_column }` — identity-only description the FE uses to render tables. |
| **pending node** | A node waiting on user input: INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD. Run stays live; FE must render a form and POST `/submit` when user is done. |
| **ETag** | Weak ETag returned on `/schema`, `/data`, `/artifacts`. Use `If-None-Match` on GET (→ 304), `If-Match` on PATCH (→ 412 if stale, 409 if missing). |

---

## 3. Response envelope

Every JSON response from the control plane is wrapped by `ResponseWrapperMiddleware`. Unwrap once on the client:

```jsonc
// Success
{ "status": "ok", "data": { /* endpoint payload */ } }

// Error
{ "status": "error",
  "data": null,
  "error": { "code": "NOT_FOUND", "message": "…", "details": { … } } }
```

HTTP status codes mirror the error class (`404 NOT_FOUND`, `400 BAD_REQUEST`, `409 PRECONDITION_REQUIRED`, `412 PRECONDITION_FAILED`, `413 PAYLOAD_TOO_LARGE`, `422 UNPROCESSABLE_ENTITY`, `423 LOCKED`). See [`01-api-reference.md`](01-api-reference.md#error-codes) for the full table.

---

## 4. Base URL & prefixes

Routes mount at the **root** of the service — there is no `/api/v1/` prefix.

```
GET  /runs
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/nodes/{node_id}/data
PATCH /runs/{run_id}/nodes/{node_id}/data
POST /runs/{run_id}/nodes/{node_id}/submit
… etc
```

Other top-level prefixes: `/workflows`, `/outputs`, `/node-palette`, `/node-definitions`, `/events`, `/workers`, `/expressions`, `/webhooks`, `/webhook-management`, `/health`.

Socket.IO lives at the default Socket.IO path (`/socket.io/`).

---

## 5. Quickstart — the 6-step happy path

Goal: start a run, show it executing, collect its output.

1. **Pick a workflow.** `GET /workflows` (optional `$filter=status eq 'active'`).
2. **Open the Socket.IO connection** and `emit('join:workflow', { workflow_id })`. See [`02-realtime-events.md`](02-realtime-events.md).
3. **Start the run.** `POST /runs` with `{ workflow_id, input_data }`. The response contains `id` (the `run_id`).
4. **Join the run room.** `emit('join:run', { run_id })` and subscribe to `run.*`, `task.*`, `edge.traversed`, `input.requested`, `artifact.added`.
5. **Render live state.** For any node the user clicks, `GET /runs/{run_id}/nodes/{node_id}/data?side=output` + `GET /runs/{run_id}/nodes/{node_id}/schema`. See [`04-output-panel.md`](04-output-panel.md).
6. **On `run.completed`**, read `output_data` from the event (or from `GET /runs/{run_id}` / `GET /outputs/{run_id}`).

If a `*.requested` event fires, render the matching form (see [`06-pending-nodes.md`](06-pending-nodes.md)).

---

## 6. The docs

| # | Doc | Use this when … |
|---|---|---|
| 01 | [API reference](01-api-reference.md) | You need the exact endpoint, method, DTO, and status codes. |
| 02 | [Realtime events](02-realtime-events.md) | Wiring Socket.IO, subscribing to rooms, handling event payloads. |
| 03 | [Input panel & drag-drop](03-input-panel-drag-drop.md) | Building the variable palette / drag source list on a node's input tab. |
| 04 | [Output panel](04-output-panel.md) | Rendering what a node produced (scalars + artifact tree). |
| 05 | [Artifacts viewer](05-artifacts-viewer.md) | Showing table data, filtering, paging, downloading. |
| 06 | [Pending nodes](06-pending-nodes.md) | Rendering forms for INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD. |
| 07 | [Data editing](07-data-editing.md) | Implementing "edit cell", "insert rows", "reset", and the PATCH /data contract. |
| 08 | [Rerun & cancel](08-rerun-and-cancel.md) | Rerun-from-here, cascade rerun, cancel / pause / resume. |
| 09 | [Design-time testing](09-design-time-testing.md) | Dry-run, single-node test, `is_test` runs. |
| 10 | [Palette & node definitions](10-node-palette-and-definitions.md) | Rendering the canvas palette, building dynamic forms from node definitions. |
| 11 | [Node-definition builder](11-node-definition-builder.md) | Building the "create a business node" UI (the tabbed NodeDefinition builder in `frontend-v2`). |

---

## 7. Related existing docs (still accurate)

These live under `workflow-control-plane-service/docs/` and are backend-flavored but still correct; link to them from the frontend docs above when more depth is needed.

- `docs/schema/node-schema-reference.md` — authoritative per-type node schema.
- `docs/schema/worker-list.json` — registry of available workers.
- `docs/csv-data-flow-guide.md` — how scalars / arrays travel through CSV under the hood.
- `docs/node-definition-integration-guide.md` + `docs/node-definition-mapping-guide.md` — building custom business nodes.
- `docs/worker-dynamic-functions.md` — dynamic-source dropdowns.
- `docs/output-mapping-user-stories.md` — output-mapping semantics.
- `docs/data-lineage-guide.md`, `docs/business-node-guide.md`.

The docs marked "retire" in the migration plan (`frontend-integration-guide.md`, `frontend-interactive-nodes-guide.md`, `input-data-api.md`, `socketio-integration-guide.md`, `node-artifacts-design.md`, `api-list-workflows-and-runs.md`) reference removed endpoints and are being replaced by the guides above.
