# LOOP Design Spike — Event-Driven Migration

> **Phase 3 deliverable** of the event-driven migration execution plan. A research-only spike that answers, on paper, whether LOOP can survive an event-driven rewrite without losing current semantics. The spike gates Phase 4 (Dispatcher / Projector split) and Phase 5 (event-driven BUILTIN promotion).
>
> Date: **2026-04-11**. No code changes.
>
> Companion documents:
> - [`event-driven-orchestration.md`](./event-driven-orchestration.md) — target architecture
> - [`event-driven-execution-plan.md`](./event-driven-execution-plan.md) — phased migration plan
> - [`context-validator-resolver-migration.md`](./context-validator-resolver-migration.md) — how RunContext / Validator / Resolver change across phases
> - [`child-workflow-input-bug-analysis.md`](./child-workflow-input-bug-analysis.md) — related latent bug in the queued-exec path
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. Executive summary

**Can LOOP be migrated to event-driven without losing behaviour? Yes — and the migration also fixes a pre-existing bug that makes loops unusable in queued (production) mode.**

Key findings from the code audit (with file:line evidence, not speculation):

1. **The data model does not actually support nested loops today.** `Task.loop_context: dict | None` and `Task.iteration_index: int | None` are scalar fields, and `run.context.variables["loop"]` is a single dict that each iteration overwrites. The loop-depth field is present but wired to `0`. Any nested-loop workflow today silently clobbers the outer loop's iteration context.
2. **The queued execution path for loops is dead code.** `LoopIterationAction` is defined, routed by the worker, and handled by `orchestrate_loop_iteration` — but **nothing enqueues it**. In queued (production) mode, loops run the first body task (or first parallel batch), then stall indefinitely because the loop handler is never re-invoked. This is a latent bug discovered during the spike, separate from any migration concern.
3. **The inline path (`_run_loop_iterations`) works.** It is a Python `while` loop over live state that holds the iteration counter, the pending-task list, and the `run.context.loop_state` dict in-process. This is the one place loops work today — and only when the orchestration queue is `None` (tests, mock mode).
4. **`break` / `continue` / early-exit are not implemented.** There is no break node type. Condition-based loops terminate only when the boolean expression evaluates false at the top of the next iteration.
5. **Parallel-inside-loop bodies are supported**, but with a caveat: the current `MergeNodeHandler` reads `completed_nodes`, which is discarded between iterations via `run.context.completed_nodes.discard(body_task.node_id)`. Merge semantics inside a loop body are fragile and tested only in the happy path.

**Recommendation: proceed with Phase 5 LOOP event-driven promotion.** The migration gives us:
- Actual nested-loop support (via explicit `scope_stack` on events).
- Working queued-mode iteration (via an event type, not an orphaned action class).
- Break / early-exit via an explicit `loop.exit` event type.
- Per-iteration merge quorum buffers keyed by a scope-derived `invocation_id`.
- Deterministic replay from the event log for debugging.

**Migration risk is medium, not high.** The hardest piece is not the LOOP handler itself — it's the companion changes in the Merge handler (for scoped quorum keys) and the Condition/Switch handlers (for scoped edge emission). Phase 5 can land LOOP incrementally with a feature flag: event-driven LOOP behind a config switch, inline LOOP as the default until the event-driven path has production mileage.

The remainder of this document walks through the evidence, the proposed event schema, the state-reconstruction algorithm, and the nested-loop / parallel / break semantics in detail.

---

## 2. Current state — code audit

### 2.1 Data model

**`app/layer1_domain/entities/task.py:20-21`:**

```python
loop_context: dict[str, Any] | None = None
iteration_index: int | None = None
```

Both fields are scalars. They carry at most one loop's iteration info.

**`app/layer1_domain/entities/workflow_run.py` (RunContext):**

```python
loop_state: dict[str, Any] = field(default_factory=dict)
```

A flat dict keyed by LOOP node id. Each entry has the shape produced by `LoopNodeHandler._initialize_loop` at `loop_handler.py:88-99`:

```python
loop_state = {
    "items": items,
    "current_index": 0,
    "total": len(items),
    "completed_iterations": [],
    "failed_iterations": [],
    "mode": mode,                    # "sequential" | "parallel"
    "loop_type": loop_type,          # "collection" | "count" | "condition"
    "condition": node.config.get("condition", ""),
    "counter_variable": counter_variable,
    "next_dispatch_index": 0,
}
```

**`run.context.variables["loop"]`** (`loop_handler.py:108-115`): a single dict that is overwritten on each iteration:

```python
loop_var = {
    "index": 0,
    "item": items[0],
    "total": len(items),
    "first": True,
    "last": len(items) == 1,
    "depth": 0,  # ← always 0; nested-loop depth is a stub
}
result.state_updates["loop"] = loop_var
```

### 2.2 Loop types and modes

From `loop_handler.py:50-85`:

- **`loop_type="count"`** — iterate `range(max_iterations)`. Bounded by `max_iterations` clamped to `[0, 100_000]`.
- **`loop_type="condition"`** — generate `range(max_iterations)` as indices; the actual exit check runs in `_handle_iteration_complete` after each body completion (`loop_handler.py:176-189`). If the condition expression fails to resolve, the loop exits immediately (defensive against infinite loops).
- **`loop_type="collection"`** (default) — resolves `node.config.get("collection") or node.config.get("items_expression")` via the variable resolver. Non-list results are treated as empty.

Modes (from `node.config.get("mode", "sequential")`):

- **`sequential`** — one body task at a time, next dispatched after the previous completes (`loop_handler.py:191-218`).
- **`parallel`** — first batch of `parallelism` body tasks dispatched at once; `next_dispatch_index` advances as each iteration completes (`loop_handler.py:119-135, 220-237`).

### 2.3 Inline execution path (`_run_loop_iterations`)

`orchestration_engine.py:615-735`. This is the ONLY path that actually works for loops today.

```python
async def _run_loop_iterations(
    self, loop_node, loop_task, run, workflow, dag_info, handler_result, depth,
) -> None:
    ...
    pending: list[Task] = []
    for body_task in handler_result.tasks_to_dispatch:
        ...
        self._task_repo.save(body_task)
        pending.append(body_task)

    while pending:
        next_pending: list[Task] = []
        for body_task in pending:
            # Allow re-executing the same body node on each iteration
            run.context.completed_nodes.discard(body_task.node_id)

            # Emit edge.traversed from loop node → body entry
            ... publish EdgeTraversed ...

            await self._enqueue_or_dispatch(body_task, run, workflow, dag_info)

            # Feed this iteration's completion back to the loop handler
            synthetic = Task(
                id=f"{run.id}-{loop_node.id}-iter-{iter_idx}",
                node_id=loop_node.id, run_id=run.id,
                status=TaskStatus.COMPLETED, iteration_index=iter_idx,
            )
            iter_result = await invoke_handler(
                handler, monitor=self._monitor,
                node=loop_node, run=run, ...,
                completed_task=synthetic,
            )
            ... apply state updates ...

            # Check if loop is done
            completed_count = len(loop_state.get("completed_iterations", []))
            if completed_count >= total:
                ... dispatch exit tasks, return ...
            else:
                ... queue new body tasks → next_pending ...

        pending = next_pending
```

**Observations:**

- **Live-state mutation.** The `pending` list, the iteration counter, and the decision of whether to dispatch another body task all live in Python stack-frame variables during the `while`. Nothing persists between invocations except via `run.context.loop_state`.
- **Skips the successor processor.** Body tasks go through `_enqueue_or_dispatch` directly; the successor-readiness guard in `SuccessorProcessor.process` is bypassed for body tasks because they're expected to be re-runnable.
- **`run.context.completed_nodes.discard(body_task.node_id)`** — this is the mechanism for making a body node re-runnable across iterations. It has a subtle consequence: if a body node has been marked completed for other reasons (e.g., successor processor), it gets un-marked every iteration. This is fine for non-nested cases.
- **Body task `loop_context` and `iteration_index`** are set when the LOOP handler creates them (`loop_handler.py:127-143, 206-217, 223-237`), but successor tasks dispatched from the body entry downstream **do not inherit** the loop context. Only the body entry itself carries it.

### 2.4 Queued execution path — dead code

`orchestration_engine.py:1744-1859`: `async def orchestrate_loop_iteration(self, run_id, task_id, loop_node_id, iteration_index)`.

This method is designed to be called when a `LoopIterationAction` is dequeued by `OrchestrationWorker`. The worker at `app/layer4_frameworks/orchestration_queue/worker.py:79` correctly routes `LoopIterationAction` to `engine.orchestrate_loop_iteration(...)`.

**But nothing enqueues a `LoopIterationAction`.** Grep confirms:

```
$ grep -rn "LoopIterationAction(" app/
app/layer2_application/interfaces/orchestration_queue_interface.py:68:class LoopIterationAction(OrchestrationAction):
```

Only the class definition itself. No constructor call anywhere in the codebase. The `_exec_builtin_queued` method for LOOP (`orchestration_engine.py:1521-1600`) enqueues body tasks as plain `ExecuteNodeAction`s:

```python
# orchestration_engine.py:1554-1580
if loop_has_body:
    for body_task in handler_result.tasks_to_dispatch:
        ...
        await self._queue.enqueue(
            ExecuteNodeAction(run_id=run.id, task_id=body_task.id)
        )
```

**Consequence:** in queued mode (production), the first body task (or first parallel batch) runs, completes via `_exec_builtin_queued` / `_exec_task_queued`, marks itself completed — and nothing notifies the LOOP handler to advance. The loop stalls after one iteration. This is a **pre-existing bug** and not caused by the migration.

This has been invisible to the test suite because **no test configures an orchestration queue**. Every loop test runs inline via `_run_loop_iterations` and works fine there.

The spike therefore inherits a specific migration opportunity: Phase 5 will replace `orchestrate_loop_iteration` with an event-driven path anyway, so we don't need to back-fix the dead code. We just need to ensure the event-driven replacement actually fires.

### 2.5 Condition-based loops

`loop_handler.py:176-189`:

```python
if loop_type == "condition":
    condition_expr = loop_state.get("condition", "")
    if condition_expr:
        try:
            cond_result = await self._resolver.resolve(condition_expr, run.context.variables)
            if not cond_result:
                result.loop_state_updates = {node.id: loop_state}
                return self._dispatch_exit(node, run, workflow, dag_info, task_factory, result)
        except VariableResolutionError:
            result.loop_state_updates = {node.id: loop_state}
            return self._dispatch_exit(node, run, workflow, dag_info, task_factory, result)
```

Condition evaluation happens **after** each body completion. If the condition resolves false → dispatch exit. If resolution errors → exit (defensive). There is no while-style "check condition before running body".

### 2.6 Body entry discovery

`loop_handler.py:274-293`:

```python
@staticmethod
def _get_body_entry(node, dag_info, workflow) -> WorkflowNode | None:
    outgoing_edges = dag_info.edge_map.get(node.id, [])
    # Priority 1: explicit body edge
    for edge in outgoing_edges:
        edge_cond = (edge.condition or "").lower()
        port_id = (edge.source_port_id or "").lower()
        if edge_cond == "body" or port_id in ("loop_body", "body"):
            return workflow.get_node(edge.target_node_id)
    # Priority 2: first edge that is NOT an exit edge
    for edge in outgoing_edges:
        edge_cond = (edge.condition or "").lower()
        port_id = (edge.source_port_id or "").lower()
        if edge_cond not in ("exit", "complete") and port_id not in ("loop_complete", "exit", "complete"):
            return workflow.get_node(edge.target_node_id)
    return None
```

There is exactly one body entry per LOOP. A body can be arbitrarily complex downstream of that entry — any subgraph until a node matched by the exit edge detection in `_dispatch_exit` (`loop_handler.py:244-272`).

### 2.7 Events emitted today

**`LoopIterationCompleted`** (`events.py:204-209`):

```python
@dataclass
class LoopIterationCompleted(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    iteration: int = 0
    total: int = 0
```

Emitted from `_handle_iteration_complete` (`loop_handler.py:163-174`) per iteration. There is no `LoopStarted` / `LoopCompleted` today — those transitions are implicit in the `task.dispatched` / `task.completed` events for the LOOP node itself.

### 2.8 What does NOT exist today

| Missing | Impact |
|---|---|
| Nested-loop support | Scalar `loop_context` + scalar `iteration_index` + `loop_var["depth"] = 0` stub. Any nested loop clobbers the outer loop's counter. |
| `break` / `continue` node types | Only exit condition for early termination is condition-based loop's top-of-iteration check. |
| Per-iteration merge quorum keys | `MergeNodeHandler` reads `completed_nodes`, which is discarded per iteration. Merge inside a loop body is not tested with nested parallel branches. |
| Queued-mode loop iteration | `LoopIterationAction` is unreachable dead code. |
| Loop output accumulation node | There is no first-class "collect outputs from each iteration into a list" mechanism. Users have to build one manually using DATA_EDIT or external state. |
| Inheritance of `loop_context` in successors | Only the body entry carries it; tasks further downstream in the body subgraph lose the loop scope. The `run.context.variables["loop"]` dict compensates but is mutable across iterations. |

The spike should honour existing behaviour (no regressions in the tested paths) but does **not** need to backfill features that were never implemented.

---

## 3. Proposed event-driven model

### 3.1 Design goals

1. **Make nested loops actually work** by introducing a `scope_stack` on every event, so each iteration of each loop has a unique coordinate.
2. **Make iteration a first-class event** so queued mode works naturally.
3. **Preserve current semantics** for non-nested loops (collection / count / condition / sequential / parallel modes, body discovery, exit edge detection).
4. **Deterministic replay** from the event log: given the same event stream, the Projector should reconstruct the same loop state.
5. **No behavioural regression** for the inline path until the event-driven path has production mileage.

### 3.2 The scope stack

Every event emitted inside a loop body carries a `scope_stack`:

```python
scope_stack: list[LoopScope]  # outermost first, innermost last

@dataclass(frozen=True)
class LoopScope:
    loop_node_id: str   # the LOOP node whose iteration this is
    invocation_id: str  # uuid5 derived from (run_id, loop_node_id, enclosing scope stack)
    iter: int           # 0-based iteration index within this loop
    total: int          # known total at iteration start (for collection/count)
```

**Key properties:**

- **Outermost first.** `scope_stack[0]` is the outermost enclosing loop; `scope_stack[-1]` is the immediate enclosing loop.
- **`invocation_id` stable across iterations of the same loop invocation.** Derived from `run_id + loop_node_id + enclosing scope_stack` so that a loop inside a loop gets a **different** invocation_id for each outer iteration. This is how a merge-point inside a nested loop body gets its own quorum buffer per outer iteration.
- **`iter` changes between iterations; `invocation_id` does not.** This lets consumers group all events from a single invocation, then disambiguate individual iterations.

Derivation:

```python
def derive_invocation_id(run_id: str, loop_node_id: str, enclosing: list[LoopScope]) -> str:
    key = run_id + "|" + loop_node_id + "|" + "|".join(
        f"{s.loop_node_id}#{s.invocation_id}#{s.iter}" for s in enclosing
    )
    return uuid.uuid5(_LOOP_NAMESPACE, key).hex
```

The `invocation_id` is deterministic, so replay reconstructs the same values.

### 3.3 New event types

Added to `app/layer1_domain/events.py` in Phase 5 (not this session):

```python
@dataclass
class LoopStarted(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict] = field(default_factory=list)  # outer scopes, not including self
    loop_type: str = ""      # collection | count | condition
    mode: str = ""           # sequential | parallel
    total: int = 0           # -1 for condition loops (unknown)
    parallelism: int = 1

@dataclass
class LoopIterationStarted(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict] = field(default_factory=list)  # includes self at the tail
    iter: int = 0
    item: Any = None         # loop variable snapshot for this iteration

@dataclass
class LoopIterationCompleted(DomainEvent):
    # Existing event, extended with scope + invocation_id + outcome
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict] = field(default_factory=list)
    iter: int = 0
    total: int = 0
    outcome: str = "ok"      # ok | failed | skipped

@dataclass
class LoopCompleted(DomainEvent):
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict] = field(default_factory=list)
    total_iterations: int = 0
    reason: str = "all_done" # all_done | condition_false | break | early_exit_error

@dataclass
class LoopExitRequested(DomainEvent):
    """Emitted by a break/exit node inside the body. The loop handler
    observes this and emits LoopCompleted with reason='break' at the
    next iteration boundary."""
    run_id: str = ""
    workflow_id: str = ""
    loop_node_id: str = ""
    invocation_id: str = ""
    scope_stack: list[dict] = field(default_factory=list)
```

**Why a separate `LoopExitRequested` instead of reusing `LoopCompleted`:** a break node can fire mid-iteration. The loop handler needs a checkpoint to process it (at the next iteration boundary or at the current iteration's completion). Emitting `LoopExitRequested` as a signal and `LoopCompleted` as the terminal transition keeps the state machine explicit.

### 3.4 Event flow — sequential collection loop

For a workflow `TRIGGER → LOOP → body → LOOP_EXIT → AFTER` with collection `[a, b, c]`:

```
1. task.dispatched  (LOOP, iter=0 initializing)
2. loop.started     (node_id=LOOP, invocation_id=I, scope_stack=[], total=3, mode=sequential)
3. loop.iteration.started (iter=0, item=a, scope_stack=[{LOOP, I, 0, 3}])
4. task.dispatched  (body, scope_stack=[{LOOP, I, 0, 3}])
5. task.completed   (body, scope_stack=[{LOOP, I, 0, 3}])
6. loop.iteration.completed (iter=0, outcome=ok, scope_stack=[{LOOP, I, 0, 3}])
7. loop.iteration.started (iter=1, item=b, scope_stack=[{LOOP, I, 1, 3}])
8. task.dispatched  (body, scope_stack=[{LOOP, I, 1, 3}])
9. task.completed   (body, scope_stack=[{LOOP, I, 1, 3}])
10. loop.iteration.completed (iter=1, ...)
11. loop.iteration.started (iter=2, item=c, scope_stack=[{LOOP, I, 2, 3}])
12. task.dispatched  (body, scope_stack=[{LOOP, I, 2, 3}])
13. task.completed   (body, scope_stack=[{LOOP, I, 2, 3}])
14. loop.iteration.completed (iter=2, ...)
15. loop.completed   (total_iterations=3, reason=all_done)
16. edge.traversed   (LOOP → AFTER, scope_stack=[])
17. task.dispatched  (AFTER, scope_stack=[])
```

**Invariants:**
- Every event inside the body (4, 5, 8, 9, 12, 13) carries `scope_stack` with the innermost loop scope at the tail.
- Iteration start event (3, 7, 11) and iteration completed event (6, 10, 14) bracket the body events.
- `loop.completed` (15) clears the scope — subsequent events (16, 17) do not carry the loop scope.

### 3.5 Event flow — parallel mode

Same DAG, `mode="parallel"`, `parallelism=2`, items `[a, b, c]`:

```
1. loop.started  (mode=parallel, total=3, parallelism=2)
2. loop.iteration.started (iter=0, item=a)
3. loop.iteration.started (iter=1, item=b)
4. task.dispatched (body, iter=0)
5. task.dispatched (body, iter=1)
6. task.completed (body, iter=0)
7. loop.iteration.completed (iter=0)
8. loop.iteration.started (iter=2, item=c)         # dispatched as a replaces in the batch
9. task.dispatched (body, iter=2)
10. task.completed (body, iter=1)
11. loop.iteration.completed (iter=1)
12. task.completed (body, iter=2)
13. loop.iteration.completed (iter=2)
14. loop.completed (total_iterations=3)
```

The parallel scheduler inside the LOOP handler (or its event-driven replacement) ensures at most `parallelism` concurrent iterations.

### 3.6 Event flow — nested loop

Outer LOOP_O over `[x, y]`, body contains inner LOOP_I over `[p, q]`:

```
1. loop.started (LOOP_O, invocation_id=OUTER, scope_stack=[])
2. loop.iteration.started (LOOP_O, iter=0, item=x, scope_stack=[{LOOP_O, OUTER, 0, 2}])
3. task.dispatched (LOOP_I, scope_stack=[{LOOP_O, OUTER, 0, 2}])
4. loop.started (LOOP_I, invocation_id=INNER_0, scope_stack=[{LOOP_O, OUTER, 0, 2}])
5. loop.iteration.started (LOOP_I, iter=0, item=p, scope_stack=[{LOOP_O, OUTER, 0, 2}, {LOOP_I, INNER_0, 0, 2}])
6. ...body events with full scope_stack...
7. loop.iteration.completed (LOOP_I, iter=0)
8. loop.iteration.started (LOOP_I, iter=1, item=q, ...)
9. ...body events...
10. loop.iteration.completed (LOOP_I, iter=1)
11. loop.completed (LOOP_I, invocation_id=INNER_0)
12. loop.iteration.completed (LOOP_O, iter=0)
13. loop.iteration.started (LOOP_O, iter=1, item=y, scope_stack=[{LOOP_O, OUTER, 1, 2}])
14. loop.started (LOOP_I, invocation_id=INNER_1 — different from INNER_0!, scope_stack=[{LOOP_O, OUTER, 1, 2}])
15. loop.iteration.started (LOOP_I, iter=0, item=p, scope_stack=[{LOOP_O, OUTER, 1, 2}, {LOOP_I, INNER_1, 0, 2}])
...
```

**Critical observation:** `LOOP_I` gets a different `invocation_id` for each outer iteration (`INNER_0`, `INNER_1`). This is what makes a merge node inside the inner body get a fresh quorum buffer for each outer iteration.

### 3.7 State reconstruction from event log

The Projector (Phase 4) rebuilds `run.context.loop_state` from the log by applying a pure function:

```python
def apply_loop_event(state: RunState, event: DomainEvent) -> RunState:
    if isinstance(event, LoopStarted):
        loop_key = (event.node_id, event.invocation_id)
        state.loops[loop_key] = LoopStateEntry(
            loop_type=event.loop_type,
            mode=event.mode,
            total=event.total,
            parallelism=event.parallelism,
            completed_iterations=[],
            failed_iterations=[],
            status="running",
            scope_stack=event.scope_stack,
        )
    elif isinstance(event, LoopIterationStarted):
        loop_key = (event.node_id, event.invocation_id)
        # Iteration start doesn't mutate state; it's a marker for the UI.
        pass
    elif isinstance(event, LoopIterationCompleted):
        loop_key = (event.node_id, event.invocation_id)
        entry = state.loops[loop_key]
        if event.outcome == "ok":
            entry.completed_iterations.append(event.iter)
        else:
            entry.failed_iterations.append((event.iter, event.outcome))
    elif isinstance(event, LoopCompleted):
        loop_key = (event.node_id, event.invocation_id)
        state.loops[loop_key].status = "completed"
        state.loops[loop_key].final_reason = event.reason
```

**Properties:**

- **Deterministic.** Same log → same state. No wall-clock, no random IDs (invocation_id is `uuid5`-derived).
- **Idempotent.** Replaying the same event twice is a no-op because `completed_iterations` only tracks which `iter`s are done; the append is to a set-like structure with dedup.
- **Parallel-safe.** Loop entries are keyed by `(node_id, invocation_id)`, so nested invocations never collide. Outer iteration 0 and outer iteration 1 produce disjoint keys for the same inner loop node.

### 3.8 Quorum buffers for merge-inside-loop

A merge node `M` inside a loop body waits for all predecessor branches to complete for the **current iteration**. The buffer key is:

```python
buffer_key = (run_id, M.node_id, innermost_invocation_id, innermost_iter)
```

Derivation:
- `run_id` from the event.
- `M.node_id` from the target node.
- `innermost_invocation_id` = `scope_stack[-1].invocation_id` (empty stack → `None` or a sentinel).
- `innermost_iter` = `scope_stack[-1].iter`.

This guarantees:
- Merge in iteration 0 doesn't collide with merge in iteration 1 — different buffer.
- Merge in outer-iter 0 / inner-iter 0 doesn't collide with merge in outer-iter 1 / inner-iter 0 — different `invocation_id`.
- Merge outside any loop works unchanged — the scope_stack is empty and the buffer key reduces to `(run_id, M.node_id)`.

The Quorum buffer design from [`event-driven-orchestration.md`](./event-driven-orchestration.md) §25.2 already reserves the `invocation_id` field for this. Phase 5 wires it up.

### 3.9 Break / early exit

A new node type `LOOP_EXIT` (or a `Break` handler) emits `LoopExitRequested` targeting the innermost enclosing loop. The loop handler's event listener watches for `LoopExitRequested` with `loop_node_id == self.node_id AND invocation_id == current_invocation`; on match, at the next iteration boundary (or immediately if we're willing to abandon in-flight iterations) emits `LoopCompleted(reason="break")` and skips remaining body dispatches.

**Why not abandon in-flight iterations immediately:** parallel mode may have N concurrent iterations. Breaking one shouldn't leave the others in a half-dispatched state. Wait for the in-flight set to settle, then emit completion.

### 3.10 CONDITION and SWITCH inside a loop body

Today, `ConditionNodeHandler` and `SwitchNodeHandler` populate `NodeHandlerResult.taken_edge_ports` and the engine's unified edge emitter reads that dict. In the event-driven model, each edge event carries `scope_stack`, so there's no double-emission risk — the projector keys edge-traversal events by `(edge_id, scope_stack)` naturally.

The `_HANDLER_EMITS_EDGE_EVENTS` carve-out at `orchestration_engine.py:50` becomes obsolete. The scope_stack provides enough context to disambiguate which iteration emitted the edge.

---

## 4. Migration sketch (Phase 5)

Not implementation — a sketch of the work items so reviewers can assess scope.

### 4.1 Data model changes

- `Task.loop_context: dict | None` → `Task.scope_stack: list[LoopScope]`. Add a migration: old single-dict format is accepted and promoted to a single-element stack.
- `Task.iteration_index: int | None` stays (it's the innermost `iter`, redundant with `scope_stack[-1].iter` but keeps the frontend happy).
- `RunContext.loop_state` → keyed by `(node_id, invocation_id)` instead of just `node_id`. Projector handles the migration.
- `run.context.variables["loop"]` stays as a convenience for expression resolution — the innermost loop's variables. Expressions inside a nested loop see the innermost scope; outer scopes are reached via `{{$loop[0].item}}` / `{{$loop_<node_id>.item}}` (new syntax).

### 4.2 Handler changes

- `LoopNodeHandler` — rewrite. The state machine moves from `loop_state` dict mutation to event emission: `LoopStarted` at initialization, `LoopIterationStarted` + body dispatch per iteration, `LoopIterationCompleted` per iteration, `LoopCompleted` at end. Still Python code; still synchronous from the handler's perspective; but the "state" is expressed as events rather than live dict mutation.
- `MergeNodeHandler` — rewrite to use the scope-aware quorum buffer (Phase 5 introduces a `QuorumBufferStore` service).
- `ConditionNodeHandler` / `SwitchNodeHandler` — unchanged in logic, but edge events now carry `scope_stack`.

### 4.3 Engine changes

- Remove `_run_loop_iterations` (the inline while-loop path).
- Remove `orchestrate_loop_iteration` (the dead queued path — or reshape it to handle `LoopIterationStarted` events).
- Replace `LoopIterationAction` with subscriptions: the Dispatcher watches for `task.completed` events whose `scope_stack[-1].loop_node_id == L` and triggers the next iteration of `L`.
- `dispatch_or_handle_task` and friends remain for non-LOOP BUILTINs; LOOP gets its own event-driven driver.

### 4.4 Feature flag strategy

Introduce `settings.EVENT_DRIVEN_LOOP: bool = False` (default off). When off, keep the current inline `_run_loop_iterations` path for regression safety. When on, the new event-driven path takes over.

Land in this order:
1. Add the new event types (no behaviour change; events are emitted but ignored).
2. Add the scope_stack plumbing on Task + RunContext (backward-compatible: old code ignores the extra fields).
3. Add the event-driven LOOP driver behind the feature flag.
4. Run the existing LOOP test suite with the flag on; fix gaps.
5. Enable the flag in one environment; monitor.
6. Enable globally; remove the old inline path.

Each step is independently revertable.

### 4.5 Ancillary fix: wire the queued path

Even before Phase 5, the dead `orchestrate_loop_iteration` should be removed or wired up. Recommendation: **remove the method and the `LoopIterationAction` class entirely** as part of Phase 5, since the event-driven model replaces both. Alternatively, land a targeted fix now that enqueues a `LoopIterationAction` from `_exec_builtin_queued` after each body task completion — but that's throwaway work.

Priority: low (queued loops don't work today and nothing new depends on them).

---

## 5. Risks and open questions

### 5.1 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Feature flag drift — one environment on event-driven LOOP, another on inline, divergent behaviour | Medium | Land event-driven behind the flag; run the full LOOP test suite in both modes in CI before flipping. |
| Nested-loop workflows in production rely on the single-depth clobbering behaviour | Low | Scan production workflows for nested LOOP nodes. If any exist, audit manually before enabling the flag. |
| MERGE semantics change for merge-inside-loop cases that were previously broken | Medium | Treat the first event-driven cut as "new merge semantics, may break merge-inside-parallel-loop workflows that relied on the implicit scope collapse". Document explicitly. |
| Scope_stack serialization format for persistence (hana/memory repos) | Low | List of dicts serialize cleanly via pickle/json; use the memory repo first, then HANA. |
| LoopExitRequested race with parallel iterations | Medium | Wait for in-flight iterations to settle before emitting LoopCompleted(reason=break). Document this explicitly as "break is cooperative, not pre-emptive". |
| `run.context.variables["loop"]` convenience key becomes stale in nested loops | Low | Define the semantics: "loop" always refers to the innermost enclosing loop. Outer loops are accessed via `{{$loop_by_id.<loop_node_id>.item}}` or similar. |

### 5.2 Open questions (to resolve before Phase 5 code lands)

1. **Loop output accumulation.** Should the event-driven LOOP also introduce a first-class "output accumulator" (collect each iteration's body output into a list and emit it as the loop node's own output)? Currently users build this manually. Could be a Phase 5 enhancement or deferred.
2. **Break from nested inner loop.** Does `LoopExitRequested` target the immediate enclosing loop or can it specify an outer loop? Propose: always immediate unless the emitting node explicitly declares `target_loop_depth`.
3. **Condition-loop determinism under replay.** If the condition expression calls `$now()` or reads from an external service, replay may diverge. Propose: require condition expressions to be pure functions of `run.context.variables`, document as a contract. Non-compliance is undefined behaviour.
4. **`parallel` mode parallelism across nested loops.** If an outer loop has `parallelism=2` and each outer iteration spawns an inner loop with `parallelism=3`, we have up to 6 concurrent body tasks. Is this bounded anywhere? Today: no. Proposed: add a global `max_concurrent_iterations_per_run` config.

### 5.3 Edge cases not covered by this spike

- **LOOP nodes with no body** — currently `_initialize_loop` dispatches the exit edge immediately (`loop_handler.py:106-107`). The event-driven path should do the same: emit `LoopStarted` + `LoopCompleted` back-to-back with `total=0`.
- **LOOP nodes where body contains a WORKFLOW node (child workflow)** — cross-run boundaries. The child workflow's events are in its own event log; the parent's LOOP waits on the child's `run.completed` event. This works today and the spike does not change it; the scope_stack on the parent's body events carries the outer loop's scope, and the child run's events are isolated.
- **LOOP with HITL body** — body contains an INPUT or HUMAN_ACTION node. Current behaviour: the iteration pauses at the HITL node, user resumes, iteration continues. Event-driven path: same — `loop.iteration.started` fires, body dispatches, INPUT emits `input.requested`, waits; on resume, `input.submitted` fires and the downstream of the body runs; eventually `loop.iteration.completed` fires and the next iteration starts. No special handling needed.

---

## 6. Recommendation

**Go.** Proceed with Phase 5 event-driven LOOP promotion, gated by a `settings.EVENT_DRIVEN_LOOP` feature flag that defaults off. The event-driven model cleanly supports everything the current LOOP does, and fixes three latent bugs along the way (nested loops, queued-mode stall, merge-inside-loop quorum).

**Do not block on.** Loop output accumulation, global concurrency limits, or break-from-outer-loop — these are enhancements that can ship in a follow-up phase. The Phase 5 scope is strictly "preserve current LOOP semantics + nested support + queued-mode working + scope_stack on events + scope-aware merge quorum".

**Scope for Phase 5 LOOP work:**

1. Add event types (`LoopStarted`, `LoopIterationStarted`, `LoopCompleted`, `LoopExitRequested`). Extend existing `LoopIterationCompleted` with `invocation_id` + `scope_stack` + `outcome`.
2. Extend `Task` with `scope_stack: list[LoopScope]`. Promote old `loop_context` to a single-element stack on read.
3. Rewrite `LoopNodeHandler` as an event emitter. Behind `EVENT_DRIVEN_LOOP` feature flag.
4. Rewrite `MergeNodeHandler` to use scope-aware quorum buffers.
5. Update `ConditionNodeHandler` / `SwitchNodeHandler` edge emission to carry `scope_stack` (no behaviour change, just pass-through).
6. Delete `orchestrate_loop_iteration` + `LoopIterationAction` (dead code).
7. Delete `_run_loop_iterations` once the feature flag is enabled in production and the old path is no longer needed.

**Estimated effort:** 2-3 engineer-weeks for the happy path + full test coverage. Another 1 engineer-week for production hardening and monitoring.

**Gate to Phase 4:** Phase 4 (Dispatcher + Projector split) does not need LOOP to be event-driven. Phase 4 can land with the inline `_run_loop_iterations` still in place. Phase 5 is where LOOP actually flips. This spike confirms no blocker exists in the LOOP semantics that would force us to roll back Phase 4.

---

## 7. Appendix — LOOP test coverage map (pre-Phase-5 baseline)

From `tests/unit/test_node_handlers.py::TestLoopHandler`:

| Test | Covers |
|---|---|
| `test_initialize_loop_sequential` | Happy-path sequential init: 3 items, dispatches iteration 0 body task with `iteration_index=0`. |
| `test_initialize_loop_parallel` | Parallel init with `parallelism=2`: dispatches 2 body tasks on first pass. |
| `test_empty_items_dispatches_exit` | Empty items → `_dispatch_exit` fires immediately. |
| `test_iteration_complete_sequential` | Sequential iteration complete → next iteration dispatched. |
| `test_iteration_complete_parallel_more_items_remaining` | Parallel iteration complete → next item in queue dispatched. |
| `test_iteration_complete_all_done_sequential` | All iterations complete → exit task dispatched. |
| `test_iteration_complete_all_done_parallel` | Same for parallel mode. |
| `test_condition_loop_exits_when_condition_false` | Condition-based loop exits when expression resolves false. |
| `test_count_loop_iterates_n_times` | Count loop iterates exactly `count` times. |

**What's NOT tested:**
- Nested loops (no test file constructs a LOOP body containing another LOOP).
- Merge-inside-loop-body with multiple parallel branches.
- LOOP body containing an INPUT node (HITL inside a loop).
- Queued-mode loops (no test configures an orchestration queue).
- Break / early-exit from a loop (no such node type exists).
- LOOP with a body that contains a WORKFLOW sub-workflow invocation.

Phase 5's test plan should add coverage for at least the first four items before flipping the feature flag.

---

## 8. Summary of evidence

| Finding | File / line |
|---|---|
| Scalar `loop_context` on Task | `app/layer1_domain/entities/task.py:20` |
| Scalar `iteration_index` on Task | `app/layer1_domain/entities/task.py:21` |
| `loop_state` flat dict on RunContext | `app/layer1_domain/entities/workflow_run.py` (RunContext) |
| `loop_var["depth"] = 0` stub | `app/layer1_domain/services/node_handlers/loop_handler.py:113` |
| `_initialize_loop` with `count`/`condition`/`collection` loop types | `app/layer1_domain/services/node_handlers/loop_handler.py:41-144` |
| `_handle_iteration_complete` dispatches next iteration or exit | `app/layer1_domain/services/node_handlers/loop_handler.py:146-242` |
| Condition loop exit on resolve failure | `app/layer1_domain/services/node_handlers/loop_handler.py:186-189` |
| Inline `_run_loop_iterations` while-loop | `app/layer1_domain/services/orchestration_engine.py:615-735` |
| `run.context.completed_nodes.discard(body_task.node_id)` per iteration | `app/layer1_domain/services/orchestration_engine.py:645` |
| Dead code: `orchestrate_loop_iteration` | `app/layer1_domain/services/orchestration_engine.py:1744-1859` |
| Dead code: nothing enqueues `LoopIterationAction` | grep verified — only class definition at `app/layer2_application/interfaces/orchestration_queue_interface.py:68` |
| `_exec_builtin_queued` enqueues body tasks as `ExecuteNodeAction`, not `LoopIterationAction` | `app/layer1_domain/services/orchestration_engine.py:1554-1580` |
| `LoopIterationCompleted` event currently has no scope_stack | `app/layer1_domain/events.py:204-209` |
| LOOP handler test coverage | `tests/unit/test_node_handlers.py:622+` |

---

## 9. Gate decision

**This spike approves Phase 4 (Dispatcher + Projector split) to proceed unblocked.**

Phase 4 does not require LOOP changes. Phase 5 is where LOOP becomes event-driven, and the spike has confirmed that (a) the current LOOP semantics are thin enough to re-express as events, (b) the nested-loop and queued-mode issues are latent bugs that Phase 5 can fix as a side effect, and (c) a feature-flag strategy keeps the migration safe.

**Review checklist for the next reviewer:**

- [ ] Agreement on the `scope_stack` shape (list of `LoopScope` dataclasses, outermost first).
- [ ] Agreement on `invocation_id` derivation (uuid5 over the enclosing scopes).
- [ ] Agreement on the feature-flag strategy (`EVENT_DRIVEN_LOOP` default off, flip per environment).
- [ ] Agreement that the dead `orchestrate_loop_iteration` / `LoopIterationAction` code can be removed as part of Phase 5 (not back-fixed earlier).
- [ ] Agreement on which open questions (§5.2) must be resolved before code lands vs. which can be deferred to a post-Phase-5 follow-up.

Sign-off required before Phase 4 starts: **spike reviewed, no blocker identified**.
