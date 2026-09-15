# Introduction

The **AI Workflow Management** engine runs workflows: directed graphs of **nodes** connected by **edges**. You design a graph once, then start **runs** of it with input data. The engine walks the graph, executing each node, routing along edges, pausing for people when needed, and dispatching heavy or specialized work to external **workers**.

This guide explains every node you can place on a canvas, how to set the system up, and how to drive a workflow from start to finish.

## What you can build

- **Automations** — call APIs, transform their responses, branch on the result, and notify a channel.
- **Approvals** — pause a run for a person to approve, reject, or fill in a form, then continue.
- **Data pipelines** — fetch rows, map fields, loop over a collection, and write results.
- **AI workflows** — assemble a prompt from upstream data, send it to an LLM agent, and act on the answer.
- **Composite processes** — call other workflows as reusable sub-steps.

## What the engine supports

| Capability | Summary |
|---|---|
| **Control flow** | Conditions, multi-way switches, loops (collection / count / condition), parallel fan-out, and quorum merge. |
| **Sub-workflows** | A workflow can invoke another workflow as a child run, with input mapping and output binding. |
| **Human-in-the-loop** | Pause for structured input, approval/rejection, in-place data editing, or file upload — then resume. |
| **Worker tasks** | Dispatch a node to an external worker process (HTTP, AI agent, data mapping, wait, database, MS Teams, gateway). |
| **Expressions** | Reference upstream outputs, run metadata, loop indices, and run-scoped variables with `{{$...}}` syntax. |
| **Run control** | Start, pause, resume, cancel, rerun, and replay over a REST API. |
| **Real-time events** | Every state change is broadcast over Socket.IO to per-run and per-workflow rooms. |
| **Validation** | Workflows are structurally validated (DAG checks) before a run starts. |

## How a workflow is structured

A workflow is a graph:

- **Nodes** are the steps. Each node has a **type** (`TRIGGER`, `CONDITION`, `TASK`, …) that determines what it does, a **config** that parameterizes it, and one or more **ports** for incoming and outgoing edges.
- **Edges** connect an output port of one node to an input port of another. Branching nodes (like `CONDITION` and `SWITCH`) decide which output port "fires", and only edges from a fired port are followed.
- **A `TRIGGER`** is the entry point. Execution flows out of the trigger and follows edges until every reachable path completes.

```
TRIGGER ──▶ TASK (HTTP) ──▶ CONDITION ──true──▶ HUMAN_ACTION ──confirm──▶ OUTPUT
                                  └──false─────▶ OUTPUT
```

## Where each node runs

Every node type has a **runtime** that decides where its work happens. This is the single most useful thing to understand about the catalog:

| Runtime | What it means | Node types |
|---|---|---|
| **Engine** (inline) | The control plane executes it directly and immediately. No worker, no pause. | `TRIGGER`, `CONDITION`, `SWITCH`, `LOOP`, `LOOP_EXIT`, `LOOP_CONTINUE`, `PARALLEL`, `MERGE`, `WORKFLOW`, `COMPUTE`, `SET_VARIABLE`, `PROMPT`, `OUTPUT`, `APPROVAL_FLOW` |
| **Pending** (human) | The run **pauses** and waits for a person to act, then resumes. | `INPUT`, `HUMAN_ACTION`, `DATA_EDIT`, `FILE_UPLOAD` |
| **Worker** (dispatched) | The node is handed to an external worker process over the worker SDK. | `TASK` |

::: tip
This classification is **derived from each node's spec** — it is not a hand-maintained list. Adding a node with `runtime="worker"` automatically makes it dispatch to a worker.
:::

## Next steps

- New here? Read [Core Concepts](./concepts) to learn the vocabulary, then [Architecture](./architecture) for the moving parts.
- Want to run it? Jump to [Installation & Run](./setup).
- Looking for a specific node? Go straight to the [Node Catalog](/nodes/).
