# Socket.IO Realtime Events Guide

How the frontend connects to the Workflow Control Plane's realtime push system and the payload shape of every event.

## Connection

```javascript
import { io } from "socket.io-client";

const socket = io("http://<host>:8001", {
  path: "/socket.io/",       // python-socketio default path
  transports: ["websocket"],
});
```

## Event Catalog API

Fetch the full event catalog programmatically (schema, fields, examples):

```
GET /events/config
```

## Room Model

| Room | Join Event | Leave Event | Use |
|------|-----------|-------------|-----|
| `run:{run_id}` | `join:run` | `leave:run` | All events for a specific run |
| `workflow:{workflow_id}` | `join:workflow` | `leave:workflow` | All events for any run of a workflow |

```javascript
// Subscribe
socket.emit("join:run", { run_id: "run-abc" });
socket.emit("join:workflow", { workflow_id: "wf-123" });

// Unsubscribe
socket.emit("leave:run", { run_id: "run-abc" });
```

## Meta Fields (Every Event)

Every event payload includes these auto-injected fields:

| Field | Type | Description |
|-------|------|-------------|
| `_event` | string | Normalized event name (e.g. `task.completed`) |
| `_timestamp` | string | ISO-8601 UTC timestamp of Socket.IO emission |
| `event_id` | string | UUID v4 unique event identifier |
| `timestamp` | string | ISO-8601 UTC domain event creation time |
| `source` | string | Service that produced the event (`workflow-control-plane`) |

## All Events (24)

### Run Lifecycle

**`run.started`**
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |

**`run.completed`**
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `status` | string (`COMPLETED`) |
| `correlation_id` | string |
| `callback_topic` | string |
| `output_data` | object |

**`run.failed`**
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `error` | string |
| `failed_task_id` | string |
| `correlation_id` | string |
| `callback_topic` | string |

**`run.cancelled`**
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `cancelled_by` | string |
| `reason` | string |

**`run.paused`** / **`run.resumed`**
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |

### Task Lifecycle

**`task.dispatched`** — Task sent to worker (includes full input)
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `worker_type` | string |
| `input` | object (full resolved_input) |
| `node_config` | object |
| `idempotency_key` | string |
| `correlation_id` | string |

**`task.completed`** — Worker finished (no output data; fetch via REST)
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `status` | string (`COMPLETED`) |
| `execution_time_ms` | integer |

**`task.failed`** — Task failed
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `error_code` | string |
| `error_message` | string |
| `attempt` | integer |

**`task.retrying`** — Retrying after failure
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `attempt` | integer |
| `next_retry_at` | string |

**`task.resumed`** — Pending task resumed with user data
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |

**`task.skipped`** — Task skipped (branch not taken)
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `reason` | string |

**`task.rerun`** — Task re-executed
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `previous_status` | string |

### Human Interaction

**`input.requested`** — INPUT node pauses for user data
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `input_schema` | array |
| `instruction` | string |

**`input.submitted`** — User submitted INPUT data
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |

**`human_action.requested`** — HUMAN_ACTION node pauses for confirm/reject
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `instruction` | string |
| `input_schema` | array |
| `confirm_label` | string (default `"Confirm"`) |
| `reject_label` | string (default `"Reject"`) |

**`human_action.completed`** — User confirmed or rejected
| Field | Type |
|-------|------|
| `task_id` | string |
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `action` | string (`confirm` or `reject`) |

### Node Execution

**`condition.evaluated`** — CONDITION node picked a branch
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `expression` | string |
| `result` | boolean |
| `branch` | string (`then` or `else`) |

**`loop.iteration.completed`** — LOOP iteration done
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `iteration` | integer |
| `total` | integer |

### Edge & Context

**`edge.traversed`** — Execution follows an edge (for animation)
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `edge_id` | string |
| `source_node_id` | string |
| `target_node_id` | string |
| `source_port_id` | string |

**`context.updated`** — Node output stored in run context
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `node_name` | string |
| `data_type` | string (`inline` or `file_ref`) |

### Child Workflow

**`child_run.started`** — WORKFLOW node launched child run
| Field | Type | Note |
|-------|------|------|
| `parent_run_id` | string | Room key (not run_id) |
| `parent_node_id` | string | |
| `child_run_id` | string | |
| `child_workflow_id` | string | |

**`child_run.completed`** — Child workflow finished
| Field | Type |
|-------|------|
| `parent_run_id` | string |
| `parent_node_id` | string |
| `child_run_id` | string |
| `child_workflow_id` | string |
| `status` | string |

### Artifact

**`artifact.added`** — File attached to a node's output
| Field | Type |
|-------|------|
| `run_id` | string |
| `workflow_id` | string |
| `node_id` | string |
| `file_id` | string |
| `name` | string |
| `content_type` | string |
| `artifact_type` | string |
