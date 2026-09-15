# Architecture

The engine is split into three cooperating services plus optional infrastructure. You can run all three on one machine for development.

## The big picture

```
                          ┌─────────────────────────────┐
   REST / Socket.IO  ◀───▶│   Workflow Control Plane    │   :8001
   (clients, UI)          │  ─────────────────────────  │
                          │  • Workflow & run storage    │
                          │  • Orchestration engine      │
                          │  • Inline node handlers      │
                          │    (CONDITION, LOOP, …)      │
                          │  • Expression resolver       │
                          └──────────────┬──────────────┘
                                         │  dispatch TASK
                                         ▼
                          ┌─────────────────────────────┐
                          │     Worker Executor         │   :8004
                          │  • Receives tasks            │
                          │  • Routes to worker by type  │
                          │  • Reports results back      │
                          └──────────────┬──────────────┘
                                         │  HTTP / gRPC
                        ┌────────────────┼────────────────┐
                        ▼                ▼                ▼
                 ┌────────────┐  ┌────────────┐   ┌────────────┐
                 │ http-      │  │ agent-     │   │ … other    │   :35000+
                 │ request    │  │ worker     │   │ workers    │
                 └────────────┘  └────────────┘   └────────────┘
```

Optional backing infrastructure (chosen by `INFRA_MODE`): **Kafka** or **SAP Event Mesh** for messaging, **SAP HANA** for persistence, **Redis** for Socket.IO scaling. In the default `mock` mode none of these are required.

## Workflow Control Plane

The brain of the system. It:

- Stores **workflows** (definitions, versions) and **runs** (executions, context, event log).
- Runs the **orchestration engine** that walks the graph.
- Executes all **engine-runtime** node handlers inline — `CONDITION`, `SWITCH`, `LOOP`, `PARALLEL`, `MERGE`, `COMPUTE`, `SET_VARIABLE`, `PROMPT`, `OUTPUT`, `WORKFLOW`, `APPROVAL_FLOW`, `TRIGGER`.
- Manages **pending** nodes (the human-in-the-loop pauses) and accepts the submit/approve/upload that resumes them.
- Resolves `{{$...}}` [expressions](./expressions) against the run context.
- Exposes the [REST API](/reference/rest-api) and streams real-time events over Socket.IO.

Default HTTP port **`8001`**. Entry point: `main.py`.

## Worker Executor

A thin dispatcher between the control plane and the workers. When the control plane reaches a `TASK` node, it hands the task to the executor. The executor looks up which worker registered for that `worker_type`, forwards the task (over HTTP or gRPC), waits for the result, and reports it back to the control plane.

Default HTTP port **`8004`**. Entry point: `main.py`. The control plane finds it via `WORKER_EXECUTION_URL`.

## Workers

Independent processes, one per capability, built on the **worker SDK**. Each worker:

- Declares a **`worker_type`** identifier (e.g. `http_request`, `agent`).
- **Registers** itself with the executor at startup (via `REGISTRY_URL`).
- Publishes an **input/output schema** describing the fields its task accepts and returns.
- Executes tasks and returns structured results.

The built-in workers are documented in the [Workers reference](/reference/workers). You can add your own by building a new worker with the SDK and pointing it at the executor.

## Infrastructure modes (`INFRA_MODE`)

A single environment variable selects the persistence and messaging backends, so you can scale from a laptop to production without code changes:

| Mode | Repositories | Messaging | Persistence | Use for |
|---|---|---|---|---|
| `mock` *(default)* | in-memory | console (no broker) | none | Local dev, tests — **zero external dependencies**. |
| `sqlite` | in-memory | console | SQLite event log | Local dev with a durable event log across restarts. |
| `local` | in-memory | Kafka | none | Local Docker stack with a real broker. |
| `sap` | SAP HANA | SAP Event Mesh | SAP HANA | Production on SAP BTP. |

## Execution model

The control plane's [orchestration engine](./orchestration-engine) runs every workflow through one uniform pattern: **dispatch → execute → complete-with-port → route**. Each handler returns a result tagged with a [completion mode](./concepts#completion-mode) that tells the engine whether the node finished inline, is waiting on a worker, is paused for a human, or spawned a child run.

State is **event-sourced**: every transition is appended to a per-run event log and folded into a projected run state, and all advancing work is serialized through an orchestration queue. This is what makes pause/resume, rerun, and replay reliable.

→ See **[The Orchestration Engine](./orchestration-engine)** for the full treatment: the uniform execution path, routing and fan-out, how a run is judged finished, event sourcing, the orchestration queue, and the `EXECUTOR_MODE` scaling topology.

## Real-time events

Every meaningful state change (node started, node completed, run paused, …) is broadcast over **Socket.IO**:

- `run:{run_id}` — events for a single run.
- `workflow:{workflow_id}` — events for any run of a workflow.

A UI subscribes to these rooms to render live progress. With `REDIS_URL` set, events fan out across multiple control-plane instances.
