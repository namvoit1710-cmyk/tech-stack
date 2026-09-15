# Node Cheat Sheet

A one-screen reference for every node type. For full config fields and examples, follow the link in each row.

## Engine nodes (run inline, immediate)

| Node | Ports out | One-liner | Page |
|---|---|---|---|
| `TRIGGER` | `out_default` | Entry point (manual / schedule / event / webhook). | [↗](/nodes/entry-output#trigger) |
| `OUTPUT` | `out_default` | Declare the workflow's result fields. | [↗](/nodes/entry-output#output) |
| `CONDITION` | `true_branch`, `false_branch` | Boolean branch. | [↗](/nodes/branching#condition) |
| `SWITCH` | one per case + `default` | Multi-way routing by value. | [↗](/nodes/branching#switch) |
| `LOOP` | `loop_body`, `loop_complete` | Iterate collection / count / condition. | [↗](/nodes/loops#loop) |
| `LOOP_EXIT` | — | Break the loop. | [↗](/nodes/loops#loop-exit) |
| `LOOP_CONTINUE` | — | Skip to next iteration. | [↗](/nodes/loops#loop-continue) |
| `PARALLEL` | one per branch | Fan out concurrently. | [↗](/nodes/concurrency#parallel) |
| `MERGE` | `out_default` | Join branches (all / any / n_of_m). | [↗](/nodes/concurrency#merge) |
| `WORKFLOW` | `success`, `failure` | Call a sub-workflow. | [↗](/nodes/composition#workflow) |
| `COMPUTE` | `success`, `failure` | Inline expression / computation. | [↗](/nodes/transform#compute) |
| `SET_VARIABLE` | `out_default` | Write `{{$variables.*}}`. | [↗](/nodes/transform#set-variable) |
| `PROMPT` | `out_default` | Assemble prompt text for an LLM. | [↗](/nodes/transform#prompt) |
| `APPROVAL_FLOW` | `success`, `failure` | Fetch & graph an approval process. | [↗](/nodes/integration#approval-flow) |

## Pending nodes (pause for a human)

| Node | Ports out | One-liner | Resume via | Page |
|---|---|---|---|---|
| `INPUT` | `out_default` | Collect structured input. | `POST .../submit` | [↗](/nodes/human-in-the-loop#input) |
| `HUMAN_ACTION` | `confirm`, `reject` | Approve / reject. | `POST .../submit` | [↗](/nodes/human-in-the-loop#human-action) |
| `DATA_EDIT` | `out_default` | Review / edit tabular data. | `PATCH .../data` | [↗](/nodes/human-in-the-loop#data-edit) |
| `FILE_UPLOAD` | `out_default` | Upload a file. | upload + register | [↗](/nodes/human-in-the-loop#file-upload) |

## Worker node (dispatched)

| Node | Ports out | One-liner | Page |
|---|---|---|---|
| `TASK` | `success`, `failure` | Dispatch to a worker (`worker_type`). | [↗](/nodes/task) |

## Worker types

| `worker_type` | Does | Port |
|---|---|---|
| `http_request` | HTTP/REST calls. | `35000` |
| `agent` | LLM inference (OpenAI). | — |
| `mapping_data` | Data field/array mapping. | — |
| `wait` | Timed / manual waits. | `35000` |
| `database_connection` | SQL (PostgreSQL/MySQL/HANA). | `37000` |
| `ms_teams` | Teams messages / cards. | `38000` |
| `gateway-worker` | Gateway-function passthrough. | `35001` |

## Expression quick reference

| Syntax | Resolves to |
|---|---|
| `{{$<node>.output.<field>}}` | Upstream node output. |
| `{{$input.<field>}}` | Run input payload. |
| `{{$variables.<name>}}` | Run-scoped variable. |
| `{{$metadata.<field>}}` | Run metadata (`run_id`, …). |
| `{{$loop.index}}` / `{{$loop.item}}` | Current loop iteration. |

Operators: `=` `!=` `>` `>=` `<` `<=` `contains` `starts_with` `is_empty`.

## Service ports

| Service | Port |
|---|---|
| Control Plane | `8001` |
| Worker Executor | `8004` |
| Workers | `35000`+ |

## Run control endpoints

`POST /runs/{id}/` + `pause` · `resume` · `cancel` · `rerun` · `replay-pending-requests`
