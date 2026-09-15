# Realtime Events (Socket.IO)

Asynchronous notifications for run / task / data lifecycle. The frontend subscribes to rooms and reacts. REST is still the system of record for reads — events are pointers that say "go re-GET this".

Transport: stock Socket.IO at the default path (`/socket.io/`).

---

## 1. Rooms

Events are broadcast into rooms. Clients join explicitly.

| Room | Contains |
|---|---|
| `run:{run_id}` | All events for one run (lifecycle, tasks, edges, pending prompts, context, artifacts). |
| `workflow:{workflow_id}` | Broadcast events for a workflow (e.g. run started for any run of this workflow). |

### Join / leave

```js
socket.emit('join:run',   { run_id });
socket.emit('leave:run',  { run_id });
socket.emit('join:workflow',  { workflow_id });
socket.emit('leave:workflow', { workflow_id });
```

A run typically emits into both `run:{run_id}` and `workflow:{workflow_id}`. Join the `run:` room to get full detail; join the `workflow:` room if you want a passive "runs of this workflow" feed.

---

## 2. Priority lanes

The server classifies every event into one of three lanes. Under load, lower-priority lanes are dropped first.

| Lane | Capacity | Dropped? | Topics |
|---|---|---|---|
| **CRITICAL** | 1 000 | Never | `task.dispatched`, `input.requested`, `human_action.requested`, `data_edit.requested`, `child_run.started`, `run.completed`, `run.failed`, `run.cancelled` |
| **NORMAL** | 5 000 | When queue full | `edge.traversed`, `task.completed`, `task.failed`, `task.retrying`, `task.skipped`, `task.resumed`, `task.rerun`, `condition.evaluated`, `loop.iteration.completed`, `child_run.completed`, `input.submitted`, `human_action.completed`, `data_edit.completed` |
| **BULK** | 10 000 | Freely | `context.updated`, `artifact.added`, `node_input.edited`, `node.output.produced`, other `loop.*` |

**Implication for the FE**: treat BULK events as hints, not data. After a reconnect (or a BULK-heavy burst), re-GET the relevant resource to pick up anything that was dropped.

---

## 3. Full event catalog

All payloads carry `meta: { event_id, timestamp }` (when produced through the standard publisher) plus topic-specific fields below. The Socket.IO event name is the dot-notation topic (`run.started`, `task.completed`, …).

### Run lifecycle

| Event | Lane | Payload highlights |
|---|---|---|
| `run.started` | C | `run_id`, `workflow_id`, `started_at`, `input_data` |
| `run.completed` | C | `run_id`, `output_data`, `completed_at` |
| `run.failed` | C | `run_id`, `error`, `failed_task_id?` |
| `run.cancelled` | C | `run_id`, `reason?` |
| `run.paused` | N | `run_id` |
| `run.resumed` | N | `run_id` |

### Task lifecycle

| Event | Lane | Payload highlights |
|---|---|---|
| `task.dispatched` | C | `run_id`, `node_id`, `task_id`, `worker_type?` |
| `task.completed` | N | `run_id`, `node_id`, `task_id`, `output_data?` |
| `task.failed` | N | `run_id`, `node_id`, `task_id`, `error` |
| `task.retrying` | N | `run_id`, `node_id`, `task_id`, `attempt_number` |
| `task.skipped` | N | `run_id`, `node_id`, `task_id`, `reason` |
| `task.resumed` | N | `run_id`, `node_id`, `task_id` |
| `task.rerun` | N | `run_id`, `node_id`, `task_id`, `new_input?` |

### DAG flow

| Event | Lane | Payload highlights |
|---|---|---|
| `edge.traversed` | N | `run_id`, `from_node`, `to_node`, `source_port_id?` |
| `condition.evaluated` | N | `run_id`, `node_id`, `result`, `branch_taken` |
| `loop.iteration.completed` | N | `run_id`, `node_id`, `iteration_number` |
| `loop.started` / `loop.completed` | B | `run_id`, `node_id` |
| `loop.iteration.started` | B | `run_id`, `node_id`, `iteration_number` |

### Pending-node prompts (CRITICAL)

| Event | Lane | Payload highlights |
|---|---|---|
| `input.requested` | C | `run_id`, `node_id`, `task_id`, `input_schema`, `instruction?`, `prefilled?` |
| `human_action.requested` | C | `run_id`, `node_id`, `task_id`, `actions` (port list), `instruction?` |
| `data_edit.requested` | C | `run_id`, `node_id`, `task_id`, `artifact_id`, `file_id`, `schema` |

### Pending-node completions

| Event | Lane | Payload highlights |
|---|---|---|
| `input.submitted` | N | `run_id`, `node_id`, `task_id`, `data_keys[]` (no raw values — re-GET) |
| `human_action.completed` | N | `run_id`, `node_id`, `task_id`, `action` (`confirm`/`reject`/…) |
| `data_edit.completed` | N | `run_id`, `node_id`, `task_id`, `artifact_id` |

### Child runs (sub-workflows)

| Event | Lane | Payload highlights |
|---|---|---|
| `child_run.started` | C | `parent_run_id`, `parent_node_id`, `child_run_id`, `workflow_id` |
| `child_run.completed` | N | `parent_run_id`, `child_run_id`, `status`, `output_data?` |

### Data / context (BULK — treat as hints)

| Event | Lane | Payload highlights |
|---|---|---|
| `context.updated` | B | `run_id`, `changed_keys[]` |
| `artifact.added` | B | `run_id`, `artifact_name`, `artifact_id`, `file_id`, `produced_by_node` |
| `node_input.edited` | B | `run_id`, `node_id`, `etag`, `affected_artifact_ids[]` |
| `node.output.produced` | B | `run_id`, `node_id`, `produced_artifacts[]` |

---

## 4. Patterns

### A. Subscribe → dispatch

```js
socket.on('task.completed', (evt) => {
  store.dispatch(taskCompleted(evt));
  // If this task is the one the user has open, refresh:
  if (uiState.openNode === evt.node_id) {
    refetch(`/runs/${evt.run_id}/nodes/${evt.node_id}/data?side=output`);
  }
});
```

### B. Handle pending prompts

`input.requested` / `human_action.requested` / `data_edit.requested` are CRITICAL and never dropped. Wire them to the pending-node modal — see [`06-pending-nodes.md`](06-pending-nodes.md).

### C. Refresh caches on BULK events

When `artifact.added` fires, the run's `$artifacts` registry has changed. Re-GET:
```
GET /runs/{run_id}/artifacts   (with current If-None-Match)
```
304 → no change; 200 → update store.

When `node_input.edited` fires, any open node-data panel is now stale:
```
GET /runs/{run_id}/nodes/{node_id}/data?side=input|output|both   (If-None-Match)
```

### D. Reconnect / catch-up

On reconnect (and before resubscribing):

1. Re-join all rooms (`join:run`, `join:workflow`).
2. Full-state reconcile:
   - `GET /runs/{run_id}` — run status + final output (if completed).
   - `GET /runs/{run_id}/artifacts` — artifact registry.
   - For each open panel: `GET /runs/{run_id}/nodes/{node_id}/data` and `/schema`.
3. After that, accept incoming events as deltas.

Never rely on "I stayed connected, I must have the latest" — BULK lane drops under load even without disconnection.

### E. De-dup via `meta.event_id`

Events may be redelivered after reconnect. Keep a small recent-`event_id` set (LRU of ~1 000) and discard duplicates.

---

## 5. Configuration endpoint

`GET /events` returns the event catalog as the server sees it (names, default rooms, priority). Useful for FE integration tests and for keeping this doc honest.

```jsonc
{
  "events": [
    { "name": "run.started", "rooms": ["run:{run_id}", "workflow:{workflow_id}"], "priority": "critical" },
    …
  ]
}
```

---

## 6. Source of truth

- Lane classification: `workflow-control-plane-service/app/layer4_frameworks/realtime/socketio_consumer.py`
- Event topic enum: `workflow-control-plane-service/app/layer1_domain/events.py` (`EventTopic`)
- Realtime broadcasting publisher (name normalization + room selection): `workflow-control-plane-service/app/layer4_frameworks/realtime/realtime_broadcasting_publisher.py`
