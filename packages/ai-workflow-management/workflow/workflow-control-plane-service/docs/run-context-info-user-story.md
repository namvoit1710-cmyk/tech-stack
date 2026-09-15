# Workflow - [Backend] Return run context information

## Story

**As a** frontend developer building the run-monitoring UI,
**I want** the run APIs to return rich run-context information — which nodes are running right now, what is blocking the run, and nested-run relationships —
**so that** I can render an accurate live view of a workflow run (including parent/child workflows and pending-input nodes) without issuing many follow-up calls.

---

## Context

The platform already tracks progress, blocking, and nested-run state inside the domain model
([`WorkflowRun`](../app/layer1_domain/entities/workflow_run.py) and
[`IRunContextView`](../app/layer2_application/interfaces/run_context_view_interface.py)),
but **none of it is surfaced through the REST API**. Today the REST layer only exposes flat run fields.

### Current API surface

**`GET /runs`** — [list_runs_use_case.py](../app/layer2_application/features/list_runs/use_cases/list_runs_use_case.py)

```json
{
  "id": "…",
  "workflow_id": "…",
  "workflow_name": "…",
  "status": "RUNNING",
  "started_at": "…",
  "completed_at": null,
  "error": null,
  "is_test": false,
  "tenant_id": "…"
}
```

**`GET /runs/{run_id}`** — [get_run_status_use_case.py](../app/layer2_application/features/get_run_status/use_cases/get_run_status_use_case.py)

```json
{
  "id": "…",
  "workflow_id": "…",
  "status": "RUNNING",
  "started_at": "…",
  "completed_at": null,
  "error": null,
  "error_code": "",
  "error_details": {},
  "output_data": null
}
```

### Gaps

| Consumer need | Missing from current API |
|---|---|
| “What is the run doing **right now**?” | List of currently running nodes (a DAG can run many concurrently — PARALLEL, multiple entry nodes, LOOP iterations) |
| “**Why is it stuck** / what is blocking it?” | Pending nodes (INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD) with their `node_id`, `node_type`, `task_id` |
| “Is this a **nested** run?” | `parent_run_id`, `parent_node_id`, `root_run_id`, `depth_level` |
| “Show me the **whole tree**” | No endpoint returns descendants even though `IRunContextViewStore.get_by_root_id(root_id)` is already available |
| “Show list-level **progress / blocking indicators**” | `completed_count`, `total_nodes`, `is_blocked`, `depth_level`, `root_run_id` on each list item |

> **Note on status semantics.** `RunStatus` has no `BLOCKED` value — a run waiting on an INPUT/HUMAN_ACTION/DATA_EDIT/FILE_UPLOAD node stays `RUNNING`. Blocking is a property of **pending nodes**, not of the run itself, and must be expressed as a separate field.

> **Note on “current node”.** There is no single current node in a DAG. `RunContext.current_node` is a lightweight pointer (last dispatched / last evaluated node) and is insufficient on its own: PARALLEL branches, multi-entry DAGs, and LOOP iterations can have many concurrent running nodes. The API returns a **list** of running nodes.

---

## Goals

1. Enrich `GET /runs/{run_id}` so a single call tells the FE *what is running* and *what is blocking*.
2. Expose *nested-run* relationships (parent, root, depth, and direct children) on run detail.
3. Provide a way to fetch the **whole tree** of a root run for hierarchical views.
4. Enhance `GET /runs` list items with lightweight progress / nested / blocking indicators for filtering and badges.

**Non-goals**

- Backward compatibility of the response shape. The response envelope is versioned under `/v1`; this story reshapes it freely. The frontend will update in lockstep — no transitional shape, no deprecation window.
- A `next_node` / `next_node_id` field. Next node is **non-deterministic** in branching DAGs: CONDITION / SWITCH branches are decided at runtime from data; MERGE nodes wait on quorum; LOOP iterates; PARALLEL has many. For linear paths the frontend already has the DAG (`adjacency`) and can compute successors of any running or just-completed node locally — duplicating that in the backend adds surface without adding information. See *Out of scope* for the full rationale.
- A `BLOCKED` run-status enum value — keep `RunStatus` flat; expose blocking separately as a property of pending nodes.
- Returning full node-data / artifact payloads inline — existing per-node endpoints remain authoritative.
- Changing orchestration behavior or the event model.

---

## Proposed API changes

### 1. Reshape `GET /runs/{run_id}` — replace with a context-rich DTO

No additive-shim: top-level flat fields go away in favor of a cleaner grouped shape.

```jsonc
{
  "id":            "run-123",
  "workflow_id":   "wf-abc",
  "workflow_name": "Onboarding",
  "status":        "RUNNING",
  "started_at":    "…",
  "completed_at":  null,
  "tenant_id":     "…",
  "is_test":       false,

  "error": {
    "message": null,
    "code":    "",
    "details": {}
  },
  "output_data": null,

  "progress": {
    "completed_nodes": ["node-1", "node-2"],
    "skipped_nodes":   ["node-3"],
    "completed_count": 2,
    "skipped_count":   1,
    "total_nodes":     8
  },

  "running_nodes": [
    {
      "node_id":    "node-5",
      "node_type":  "TASK",
      "task_id":    "task-9",
      "started_at": "2026-04-21T10:10:00Z"
    }
  ],

  "blocking": {
    "is_blocked": true,
    "pending_nodes": [
      {
        "node_id":   "node-7",
        "node_type": "HUMAN_ACTION",
        "task_id":   "task-11",
        "since":     "2026-04-21T10:12:00Z"
      }
    ]
  },

  "nesting": {
    "parent_run_id":  "run-root-1",
    "parent_node_id": "node-workflow-invoker",
    "root_run_id":    "run-root-1",
    "depth_level":    1,
    "child_runs": [
      { "parent_node_id": "node-sub-wf", "child_run_id": "run-child-9" }
    ]
  }
}
```

Notes:

- `running_nodes[]` is **plural** and unifies what was previously a separate `current_node` scalar and `running_tasks[]` list. One node currently executing = one entry; parallel branches = many entries.
- Error fields are grouped under `error` (object) instead of three flat top-level keys. A successful run serializes as `error: { message: null, code: "", details: {} }`.
- `nesting.child_runs` mirrors `RunContext.child_runs` as a list of `{parent_node_id, child_run_id}` pairs so ordering is stable and extensible.

### 2. New endpoint `GET /runs/{run_id}/tree`

Returns every run in the same root group (the run itself, its ancestors up to root, and all descendants), using the already-existing
[`IRunContextViewStore.get_by_root_id`](../app/layer2_application/interfaces/run_context_view_interface.py).

```jsonc
{
  "root_run_id": "run-root-1",
  "nodes": [
    {
      "run_id":           "run-root-1",
      "parent_run_id":    null,
      "parent_node_id":   null,
      "workflow_id":      "wf-parent",
      "status":           "RUNNING",
      "depth_level":      0,
      "running_node_ids": ["node-workflow-invoker"],
      "is_blocked":       false
    },
    {
      "run_id":           "run-child-9",
      "parent_run_id":    "run-root-1",
      "parent_node_id":   "node-sub-wf",
      "workflow_id":      "wf-child",
      "status":           "RUNNING",
      "depth_level":      1,
      "running_node_ids": ["node-5"],
      "is_blocked":       true
    }
  ]
}
```

- Ordering: BFS from root. Callers reconstruct a tree from `(run_id, parent_run_id)`.
- Per-entry payload is kept small (IDs only, no `node_type` / `task_id`); clients needing per-run depth call `GET /runs/{run_id}` for that child.

### 3. Reshape `GET /runs` list items

No additive-shim — list items adopt the same grouped shape as detail, minus the expensive fields:

```jsonc
{
  "id":            "…",
  "workflow_id":   "…",
  "workflow_name": "…",
  "status":        "RUNNING",
  "started_at":    "…",
  "completed_at":  null,
  "tenant_id":     "…",
  "is_test":       false,
  "error":         { "message": null, "code": "", "details": {} },

  "root_run_id":   "run-root-1",
  "parent_run_id": null,
  "depth_level":   0,

  "progress":         { "completed_count": 2, "skipped_count": 1, "total_nodes": 8 },
  "running_node_ids": ["node-5"],
  "is_blocked":       true
}
```

- List payloads use `running_node_ids[]` (string array) instead of the full `running_nodes[]` objects used in detail — keeps the list cheap.
- `pending_nodes[]` is **not** included on list items; only `is_blocked`. Callers needing pending-node details fetch detail.
- New optional query param `?root_run_id=…` filters items to a single root group (alternative to `/tree`).

---

## Acceptance criteria

### Detail endpoint (`GET /runs/{run_id}`)

- [ ] Response uses the reshaped DTO above — no legacy flat fields retained.
- [ ] `progress.completed_nodes` / `skipped_nodes` / counts are derived from the view store; `total_nodes` is derived from `dag_info`.
- [ ] `running_nodes[]` contains one entry per task currently in `RUNNING`/`DISPATCHED` state for the run, with `node_id`, `node_type`, `task_id`, `started_at`.
- [ ] `blocking.pending_nodes[]` lists every node with a pending task of type `INPUT`, `HUMAN_ACTION`, `DATA_EDIT`, `FILE_UPLOAD`; `blocking.is_blocked` is `len(pending_nodes) > 0`.
- [ ] `nesting` exposes `parent_run_id`, `parent_node_id`, `root_run_id`, `depth_level`, and `child_runs[]` (derived from `RunContext.child_runs`).
- [ ] For a completed run: `running_nodes` and `pending_nodes` are both empty, `is_blocked: false`.
- [ ] No `next_node` / `next_node_id` / similar field is present.
- [ ] Missing run → `404 RUN_NOT_FOUND`.
- [ ] OpenAPI schema updated.

### Tree endpoint (`GET /runs/{run_id}/tree`)

- [ ] Uses `IRunContextViewStore.get_by_root_id(view.root_run_id)`.
- [ ] Returns `root_run_id` plus a flat list ordered BFS from root.
- [ ] Each entry contains: `run_id`, `parent_run_id`, `parent_node_id`, `workflow_id`, `status`, `depth_level`, `running_node_ids[]`, `is_blocked`.
- [ ] Works whether the supplied `run_id` is the root or any descendant (always resolves to the root first).
- [ ] Missing run → `404 RUN_NOT_FOUND`.

### List endpoint (`GET /runs`)

- [ ] Items use the reshaped DTO above.
- [ ] Existing filters (`$filter`, `workflow_id`, `status`, `tenant_id`, `is_test`) keep working unchanged.
- [ ] New optional query param `root_run_id` filters items to a single root group.
- [ ] Pagination / OData semantics unchanged.
- [ ] `running_node_ids[]` is a string array (not a full object array) to keep the list payload cheap.

### Tests

- [ ] Unit tests for each new/changed use case covering: single-level run, nested run (depth ≥ 1), blocked run (pending node), run with multiple concurrent running nodes (PARALLEL), completed run, failed run, cancelled run.
- [ ] Integration tests for all three endpoints asserting the full response shape.
- [ ] OpenAPI snapshot test updated.

---

## Design notes

- Prefer a **new use case** `GetRunContextDetail` (sibling to `GetRunStatus`) rather than overloading `GetRunStatus`. If the old `GetRunStatusUseCase` becomes redundant, delete it — do not keep it as a shim.
- Prefer a **new use case** `GetRunTree` that wraps `IRunContextViewStore.get_by_root_id` and projects each view to the tree DTO.
- Extend `ListRunsUseCase` to also project `root_run_id`, `parent_run_id`, `depth_level`, `completed_count`, `skipped_count`, `total_nodes`, `running_node_ids`, `is_blocked`.
- Running/pending-node detection requires reading task states. If the current `IRunContextView` does not expose them, add a read path through the task/execution repository — do **not** reach into mutable aggregates from the controller.
- Keep all new fields Phase-4b-safe: rely only on `IRunContextView` / `IRunContextViewStore` so the projector swap requires no controller change.

---

## Out of scope / Follow-ups

### `next_node` / `next_node_id` (explicitly rejected)

In a DAG the notion of a single "next node" is ill-defined:

| DAG shape | What "next" means |
|---|---|
| Linear chain (A→B→C) | Deterministic: next = successor of current |
| CONDITION / SWITCH | Unknown until the condition evaluates at runtime — could be any of N branches |
| MERGE | Successor exists but waits for **all** predecessors to complete — "next" is known, "when" is not |
| LOOP | Cycles between body and exit depending on iteration state |
| PARALLEL | Many "nexts" simultaneously |

A scalar `next_node_id` is either misleading (branching) or redundant (linear — the frontend has the DAG and can read `adjacency[running_node_id]` itself). A `candidate_next_nodes[]` field was considered but rejected for the same reason: it is trivially derivable client-side from state already on the wire (`running_nodes`, `completed_nodes`, `skipped_nodes`, `pending_nodes`) plus the workflow's `adjacency`. Revisit only if a concrete UI case arises where the frontend cannot access the workflow definition at render time.

### Other follow-ups

- Socket.IO push of a `run.context.updated` event whenever `running_nodes` / pending-node set changes (once the read model stabilizes).
- Historical timeline of blocking events (would require querying the event log, not the current projection).
- `BLOCKED` as a first-class `RunStatus` value — explicitly rejected; blocking is a node-level property.

---

## Open questions

1. Should `GET /runs/{run_id}/tree` include a full `running_nodes[]` with `node_type`/`task_id` per entry, or stay with ID-only as proposed? (Default: ID-only; clients fetch per-run detail if they need more.)
2. For `total_nodes`, do we count every node in the DAG, or only reachable nodes given the path actually taken? (Default: count all DAG nodes; `skipped_count` lets the UI compute either view.)
3. Should `/tree` accept a `depth` query param (e.g. `?depth=1` for only direct children) or always return the full group? (Default: full group — groups are expected to stay small.)
