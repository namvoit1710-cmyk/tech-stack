# Core Concepts

A short glossary of the terms used throughout this guide. If a term in another page is unclear, it is almost certainly defined here.

## Workflow

A reusable graph definition: a set of **nodes** and **edges**, plus metadata (name, version). A workflow is a *template* — it does nothing on its own. You execute it by starting a **run**.

Workflows are **versioned**. Editing a published workflow creates a new version; older runs keep pointing at the version they started with.

## Node

A single step in a workflow. Every node has:

- **`id`** — unique within the workflow.
- **`type`** — one of the 19 [node types](/nodes/) (`TRIGGER`, `TASK`, `CONDITION`, …).
- **`config`** — type-specific settings. Each type has a [config schema](/nodes/) describing the allowed fields.
- **ports** — named connection points. Input ports receive edges; output ports emit them.

## Port

A named connection point on a node. Branching nodes have multiple output ports and choose which one "fires":

- A `CONDITION` has `true_branch` and `false_branch`.
- A `SWITCH` has one port per case plus a `default`.
- A `TASK` and most worker-backed nodes have `success` and `failure`.
- Most plain nodes have a single `in_default` / `out_default`.

Only edges leaving a **fired** port are followed. This is how routing works.

## Edge

A directed connection from one node's output port to another node's input port. Edges define the order of execution and how data and control flow through the graph.

## Run

One execution of a workflow. A run has:

- A **`run_id`** — unique identity for this execution.
- **Input data** — the payload you pass when starting it.
- A **status** — `RUNNING`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`, etc.
- A **context** — the accumulated state: every node's output, run-scoped variables, and metadata.

See [Run Lifecycle & Control](./lifecycle) for the full status model and the control operations.

## Context & variables

As a run executes, each node's output is stored in the run **context**, keyed by node id. Downstream nodes read those outputs through [expressions](./expressions) like `{{$node-1.output.field}}`.

There are three layers a variable reference can resolve against:

1. **Node outputs** — `{{$<node_id>.output.<field>}}` (upstream traversal).
2. **Run-scoped variables** — `{{$variables.<name>}}`, written by a [`SET_VARIABLE`](/nodes/transform#set-variable) node.
3. **Run metadata** — `{{$metadata.run_id}}`, `{{$input.*}}`, `{{$loop.index}}`, etc.

## Worker

An external process that executes `TASK` nodes. The control plane never runs business HTTP calls, AI inference, or SQL itself — it **dispatches** them to a worker that registered for that `worker_type`. Workers self-register over the worker SDK. See the [Workers reference](/reference/workers).

## Completion mode

When a node finishes its handler, it reports *how* it completed. This drives what the engine does next:

| Mode | Meaning |
|---|---|
| `immediate` | Finished inline; route to the next node now. (Most engine nodes.) |
| `dispatched` | Handed to a worker; wait for the worker's result. (`TASK`.) |
| `pending` | Paused for a human; wait for a submit/approve/upload. (HITL nodes.) |
| `child_run` | Started a sub-workflow; wait for the child run. (`WORKFLOW`.) |
| `loop_body` | Scheduled the next loop iteration. (`LOOP`.) |
| `deferred` | Queued for later orchestration. |

## The three services

| Service | Default port | Role |
|---|---|---|
| **Control Plane** | `8001` | The orchestration engine + REST API. Owns workflows, runs, and inline node execution. |
| **Worker Executor** | `8004` | Receives dispatched tasks, routes them to the right worker, reports results back. |
| **Workers** | `35000`+ | The processes that do worker-backed work (HTTP, agent, etc.). |

More detail in [Architecture](./architecture).
