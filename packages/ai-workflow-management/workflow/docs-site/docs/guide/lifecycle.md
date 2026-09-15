# Run Lifecycle & Control

A **run** moves through a series of states as the engine walks the graph. This page describes those states and the operations you can use to control a run.

## Run states

| Status | Meaning |
|---|---|
| `RUNNING` | The engine is actively executing nodes. |
| `PAUSED` | Explicitly paused (via `pause`) or waiting at a **pending** human-in-the-loop node. |
| `COMPLETED` | All reachable paths finished; `OUTPUT` fields are available as `output_data`. |
| `FAILED` | A node failed and the failure was not routed/handled. |
| `CANCELLED` | The run was cancelled before completion. |

Individual **nodes** also have status — `RUNNING`, `COMPLETED`, `PENDING`, `FAILED`, `SKIPPED` — visible via `GET /runs/{run_id}/nodes/status`.

## How a run progresses

1. **Start** — `POST /runs` creates the run, seeds `input_data` and any `user_variables`, and begins at the `TRIGGER`.
2. **Walk** — the engine executes each node. Engine-runtime nodes finish inline; `TASK` nodes are dispatched to a worker; `WORKFLOW` nodes spawn a child run.
3. **Route** — when a node completes it reports which output **port** fired. Only edges from a fired port are followed. A successor is **skipped** only if *all* of its predecessors skipped.
4. **Pause** — a human-in-the-loop node sets the run to wait. The run stays parked until you submit/approve/upload.
5. **Finish** — when no path can advance, the run settles into `COMPLETED` / `FAILED` / `CANCELLED`.

## Control operations

All under `POST /runs/{run_id}/...`:

| Operation | Endpoint | Effect |
|---|---|---|
| **Pause** | `/pause` | Stop advancing the run. In-flight work is allowed to settle. |
| **Resume** | `/resume` | Continue a paused run. |
| **Cancel** | `/cancel` | Terminate the run; it settles to `CANCELLED`. |
| **Rerun** | `/rerun` | Re-execute the run from the start. |
| **Replay pending requests** | `/replay-pending-requests` | Re-emit the requests for nodes currently pending input (useful if a UI missed the original event). |

### Task-level control

For a worker-backed `TASK` node you can act on its underlying task:

| Operation | Endpoint |
|---|---|
| Get task | `GET /runs/{run_id}/tasks/{task_id}` |
| Complete task | `POST /runs/{run_id}/tasks/{task_id}/complete` |
| Fail task | `POST /runs/{run_id}/tasks/{task_id}/fail` |
| Rerun task | `POST /runs/{run_id}/tasks/{task_id}/rerun` |

`complete` and `fail` are normally called by the worker executor, but they are also useful for manual intervention and testing. `rerun` re-executes a single task, optionally with `new_input`.

## Failure handling

- A node that raises is marked **`FAILED`** with its error message captured.
- Worker-backed and several engine nodes expose a dedicated **`failure`** output port. Wire an edge from it to route failures to an error-handling branch instead of failing the whole run.
- [`MERGE`](/nodes/concurrency#merge) has an `on_branch_failure` setting (`fail` vs. `partial`) controlling whether one failed branch fails the join.
- [`WORKFLOW`](/nodes/composition#workflow) has an `on_failure` setting for child-run failures.
- **Timeouts** — several nodes (`MERGE`, `WORKFLOW`, `wait` tasks) accept a timeout after which they fail or time out rather than block forever.

::: warning Task retry is roadmap
`max_retries` is parsed from node config but **not yet executed** by the engine (it logs a warning). Treat automatic task retry as a known gap; for now, handle retries explicitly via the `failure` port + a retry branch, or `rerun` the task.
:::

## Real-time observation

Rather than polling, subscribe over Socket.IO:

- `run:{run_id}` — every state change for one run.
- `workflow:{workflow_id}` — state changes across all runs of a workflow.

Events cover node started/completed, run paused/resumed, pending requests, and completion — enough to render a live execution view.

## Durability & replay

Run state is **event-sourced**: each transition is appended to a per-run event log and folded into a projected state. This is what makes pause/resume reliable and makes rerun/replay possible. In `mock`/`sqlite` modes the log lives in memory/SQLite; in `sap` mode it is persisted in SAP HANA.
