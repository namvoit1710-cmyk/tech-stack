# The Orchestration Engine

The orchestration engine is the part of the [control plane](./architecture#workflow-control-plane) that actually *runs* a workflow: it walks the graph, decides which node fires next, invokes node handlers, dispatches `TASK` work to workers, pauses for humans, and decides when a run is finished. It is a single domain service (`OrchestrationEngine`) that every run-advancing operation depends on through one interface.

Three ideas hold the whole engine together:

1. **One uniform execution path** for every node type.
2. **Event sourcing** — the event log is the source of truth; live state is a projection of it.
3. **A serialized orchestration queue** — all advancing work for a run happens one step at a time.

## The uniform execution model

Every node — `TRIGGER`, `TASK`, `CONDITION`, `LOOP`, `WORKFLOW`, a human-in-the-loop pause — goes through the same four steps:

**dispatch → execute → complete-with-port → route**

1. **Dispatch** — the engine builds a *task* for the node, resolves its `{{$...}}` inputs against the run context, and emits a `node.input` event recording the resolved input.
2. **Execute** — the node's handler runs. Every handler has the same shape; the only thing that differs between node types is *how the handler reports it completed*.
3. **Complete with a port** — the handler returns a result tagged with a [completion mode](./concepts#completion-mode) and the **output port** that fired (`success`, `failure`, a branch name, …):

   | Mode | The node… | …so the engine |
   |---|---|---|
   | `immediate` | finished inline | routes to successors now |
   | `dispatched` | handed work to a worker | waits for the worker result |
   | `pending` | paused for a human | waits for a submit/approve/upload |
   | `child_run` | started a sub-workflow | waits for the child run |
   | `loop_body` | scheduled an iteration | runs the body, then re-checks |
   | `deferred` | needs more orchestration | re-enqueues itself |

4. **Route** — once the node finishes, the engine follows only the edges leaving the **fired** port.

Because every type shares this spine, pause/resume, rerun, and live events behave identically for all of them — there is no per-type special-casing at the orchestration layer.

## Routing and fan-out

Routing is **edge-based** and fires **on the first incoming edge taken**:

- Each node has named **output ports** — `TASK`/`COMPUTE`/`WORKFLOW` fire `success` or `failure`; `CONDITION` fires `true_branch`/`false_branch`; `SWITCH` fires a case or `default`; most others have a single default port.
- When a node completes, the engine computes its fan-out, keeps only the edges leaving the **fired** port, and dispatches each target.
- Edges leaving an **untaken** port are not followed. A node reachable *only* through untaken edges is marked **skipped** (`branch_not_taken`).
- A target fires as soon as **one** incoming edge is taken — it does not wait for all predecessors. The wait-for-all-branches behaviour lives inside the [`MERGE`](/nodes/concurrency#merge) node, which buffers its branches explicitly.

The fan-out is a **pure function** of the graph snapshot — identical inputs always produce identical edge-traversal events — which is what makes replay deterministic. Cycles are supported: a taken edge into an already-skipped node **un-skips** it and bumps the node's visit counter, so the re-run gets a fresh task.

## Deciding a run is finished

A run completes by **quiescence**, not by reaching a designated end node:

- It settles to **`COMPLETED`** when there are **no non-terminal tasks and no unhandled failures** — every path that could advance has advanced.
- It settles to **`FAILED`** when a node failed and no `failure` edge routed the error.
- A failure routed through a `failure` port is *not* a run failure — it just continues down the error branch.

`OUTPUT`-node fields are collected into the run's `output_data` at completion. See [Run Lifecycle & Control](./lifecycle) for the full state model and the control operations.

## Event sourcing

The engine never treats in-memory state as the truth. Every meaningful transition is emitted as a **domain event** and appended to a per-run **event log**:

```
 node handler ──emit──▶   event log    ──fold──▶  ProjectedRunState
 (RunStatusChanged,       append-only,            (status, completed/
  NodeCompleted,          ordered,                 skipped nodes,
  NodeOutputProduced,     deduped                  variables, output,
  EdgeTraversed, …)                                child runs)
```

- **Events are emitted before state is mutated**, so the log is always authoritative — a `NodeOutputProduced` carries the full write-set (node output plus name aliases) and *then* the variables are updated.
- A **projector** folds the log into a `ProjectedRunState` through a pure, idempotent reducer (`new_state = apply_event(old_state, event)`). Applying the same event twice is a no-op — set-union for node sets, last-write for scalars — matching the log-level dedupe.
- The read side that handlers and the API use (`GET /runs/{id}/data`, node status, the Output panel) is served from this projection through a cached view store.
- **Unknown event types fold to a no-op**, so a log written by a newer version still replays cleanly.

This is what makes the control operations reliable: **pause/resume** parks and re-enters the walk; **rerun** clears the run's log and walks it again from the start; and the projection can always be rebuilt from the log alone. In `mock`/`sqlite` the log lives in memory/SQLite; in `sap` mode it is persisted in SAP HANA. For long runs, periodic **snapshots** (`SNAPSHOT_MODE`) let a cold start replay only the tail of the log rather than the whole thing.

## The orchestration queue

Advancing work is **serialized through an orchestration queue** instead of running on the request thread. A REST call (start, resume, complete-task, …) enqueues an **action**; a background **orchestration worker** dequeues actions one at a time and calls the matching engine method:

| Action | Triggered by | Engine method |
|---|---|---|
| `StartRunAction` | `POST /runs` | `orchestrate_start` |
| `ContinueRunAction` | a worker task completed | `orchestrate_continue` |
| `FailTaskAction` | a worker task failed | `orchestrate_fail` |
| `ResumeTaskAction` | a pending node was submitted | `orchestrate_resume` |
| `ExecuteNodeAction` | an engine node is ready | `orchestrate_execute_node` |
| `LoopIterationAction` | a loop scheduled its next pass | `orchestrate_loop_iteration` |
| `ResumeRunAction` / `CheckRunCompletionAction` | resume / completion sweep | `orchestrate_resume_run` / `orchestrate_check_completion` |
| `EditNodeDataAction` | `PATCH /runs/{id}/nodes/{id}/data` | the edit-node-data use case |

A single worker drains the queue (`ORCHESTRATION_WORKER_COUNT=1` by default), so every step for a run happens in order with no in-process locking to fight over. The queue is in-memory in `mock`/`sqlite` and broker-backed (Kafka / Event Mesh) in `local`/`sap`, where partition keys keep each run's actions on one consumer. If an action handler throws, the worker forces the run terminal so it can never hang in `RUNNING` forever, and synchronous callers (like the PATCH edit) get the error surfaced through a response registry.

## Where node handlers run (scaling)

By default the engine invokes engine-runtime handlers **in the control-plane process** — the simplest topology and the right one for a laptop. The `EXECUTOR_MODE` flag can move handler execution out of the engine without touching any workflow:

| `EXECUTOR_MODE` | Behaviour |
|---|---|
| `in_process` | The engine invokes handlers directly. |
| `shadow` *(default)* | The engine stays authoritative, but a standalone executor *also* recomputes each node from the `node.input` event and a comparator checks the two agree — a zero-drift safety net before promotion. |
| `executor` | The engine only emits `node.input` and returns; a separate executor owns handler execution and emits `NodeOutputProduced`, and the projector + fan-out carry the run forward. |

It is the same event log and projection in every mode — moving execution off the control plane is a deployment choice, not a rewrite. `TASK` nodes are *always* executed off-process (that is what [workers](./architecture#workers) are for); `EXECUTOR_MODE` only governs where the **engine-runtime** handlers run.
