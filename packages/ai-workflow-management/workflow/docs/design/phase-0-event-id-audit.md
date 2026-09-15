# Phase 0 Event ID Determinism Audit

> Produced by Phase 0 of the engine-split work
> ([engine-split-dispatcher-executor-projector.md](./engine-split-dispatcher-executor-projector.md)).
>
> Catalogues every event construction site in `workflow-control-plane-service`
> that today generates a fresh `uuid4()` via `DomainEvent.event_id`'s default
> factory instead of calling `deterministic_event_id(causation_id, correlation_key)`.
>
> **Why it matters:** replay-from-checkpoint and at-least-once redelivery dedupe
> both require stable event ids. A redelivered event must carry the same id as
> the original; a replayed fold must produce the same projected state byte-for-byte.
> Non-deterministic ids block both.

## 1. Scope

Audit ran against `workflow-control-plane-service/app/` on 2026-04-21
(Phase 0 implementation commit). Counted any call site matching the pattern
`<EventDataclass>(...)` that did not pass `event_id=...` explicitly.

- **Total event construction sites:** 95 (including dataclass definitions)
- **Of which: dataclass definitions in `events.py`:** 36 (not call sites — false positives)
- **Actual emission call sites:** 59
- **Using `deterministic_event_id`:** 15
- **Using uuid4 default:** 44

## 2. Already deterministic

Sites that already call `deterministic_event_id` and should stay that way:

| File | Count | Notes |
|---|---|---|
| `services/dispatcher/fanout.py` | 2 | `FanoutAction` + `SkipAction` event ids |
| `services/loop_driver/event_driven_loop_driver.py` | 4 | All loop event ids |
| `services/node_handlers/loop_exit_handler.py` | 1 | `LoopCompleted` on break |
| `services/node_handlers/loop_continue_handler.py` | 1 | `LoopContinueRequested` |
| `services/successor_processor.py` | 2 | `EdgeTraversed` + `TaskSkipped` via `FanoutAction.event_id` |
| `services/orchestration_engine.py` | 5 | `NodeOutputProduced`, `NodeInput`, `WireSkipped`, `WireDeadLetter` (Phase 0), plus CONDITION/SWITCH `EdgeTraversed` (Phase 0) |

## 3. Remaining non-deterministic sites

Grouped by category. Each entry is a candidate for a later phase's fix.

### 3.1 Run lifecycle — `orchestration_engine.py`

| Line | Event | Suggested causation / correlation |
|---|---|---|
| 557, 720, 1146, 1176, 1308 | `RunFailed` | `(run.id, f"run_failed:{failed_task_id}")` |
| 593 | `RunCompleted` | `(run.id, "run_completed")` |
| 706 | `RunStarted` | `(run.id, "run_started")` |
| 736 | `RunCancelled` | `(run.id, f"run_cancelled:{cancelled_by}")` |
| 748 | `RunPaused` | `(run.id, "run_paused")` |
| 760 | `RunResumed` | `(run.id, "run_resumed")` |

### 3.2 Task lifecycle — `orchestration_engine.py`

| Line | Event | Suggested causation / correlation |
|---|---|---|
| 774, 1476, 1541, 1565, 1592, 1619 | `TaskDispatched` | `(task.id, "task_dispatched")` |
| 846 | `TaskRetrying` | `(task.id, f"retry:{attempt}")` |
| 877 | `TaskSkipped` | `(task.id, f"skipped:{node_id}")` |
| 893 | `TaskRerun` | `(task.id, "rerun")` |

Also `services/successor_processor.py:135` — `TaskFailed` on handler exception.

### 3.3 Edge traversal — `orchestration_engine.py`

| Line | Event | Context |
|---|---|---|
| 415 | `EdgeTraversed` | `_emit_edge_events` helper (legacy — candidate for removal once confirmed dead) |
| 1643 | `EdgeTraversed` | Loop body → entry edge emission |
| 1984, 2008 | `EdgeTraversed` | Loop-iteration body re-dispatch / exit edge emission |

All three should use `deterministic_event_id(task.id, f"edge:{edge.id}:{target_id}")`
matching the `compute_fanout` convention.

### 3.4 HITL (pending-node) events — handlers

| File | Line | Event |
|---|---|---|
| `node_handlers/input_handler.py` | 128 | `InputRequested` |
| `node_handlers/input_handler.py` | 306 | `InputSubmitted` |
| `node_handlers/human_action_handler.py` | 74 | `HumanActionRequested` |
| `node_handlers/human_action_handler.py` | 98 | `HumanActionCompleted` |
| `node_handlers/data_edit_handler.py` | 177 | `DataEditRequested` |
| `node_handlers/data_edit_handler.py` | 201 | `DataEditCompleted` |
| `node_handlers/file_upload_handler.py` | 80 | `InputRequested` |
| `node_handlers/file_upload_handler.py` | 102 | `InputSubmitted` |

HITL events are the subtlest case: resume events arrive from a user action,
not from replaying a causation chain. The dedupe key should include the
resume-submission-id so double-submits from a flaky client are deduped
without conflating them with initial-request events. Phase 3 (Executor
extraction + outbox) is the natural home for this work.

### 3.5 Miscellaneous — `orchestration_engine.py`

| Line | Event | Correlation key suggestion |
|---|---|---|
| 808 | `ContextUpdated` | `(task.id, f"context:{node_id}")` |
| 920 | `HumanActionCompleted` | Resume submission id |
| 953 | `ArtifactAdded` | `(task.id, f"artifact:{file_id}")` |
| 977 | `NodeInputEdited` | `(task.id, f"edit:{file_id}")` |
| 1229, 1452 | `LoopIterationCompleted` | `(run.id, f"loop:{loop_node_id}:iter:{iter}")` |
| 2249 | `ChildRunStarted` | `(parent_task.id, f"child_run:{child_run_id}")` |
| 2434 | `ChildRunCompleted` | `(parent_task.id, f"child_run_completed:{child_run_id}")` |

### 3.6 Handler-emitted — `node_handlers/condition_handler.py`

| Line | Event |
|---|---|
| 69 | `ConditionEvaluated` |

### 3.7 Loop driver — `loop_driver/event_driven_loop_driver.py`

| Line | Event |
|---|---|
| 402 | `LoopCompleted` (the other four loop events at this module are already deterministic) |

## 4. Phased fix plan

| Phase | Scope | Why |
|---|---|---|
| **Phase 0 (done)** | New events only (`NodeInput`, `WireSkipped`, `WireDeadLetter`) + CONDITION/SWITCH `EdgeTraversed` + existing deterministic helper audit | Establishes the pattern without touching stable code |
| **Phase 1 (Projector shadow)** | Run lifecycle + Task lifecycle (§3.1, §3.2) | Projector replay needs these authoritative first |
| **Phase 2 (Dispatcher extraction)** | Edge traversal (§3.3) + Loop events (§3.7) + Context/Artifact misc (§3.5 except HITL) | Dispatcher owns routing; redelivery dedupe depends on these |
| **Phase 3 (Executor extraction + outbox)** | HITL events (§3.4) + HITL completion on engine side (§3.5 `HumanActionCompleted`) + Child-run (§3.5) | Outbox pattern carries the resume-submission-id needed for dedupe |

## 5. Lint/test guard

A regression test in `tests/unit/test_event_id_determinism.py` verifies that
events introduced in Phase 0 all carry deterministic ids. Extend the test's
allow-list as subsequent phases retrofit their call sites.

The test does **not** fail on the currently-non-deterministic sites catalogued
above — that would either freeze the gap or force a premature rewrite. Instead
it asserts positive coverage (every Phase 0 event IS deterministic) and
documents the remaining scope.

## 6. Anti-pattern reminders

- Never pass `event_id=str(uuid.uuid4())` — that defeats replay but passes the
  `event_id=` grep check. A dedicated test detects this in Phase 0+ events.
- Correlation keys MUST be strings. Tuples, enums, or objects silently break
  `uuid5` hashing.
- Causation ids MUST reference a real upstream event or task id. `run.id`
  alone is acceptable only for the top-level `RunStarted` event.
