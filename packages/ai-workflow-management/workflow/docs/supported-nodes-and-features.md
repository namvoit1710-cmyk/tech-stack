# Supported Nodes and Features

Reference documentation for the workflow engine's current capabilities. Covers built-in nodes (handled inline by the control plane) and worker-based nodes (dispatched to external workers over the worker SDK).

Last reviewed: 2026-04-13 (branch `feature/workflow/new-engine`).

---

## 1. Built-in Nodes (Inline Handlers)

Built-in nodes are executed directly by the **workflow-control-plane-service** orchestration engine. They do not require a worker process.

- Handler registry: [bootstrap.py:164-190](../workflow-control-plane-service/bootstrap.py#L164-L190)
- Node type enum: [node_type.py](../workflow-control-plane-service/app/layer1_domain/value_objects/node_type.py)
- Descriptor registry: [node_type_registry.py:33-198](../workflow-control-plane-service/app/layer1_domain/value_objects/node_type_registry.py#L33-L198)

| Node Type | Purpose | Key Config | Completion Mode |
|---|---|---|---|
| `TRIGGER` | Workflow entry point | — | immediate |
| `CONDITION` | Binary branching on boolean expression | `expression` / `input_binding` | immediate |
| `SWITCH` | Multi-way routing by value match | `expression`, `cases[]` (`value`/`values`/`target`), `default` | immediate |
| `LOOP` | Iterate over an array, execute body per item | `array`, `event_driven` (optional) | loop_body |
| `LOOP_EXIT` | Break out of enclosing loop | — | immediate |
| `LOOP_CONTINUE` | Skip to next loop iteration | — | immediate |
| `PARALLEL` | Fan-out to multiple branches | `branches[]` with optional per-branch `condition` | immediate |
| `MERGE` | Synchronize parallel paths (quorum join) | — | immediate |
| `TASK` | Dispatch to an external worker (see §2) | `worker_type`, `input_binding`, `input_require[]` | dispatched |
| `COMPUTE` | Inline script execution | `script`, `language` (python / node) | immediate |
| `WORKFLOW` | Invoke sub-workflow as a child run | `workflow_id`, `input_binding` | child_run |
| `INPUT` | Pause run, wait for user input | — | pending |
| `HUMAN_ACTION` | Pause for approval / rejection | — | pending |
| `DATA_EDIT` | Pause for in-place data editing | — | pending |
| `FILE_UPLOAD` | Pause, wait for a file upload | — | pending |

---

## 2. Worker-Based Nodes

`TASK` nodes are dispatched to external workers via the **worker-sdk**. The `worker_type` field selects which worker processes the task.

Worker root: [workflow/worker/](../worker/)

### 2.1 `HTTP_REQUEST` — http-request-worker

General-purpose HTTP / REST client.

- **Methods:** GET, POST, PUT, DELETE, PATCH
- **Config:** `url`, `method`, `headers[]`, `query_params[]`, `body_type` (JSON / form / raw), `body`
- **Auth:** none, basic, bearer, oauth2
- Source: [workflow/worker/http-request-worker/](../worker/http-request-worker/)

### 2.2 `AGENT` — agent-worker

LLM agent execution.

- **Providers:** OpenAI
- **Config:** `provider`, `model`, `api_key`, `system_prompt`, `user_prompt`, `prompt_variables[]`, `conversation_history[]`, `response_format`
- Source: [workflow/worker/agent-worker/](../worker/agent-worker/)

### 2.3 `MAPPING_DATA` — mapping-data-worker

Data transformation / field mapping.

- **Config:** `source_data`, `array_mappings[]` (each with `source_array_path` and `item_mappings[]`), `strict_mode`
- Supports nested array iteration and per-field extraction.
- Source: [workflow/worker/mapping-data-worker/](../worker/mapping-data-worker/)

### 2.4 `WAIT` — wait-worker

Timed / manual wait.

- **Wait types:** `FIXED_DURATION`, `UNTIL_DATETIME`, `MANUAL_APPROVAL`
- **Config:** `duration_seconds`, `until_datetime`, `timeout_seconds`
- Source: [workflow/worker/wait-worker/](../worker/wait-worker/)

---

## 3. Engine Features

### 3.1 Control Flow

- **Conditions** — binary `true_branch` / `false_branch` ports. Operators: `=`, `!=`, `>`, `>=`, `<`, `<=`, `contains`, `starts_with`, `is_empty`.
- **Switch** — multi-case routing with `value` / `values` matching and a `default` fallback.
- **Loops** — iterate over an array; body nodes re-execute per item. `LOOP_EXIT` / `LOOP_CONTINUE` supported. Event-driven loop driver available (Phase 5.2.1+).
- **Nested loops** — scope-stack tracking (`invocation_id`, `loop_node_id`, `iter` per layer).
- **Parallel** — fan-out with optional per-branch guard conditions.
- **Merge** — quorum join: waits until all incoming predecessors complete (or skip propagates).
- **Skip propagation** — a successor is skipped only if **all** predecessors skipped.

### 3.2 Variables & Expressions

- **Expression syntax** (n8n-style): `{{$node_id.field}}`, `{{$input}}`, `{{$loop.index}}`, `{{$metadata.*}}`.
- **Resolution chain:** traversal (upstream node outputs) → metadata (`run_id`, `workflow_id`, etc.) → file-backed via `DataStoreService`.
- **Per-node input binding** resolved by [VariableResolver.resolve_mapping()](../workflow-control-plane-service/app/layer1_domain/services/variable_resolver.py).
- Node outputs persisted in `run.context.variables`, keyed by `node_id`.

### 3.3 Execution Model (Phase 6)

Unified `NodeHandlerResult` with `completion_mode`:

- `immediate` — finishes inline (conditions, switch, parallel fan-out, merge, compute).
- `dispatched` — handed off to an external worker (`TASK`).
- `pending` — awaits external user action (`INPUT`, `HUMAN_ACTION`, `DATA_EDIT`, `FILE_UPLOAD`).
- `child_run` — sub-workflow invocation (`WORKFLOW`).
- `loop_body` — loop iteration scheduling.
- `deferred` — queued for later orchestration.

Optional orchestration queue decouples node execution; fallback is inline dispatch. Max recursion depth: 100.

### 3.4 Sub-workflows

- `WORKFLOW` node invokes another workflow as a child run with its own context and depth tracking.
- Inputs bound via `input_binding`; outputs exposed back to parent on completion.

### 3.5 Error Handling

- **Timeouts** — enforced via `timeout_at` on task status checks.
- **Failure routing** — dedicated failure port can route to an error-handler branch.
- **Retry** — `max_retries` is parsed from node config but **not yet implemented** (engine logs a warning). Treat as roadmap.
- Handler exceptions → task marked `FAILED` with captured error message.

### 3.6 Validation & Observability

- **DAG validation** at run-start via `DAGValidator` (structural checks).
- **Realtime events** broadcast over Socket.IO to `run:{run_id}` and `workflow:{workflow_id}` rooms.

---

## 4. Roadmap / Known Gaps

- Task-level retry (`max_retries`) — parsed but not executed.
- Additional LLM providers for `AGENT` beyond OpenAI.
- Richer expression helpers / functions inside `{{ ... }}`.
