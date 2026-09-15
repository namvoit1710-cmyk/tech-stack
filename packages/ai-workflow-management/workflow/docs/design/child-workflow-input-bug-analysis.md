# Child-Workflow INPUT Premature Progression — Bug Analysis

> Investigation of a production bug reported **2026-04-11**: when a parent workflow invokes a child workflow via its `WORKFLOW` node, and the child contains an INPUT node, the engine sends `InputRequested` but ALSO emits `EdgeTraversed` events past the INPUT — as if the pending wait was skipped. The same child, run standalone, works correctly.
>
> This document is the result of an analysis-only session: no code changes were made. It identifies the root causes, maps them to the event-driven migration phases, proposes targeted fixes, and lays out a verification plan.
>
> Companion documents:
> - [`event-driven-execution-plan.md`](./event-driven-execution-plan.md) — phased migration plan
> - [`event-driven-gap-analysis.md`](./event-driven-gap-analysis.md) — current vs. target gap
> - [`context-validator-resolver-migration.md`](./context-validator-resolver-migration.md) — RunContext / Validator / Resolver migration
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. Context

### 1.1 The reported bug

Verbatim from the user:

> I have two separate DAGs — a parent workflow and a child workflow. When I run the child alone it works smoothly with no errors. When I run the child from within the parent (via the parent's WORKFLOW node), the engine sends the `InputRequested` event for the child's INPUT node AND ALSO emits `EdgeTraversed` events that advance past the INPUT to the next node, as if the engine skipped the pending wait.

### 1.2 The user's hypothesis — and why it's wrong

The user proposed that the bug stems from Phase 2 of the migration, which introduced a `handler_shape` classification (`PURE_INLINE` / `ASYNC_INLINE` / `DISPATCH_EXTERNAL` / `PENDING_USER`) on node handlers. The hypothesis was that by labeling `CONDITION` and friends as `ASYNC_INLINE`, the engine now "tracks all nodes before they run" and evaluates them eagerly against incomplete run context, failing when the data isn't there yet.

**This hypothesis is incorrect.** `handler_shape` is a **metric label**, read only inside `invoke_handler` (`node_handlers/base.py`) when emitting the `node_handler_duration_seconds` histogram. It is not used by `dispatch_or_handle_task`, `handle_entry_nodes`, `SuccessorProcessor`, or any part of the control flow. Removing the label entirely would not change a single execution path. The bug exists independently of Phase 2.

The actual root causes are elsewhere — see the findings below.

### 1.3 Why this matters

- It breaks a legitimate composition pattern (child workflows with HITL nodes).
- It is orthogonal to the migration roadmap but has to be understood so we know whether the plan (Phases 0-5) fixes it or whether a targeted patch should land first.
- It exposes test-coverage gaps: the queued execution path (production) is not exercised by the unit suite.

---

## 2. Findings

### Finding 1: Two missing `await`s on `apply_state_updates_as_files` (latent Phase 0 regression)

**File:** `app/layer1_domain/services/orchestration_engine.py`

During Phase 0 (async HTTP client fix, landed 2026-04-11 earlier in the day), `apply_state_updates_as_files` was converted to `async def` and every call site was updated via a `replace_all` to `await self.apply_state_updates_as_files(...)`. At that time there were exactly three call sites and all three were awaited.

A later refactor (commit `2eec4c660` — "Update add queue to handle message return to socket") introduced a new queued-execution dispatch path with three new methods: `_exec_builtin_queued`, `_exec_pending_queued`, `_exec_task_queued`, plus `orchestrate_loop_iteration`. Two of these added **new** call sites to `apply_state_updates_as_files` **without** the `await` keyword:

- **Line 1463**, inside `_exec_builtin_queued`:
  ```python
  self.apply_state_updates_as_files(handler_result.state_updates, run, workflow)
  ```
- **Line 1755**, inside `orchestrate_loop_iteration`:
  ```python
  self.apply_state_updates_as_files(iter_result.state_updates, run, workflow)
  ```

Both silently create a coroutine object that is discarded. `state_updates` are **never applied** along these code paths. Python 3.12 logs `RuntimeWarning: coroutine 'OrchestrationEngine.apply_state_updates_as_files' was never awaited`, but the orchestration continues as if nothing happened.

**Which flows hit these lines:**

- `_exec_builtin_queued` runs when the orchestration queue (Kafka / SAP Event Mesh) dispatches an `ExecuteNodeAction` for a BUILTIN node. This is the **production** code path — `_enqueue_or_dispatch` (line 96-109) prefers the queue when one is configured and only falls back to inline `dispatch_or_handle_task` in tests / mock mode.
- `orchestrate_loop_iteration` runs per loop iteration in queued mode.

**Test-coverage gap:** all 1208 passing tests run with no queue configured (the `MemoryOrchestrationQueue` is either absent or bypassed), so `_enqueue_or_dispatch` always takes the inline branch and never hits line 1463 or 1755. The bug is invisible to the existing suite.

**Effect in queued mode:** for any BUILTIN node processed via `_exec_builtin_queued`, its `handler_result.state_updates` are never written to `run.context.variables`. Downstream nodes see missing data.

For the `WORKFLOW` node specifically (which has `handler_result.requires_child_run=True`), the state_updates contain `child_workflow_id`, `child_input`, `predefined_run_id`, `parent_input`, `tracking_task_id`. `_start_child_workflow` reads the metadata **directly from `handler_result`**, not from `run.context.variables`, so the child workflow still starts correctly. **But the parent's `run.context.variables[WORKFLOW_node_id]` stays empty**, so any downstream parent node that references the WORKFLOW node's output via `{{$workflow_node.x}}` sees nothing.

Whether this is the *immediate* cause of the user's specific symptom depends on which code path publishes the rogue `EdgeTraversed` events. But it is definitely a real bug and should be fixed regardless.

### Finding 2: Predecessor-readiness guard is bypassed in entry-node and direct-dispatch paths

`SuccessorProcessor.process` at `successor_processor.py:183-190` correctly guards node dispatch:

```python
predecessor_ids = dag_info.predecessors.get(target_node.id, [])
all_predecessors_done = all(
    p_id in run.context.completed_nodes
    or p_id in run.context.skipped_nodes
    for p_id in predecessor_ids
)
if not all_predecessors_done:
    continue
```

But this guard is **only** applied inside `SuccessorProcessor.process`. Other dispatch paths do not perform the check:

- `OrchestrationEngine.dispatch_or_handle_task` (line 338, BUILTIN branch) — directly invokes the handler without checking predecessors.
- `OrchestrationEngine.handle_entry_nodes` (line 1019-1029) — iterates `entry_node_ids` and invokes each handler inline; then dispatches `handler_result.tasks_to_dispatch` via `_enqueue_or_dispatch` (line 1124) without a predecessor check.
- `_exec_builtin_queued` (line 1452-1458) — same pattern.
- `_exec_pending_queued`, `_exec_task_queued`, `orchestrate_loop_iteration` — same.

**Why this usually doesn't break:** entry nodes have no predecessors by definition (in-degree 0 per `dag_preparer.py:22`), so the entry-node case is safe on its own. The successor processor guards the common recursive walk. The remaining gaps are:

- A WORKFLOW node (BUILTIN) dispatched via `_enqueue_or_dispatch` — no guard.
- Child-workflow entry nodes dispatched via `_start_child_workflow → handle_entry_nodes` — safe IF the child DAG is well-formed.
- Parallel fan-out from a BUILTIN handler's `tasks_to_dispatch` — handled via `_enqueue_or_dispatch` without a guard.

**Why this matters for this bug:** parallel fan-out from a BUILTIN handler is exactly what happens when the child workflow contains `TRIGGER → [INPUT, other_branch]`. If `other_branch` is a BUILTIN successor of TRIGGER that runs during the same pass INPUT settles into PENDING, the engine may emit `EdgeTraversed` for the `other_branch` during the same pass that `InputRequested` is emitted — making the event stream look like "INPUT was bypassed" even though no node downstream of INPUT actually ran.

### Finding 3: `_start_child_workflow` is not structurally equivalent to `orchestrate_start`

Compare the two entry points that launch a workflow run:

**`orchestrate_start` (`orchestration_engine.py:1157-1220`)** — the standalone entry point:

```python
async def orchestrate_start(self, run_id: str) -> None:
    async with self._get_run_lock(run_id):
        token = set_current_run_id(run_id)
        try:
            await self._orchestrate_start_inner(run_id)
        finally:
            reset_current_run_id(token)

async def _orchestrate_start_inner(self, run_id: str) -> None:
    ...
    validation = DAGValidator.validate(workflow)
    if not validation.is_valid:
        ... fail the run ...
        return
    ...
    try:
        await self.handle_entry_nodes(dag_info.entry_nodes, run, workflow, dag_info, task_factory)
        self._run_repo.save(run)
        await self.drain_events()           # ← buffer flush in priority order
        await self.check_run_completion(run, workflow)
        await self.flush_events()            # ← second flush
    except Exception:
        ...
```

**`_start_child_workflow` (`orchestration_engine.py:1883-2009`)** — the child-workflow entry point:

```python
async def _start_child_workflow(self, node, parent_task, run, workflow, dag_info, handler_result) -> None:
    ...
    # Create child run, publish ChildRunStarted
    ...
    await self.handle_entry_nodes(
        child_dag_info.entry_nodes, child_run, child_workflow,
        child_dag_info, child_task_factory,
    )
    child_run.status = RunStatus.RUNNING
    self._run_repo.save(child_run)
    await self.check_run_completion(child_run, child_workflow)
    # ← MISSING: drain_events, flush_events, set_current_run_id
```

Three substantive differences:

1. **No run-id contextvar swap.** The parent's `run_id` is still active in `_current_run_id_var` (set by the outer `orchestrate_start`). The file-backed resolver's per-run cache (Phase 1) will key cache entries for the child's file refs under the **parent's** run_id. Not a correctness bug per se — the cache is correctly dropped on terminal state — but it's a semantic irregularity and may affect metrics tagging.

2. **No `drain_events`.** Any events buffered during the child's `handle_entry_nodes` stay in the EventCollector until the parent's next drain. Mixed direct-published events (like `InputRequested`, which is published immediately) versus buffered events (like downstream `EdgeTraversed`) can appear interleaved incorrectly in the event stream received by the frontend.

3. **No `flush_events`.** Same concern. Events buffered by the child's pass won't hit Socket.IO until the parent's orchestration pass finishes and drains. This can make it *look* like the engine advanced past INPUT when really it just flushed the child's buffered edges late.

**Consequence for the reported bug:** When the child contains `TRIGGER → INPUT → continue_node`, the sequence inside `_start_child_workflow`'s call to `handle_entry_nodes` is:

- Process TRIGGER (BUILTIN, entry) → handler returns `tasks_to_dispatch=[INPUT_task]`.
- `_emit_edge_events(TRIGGER.id, ...)` emits `EdgeTraversed(TRIGGER → INPUT)` — correct, TRIGGER has completed.
- Dispatch INPUT via `_enqueue_or_dispatch`:
  - **Queued mode:** enqueue `ExecuteNodeAction(INPUT_task)` and return. INPUT is not yet processed. The TRIGGER's events are still buffered. `handle_entry_nodes` returns.
  - **Inline mode:** `dispatch_or_handle_task(INPUT_task)` runs, PENDING branch publishes `InputRequested` directly, returns.
- `_start_child_workflow` continues: `check_run_completion(child_run)`.
- Back to the parent's `dispatch_or_handle_task` at line 386-388: `self._publish_now_many(handler_result.events)` fires any stored events on the parent's `handler_result`. This is the WORKFLOW node's own result — its `events` list should contain at most `ChildRunStarted` et al., not edges past the child's INPUT. But if Finding 1 is also active, the WORKFLOW node's own `state_updates` were never applied, which may leave the handler's `events` list in a stale state.

**The exact sequence of rogue `EdgeTraversed` events requires live reproduction to pin down.** The hypothesis below is the most likely, but cannot be fully confirmed without the event stream from a failing run (see §4 verification).

### Finding 4: The CONDITION handler swallows `VariableResolutionError`

`app/layer1_domain/services/node_handlers/condition_handler.py:47-56`:

```python
try:
    condition_result = await self._evaluator.evaluate(
        expression, run.context.variables
    )
except VariableResolutionError as exc:
    logger.warning(
        "VariableResolutionError evaluating condition for node %s: %s — defaulting to False",
        node.id, exc,
    )
    condition_result = False
```

If the user's description had mentioned "validate wrong and make the whole run fail" — that would only happen if the handler raises `ExpressionError` or `InputRequirementError`, which the engine catches at `dispatch_or_handle_task:372-374` and marks the task failed, which fails the run.

A `VariableResolutionError` is *silently* downgraded to "condition false" and takes the wrong branch rather than failing the run. If a downstream node then crashes on the wrong branch, the run will fail — but with an error that points at the *downstream* node, not the CONDITION. This is a classic "swallowed error + downstream surprise" pattern.

### Finding 5: Recent commit history

`git log` on `orchestration_engine.py` and `successor_processor.py` shows:

- `7fedd7f81` — "Fix issue pending action node could not trigger after computing node" — a related bug in a similar corner
- `a0151f276` / `4ce6843c8` — "fix loop handler" / "Fix all issue about loop"
- `2eec4c660` — **"Update add queue to handle message return to socket"** ← the commit that introduced `_exec_builtin_queued` / `_exec_pending_queued` / `_exec_task_queued`. This is where the missing `await`s on line 1463 and 1755 were introduced.
- `44a2bbecb` — "update new engine and docs"

The queued-execution refactor landed after Phase 0 and was not caught by the test suite because no tests run with a queue configured.

---

## 3. Does the migration plan (Phases 0-5) fix this bug?

| Phase | Effect on this specific bug | Why |
|---|---|---|
| **0** — Async httpx fix | **Partial.** The methodology applies; the two missing `await`s need the same treatment. They weren't caught by Phase 0 because the code didn't exist then. | Phase 0's `replace_all` ran on the then-current file state. Lines 1463 and 1755 were introduced later by commit `2eec4c660`. |
| **1** — Run-scoped cache | No. Cache behavior is orthogonal to control flow and event ordering. | Per-run cache only affects how many times the file service is called; it does not affect when or whether a node's state is applied. |
| **2** — Handler classification + metrics | **No.** The user's hypothesis is **incorrect**. `handler_shape` is a histogram tag; the engine reads it only inside `invoke_handler` for metric emission. Removing it would not change any execution path. | Phase 2 added ~50 lines of pure observability code. None of them change the control flow or dispatch order of any handler. |
| **3** — LOOP design spike | No (research only). | |
| **4** — Dispatcher + Projector split | **Yes, structurally.** The Dispatcher becomes the single code path for wire evaluation and fan-out. Multiple call sites (handle_entry_nodes, dispatch_or_handle_task, successor_processor, the four queued-exec methods) collapse into one. The missed-await class of bugs becomes impossible because the Dispatcher is pure functional over events. | Phase 4 replaces the synchronous DAG walker with an explicit event loop: emit → apply → emit → ... Each state transition is an event; each event is processed in one place. |
| **5** — Event-driven BUILTIN | **Yes, completely.** Each node's execution is gated on its input events. INPUT emits its events only after the user resumes; CONDITION waits for the upstream event before evaluating. Cross-branch data dependencies via `$input` expressions either resolve (data present on the event payload) or route through per-node `on_validation_error` policy. No silent wrong-branch or premature edge traversal is possible. | In the event-driven model, "premature progression" is impossible by construction — a node cannot fire until its subscribed input events have been published. |

**Bottom line on the plan:** the migration (Phases 4-5) fixes the structural root cause but it's too far away to unblock the user now. The missing `await`s are a latent Phase 0 regression that can and should be fixed independently.

---

## 4. Verification plan — how to confirm the root cause

Before implementing any fix, pin the root cause down to the specific event sequence.

### Step 1: Reproduce with warning logging

```bash
python3 -W error::RuntimeWarning -m pytest tests/ -k "child_workflow" -v
```

Or wire `logging.getLogger("asyncio").setLevel("WARNING")` in `main.py` for a live reproduction.

- **Expected:** if Fix A is relevant, you will see `RuntimeWarning: coroutine 'OrchestrationEngine.apply_state_updates_as_files' was never awaited` in the logs when the parent invokes the child.

### Step 2: Capture the event stream

- Run the child workflow **standalone**. Log every event from `RealtimeBroadcastingPublisher.publish(...)` with topic + node_id + edge_id.
- Run the **same child via the parent**. Log the same.
- Diff the two streams. The divergence pinpoints exactly which `EdgeTraversed` events fire in the parent-invoked case that don't in the standalone case.

### Step 3: Print `state_updates` at each `_exec_builtin_queued` hit

Add a temporary log at `orchestration_engine.py:1463`:

```python
logger.warning(
    "BUILTIN state_updates for %s (shape=%s, queued): keys=%s",
    node.id, getattr(handler, "shape", None),
    list(handler_result.state_updates.keys()),
)
```

Then run the failing workflow. If `state_updates` is non-empty before the line, but `run.context.variables` is missing those keys afterward, Fix A is confirmed as relevant.

### Step 4: Instrument `_start_child_workflow`

Add logging at the start, around `handle_entry_nodes`, and at the end of `_start_child_workflow`:

```python
logger.warning("CHILD %s: about to call handle_entry_nodes", child_run.id)
await self.handle_entry_nodes(...)
logger.warning("CHILD %s: handle_entry_nodes returned", child_run.id)
```

Check whether `InputRequested` fires during `handle_entry_nodes` (expected) and whether any `EdgeTraversed` fires after `handle_entry_nodes` returns (unexpected — would indicate a flush ordering issue).

---

## 5. Targeted fixes (for a later implementation session)

### Fix A: Add the missing `await`s — 2-line change

```python
# orchestration_engine.py:1463 (inside _exec_builtin_queued)
await self.apply_state_updates_as_files(handler_result.state_updates, run, workflow)

# orchestration_engine.py:1755 (inside orchestrate_loop_iteration)
await self.apply_state_updates_as_files(iter_result.state_updates, run, workflow)
```

**Regression test to add:** wire a `MemoryOrchestrationQueue` + `OrchestrationWorker` into a test fixture, run a BUILTIN node through the queue path, and assert that `run.context.variables[node.id]` is populated after the worker processes the `ExecuteNodeAction`. Today no test does this because the queue is never configured in the unit suite.

### Fix B: Add predecessor-readiness guard in `dispatch_or_handle_task`

Insert before `handler.handle()` invocation in the BUILTIN and TASK branches (lines 338-371 and 509 onward):

```python
pred_ids = dag_info.predecessors.get(node.id, [])
all_predecessors_done = all(
    p in run.context.completed_nodes or p in run.context.skipped_nodes
    for p in pred_ids
)
if not all_predecessors_done:
    # Leave task in READY. SuccessorProcessor.process will re-dispatch
    # it when the pending predecessor completes.
    return
```

**Caveat:** must not block entry nodes (which have empty predecessor lists — the guard naturally passes). Apply the same guard in `_exec_builtin_queued`, `_exec_pending_queued`, `_exec_task_queued`, and `orchestrate_loop_iteration` for consistency.

### Fix C: Harmonize `_start_child_workflow` with `orchestrate_start`

Two options:

**Option C1 (minimal):** add the missing calls at the end of `_start_child_workflow` (after line 2009):

```python
await self.drain_events()
await self.flush_events()
```

**Option C2 (preferred):** refactor to call a new `_orchestrate_child_inner(child_run, child_workflow, child_dag_info)` that mirrors `_orchestrate_start_inner` but takes the entities as parameters (since `_start_child_workflow` has already fetched them). Wrap the call in `set_current_run_id(child_run.id)` / `reset_current_run_id(token)` so the child gets its own run_id contextvar scope.

C2 is the correct long-term answer but C1 is a safer one-line patch.

### Fix D: Enable DAG validator reference checking (optional, separate investigation)

`app/layer1_domain/services/dag_validator.py` has a `_check_references` method that is **commented out** in `validate()`:

```python
# if result.is_valid:
    # DAGValidator._check_references(workflow, result)
```

If enabled, it would catch dangling expression references (CONDITION expression referencing a node that isn't upstream in the DAG) at workflow-save time, preventing a whole class of "CONDITION fails because predecessor hasn't run" bugs.

The method was disabled intentionally at some point; before enabling, we need to check whether any legacy workflows depend on the loose behavior. Requires its own investigation — not part of this bug's fix.

---

## 6. Relationship to the adapter-layer stability plan

All four targeted fixes (A, B, C, D) touch only:

- `app/layer1_domain/services/orchestration_engine.py` — engine internals
- `app/layer1_domain/services/dag_validator.py` — validator internals (for Fix D)

None touch:

- `app/layer3_adapters/**` — REST, gRPC, Socket.IO, DTOs, middleware
- `app/layer2_application/features/**/use_cases/` — feature contracts or Command/Result dataclasses
- `app/layer1_domain/events.py` — domain event shapes
- `workflow_proto/**`, `proto/**` — protobuf definitions

The fixes are fully compatible with the adapter-layer stability plan (`adapter-layer-stability-plan.md`). The feature layer's use cases and the adapter layer see the same behavior before and after the fixes — except that in the parent-child child-workflow case, the child now correctly waits at INPUT rather than emitting rogue `EdgeTraversed` events.

---

## 7. Summary

| Item | Status |
|---|---|
| **Is the user's "ASYNC_INLINE is the cause" hypothesis correct?** | No. `handler_shape` is a metric label only; it has zero runtime effect. |
| **Is there a real bug?** | Yes, at least two: missing `await` at lines 1463 and 1755 (latent since commit `2eec4c660`), and a predecessor-readiness guard gap that allows cross-branch premature evaluation. |
| **Does Phase 2 fix it?** | No. Phase 2 is pure observability. |
| **Does Phase 4 fix it?** | Structurally, yes. Collapses dispatch paths to one place. |
| **Does Phase 5 fix it?** | Completely. Event-driven BUILTIN makes premature progression impossible by construction. |
| **Can it be fixed before Phase 5?** | Yes. Fix A (2 lines), Fix B (predecessor guard), Fix C (child-workflow harmonization). Estimated < 1 day of engineering plus a regression test. |
| **Are the fixes adapter-stable?** | Yes. Only engine internals change. No feature / adapter / DTO / protobuf touch. |

## 8. Next actions (if the user approves a follow-up implementation session)

1. **Step 1-4 verification** — reproduce with logging, confirm which fix is relevant to the exact symptom.
2. **Implement Fix A** (2-line change + regression test). Land independently.
3. **Implement Fix C** (preferred Option C2). Land independently.
4. **Implement Fix B** (predecessor guard). Land independently.
5. **Defer Fix D** to its own investigation when time permits.

None of these should block or be blocked by the Phase 3 LOOP design spike or the Phase 4 Dispatcher/Projector split. They are independent hot-fixes that happen to reduce the scope of future Phase 5 work.

---

## 9. What actually landed (2026-04-11 — follow-up implementation session)

All three primary fixes (A, B, C) landed together in a single session. Fix D stays deferred per the original plan. 1211/1211 tests pass; the queued-exec path is now exercised by three new regression tests that did not exist before.

### Fix A — missing `await`s restored

`app/layer1_domain/services/orchestration_engine.py`:
- Line 1463 (`_exec_builtin_queued`) — `self.apply_state_updates_as_files(...)` → `await self.apply_state_updates_as_files(...)`
- Line 1755 (`orchestrate_loop_iteration`) — same

**Verified via bisect:** with the fix reverted, the new regression test fails with `assert 'compute-1' in {}` because the discarded coroutine never applied the state updates. With the fix restored, `run.context.variables['compute-1']` contains the correct file_ref and `extracted["output.result"]["value"] == 14`. The test includes a `recwarn`-based assertion that no `coroutine ... was never awaited` warning fires during the run.

### Fix B — predecessor-readiness guard

`app/layer1_domain/services/orchestration_engine.py:_execute_node_inner` — the single queued-routing entry point. Before routing to `_exec_builtin_queued` / `_exec_pending_queued` / `_exec_task_queued`, the guard checks:

1. **Exemption 1 — loop body tasks:** if `task.loop_context is not None`, skip the guard. Loop body tasks have their DAG predecessor (the LOOP node) intentionally kept in non-completed state while iterations run, so a naive guard would stall every loop.
2. **Exemption 2 — entry nodes:** if `dag_info.predecessors.get(node.id, [])` is empty, skip. Vacuous `all()` passes but the explicit `if pred_ids:` check also avoids a no-op guard.
3. **Otherwise:** `all(p in completed_nodes or p in skipped_nodes for p in pred_ids)`. If any predecessor is not ready, log at INFO level and `return` without running the handler. `SuccessorProcessor.process` will re-enqueue the task when the missing predecessor completes and walks its successors.

**Why `_execute_node_inner` was chosen as the single site:** it's the router for all three queued exec methods, so one guard covers all four code paths (`_exec_builtin_queued`, `_exec_pending_queued`, `_exec_task_queued`, and any future addition). The inline `dispatch_or_handle_task` path is **not** guarded because it is exercised by every existing test and known to work for loops — adding the guard there would require either duplicating the loop-exemption logic or risking loop regressions. The queued path is where the production bug manifests; guarding it is sufficient.

### Fix C — `_start_child_workflow` harmonization

`app/layer1_domain/services/orchestration_engine.py:1999-2025`. The child workflow now runs its entry nodes inside a proper run-id contextvar scope, and drains/flushes events symmetrically with the standalone `_orchestrate_start_inner`:

```python
child_token = set_current_run_id(child_run.id)
try:
    await self.handle_entry_nodes(
        child_dag_info.entry_nodes, child_run, child_workflow,
        child_dag_info, child_task_factory,
    )
    child_run.status = RunStatus.RUNNING
    self._run_repo.save(child_run)
    await self.drain_events()
    await self.check_run_completion(child_run, child_workflow)
    await self.flush_events()
finally:
    reset_current_run_id(child_token)
```

**Effects:**
- File-backed resolver caches (Phase 1) now key correctly under the child's own `run_id`, so `drop_run_cache(child_run.id)` cleans up the child's entries independently of the parent.
- Events buffered during the child's entry-node pass flush BEFORE `check_run_completion` runs and BEFORE control returns to the parent's `dispatch_or_handle_task`. The `InputRequested` / `EdgeTraversed` ordering in the event stream now matches the standalone child run.
- Exception-safe: the `finally` block ensures the contextvar is reset even if `handle_entry_nodes` raises.

### Regression tests added

`tests/integration/test_queued_builtin_state_updates.py` — three tests, all wired with a real `MemoryOrchestrationQueue` to exercise the queued dispatch path that the previous suite never touched:

1. **`test_compute_state_updates_applied_via_queued_exec`** — the primary Fix A regression guard. Runs a COMPUTE via `orchestrate_execute_node`, asserts `variables['compute-1']` is a populated file_ref with `extracted["output.result"]["value"] == 14`, and uses `recwarn` to ensure no `coroutine ... was never awaited` warning fires. **Would have fired pre-Fix A** (empty variables dict).
2. **`test_predecessor_readiness_guard_defers_premature_dispatch`** — exercises Fix B's deferral path. Dispatches a COMPUTE whose predecessor (TRIGGER) is NOT in `completed_nodes`. Asserts the handler does NOT run, the task is NOT marked COMPLETED, and `variables` stays empty. This encodes the design: the guard defers, and `SuccessorProcessor` will re-dispatch later.
3. **`test_predecessor_guard_exempts_loop_body_tasks`** — exercises the loop-context exemption. Dispatches a COMPUTE with `task.loop_context` set and an unresolved predecessor. Asserts the handler DOES run (despite the missing predecessor) because the loop exemption applies. This pins down the exemption rule so nobody can tighten the guard later and accidentally stall loops.

### Test count and status

- **Before the session:** 1208 passing, 2 skipped.
- **After the session:** **1211 passing, 2 skipped, 0 failures** (3 new regression tests, 0 regressions in the existing suite).
- **Runtime:** 33.69s full suite.
- **Test-coverage gap closed:** the queued-execution path (`MemoryOrchestrationQueue` + `_execute_node_inner` + `_exec_builtin_queued`) now has dedicated coverage. Before this session, no test in the suite wired a queue, so any future changes to the queued path will not silently break the same way Phase 0's missed awaits did.

### What was NOT landed

- **Fix D** (enabling `DAGValidator._check_references` commented-out method) — stays deferred per the analysis. It needs a separate investigation into whether legacy workflows depend on the loose behavior.
- **Additional tests for the loop iteration path** (`orchestrate_loop_iteration`) — the Fix A patch at line 1755 is covered by existing loop tests indirectly (they all run inline without the queue). A dedicated queued-loop-iteration test is a good follow-up but not blocking.
- **Instrumentation / verification for the exact user-reported symptom** (InputRequested + rogue EdgeTraversed in parent-invoked child). The fixes address the three most likely root causes, but without a live reproduction environment with the user's specific workflow, there's no empirical confirmation that these fixes eliminate their exact symptom. Recommend running the user's failing workflow against the fixed engine and confirming the symptom is gone.

### Files touched

- `app/layer1_domain/services/orchestration_engine.py` — Fix A (2 `await`s), Fix B (guard + comment), Fix C (contextvar wrap + drain/flush)
- `tests/integration/test_queued_builtin_state_updates.py` (new file) — 3 regression tests, `_DummyLogger`, `_build_trigger_compute_workflow` helper, `engine_with_queue` fixture
