# Phase 6 Uniform Execution — Completion Plan

**Status:** Proposed
**Date:** 2026-04-15
**Scope:** `workflow-control-plane-service/app/layer1_domain/services/orchestration_engine.py` and adjacent handlers

---

## Context

Phase 6 introduced a uniform `dispatch → execute → complete-with-port → route` pipeline so that TASK and built-in nodes are handled through the same engine code. The pipeline lives in `OrchestrationEngine._exec_uniform` (`orchestration_engine.py:1804-2030`) and is keyed by `NodeHandlerResult.completion_mode`, which every handler now sets:

| `completion_mode` | Producers |
|---|---|
| `immediate` | CONDITION, SWITCH, COMPUTE, TRIGGER, PARALLEL, MERGE (quorum met), LOOP_CONTINUE, LOOP_EXIT |
| `dispatched` | TASK |
| `pending` | INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD |
| `child_run` | WORKFLOW |
| `loop_body` | LOOP (with iterations) |
| `deferred` | MERGE (quorum unmet) |

The model is sound. The work that remains is removing the **legacy parallel paths** that bypass `_exec_uniform`. As long as those paths exist, every change to dispatch semantics has to be made twice and risks drift — the entry-TASK dispatch bug fixed on 2026-04-14 is one example.

**Intended outcome:** a single execution code path (`_exec_uniform`) for every node type, in both queued and inline modes, with the "kind of node" decided entirely by the handler that runs.

---

## Current state

### Where `_exec_uniform` is reached today

Only via `orchestrate_execute_node` (`orchestration_engine.py:1801`), which is the **queued-mode** entry point. When no queue is wired (mock / single-process default), every dispatch falls back to the legacy path via `_enqueue_or_dispatch` (`orchestration_engine.py:114-127`).

### Legacy path: `dispatch_or_handle_task` (lines 520-840)

Re-implements `_exec_uniform` for the inline case. The duplication is one-to-one:

| Concern | `_exec_uniform` | Legacy `dispatch_or_handle_task` |
|---|---|---|
| TASK status → RUNNING | line 1932 | line 807-808 |
| `_apply_csv_data_mode` | line 1911 | line 775 |
| `store_data` for inline input | line 1919-1931 | line 787-799 |
| `publish_event(TaskDispatched)` | line 1942-1956 | line 823-840 |
| `_stage_recorder.record` for TASK | line 1935-1941 | line 811-819 |
| Builtin: `apply_state_updates_as_files` | line 1875 | line 674 |
| Builtin: `_complete_builtin_inline` | implicit via "immediate" branch | line 694 |
| Builtin: `_route_to_successors` | line 1900-1902 | line 700 |
| Pending: save + buffered TaskDispatched | "pending" branch (1958-1987) | `_save_pending_node_tasks` (498-518) |

### Other bypasses

- **`handle_entry_nodes` (1372-1473)** — BUILTIN entry nodes still call `invoke_handler` + `_complete_builtin_inline` + `_route_to_successors` inline (1416-1473); PENDING entry nodes call `_save_pending_node_tasks` directly. Only the TASK branch (post 2026-04-14 fix at 1396-1402) delegates to `_enqueue_or_dispatch`.
- **LOOP early short-circuit (583-655)** — `dispatch_or_handle_task` detects `node.node_type == LOOP` *before* invoking the handler and diverts to `event_driven_loop_driver.run_loop` / `initialize_loop_queued`. The driver short-circuits the uniform pipeline even though `LoopHandler` already returns `completion_mode="loop_body"`.
- **PENDING resume use cases** — fall through `process_and_dispatch_successors` → `_enqueue_or_dispatch` → legacy path. Resolves automatically once the inline path is collapsed.

### Cannot be uniform (and that's fine)

- **Child workflow** (`child_run`) — parent task parks in RUNNING; child completes via `orchestrate_complete_child` callback. `_exec_uniform` already handles this at 1989-1997.
- **PENDING wait** — handler runs once, task sits in PENDING, resume re-enters the pipeline via successor processing. Already uniform end-to-end on the queued path.
- **LOOP iteration scheduling** — the *driver* must still decide "advance vs. exit" between iterations; only the *entry* into the driver needs to flow through `_exec_uniform`.

---

## Proposed approach

Phase 6 finishes when `_exec_uniform` is the only execute-a-node code path. The migration is mechanical and can be done in four sequenced steps with no behavior change at each step.

### Step 1 — Collapse inline mode into `_exec_uniform`

**Goal:** delete the duplicate TASK / BUILTIN / PENDING branches in `dispatch_or_handle_task`.

**Edit:** `_enqueue_or_dispatch` (`orchestration_engine.py:114-127`):
- When `self._queue is None`, call `await self._exec_uniform(task, run, workflow, dag_info)` directly instead of `await self.dispatch_or_handle_task(...)`.
- `_exec_uniform` already invokes the handler internally (1859-1865) when `completed_task is None`, so no caller change is needed.

**Removals:** in `dispatch_or_handle_task` (520-840), delete the BUILTIN block (553-709), the PENDING block (710-733), and the TASK block (735-840). Keep only the LOOP short-circuit until Step 3 retires it.

**Risk surface:**
- The legacy TASK path has a `needs_resolution` gate (740-762) that re-runs the handler when `task.resolved_input is None`. `_exec_uniform` always invokes the handler when `completed_task is None`, so this works automatically — just confirm the handler is not invoked twice for entry-TASK nodes (it isn't, because `handle_entry_nodes` doesn't pre-invoke).
- The CSV `dispatch_input` capture (line 772) preserves the pre-conversion payload for the worker. `_exec_uniform` does the same at 1903-1923 — verify identical ordering with a regression test.

**Test:** the existing 1403 tests must pass unchanged. Add one assertion in `tests/unit/test_orchestration_engine.py` that `_enqueue_or_dispatch` with no queue calls `_exec_uniform` (mock and verify).

### Step 2 — Route entry nodes through `_exec_uniform`

**Goal:** make `handle_entry_nodes` symmetric across all node types.

**Edit:** in `handle_entry_nodes` (`orchestration_engine.py:1386-1473`), replace the per-type branching with:

```python
for entry_node_id in entry_node_ids:
    node = workflow.get_node(entry_node_id)
    if not node:
        continue
    entry_task = task_factory.create_task(node, run)
    self._task_repo.save(entry_task)
    try:
        await self._exec_uniform(entry_task, run, workflow, dag_info)
    except (ExpressionError, InputRequirementError) as exc:
        self._mark_task_failed(entry_task, node, run, str(exc))
```

**Removals:** the BUILTIN buffered-`TaskDispatched` block (1396-1414), the inline handler invocation (1418-1424), `_complete_builtin_inline` call (1449), `_save_pending_node_tasks` call (1467-1468), and the `_route_to_successors` else-branch (1471-1473).

**Risk surface:**
- The buffered-event ordering for BUILTIN entries (event currently buffered then drained) becomes a `publish_event` from `_exec_uniform`. Verify the front-end doesn't depend on the specific buffering order — `tests/unit/test_orchestration_engine.py::test_entry_node_expression_error_continues_other_entries` and the three regression tests added on 2026-04-14 cover the main cases; add coverage for BUILTIN entry (TRIGGER, MERGE) emitted-event sequence.
- Loop entries with `loop_has_body` (1450-1457) currently enqueue body tasks manually. `_exec_uniform`'s `loop_body` branch (1999-2028) does the same enqueue — confirm equivalent semantics with a LOOP-as-entry-node test.

### Step 3 — Move the LOOP driver invocation inside `_exec_uniform`

**Goal:** remove the pre-handler short-circuit at `dispatch_or_handle_task:583-655`. After Step 1 this lives in `_exec_uniform` instead.

**Edit:** in `_exec_uniform`'s `loop_body` branch (1999-2028), delegate to `event_driven_loop_driver` (queued-mode `initialize_loop_queued`, inline-mode `run_loop`) using the same selection logic that lives in `dispatch_or_handle_task` today (the `_event_driven_loop_driver` presence + queue presence check).

**Removals:** the LOOP block in `dispatch_or_handle_task` (583-655) — at this point the function is empty or a thin pass-through and can be deleted.

**Risk surface:**
- `LoopHandler` currently sets `loop_state` *before* the driver runs. Confirm `_exec_uniform` applies `state_updates` / `loop_state_updates` (line 1875+) before reaching the `loop_body` branch — it does (the apply is unconditional at 1875).
- Inline-mode `run_loop` mutates `run.context.completed_nodes` directly. After Step 1, the run is no longer saved by `dispatch_or_handle_task`; ensure the driver or the post-`_exec_uniform` caller saves the run. Existing `_orchestrate_start_inner` saves at 1598 — check parity for `orchestrate_continue` (1633), `orchestrate_fail` (1655), and the resume use cases.

### Step 4 — Verify PENDING resume works automatically

**Goal:** confirm Step 1 covers the resume path; no edits expected.

**Test:** trace `ResumeInputUseCase` / `ResumeHumanActionUseCase` → `process_and_dispatch_successors` → `_enqueue_or_dispatch` → (new) `_exec_uniform`. Run `tests/unit/test_resume_input_node.py`, `test_resume_pending_task.py`, and `test_csv_hitl_flow.py` to validate.

---

## Files to modify

| File | Change |
|---|---|
| `app/layer1_domain/services/orchestration_engine.py` | `_enqueue_or_dispatch` (114), `handle_entry_nodes` (1372), delete legacy TASK/BUILTIN/PENDING/LOOP blocks in `dispatch_or_handle_task` (520-840) |
| `tests/unit/test_orchestration_engine.py` | Add: inline-mode `_exec_uniform` delegation, BUILTIN entry uniform path, LOOP entry uniform path |

No handler changes — they already produce the right `completion_mode`.

## Files reused (no new abstractions)

- `_exec_uniform` (`orchestration_engine.py:1804-2030`) — the single execution pipeline.
- `_route_to_successors` (`orchestration_engine.py:2032+`) and `compute_fanout` (`dispatcher/fanout.py`) — port-based successor routing.
- `event_driven_loop_driver` (`loop/event_driven_loop_driver.py`) — iteration scheduling, retained as an internal detail of the `loop_body` branch.
- All `NodeHandler` implementations — no changes; they already set `completion_mode`, `output_port`, `taken_edge_ports`, `tasks_to_dispatch`.

## Verification

After each step, the full test suite must remain green:

```bash
cd apps/backend/ai-workflow-management/workflow/workflow-control-plane-service
python3 -m pytest tests/ -q
```

End-to-end (mock mode) validation across the high-risk surfaces:

| Scenario | Files |
|---|---|
| TASK entry with success edges | `tests/unit/test_orchestration_engine.py::TestHandleEntryNodesTask` |
| BUILTIN entry (TRIGGER) emits state updates | extend `test_entry_node_expression_error_continues_other_entries` |
| Branching: CONDITION / SWITCH `taken_edge_ports` | `test_node_handlers.py`, `test_successor_processor.py` |
| LOOP queued + inline | `test_event_driven_loop_driver.py`, `test_loop_handler_event_driven.py`, `test_loop_driver_body_subgraph.py` |
| MERGE quorum (deferred → immediate) | existing merge tests in `test_node_handlers.py` |
| Child workflow | `test_child_workflow.py` |
| PENDING resume (INPUT, HUMAN_ACTION, CSV) | `test_resume_input_node.py`, `test_resume_pending_task.py`, `test_csv_hitl_flow.py` |
| Parallel INPUT (multi-entry race) | `test_parallel_input_flow.py` |

Manual smoke (mock infra mode) using the `test trigger both` shape from the 2026-04-14 bug:

```bash
cd workflow-control-plane-service && python main.py &
cd worker-executor-service && python main.py &
cd worker/http-request-worker && python main.py &
# POST: A,B → C → D,E (all TASK, success edges)
# Expected: 5 worker invocations; run COMPLETED; no TaskSkipped events on success edges
```

## What this unlocks

Once `_exec_uniform` is the only execute path:

1. **Adding a new node type** = writing one handler that returns the right `completion_mode`. No engine edits.
2. **Adding a new completion mode** = one branch in `_exec_uniform`. No legacy mirror to update.
3. **The "should builtins move to the executor?" question becomes moot** — the engine's job is "run a pipeline of nodes," and the kind of node is decided by the handler. Moving builtin handlers across a service boundary still costs a network round-trip per evaluation and still requires distributed run-state coordination, but the *engine architecture* no longer leans toward one answer or the other.
4. **The path to multi-instance correctness shortens** — once there is one execute path, wrapping it in a distributed lock (see `phase-6-uniform-execution-completion.md` follow-up: distributed-lock plan) is one change instead of three.

## Out of scope

- **Distributed lock for multi-instance deployment** — tracked separately. The current `asyncio.Lock` per `run_id` (`orchestration_engine.py:108, 239-242`) remains single-process only.
- **Moving handlers to a separate service** — explicitly *not* recommended; see "What this unlocks" item 3.
- **Schema or DTO changes** — none required.
