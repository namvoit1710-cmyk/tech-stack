# Output Panel — Rendering Node Output

What to render when the user clicks a node in a completed or in-progress run and asks "what did this node produce?".

---

## 1. The two calls you need

For a given `(run_id, node_id)`:

```
GET /runs/{run_id}/nodes/{node_id}/data?side=output     # identity + scalars
GET /runs/{run_id}/nodes/{node_id}/schema               # labels + output_type per field
```

Both support `If-None-Match` → 304. Both optionally take `?task_id=` for loop iterations.

---

## 2. The response shape

```jsonc
{
  "run_id":       "…",
  "node_id":      "…",
  "task_id":      "…",
  "node_kind":    "TASK",
  "node_state":   "completed" | "running" | "failed" | "skipped" | "pending" | "idle",
  "schema_ref":   { "version": "v12", "source": "runtime" | "design_time" },
  "data":         null,                                // input side; null when side=output
  "output": {
    "etag":    "…",
    "version": 3,
    "scalars": { "status": "ok", "score": 0.92, "message": "…" },
    "root":    ArtifactDescriptor | null               // recursive tree (identity only)
  },
  "produced_artifacts": ["orders", "invoice_pdf"],     // names to look up in /runs/{id}/artifacts
  "etag":    "…"
}
```

Same shape for `side=input` (populates `data`, leaves `output` null) and `side=both` (populates both).

### Per-field type

The top-level response does not carry output types. Zip scalars with `GET /schema` → for each scalar key, find the matching field in the schema tree to label it (`String`, `Number`, `File`, etc.). Schema is ETag-cached with `schema_version`; re-fetch only when that changes.

---

## 3. Render tree

```
┌───────────────────────────────────────────────────────────┐
│ Node: <node_name>                      State: <node_state>│
│ Port taken: <port_id>   (from edge.traversed / task.port) │
├───────────────────────────────────────────────────────────┤
│ Scalars                                                   │
│  • status  : "ok"                                         │
│  • score   : 0.92                                         │
│  • message : "Processed 1234 rows"                        │
├───────────────────────────────────────────────────────────┤
│ Artifacts                                                 │
│  ▼ orders (table, 1 234 rows, 8 columns)                  │
│     ┌─────────────────────────┐                           │
│     │ (first 5 rows preview)  │   [Open full viewer]      │
│     └─────────────────────────┘                           │
│    ▼ line_items (nested, 3 456 rows)                      │
│       (first 5 rows preview)                              │
│  ▶ invoice_pdf (file: application/pdf, 12 kB)             │
│     [Download]                                            │
└───────────────────────────────────────────────────────────┘
```

### 3a. Scalars

Render as a property list. Apply the output-type badge from the schema. For null values show `—`. For `object`/`array` scalars (rare but legal) render a collapsible JSON viewer.

### 3b. `ArtifactDescriptor` tree (`output.root`)

```jsonc
{
  "artifact_id":            "orders",
  "file_id":                "f_abc…",
  "columns":                ["sku", "qty", "region"],
  "parent_row_key_column":  null,          // or the column that joins to the parent artifact
  "children":               [ { /* nested array_field artifact */ } ],
  "artifact_type":          "array_field" | "nested_array_field" | null,
  "content_type":           "text/csv"     // or "application/pdf" etc for file-type outputs
}
```

**Rules**:
- `root === null` → node has no file-backed output; everything is in `scalars`. Stop there.
- `root.content_type === "text/csv"` → table. Fetch preview rows via `GET /runs/{run_id}/nodes/{node_id}/artifacts/{root.artifact_id}/odata?$top=5&$count=true`.
- `root.content_type` is a non-CSV type (e.g. `application/pdf`, `image/png`) → render a file card with download. Download via the file service using `file_id`.
- `children[]` exists → render nested expandable sub-tables. Each child is itself an `ArtifactDescriptor` — recurse.

**Never** try to inline row data — the control plane never returns rows on `/nodes/{id}/data`. Row fetches always go to `.../artifacts/{id}/odata` or to the file service. See [`05-artifacts-viewer.md`](05-artifacts-viewer.md) for the full table rendering story.

### 3c. Produced artifacts (registry names)

`produced_artifacts: string[]` — names this node contributed to the run-level `$artifacts` registry. Cross-link to `GET /runs/{run_id}/artifacts` if you want a "this artifact came from this node" affordance.

---

## 4. State-specific rendering

| `node_state` | What to render |
|---|---|
| `completed` | Full tree as above. |
| `running` | Skeleton with spinner. Disable preview fetches until `task.completed`. |
| `pending` | Show the pending-node form instead — this node is waiting on the user. See [`06-pending-nodes.md`](06-pending-nodes.md). |
| `failed` | Don't render `output` (it's empty). Show `task.error` (fetch via `GET /runs/{run_id}/tasks/{task_id}`). Offer a "Rerun" button. |
| `skipped` | Show "Skipped" badge + reason (from the latest `task.skipped` event or from the task record). No output is ever produced. |
| `idle` | Node has not started yet. Show its design-time schema only. |

---

## 5. Port taken (branch highlight)

For CONDITION / SWITCH / HUMAN_ACTION / PARALLEL nodes the interesting output is "which port fired". Sources of truth, in order:

1. `edge.traversed` event in the live feed → the event names `from_node`, `to_node`, `source_port_id`.
2. `GET /runs/{run_id}/tasks/{task_id}` → the task record may carry `taken_edge_ports` (handler-dependent).
3. Inspect `output.scalars._action` for HUMAN_ACTION (legacy fallback).

Highlight the corresponding edge in the canvas when rendering the output panel.

---

## 6. Caching model

```
If-None-Match: W/"<etag-from-previous-response>"
```

- **304 Not Modified** → keep your cached panel, clear any "updating" affordance.
- **200 OK** → replace. Grab the new `etag` from the response header AND the body (they agree; the body copy is convenient for in-memory state).

### Invalidation triggers (live):

| Event | Invalidate |
|---|---|
| `task.completed` (this node) | Invalidate output data + re-GET. |
| `task.failed` (this node) | Invalidate. Re-GET to confirm state. |
| `node_input.edited` (this node) | Invalidate data (side=input) + probably output too. |
| `artifact.added` produced by this node | Invalidate — `produced_artifacts[]` likely grew. |
| `task.rerun` / `task.retrying` (this node) | Drop cache; node state reverts. |

Don't invalidate on `context.updated` alone — it's noisy and may not affect this node.

---

## 7. Performance rules of thumb

- Keep schemas and descriptors in a long-lived cache (they rarely change within a task).
- OData `$top=5` for preview, full pagination only on the drill-in viewer.
- Never call `/nodes/{id}/data` on every event — debounce to one re-fetch per second per open panel.
- For massively-parallel loop iterations, the FE should pick one iteration's `task_id` to display; do not fan out one request per iteration.

---

## 8. Complete example sequence

User opens node `n_score` in run `r_42`.

```http
GET /runs/r_42/nodes/n_score/schema                     → 200 + ETag W/"v12"
GET /runs/r_42/nodes/n_score/data?side=output           → 200 + ETag W/"e7"

Render scalars using schema labels.
output.root !== null → fetch preview:
GET /runs/r_42/nodes/n_score/artifacts/orders/odata?$top=5&$count=true
                                                        → 200 { "@odata.count": 1234, "value": [...] }

output.root.children[0].artifact_id === "line_items":
GET /runs/r_42/nodes/n_score/artifacts/line_items/odata?$top=5
                                                        → 200 { value: [...] }
```

On socket event `task.completed` for `n_score` with a fresher etag, re-run step 2 with `If-None-Match: W/"e7"` — if 200, refresh; if 304 (shouldn't happen post-completion but possible on retries), keep cache.

---

## 9. Source of truth

- Controller: `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/run_controller.py` (`get_node_data`, `get_node_schema`).
- Response DTOs: `…/dtos/node_data_dtos.py` (`GetNodeDataResponse`, `NodeDataPayload`, `ArtifactDescriptor`, `GetNodeSchemaResponse`).
- ETag calc: `app/layer1_domain/services/node_input_etag_calculator.py`.
