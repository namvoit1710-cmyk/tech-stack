# Phase 3 — Verification

> Honest audit of the current code against the goal: **"actual
> event-driven design — events control workflow runs end-to-end."**
> Pairs with [`phase-3-executor-extraction.md`](./phase-3-executor-extraction.md)
> and the engine-split master doc
> [`engine-split-dispatcher-executor-projector.md`](./engine-split-dispatcher-executor-projector.md).

## 1. Verdict

The system is **event-shaped at every routing boundary AND every
side-effect boundary**, with handler invocation behind a flag. It is
**not yet event-driven end-to-end** because three control-flow
sites are still inline regardless of flag combinations:

1. **Task entity creation** for downstream nodes (engine inline, after
   handler returns).
2. **Run start** (`orchestrate_start` walks entry nodes synchronously).
3. **Child workflow start** (`_start_child_workflow` synchronously
   creates the child run + walks its entry nodes).

Closing those three gaps requires a dedicated PR (see §3 below). They
were deliberately deferred from Sub 3.5 / Gate 3 because each is a
multi-site refactor that touches state coordination across the
control-plane boundary — independent of executor-extraction proper.

## 2. Lifecycle audit

For each lifecycle stage, the table below classifies the code path as
**Authoritative** (event-driven, the events are the truth),
**Flag-gated** (event-driven only when a flag is on), or **Inline**
(direct method call drives the change; events fire as a side effect).

### A. Run lifecycle

| Stage | Status | Notes |
|---|---|---|
| `RunStarted` emission | Authoritative | Projector folds it; HTTP `GET /runs/{id}` reads from view-store |
| Run start (entry-node walk) | **Inline** | `_orchestrate_start_inner` calls `handle_entry_nodes` which loops over `dag_info.entry_nodes` and dispatches inline |
| Run completion (`RunCompleted` / `RunFailed`) | Authoritative | Projector folds; `check_run_completion` emits the event |
| Run pause/resume | Authoritative (resume side-effects flag-gated via Sub 3.4) | `RunPaused` / `RunResumed` events; HITL stage records + queue enqueues routed through outbox under `OUTBOX_HITL_RESUME=on` |
| Run cancel | Authoritative | `RunCancelled` event; projector folds; M.2.3 CAS-409 covers concurrent cancel |
| Test-run reset | Authoritative for projection | Bumps `run.generation`; G-guard rejects stale callbacks |

### B. Node lifecycle

| Stage | Status | Notes |
|---|---|---|
| Task creation for entry nodes | **Inline** | `handle_entry_nodes` creates `Task` entities directly |
| Task creation for downstream nodes | **Inline** | `_exec_uniform` post-handler-result code creates Tasks; under `EXECUTOR_MODE=executor` this is **bypassed** (engine returns after emitting NodeInput) → executor receives NodeInput with no Task entity |
| `TaskDispatched` emission | Authoritative for projection | Engine emits before handler invocation; projector folds |
| Handler invocation | Flag-gated | `EXECUTOR_MODE=in_process` (default): inline. `shadow`: inline + parallel ExecutorConsumer for parity. `executor` (Gate 3): engine returns, ExecutorConsumer invokes |
| Handler-result events (`NodeOutputProduced`, `InputRequested`, `LoopIterationCompleted`, etc.) | Authoritative | Whoever invokes (engine or executor) publishes via the same publisher chain; projector + dispatcher consume |
| `TaskCompleted` / `TaskFailed` | Authoritative | Projector folds; downstream consumers observe |

### C. Edge routing & fan-out

| Stage | Status | Notes |
|---|---|---|
| `EdgeTraversed` emission | Authoritative | `DispatcherConsumer` is the sole emitter (Phase 3a) |
| `NodeInput` emission | Authoritative | Dispatcher emits per-action; engine retains an emit-only path under executor mode (Gate 3) |
| `WireSkipped` / `WireDeadLetter` | Authoritative | DispatcherConsumer emits |
| Skip propagation | Authoritative for read | `compute_fanout` + projector; engine inline writes were deleted in Phase 3b |

### D. State (variables, completed_nodes, skipped_nodes)

| Stage | Status | Notes |
|---|---|---|
| `run.context.variables` | Authoritative | `CachedProjectorRunContextViewStore` folds `NodeOutputProduced` |
| `completed_nodes` / `skipped_nodes` | Authoritative | Same projector path |
| Pending tasks | Authoritative | `TaskDispatched` / `TaskCompleted` events fold |
| Outbox `pending_outbox` | Authoritative | Sub 3.2 projector reducer branch |

### E. Side effects

| Side effect | Status | Notes |
|---|---|---|
| File upload (DATA_EDIT) | Flag-gated | `OUTBOX_DATA_EDIT=on` → outbox-routed; off → inline. Idempotency-key dedup at file service (Sub 3.6) |
| File delete (DATA_EDIT GC) | Flag-gated | Same outbox path |
| Stage record (HITL resume) | Flag-gated | `OUTBOX_HITL_RESUME=on` → outbox; off → inline |
| Queue enqueue (`ResumeRunAction` / `ResumeTaskAction`) | Flag-gated | Same flag |
| Worker dispatch (TASK nodes) | Authoritative | TaskDispatched event triggers worker-executor pickup; this is the original event boundary that predates Phase 3 |

### F. Loop iteration

| Stage | Status | Notes |
|---|---|---|
| Queued mode (`EventDrivenLoopDriver`) | Authoritative | `LoopIterationAction` enqueued per iteration; iteration completion folds via events |
| Legacy `_run_loop_iterations` | **Inline** | Imperative `while pending` loop walks iterations; only used under `EVENT_DRIVEN_LOOP=false` |
| Loop iteration handler invocation | Flag-gated | Same `EXECUTOR_MODE` rules as other handlers |

### G. Child workflow

| Stage | Status | Notes |
|---|---|---|
| `ChildRunStarted` emission | Authoritative | Event fires; projector folds |
| Child run creation | **Inline** | `_start_child_workflow` instantiates `WorkflowRun`, saves via repo |
| Child entry-node walk | **Inline** | Same `handle_entry_nodes` path as parent run |
| Child completion → parent resume | Authoritative for read | Parent observes child's `RunCompleted`; projector reflects |

### H. PATCH `/data`

| Stage | Status | Notes |
|---|---|---|
| HTTP serialisation | Event-shaped | `EditNodeDataAction` enqueued on `orchestration_queue`; queue worker serializes per-run |
| Use-case execution | Inline (intentional) | Queue worker invokes `EditNodeDataUseCase.execute` synchronously; explicitly out of Phase 3 scope (doc §2) |
| Side effects | Flag-gated | Outbox per Sub 3.3 |
| Response routing | Authoritative cross-instance | M.3 per-instance response topic |

## 3. Gaps to true end-to-end event-driven

To make the system event-driven without `EXECUTOR_MODE=executor`
becoming a partial-mode (i.e., to make the goal **the default**),
three gaps must close. None of them is delivered by Phase 3.

### Gap 1 — Task creation must consume `NodeInput`

**Symptom under `EXECUTOR_MODE=executor` today.**
1. Engine emits `NodeInput(task_id=T1)` for entry node N1.
2. Executor invokes handler for N1, emits `NodeOutputProduced` +
   `TaskCompleted(task_id=T1)`.
3. DispatcherConsumer fans out → emits `NodeInput(task_id="")` for
   downstream node N2 (the dispatcher does not know about T2 because
   T2 doesn't exist yet — task creation today is engine inline code
   that ran before NodeInput emission).
4. Executor receives `NodeInput(task_id="")` → invokes handler with
   `task=None`.
5. Handler runs. But there is no Task entity for N2 in the repo,
   meaning HTTP `/tasks/{id}` returns 404, the projector's task view
   is missing T2, and `cancel_run` cannot target T2.

**Fix.** A new `TaskCreatorConsumer` subscribes to `NodeInput`, creates
the `Task` entity (or finds the existing one for entry nodes), and
re-emits `NodeInput` with `task_id` populated. Or — equivalently —
the DispatcherConsumer creates the Task as part of fan-out and emits
`NodeInput` with the new id. The latter is simpler because the
dispatcher already runs on the control plane and has DB access.

**Estimated cost.** 200 LoC + integration tests for entry / fan-out /
loop body / child entry tasks. Risk: medium — task ids must be
deterministic across replays so the event log dedupes redeliveries.

### Gap 2 — Run start must be event-driven

**Symptom.** `orchestrate_start` is the only entry path that creates
entry-node tasks. It runs inline from the HTTP `POST /runs` handler.
There is no `RunStarted` consumer that walks entry nodes — the event
fires after the walk, not as its trigger.

**Fix.** A `RunStartConsumer` subscribes to `RunStarted` and:
1. Loads the workflow.
2. Validates the DAG.
3. Creates entry-node tasks (or emits `NodeInput` for them — depends
   on Gap 1's resolution).

**Estimated cost.** 150 LoC + tests for: normal start, DAG-validation
failure, child run start (consumer should also handle
`ChildRunStarted`).

### Gap 3 — Child workflow start must be event-driven

**Symptom.** `_start_child_workflow` is invoked synchronously from
the `WORKFLOW` handler's post-result code. Under `EXECUTOR_MODE=executor`
the engine returns early after emitting NodeInput, so child workflow
start never runs.

**Fix.** Have the executor emit `ChildRunRequested` when a handler
returns `requires_child_run=True`, and let the `RunStartConsumer`
(Gap 2) handle it.

**Estimated cost.** 80 LoC + tests for parent → child → parent flow.

### Gap 4 — Legacy `_run_loop_iterations` retirement

`EVENT_DRIVEN_LOOP=true` (default) routes through the queued driver
which is event-driven. The legacy imperative path is preserved for
tests pinned to the old behaviour. Once those tests migrate, delete
the legacy path. **Estimated cost.** 100 LoC of test migration + the
legacy method body.

## 4. What Phase 3 actually delivered

Within its scope (executor extraction + outbox), Phase 3 delivers:

- **All six handler-invocation sites mode-gated.** Default
  `in_process` is byte-identical to pre-Phase-3 behaviour. `executor`
  short-circuits to NodeInput emission.
- **ExecutorConsumer publishes authoritatively under `executor` mode.**
  `NodeOutputProduced` + handler events + `TaskCompleted` /
  `TaskFailed`. Replay-safe deterministic event ids.
- **Outbox layer for side effects.** DATA_EDIT and HITL resume
  routes idempotent across replay when the flag is on; same idempotency
  invariant verified by the chaos test (Sub 3.6).
- **Standalone deployment artefacts.** Dockerfile + CF manifest + k8s
  Deployment + HPA + ops runbook.
- **Parity comparator.** Shadow-mode infrastructure for measuring
  drift before flipping authoritative.

This is necessary infrastructure for the goal but does not make
**default-mode runs** event-driven by itself. Default mode is still
inline; `executor` mode is the event-driven path and currently has
the four gaps above.

## 5. Recommended next phase

| Sub | Deliverable | LoC | Risk |
|---|---|---|---|
| 4.1 | `TaskCreatorConsumer` — Gap 1 fix | ~200 | Medium |
| 4.2 | `RunStartConsumer` — Gap 2 + 3 fix | ~250 | Medium |
| 4.3 | Retire `_run_loop_iterations` — Gap 4 | ~100 | Low (test migration) |
| 4.4 | Default `EXECUTOR_MODE=executor` after staging soak | 0 | Low (config flip) |

Estimated total: ~550 LoC + extensive integration tests. Smaller than
Phase 3 (~2000 LoC) but with higher correctness stakes because the
test surface narrows once the legacy paths are deleted. A 4-week
soak window between Sub 4.1-4.3 merge and the Sub 4.4 default flip
is appropriate.

## 6. How to verify in code

Run these greps to confirm the audit's accuracy:

```sh
# 1. Engine handler invocation sites — should all be guarded by
#    _is_executor_authoritative().
grep -nB2 -A2 'handler\.handle\|invoke_handler' \
  workflow-control-plane-service/app/layer1_domain/services/orchestration_engine.py

# 2. Inline task creation — should all be in engine code paths NOT
#    reached under executor mode.
grep -n '_task_repo\.save' \
  workflow-control-plane-service/app/layer1_domain/services/orchestration_engine.py

# 3. Outbox flags — both default false (Gate 1 staging knob).
grep -n 'OUTBOX_DATA_EDIT\|OUTBOX_HITL_RESUME' \
  workflow-control-plane-service/app/layer4_frameworks/config/app_config.py

# 4. ExecutorConsumer publishes — _publish_authoritative is the
#    single emit path under executor mode.
grep -nA5 '_publish_authoritative' \
  workflow-control-plane-service/app/layer4_frameworks/executor/executor_consumer.py
```

Last verified 2026-05-08 against the post-Gate-3 codebase.
