# Node Catalog

The engine ships **19 node types**. Each page in this section documents a group of related nodes with their purpose, ports, configuration fields, and an example.

Every node has a **runtime** that decides where it executes — see [Introduction → Where each node runs](/guide/introduction#where-each-node-runs).

## All node types

| Node | Category | Runtime | Purpose | Page |
|---|---|---|---|---|
| `TRIGGER` | Entry | engine | Workflow entry point (manual / schedule / event / webhook). | [Entry & Output](./entry-output#trigger) |
| `OUTPUT` | Output | engine | Defines and formats the workflow's result schema. | [Entry & Output](./entry-output#output) |
| `CONDITION` | Branching | engine | Boolean branch → `true` / `false`. | [Branching](./branching#condition) |
| `SWITCH` | Branching | engine | Multi-way routing by value match. | [Branching](./branching#switch) |
| `LOOP` | Iteration | engine | Iterate a collection / count / condition. | [Loops](./loops#loop) |
| `LOOP_EXIT` | Iteration | engine | Break out of the enclosing loop. | [Loops](./loops#loop-exit) |
| `LOOP_CONTINUE` | Iteration | engine | Skip to the next loop iteration. | [Loops](./loops#loop-continue) |
| `PARALLEL` | Concurrency | engine | Fan out to concurrent branches. | [Concurrency](./concurrency#parallel) |
| `MERGE` | Concurrency | engine | Synchronize parallel branches (quorum join). | [Concurrency](./concurrency#merge) |
| `WORKFLOW` | Composition | engine | Invoke another workflow as a child run. | [Composition](./composition#workflow) |
| `COMPUTE` | Transform | engine | In-process expression / computation. | [Transform](./transform#compute) |
| `SET_VARIABLE` | Transform | engine | Write run-scoped `{{$variables.*}}`. | [Transform](./transform#set-variable) |
| `PROMPT` | AI | engine | Hold free text (with `{{...}}`) for a downstream LLM. | [Transform](./transform#prompt) |
| `INPUT` | Human-in-the-loop | pending | Pause to collect structured input. | [Human-in-the-Loop](./human-in-the-loop#input) |
| `HUMAN_ACTION` | Human-in-the-loop | pending | Pause for approve / reject. | [Human-in-the-Loop](./human-in-the-loop#human-action) |
| `DATA_EDIT` | Human-in-the-loop | pending | Pause to review/edit tabular data. | [Human-in-the-Loop](./human-in-the-loop#data-edit) |
| `FILE_UPLOAD` | Human-in-the-loop | pending | Pause to upload a file. | [Human-in-the-Loop](./human-in-the-loop#file-upload) |
| `APPROVAL_FLOW` | Integration | engine | Fetch an approval process from governance and graph it. | [Integration](./integration#approval-flow) |
| `TASK` | Worker | worker | Dispatch to an external worker. | [Task & Workers](./task) |

## Reading the field tables

Each node page lists its **config fields**. Columns mean:

- **Field** — the config key you set.
- **Type** — `string` / `number` / `boolean` / `object` / `array` / `json`.
- **Expr** — ✅ if the field accepts `{{...}}` [expressions](/guide/expressions).
- **Notes** — defaults, allowed values, and when the field is visible.

Fields marked **internal** are set by the system or by the visual editor, not authored by hand — they are noted where relevant but you rarely set them directly.

## Ports recap

| Node | Output ports |
|---|---|
| `TRIGGER` | `out_default` |
| `CONDITION` | `true_branch`, `false_branch` |
| `SWITCH` | one per case + `default` |
| `LOOP` | `loop_body`, `loop_complete` |
| `PARALLEL` | one per branch (dynamic) |
| `MERGE` | `out_default` |
| `TASK`, `WORKFLOW`, `COMPUTE`, `APPROVAL_FLOW` | `success`, `failure` |
| `HUMAN_ACTION` | `confirm`, `reject` |
| `LOOP_EXIT`, `LOOP_CONTINUE` | *(none — dead-end signals)* |
| everything else | `out_default` |
