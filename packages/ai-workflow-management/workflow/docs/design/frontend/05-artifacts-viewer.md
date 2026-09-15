# Artifacts — List, Preview, Query, Download

Artifacts are the run's table-backed and file-backed outputs. Two flavors:

1. **Node-owned artifacts** — come from a single node's output, described by `ArtifactDescriptor` in `GET /nodes/{id}/data`. Query via the scoped OData endpoint.
2. **Run-level registry** (`$artifacts`) — a flat namespace `{ name → ArtifactRef }` the run as a whole has produced, populated as nodes complete.

Row data never lives on the control plane. The control plane hands you identity (`file_id`); rows come from the file service, either directly or via the scoped OData pass-through.

---

## 1. The run-level registry

```
GET /runs/{run_id}/artifacts         If-None-Match: …
```

Response (already unwrapped):

```jsonc
{
  "artifacts": {
    "invoice_pdf": {
      "file_id":           "f_pdf_1",
      "filename":          "invoice_2026_04_16.pdf",
      "content_type":      "application/pdf",
      "size_bytes":        12345,
      "produced_by_node":  "n_gen_invoice",
      "produced_by_task":  "t_abc",
      "registered_at":     "2026-04-16T10:12:00Z"
    },
    "orders": { ... },
    "report_csv": { ... }
  },
  "etag": "…"
}
```

This is what you show in the "Run artifacts" side panel. Each entry is an `ArtifactRef`; the download / preview UX depends on `content_type`.

### Live updates

Subscribe to `artifact.added` (BULK lane). On each event, send a conditional re-GET (`If-None-Match`) — 304 means no change.

---

## 2. Node-owned artifacts: the descriptor tree

From `GET /runs/{run_id}/nodes/{node_id}/data`, the `output.root` (or `data.root`) is an `ArtifactDescriptor`:

```jsonc
{
  "artifact_id":            "orders",
  "file_id":                "f_abc",
  "columns":                ["sku", "qty", "region"],
  "parent_row_key_column":  null,
  "children":               [ { "artifact_id": "orders.line_items", … } ],
  "artifact_type":          "array_field",
  "content_type":           "text/csv"
}
```

Keys to note:
- `artifact_id` is stable for the life of the node (dotted path for nested arrays: `"orders.line_items"`).
- `file_id` is the current revision; it changes on every PATCH that replaces the file.
- `children[]` lets you recurse into array-of-dicts columns.
- `parent_row_key_column` tells you which column in the child refers to a parent row.

---

## 3. Rendering strategy

Decide per descriptor, by `content_type`:

| `content_type` | Render as |
|---|---|
| `text/csv` | **Table**. Fetch rows via the scoped OData endpoint. |
| `application/json` | Detail panel with a JSON tree. Fetch via the file service (`GET /files/{file_id}` on the file service — *not* the control plane). |
| `application/pdf` | PDF card with "Preview" and "Download". |
| `image/*` | Image card. |
| anything else | "File: <filename> (<size>)" with a download button. |

For tables, the preview (first 5 rows) uses a single OData call; the full viewer uses paginated OData with the user's filters.

---

## 4. Scoped OData query

```
GET /runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata
```

The control plane:
1. Verifies `artifact_id` belongs to the node's working-copy tree (tenancy check; 403 / 404 otherwise).
2. Resolves `artifact_id → current file_id`.
3. Forwards **all** OData query params to the file service unmodified.

Query params (all optional):

| Param | Purpose |
|---|---|
| `$filter` | e.g. `region eq 'APAC' and qty gt 10`. |
| `$orderby` | e.g. `qty desc, sku asc`. |
| `$skip` / `$top` | Pagination. |
| `$count=true` | Include `@odata.count` in the response. |
| `$select` | Comma-separated column subset. |
| `$expand` | File-service-specific — used when the file service itself returns joined shapes. |
| `task_id` | **Control-plane-only** param; do **not** forward. Used to pick a loop iteration. |

Response shape is whatever the file service returns (`{ "@odata.count": …, "value": [ { column: value, … }, … ] }`) — the control plane does not rewrap it.

### Why scoped?

Because the scoped endpoint verifies the requester has access to this artifact in the context of this run and node, you never hand an arbitrary `file_id` to the file service from the browser without provenance.

---

## 5. Preview patterns

**First 5 rows (inline card)**:
```
GET .../artifacts/orders/odata?$top=5&$count=true
```

**Paginated grid**:
```
GET .../artifacts/orders/odata?$skip=0&$top=50&$count=true&$orderby=sku asc
```

**Server-side filter from the grid's filter UI**:
Translate user filters into OData `$filter`. Escape string literals with single quotes; use `and` / `or`; numeric/bool/null literals are bare. See the file service docs for the exact OData dialect; the control plane does not parse it.

**Column subset for wide tables**:
```
GET .../artifacts/orders/odata?$top=50&$select=sku,qty,region
```

---

## 6. Nested tables

When a descriptor has `children[]`, each child is a joined sub-table. Render it as an expandable row-detail or a second tab. Fetch with the child's own `artifact_id`:

```
GET .../artifacts/orders.line_items/odata?$top=50&$filter=order_id eq 'o_123'
```

The `parent_row_key_column` on the child tells you which column joins to a parent row — use that to scope queries when the user clicks a specific parent row.

---

## 7. Download

For files (PDF, image, binary), skip OData and fetch the file directly from the file service using the `file_id` on the `ArtifactRef` / `ArtifactDescriptor`. The control plane intentionally does not re-stream blobs.

The exact file service URL is deployment-specific — consult your environment's file-service base URL. The generic shape is `GET {file_service}/files/{file_id}`.

---

## 8. When to refresh

| Event | Refresh |
|---|---|
| `artifact.added` | Re-GET `/runs/{id}/artifacts` (conditional). |
| `task.completed` for the node that produced an artifact | Re-GET `/nodes/{id}/data` (descriptor tree may have grown). |
| `node_input.edited` | Re-GET `/nodes/{id}/data` (file_id may have rolled over). Any open OData grid should refetch the current page. |
| ETag on `/artifacts/{id}/odata` | The file service may return its own ETag — respect it if present. |

---

## 9. Failure modes

| Symptom | Cause | Action |
|---|---|---|
| `403` on scoped OData | artifact not in this node's tree (stale id after `drop_column` or `reset_artifact`) | Refresh the descriptor tree via `GET /nodes/{id}/data`. |
| `404` with `code: "NOT_FOUND"` | artifact_id gone / node deleted | Remove from UI; prompt user. |
| `410 GONE` on `GET /data` | file_id underneath was collected | Re-GET and rebuild descriptor. |
| Preview rows look empty but `$count` is > 0 | `$select` omits all visible columns | Adjust `$select`. |
| Rows change between pages without `$orderby` | file service pagination is not deterministic without order | Always include `$orderby` on paginated grids. |

---

## 10. Source of truth

- Run-level registry use case: `workflow-control-plane-service/app/layer2_application/features/get_run_artifacts/`
- Scoped OData use case: `…/features/query_node_artifact/`
- Descriptor model: `app/layer1_domain/entities/data_reference.py` (`build_file_ref`, `add_artifact`).
- Tenancy check in scoped OData: the use case resolves `artifact_id` through the run's node descriptor tree and rejects unknown ids.
