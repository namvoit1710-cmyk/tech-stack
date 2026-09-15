# Data Editing — PATCH /data with Artifact Ops

One endpoint handles every edit the frontend makes to a node's working-copy data: scalar edits, table edits (cell / bulk / insert / delete), artifact replacements, and schema changes (add/drop/rename column). Used by both the design-time review UI and the DATA_EDIT pending-node form.

```
PATCH /runs/{run_id}/nodes/{node_id}/data
```

Required header: `If-Match: W/"<etag>"`. The etag comes from a prior GET `/data` (or from the previous PATCH's response).

---

## 1. Request body

```jsonc
{
  "rerun_strategy":  "none" | "node_only" | "cascade",   // default "none"
  "reset_to_input":  false,                              // whole-node revert
  "scalars":         { "status": "ok", "threshold": 0.8 },
  "artifact_ops":    [ArtifactOp, …]                     // max 1000
}
```

At least one of `reset_to_input`, `scalars`, `artifact_ops` must be non-empty. Optional query param `?task_id=` to target a specific loop iteration.

---

## 2. `artifact_ops` — the discriminated union

All ops share a `row_path` (list of row hops) or a single `artifact_id`. `RowPathHop` = `{ artifact_id, row_index, filter }` — the `filter` is a deterministic OData filter scoped to the hop's parent table (used to resolve `row_index` stably).

| `op` | Required fields | Use for |
|---|---|---|
| `update_cell` | `row_path[]`, `column`, `value` | Single cell fix. |
| `bulk_update` | `artifact_id`, `filter`, `assignments` (`{col: value}`) | Update many rows at once. |
| `insert_rows` | `artifact_id`, `rows[]` (max 10 000) | Append / insert rows. |
| `delete_row` | `row_path[]` | Remove one row. |
| `delete_rows` | `artifact_id`, `filter` | Bulk delete matching rows. |
| `reset_artifact` | `artifact_id` | Revert one sub-table to input. |
| `replace_artifact` | `artifact_id`, `file_id`, `schema_compatibility` (`strict`/`drift_ok`), `expected_columns?` | Swap the file (e.g. after a fresh upload). |
| `add_column` | `artifact_id`, `column`, `output_type` (`string`/`number`/`boolean`), `default_value?`, `rules?[]` | Add a column (optionally computed). |
| `drop_column` | `artifact_id`, `column` | Remove a column. |
| `rename_column` | `artifact_id`, `old_name`, `new_name` | Rename a column. |

All ops use `extra="forbid"`: unknown fields error the whole batch out.

### Nested artifacts

Child artifacts (array-of-dicts columns) have dotted ids. Use the dotted id in `artifact_id` or in the innermost `RowPathHop.artifact_id`. Provide `row_path` with one hop per level to disambiguate which parent row owns the child row you're editing.

### Bulk updates — filter examples

```jsonc
{ "op": "bulk_update", "artifact_id": "orders",
  "filter": "$filter=region eq 'APAC'",
  "assignments": { "priority": "high" } }
```

The `filter` string is the OData suffix exactly as the file service expects it (same dialect as [`05-artifacts-viewer.md`](05-artifacts-viewer.md) preview filters).

---

## 3. Response

```jsonc
{
  "etag":    "…",                                           // use for next PATCH
  "version": 4,                                             // monotonic counter
  "file_lineage": { "orders": "f_new", "invoice_pdf": "f_unchanged" },
  "results": [
    { "op_index": 0, "status": "ok",     "rows_affected": 1, "row_ids": ["r_abc"] },
    { "op_index": 1, "status": "failed", "error": { "code": "UNPROCESSABLE_ENTITY", "message": "…" } }
  ],
  "rerun": {
    "strategy":       "cascade",
    "triggered":      true,
    "degraded_from":  null,           // or "cascade" if we fell back to node_only
    "reset_node_ids": ["n_downstream_a", "n_downstream_b"],
    "task_ids":       ["t_1", "t_2"]
  }
}
```

### Atomicity

If **any** op fails, the whole batch rolls back. Newly-uploaded `file_id`s get best-effort GC'd. `results[]` still has one entry per op so the FE can show which one broke it.

### `file_lineage`

Maps `artifact_id → new file_id`. Any open previews for those artifacts are now stale; re-fetch the first page of OData.

### `rerun`

Echoes the effective rerun strategy. `degraded_from` is non-null when the server had to soften your requested strategy (e.g. the node is already terminal and `cascade` isn't meaningful). `reset_node_ids` is what got reset; `task_ids` is what got dispatched.

---

## 4. Rerun strategies

| Strategy | What it does |
|---|---|
| `none` (default) | Apply edits; do not re-run anything. The working copy changes but downstream tasks keep their stale outputs. Use this for design-time review where the FE will choose when to rerun. |
| `node_only` | Apply edits and re-dispatch just this node. Useful if the node is deterministic on its inputs and you want to regenerate its output. |
| `cascade` | Apply edits, reset this node **and every transitively downstream node**, and dispatch from here. This is the "make reality consistent with my edit" button. |

Pick based on UX affordance: an inline cell edit is usually `none`; a "save and regenerate" button is `cascade`.

---

## 5. `reset_to_input`

A whole-node revert. Drops scalars and every artifact back to the input snapshot (as if this node had just been dispatched with its current input). Combine with `rerun_strategy` to control whether it re-executes.

```jsonc
{ "rerun_strategy": "cascade", "reset_to_input": true }
```

---

## 6. Error codes

| Status | Code | Meaning | Retry strategy |
|---|---|---|---|
| 400 | `BAD_REQUEST` | Malformed body (e.g. unknown op, missing discriminator). | Fix client code; don't retry. |
| 403 | `FORBIDDEN` | `artifact_id` not in this node's tree (tenancy) or op not allowed in current state. | Refresh descriptor via GET `/data`. |
| 404 | `NOT_FOUND` | Run / node / artifact absent. | Show and close. |
| 409 | `PRECONDITION_REQUIRED` | `If-Match` header missing. | Re-GET `/data`, retry with etag. |
| 410 | `GONE` | Working copy collected under us (very rare). | Re-GET; rebuild state. |
| 412 | `PRECONDITION_FAILED` | Stale `If-Match`. Someone else edited. | Re-GET, reconcile unsaved client changes, retry. Show diff to user if the conflict is material. |
| 413 | `PAYLOAD_TOO_LARGE` | `artifact_ops` > 1000 or `insert_rows.rows` > 10 000. | Batch on the client. |
| 422 | `UNPROCESSABLE_ENTITY` | Schema / type violation per op; details in `results[i].error`. | Show field-level error; do not retry. |
| 423 | `LOCKED` | Node state forbids editing (another PATCH in flight, or node is in a transitional state). | Back off 200 ms, retry once. |

---

## 7. Optimistic concurrency pattern

1. `GET /data` → keep `etag` and `version` in your store.
2. User edits → buffer ops.
3. On save: PATCH with `If-Match: W/"<etag>"`.
4. On 200 → update `etag` / `version` from response. Refresh previews for artifacts in `file_lineage`.
5. On 412 → re-GET, diff server state with your buffered edits, ask the user to reconcile, retry.

For long-lived grids (inline cell editing), PATCH each change individually so conflicts stay small. For modal editors ("Save all"), batch into one PATCH.

---

## 8. Undo / redo

Because PATCH is atomic and `version` is monotonic, you can implement undo as:

- Undo stack: last N `{etag_before, etag_after, ops}` tuples.
- On undo: compute the **inverse** ops on the client (e.g. `insert_rows` → `delete_rows` with row_ids from `results`) and PATCH with the current etag.
- Full revert: `{ "reset_to_input": true }`.

`reset_artifact` is the per-artifact form of `reset_to_input` — useful when the user says "revert just this table".

---

## 9. Source of truth

- Controller: `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/run_controller.py` (`patch_node_data`).
- DTOs: `…/dtos/node_data_dtos.py` (`PatchNodeDataRequest`, `ArtifactOp` union, `PatchNodeDataResponse`, `OpResult`, `RerunInfo`, `RowPathHop`).
- Use case: `app/layer2_application/features/edit_node_data/use_cases/edit_node_data_use_case.py`.
- Validator (type coercion, schema check): `app/layer1_domain/services/node_input_validator.py`.
