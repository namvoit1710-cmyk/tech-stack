# Rerun, Cancel, Pause, Resume

Lifecycle controls the frontend offers to the user once a run is live.

---

## 1. Cancel / pause / resume

```
POST /runs/{run_id}/cancel
POST /runs/{run_id}/pause
POST /runs/{run_id}/resume
```

All three take no body, all three return `{ success: boolean, error?: string }` (unwrapped: `{ status: "ok", data: { success: true } }`).

Corresponding events (see [`02-realtime-events.md`](02-realtime-events.md)):
- `run.cancelled` (CRITICAL)
- `run.paused` / `run.resumed` (NORMAL)

### Semantics

| Action | What the engine does |
|---|---|
| **Cancel** | Halts dispatch. In-flight tasks receive cancellation; pending nodes are abandoned. Run status becomes `cancelled`. Terminal. |
| **Pause** | Stops new dispatches at the next checkpoint; in-flight tasks continue to completion. Run status becomes `paused`. Reversible. |
| **Resume** | From `paused`, returns to `running` and dispatches pending successors. No rerun. |

UI rules of thumb:
- Show "Cancel" while `status in {running, paused}`; confirm first (it's terminal).
- Show "Pause" only while `status === "running"`; "Resume" only while `status === "paused"`.
- Disable both once `status` is terminal (`completed`, `failed`, `cancelled`).

---

## 2. Task-level rerun

```
POST /runs/{run_id}/tasks/{task_id}/rerun
Content-Type: application/json

{ "new_input": { "threshold": 0.9 } }   // optional — merges over resolved_input
```

Re-runs just this one task. No cascade. Useful for:
- "Retry this failed HTTP request" (TASK node that failed).
- "Re-run this iteration with different params" (loop-body task).

Event stream: `task.rerun` (NORMAL), followed by the usual `task.dispatched` → `task.completed|failed` cycle.

---

## 3. Run-level rerun

```
POST /runs/{run_id}/rerun
Content-Type: application/json

{
  "from_node_id":   "n_scoring",
  "rerun_strategy": "cascade",
  "variables":      { "global_threshold": 0.7 }
}
```

All fields optional. Two modes:

### Anchored rerun — `from_node_id` set

The engine calls `collect_downstream_nodes_to_reset(node_id)`:
- Walks the DAG forward from the anchor, skipping branches that weren't taken.
- Resets every node it finds (wipes task state, clears outputs).
- Dispatches from the anchor.

Use for "rerun from here" UX on a specific node.

### Full restart — `from_node_id` omitted

Dispatches from every entry node. Effectively a run restart using the same run_id.

### `variables` override

Merges into `run.context.variables` before dispatch. Use to tweak a configuration value without editing node scalars (e.g. a global threshold read via `{{$input.threshold}}`).

### Response

```jsonc
{
  "reset_node_ids":      ["n_scoring", "n_filter", "n_sink"],
  "dispatched_task_ids": ["t_new_1"]
}
```

### `rerun_strategy`

- `cascade` (default) — full downstream reset.
- `node_only` — reset just `from_node_id` and dispatch it; downstream nodes keep stale outputs (they'll see the new output when they next execute).
- `none` — no reset, just dispatch from the anchor (rarely useful at run level).

---

## 4. Rerun triggered from `PATCH /data`

`PATCH /runs/{run_id}/nodes/{node_id}/data` can also dispatch a rerun as part of the same atomic call (`rerun_strategy` field). See [`07-data-editing.md`](07-data-editing.md#4-rerun-strategies). The response includes a `rerun` block:

```jsonc
"rerun": {
  "strategy":       "cascade",
  "triggered":      true,
  "degraded_from":  null,
  "reset_node_ids": ["n_b", "n_c"],
  "task_ids":       ["t_new_1"]
}
```

If `triggered === false`, the edit applied but no rerun actually happened (e.g. run is already terminal). If `degraded_from` is non-null, the server softened your requested strategy — show a note to the user.

---

## 5. UI surfaces

| Affordance | API | Strategy |
|---|---|---|
| "Cancel run" | `POST /runs/{id}/cancel` | — |
| "Pause" / "Resume" | `POST /runs/{id}/pause` / `POST /runs/{id}/resume` | — |
| Node "Rerun from here" | `POST /runs/{id}/rerun` | `cascade` |
| Node "Rerun just this node" | `POST /runs/{id}/rerun` | `node_only` |
| Failed task "Retry" | `POST /runs/{id}/tasks/{tid}/rerun` | — |
| Full run "Restart" | `POST /runs/{id}/rerun` (no `from_node_id`) | `cascade` |
| Save edits and regenerate | `PATCH /data` with `rerun_strategy: "cascade"` | — |

---

## 6. What gets reset by `cascade`

The reset is **deterministic and BFS-limited**: a successor is reset only if every path leading to it passes through the anchor. Merge-point nodes with live incoming paths from elsewhere stay untouched.

If you want to show "this will reset N nodes" as a confirm dialog, fire a dry call or use the response to update the UI after the fact — the engine returns `reset_node_ids[]` so you can confirm what actually happened.

---

## 7. Error cases

| Status | Cause | Handling |
|---|---|---|
| `404` | Run or task not found. | Close the dialog. |
| `409` on pause/resume | Run is in a state that can't accept the transition (e.g. pausing a terminal run). | Refresh `GET /runs/{id}`. |
| `409` on rerun | `from_node_id` not in workflow or not reachable from an entry point. | Refresh workflow; disable the button. |
| `500` on rerun | Internal dispatch error. | Show error; retry once after a short delay. |

---

## 8. Source of truth

- Controllers: `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/run_controller.py` (`cancel_run`, `pause_run`, `resume_run`, `rerun_run`, `rerun_task`).
- Engine method: `app/layer1_domain/services/orchestration_engine.py` (`collect_downstream_nodes_to_reset`).
- Rerun use case: `app/layer2_application/features/rerun_run/use_cases/rerun_run_use_case.py`.
