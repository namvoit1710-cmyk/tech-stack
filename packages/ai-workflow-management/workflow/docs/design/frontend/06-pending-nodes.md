# Pending Nodes — INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD

Four node types pause the run and ask the user for something. The run stays live; the handler parks the node in a `PENDING` state and emits a CRITICAL socket event. The frontend renders a form, collects the user's data, writes it into the node's working copy via `PATCH /data`, then finalizes with `POST /submit`. The engine transitions the node to `completed` and dispatches downstream.

---

## 1. The flow at a glance

```
run is running
   │
   ▼ handler hits a pending node
   ┌─────────────────────────────┐
   │ task.status = PENDING       │
   │ event fires (see below)     │
   └─────────────────────────────┘
   │
   ▼ FE renders form from schema
   ┌─────────────────────────────┐
   │ user fills form             │
   └─────────────────────────────┘
   │
   ▼ FE writes working copy
   PATCH /runs/{rid}/nodes/{nid}/data      (scalars + artifact_ops)
   │
   ▼ FE finalizes
   POST  /runs/{rid}/nodes/{nid}/submit    (no body)
   │
   ▼ engine routes downstream
```

Key constraint: **`POST /submit` has no body.** All of the user's data must already be in the node's working copy via PATCH. This keeps the data contract identical to the design-time editing path (see [`07-data-editing.md`](07-data-editing.md)) and keeps submit a pure state transition.

---

## 2. Which event triggers which UI

| Event | Node type | Payload essentials | FE shows |
|---|---|---|---|
| `input.requested` | INPUT, FILE_UPLOAD (via `controlProps.type === "file"`) | `run_id`, `node_id`, `task_id`, `input_schema`, `instruction?`, `prefilled?` | Auto-form from `input_schema`. |
| `human_action.requested` | HUMAN_ACTION | `run_id`, `node_id`, `task_id`, `actions[]` (port descriptors), `instruction?` | Confirm/reject button row. |
| `data_edit.requested` | DATA_EDIT | `run_id`, `node_id`, `task_id`, `artifact_id`, `file_id`, `schema` | Editable grid over the inbound artifact. |

All three are CRITICAL lane — never dropped, even under load.

If you missed the event (reconnect), detect pending nodes with:
```
GET /runs/{run_id}                    # status
GET /runs/{run_id}/nodes/{node_id}/data   # node_state === "pending"
```

---

## 3. Fetching the form spec

Regardless of which event fired, use the same two endpoints to render:

```
GET /runs/{run_id}/nodes/{node_id}/schema       # form spec + version
GET /runs/{run_id}/nodes/{node_id}/data         # current scalars + artifact descriptor
```

The `schema` body is an opaque FE-owned blob — the backend never interprets it. Use it to drive your auto-form library exactly as you would design-time (see [`10-node-palette-and-definitions.md`](10-node-palette-and-definitions.md)). The `schema_source` tells you if it was handler-computed at runtime (some nodes, e.g. DATA_EDIT, emit a data-derived schema).

Use `data.scalars` for prefill values. Use `data.root` to render the inbound artifact when the form references it.

---

## 4. INPUT — generic form

Workflow:
1. User fills the form in your auto-form renderer.
2. On submit:
   ```
   PATCH /runs/{rid}/nodes/{nid}/data
   Content-Type: application/json
   If-Match: W/"<etag>"
   {
     "scalars": { "customer_name": "Acme", "plan": "pro", "score": 0.9 }
   }
   ```
3. Then:
   ```
   POST /runs/{rid}/nodes/{nid}/submit
   ```
4. Expect `task.completed` (or `task.failed`) + downstream `task.dispatched` events.

If the form has a file-upload field, follow the FILE_UPLOAD flow below for just that field.

---

## 5. FILE_UPLOAD — upload then submit

The file itself bypasses the control plane. Three steps:

1. **Upload to the file service** directly from the browser. You get back `file_id`, `filename`, `content_type`, `size_bytes`. (Exact endpoint is deployment-specific; consult your file-service base URL.)
2. **PATCH the node's scalars** with the identity object:
   ```
   PATCH /runs/{rid}/nodes/{nid}/data
   If-Match: W/"<etag>"
   {
     "scalars": {
       "file": {
         "file_id":      "f_new",
         "filename":     "data.csv",
         "content_type": "text/csv",
         "size_bytes":   45678
       }
     }
   }
   ```
   The server will pull column + row-count info from the file service itself; you do not need to PATCH those.
3. **Submit**:
   ```
   POST /runs/{rid}/nodes/{nid}/submit
   ```

The node's output becomes the file metadata object; downstream nodes can reference it via `{{$this_node.file.file_id}}` etc., and the file is also registered in the run-level `$artifacts` as a metadata entry.

---

## 6. HUMAN_ACTION — confirm/reject

The event payload's `actions[]` is a port list, typically something like:
```jsonc
[
  { "port_id": "confirm", "label": "Approve",  "style": "primary" },
  { "port_id": "reject",  "label": "Decline",  "style": "danger"  }
]
```

Render one button per action. On click:

```
PATCH /runs/{rid}/nodes/{nid}/data
If-Match: W/"<etag>"
{ "scalars": { "_action": "confirm" } }

POST /runs/{rid}/nodes/{nid}/submit
```

The `_action` scalar drives which port fires — opposite branches get `task.skipped` events. The legacy `output.metadata.action` path is gone; `_action` is the contract.

If the action carries free-form fields (e.g. a rejection reason), PATCH those as regular scalars in the same call.

---

## 7. DATA_EDIT — editing a CSV

The user is handed an artifact (a CSV the upstream produced) and allowed to edit it. The event names `artifact_id` and `file_id`. Render an editable grid powered by the scoped OData endpoint (see [`05-artifacts-viewer.md`](05-artifacts-viewer.md)).

To save edits: use the same `artifact_ops` union as design-time editing ([`07-data-editing.md`](07-data-editing.md)). Bundle all the user's changes into one PATCH:

```
PATCH /runs/{rid}/nodes/{nid}/data
If-Match: W/"<etag>"
{
  "artifact_ops": [
    { "op": "update_cell", "row_path": [{"artifact_id": "orders", "row_index": 3, "filter": "$filter=sku eq 'ABC'"}], "column": "qty", "value": 150 },
    { "op": "insert_rows", "artifact_id": "orders", "rows": [{ "sku": "XYZ", "qty": 1 }] },
    { "op": "delete_rows", "artifact_id": "orders", "filter": "$filter=qty lt 0" }
  ]
}

POST /runs/{rid}/nodes/{nid}/submit
```

If the user abandons, either call `PATCH` with `{ "reset_to_input": true }` (reverts the working copy) or simply navigate away — the node stays pending until a submit or a run-level cancel.

---

## 8. UI state machine

```
 idle ──────▶ (event arrives)
              │
              ▼
           pending ────▶ (user edits) ───┐
              │                          │
              ▼                          │
           submitting ◀──────────────────┘
              │
    ┌─────────┴─────────┐
    ▼                   ▼
 completed           failed
 (close modal,      (show error,
  await downstream   reopen on retry)
  events)
```

Show a clear "pending — awaiting your input" badge on the node in the canvas while `node_state === "pending"`. Close the modal on `task.completed` for this node; roll back and show an error on `task.failed`.

---

## 9. Submit response

```jsonc
{
  "task_id":               "t_abc",
  "node_state":            "completed",
  "downstream_dispatched": true
}
```

If `downstream_dispatched` is false, the node completed but no successor task was queued (e.g. it was a terminal HUMAN_ACTION with no edges on the taken port). That's not an error.

---

## 10. Error cases

| Status | Meaning | FE action |
|---|---|---|
| `404` | Task or node not found. | Treat the pending interaction as cancelled; close. |
| `409 PRECONDITION_REQUIRED` on PATCH | Missing `If-Match`. | Re-GET `/data`, retry with the fresh etag. |
| `412 PRECONDITION_FAILED` on PATCH | Stale etag (someone else edited concurrently). | Re-GET `/data`, reconcile with the user's unsaved changes, retry. |
| `422 UNPROCESSABLE_ENTITY` on PATCH | Schema violation. | Show field-level errors from `error.details`. |
| `423 LOCKED` on `/submit` | Node not in `pending` or another submit is in flight. | Re-GET task state; usually auto-resolved. |
| `409` on `/submit` | Pending node already finalized (race). | Treat as already-done; look for the `task.completed` event. |

---

## 11. Source of truth

- Handlers: `workflow-control-plane-service/app/layer1_domain/services/node_handlers/{input,human_action,data_edit,file_upload}_handler.py`
- Submit use case: `…/features/submit_node/use_cases/submit_node_use_case.py`
- Edit use case (shared with design-time): `…/features/edit_node_data/use_cases/edit_node_data_use_case.py`
- Deprecated `resume_pending_task` has been superseded by the PATCH + submit pair; anything referencing `updated_input` is stale.
