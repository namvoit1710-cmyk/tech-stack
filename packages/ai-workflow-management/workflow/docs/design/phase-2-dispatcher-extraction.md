# Phase 2 — Dispatcher Extraction

> Design doc for Phase 2 of the engine split
> ([engine-split-dispatcher-executor-projector.md](./engine-split-dispatcher-executor-projector.md) §4 Phase 2).
>
> Phase 1b flipped reads to the projector. The engine still routes
> successors inline and still reads run state directly off the
> in-memory `WorkflowRun` aggregate. Phase 2 breaks both: reads go
> through `IRunContextViewStore`, and a new `DispatcherConsumer` owns
> successor fanout and edge emission.

## 1. Why this phase exists

Two Phase-1b commitments land here:

1. The [Phase 1b cutover doc](./phase-1b-authoritative-cutover.md) §2.1
   names the blocker: *"engine still reads the aggregate"*. Seven use
   cases (`cancel_run`, `complete_task`, `fail_task`, `edit_node_data`,
   `resume_run`, `resume_pending_task`, `sweep_timed_out_child_runs`)
   still write `run.context.variables[...]` and `.status` inline
   because the engine reads the same fields directly and sees stale
   state otherwise. Phase 2a migrates those reads through the view
   store, unblocking the deletion in Phase 3b.

2. The master split doc §4 Phase 2 requires lifting `compute_fanout`
   out of the engine. Today's `_route_to_successors`
   (`orchestration_engine.py:1827-1920`) still calls it directly, and
   the branching path at 1857-1896 bypasses `compute_fanout` entirely.
   Phase 2b introduces a `DispatcherConsumer` that runs in shadow next
   to the inline path; Phase 3a flips the authoritative bit and
   deletes the inline routing.

The master doc's Phase 2 exit criterion — *"the engine has no direct
reference to the DAG adjacency structure"* — is split across 2a
(adjacency reads) and 3a (adjacency writes / edge emission).

## 2. Phase 2a — Reads through `IRunContextViewStore`

**Invariant kept:** the engine still mutates `run.context` on the
aggregate. Only *reads* migrate. Write deletion waits for Phase 3b.

### 2.1 Why reads, not both

Migrating reads and writes together is one huge PR with no
observability checkpoint in the middle. Reads-only is a
behaviour-preserving swap: the projector already sees every write
through the event bus (Phase 1a), so reading from the view store
returns the same values the aggregate holds — the shadow harness at
`tests/integration/test_shadow_parity_harness.py` is the regression
guard that proves it.

Splitting also matches the rollback story: if 2a introduces a read
hazard, reverting the per-file PR puts us back on the aggregate path
without touching the events that Phase 1 established.

### 2.2 Engine reads to migrate

Numbers below are line ranges on the working tree at plan time; treat
them as starting points, not invariants.

#### `orchestration_engine.py`

| Site | Purpose |
|---|---|
| 218 | Cascade-skip accumulator reads `run.context.skipped_nodes`. |
| 446, 1185, 1626 | `completed_nodes.add(...)` — writes, **leave in place** (3b). |
| 475-476 | `_try_complete_run` reads `completed` + `skipped` sets. |
| 1194-1195 | Pass-through to `compute_fanout` (arg construction, read). |
| 1513-1524 | Predecessor-readiness guard — `p_id in completed/skipped`; also `completed_nodes`/`skipped_nodes` passed to helper. |
| 1791-1792 | Loop-body dispatch filter — skip already-terminal body tasks. |
| 1865-1866 | Branching route: `target_id in completed/skipped`. |
| 1907-1908 | Port-routed route: `frozenset(completed/skipped)` passed to `compute_fanout`. |
| 2134-2135 | Loop exit — `exit_task.node_id not in completed/skipped`. |
| 2201, 2245, 2272 | `variables.items()` / references in emission helpers. |
| 2516-2573 | Child-workflow completion reads + writes parent-run context. |
| 2635-2651 | Child-workflow failure / skip cascade — reads parent context. |

Writes at 446, 1185, 1625-1626, 1973, 2127-2158, 2229-2232, 2532-2534,
2555-2563, 2635-2643 stay for now; they are in the Phase 3b bucket.

#### `successor_processor.py`

| Site | Purpose |
|---|---|
| 158 | `loop_state.get(node.id)` — read. |
| 200, 202 | `target_node.id in completed/skipped`. |
| 133, 180 | Writes (`completed_nodes.add`, `skipped_nodes.add`) — **leave in place**. |

#### `event_driven_loop_driver.py`

| Site | Purpose |
|---|---|
| 260 | `loop_state.get(loop_node_id)` — read. |
| 482 | `variables.get(body_entry.id, {})` — read body output. |
| 577, 759 | Pass `variables` to `VariableResolver.resolve(...)`. |
| 883, 888 | `merge_quorum_buffers` read (883) + delete (888; keep as-is for now, write). |
| 689 (`loop_state.update`), 709, 723 | Writes — **leave**. |

#### `variable_resolver.py`

Itself takes a `context: dict[str, Any]` parameter — no direct
`run.context.*` reads. Phase 2a does not touch this file. Its *call
sites* (compute, condition, switch, task, input, data_edit handlers)
pass `run.context.variables` explicitly; those sites belong to the
Phase 3 handler-in-Executor migration, not 2a. The one exception is
loop driver lines 577 / 759 where the engine itself passes
`run.context.variables` — those are migrated in 2a (read through view
store, pass to the resolver).

### 2.3 Threading

`IRunContextViewStore` is already exported from the container at
`bootstrap.py` under key `"run_context_view_store"`. The smart-inject
pattern means adding a `run_context_view_store: IRunContextViewStore`
parameter to `OrchestrationEngine.__init__`, `SuccessorProcessor.__init__`,
and `EventDrivenLoopDriver.__init__` wires it automatically — no new
bootstrap code required.

### 2.4 Same-call-frame fast path

The engine mutates the aggregate *synchronously* (e.g. line 1625-1626
adds a skipped node, line 1626 adds the completed node) and then
reads the mutated field several lines later. Between the mutation and
the read, the projector has not processed the corresponding event —
the view store may lag by one hop.

For 2a, handle this by **re-reading the view after the engine's own
writes**. Concretely, at sites where a block mutates then reads,
acquire the view *after* the mutation so both reads observe the same
post-write state:

```python
# Before (direct aggregate read)
run.context.skipped_nodes.add(skipped_id)
run.context.completed_nodes.add(node.id)
if target_id in run.context.completed_nodes: ...

# After (view re-read post write)
run.context.skipped_nodes.add(skipped_id)
run.context.completed_nodes.add(node.id)
view = self._view_store.get(run.id)
if target_id in view.completed_nodes: ...
```

The `CachedProjectorRunContextViewStore` caches by run_id until the
next event lands, so the re-read is cheap. This is only an interim —
Phase 3b removes the aggregate writes and the view store becomes the
only source.

### 2.5 Hazard — HITL resume projector lag

`resume_pending_task` and `resume_run` still write `run.context`
directly and emit events afterwards. After 2a, the engine's
post-resume fan-out reads through the view store; the projector may
not yet have applied the resume event. Accept this as 2a-interim
hazard — the shadow harness's drift=0 run already includes resume
paths, so the existing test gate covers it. Phase 3b deletes the
inline writes and closes the window.

### 2.6 Exit criteria (2a)

- Zero direct `run.context.{completed_nodes,skipped_nodes,variables,status,output_data}` **reads** in `OrchestrationEngine`, `SuccessorProcessor`, `EventDrivenLoopDriver`.
- Full suite passes under `PROJECTOR_READ_MODE=projector` and `shadow`.
- `tests/integration/test_shadow_parity_harness.py` drift=0.
- No new regressions vs. the baseline 55-pre-existing-failure count.

## 3. Phase 2b — `DispatcherConsumer` in shadow

### 3.1 Consumer skeleton

Mirror `SocketIORealtimeConsumer`
(`app/layer4_frameworks/realtime/socketio_consumer.py`):

- `DispatcherConsumer(broker, view_store, publisher, workflow_repo)`.
- `start()` captures the asyncio loop and subscribes via
  `broker.subscribe_to_topic(topic, callback)` for each consumed topic.
- `run_forever()` drains an `asyncio.Queue` (single lane — dispatcher
  decisions are roughly uniform in cost, no priority classification
  needed).
- Wired in `bootstrap.py` next to the Socket.IO consumer, started from
  the FastAPI lifespan.

No new deployment target. The dispatcher is an in-process consumer
on the same broker. Separate service extraction is Phase 4.

### 3.2 Subscribed topics

| Topic | Handler action |
|---|---|
| `node.output.produced` | Call `compute_fanout` with `expected_port_override=event.output_port` (field added in 3.3). Emit resulting `EdgeTraversed` / `NodeInput` / `WireSkipped` / `WireDeadLetter`. |
| `task.completed`, `task.failed` | Tracked for telemetry; compute_fanout is keyed off `node.output.produced` (one per completed node), so these don't re-fan. |
| `condition.evaluated`, `switch.evaluated` | Read extended `taken_edge_ports` (3.4), emit `EdgeTraversed` for each taken edge. Do not call `compute_fanout` — branching bypasses port filtering. |
| `run.cancelled` | No action — cancel cascade is already event-authoritative in Phase 1a; cascade-skip already observed through the projector. |

### 3.3 `NodeOutputProduced.output_port` schema extension

`NodeOutputProduced` (`events.py:611+`) today carries `run_id`,
`workflow_id`, `node_id`, `state_updates`. For the Dispatcher to
reproduce `_route_to_successors`'s port-routed decision, it needs the
handler's `output_port`. Extend the dataclass:

```python
@dataclass
class NodeOutputProduced(DomainEvent):
    # existing fields ...
    output_port: str = ""  # empty = no port routing
```

Update every emission site in `orchestration_engine.py` to populate
from `handler_result.output_port`. Additive — existing consumers
ignore. The projector's reducer adds the field to `ProjectedRunState`
only if downstream consumers read it (not required today).

### 3.4 Branching handler event extension

CONDITION and SWITCH handlers populate `NodeHandlerResult.taken_edge_ports`
in memory (`condition_handler.py:102`, `switch_handler.py:129`). The
Dispatcher cannot re-derive this from `NodeOutputProduced` without
re-evaluating the predicate against a possibly-lagged view — a
correctness hazard.

**Decision as implemented:** pivoted from adding per-handler
`ConditionEvaluated.taken_edge_ports` / `SwitchEvaluated.taken_edge_ports`
fields (the latter event didn't exist anyway) to extending
`NodeOutputProduced` itself with a `taken_edge_ports: dict[str, str]`
field. Both CONDITION and SWITCH already emit `NodeOutputProduced`
via `apply_state_updates_as_files` — piggy-backing on it keeps the
Dispatcher on one consumed event type and removes the need to coin
a new `SwitchEvaluated` event.

```python
@dataclass
class NodeOutputProduced(DomainEvent):
    # existing fields + output_port ...
    taken_edge_ports: dict[str, str] = field(default_factory=dict)
```

Handlers populate via `handler_result.taken_edge_ports` which the
engine passes through `apply_state_updates_as_files(..., taken_edge_ports=...)`.
Dispatcher reads the field: non-empty → branching path, emit
`EdgeTraversed` with `deterministic_event_id(causation_id, f"edge:{edge.id}:{target_id}")`.
Identical formula to the current inline path, so event ids match
byte-for-byte in shadow.

### 3.5 Child-workflow completion as emission point

`orchestration_engine.py:2532-2565` writes parent-run context and
calls `SuccessorProcessor.process` synchronously when a child run
completes. This does not currently emit `NodeOutputProduced` for the
parent's WORKFLOW node — it just mutates aggregate state and fan-outs
inline.

For 2b: before mutating, emit `NodeOutputProduced` with
`node_id=parent_node.id`, `state_updates={parent_node.id: child_output,
parent_node.name: child_output}`, `output_port="success"`. The
Dispatcher receives the event and fires the same fan-out the inline
path does. In shadow mode, both produce events; comparator verifies
match.

Failure path at 2635-2651 emits `NodeOutputProduced` with
`output_port="failure"` for the same reason. (Inline path currently
calls `SuccessorProcessor.process` with the WORKFLOW node marked
failed; Dispatcher achieves the same via port routing.)

### 3.6 `DISPATCHER_MODE` flag + parity comparator

New setting in `app_config.py`: `DISPATCHER_MODE: str = "inline"`,
allowed values `inline` / `shadow` (reserved: `dispatcher` for Phase 3a).

- `inline`: default. `DispatcherConsumer` not instantiated. Engine
  path unchanged.
- `shadow`: `DispatcherConsumer` subscribed, emits its own event
  stream. Engine continues to emit inline. `DispatcherParityComparator`
  buffers both streams per run, compares `(edge_id, target_id, event_id)`
  triples and `NodeInput(node_id)` sets when a terminal event
  (`RunCompleted` / `RunFailed` / `RunCancelled`) lands.

The comparator reuses the `ProjectorDriftRecorder` pattern at
`app/layer4_frameworks/monitoring/projector_drift_metrics.py`:
Prometheus counter `dispatcher_drift_total{kind}`, ring buffer of
recent drifts, ops router at `GET /internal/dispatcher-drift`.

### 3.7 Redelivery invariance

At-least-once redelivery can hand `DispatcherConsumer` the same
`NodeOutputProduced` twice. Because `compute_fanout` is pure and its
event ids are deterministic, the resulting `EdgeTraversed` /
`NodeInput` events have identical ids. The event-log dedupe in
`EventLogAppendingPublisher` swallows the duplicates before they
reach downstream consumers. Unit-tested by replaying a synthetic
envelope through the consumer and asserting event-log idempotency.

### 3.8 Ordering interactions

- **Loop iteration hook**: `maybe_enqueue_loop_iteration`
  (`orchestration_engine.py:1825`) runs after handler events and
  before `_route_to_successors` on the inline path. In shadow mode,
  the iteration hook still runs inline; the Dispatcher sees
  `NodeOutputProduced` at roughly the same moment. Order-sensitive
  tests assert "iteration queued before body successors" — that
  invariant holds on both paths because the iteration is queued
  synchronously; the Dispatcher's emission is async but the resulting
  `ExecuteNodeAction` enters the queue after the iteration task.
- **PARALLEL fan-out**: `compute_fanout` already handles parallel
  siblings. Today they emit in one synchronous loop; the Dispatcher
  emits them in the same order (`compute_fanout` returns a tuple).
  No test relies on precise interleave timing between siblings.

### 3.9 Exit criteria (2b)

- `DispatcherConsumer` subscribed under `DISPATCHER_MODE=shadow`;
  full suite green.
- New integration test `tests/integration/test_dispatcher_shadow_parity.py`
  covering TRIGGER→CONDITION→PARALLEL→MERGE, cancel cascade, HITL
  resume, child-workflow. Both streams produce identical
  `(edge_id, target_id, event_id)` triples.
- Redelivery unit test asserts deterministic downstream ids.
- 7 days staging soak + 14 days prod soak at `DISPATCHER_MODE=shadow`
  with drift=0 before Phase 3 begins.

## 4. What Phase 2 does **not** do

These items pair with Phase 3 and are out of scope here:

- Flip `DISPATCHER_MODE=dispatcher` (3a).
- Delete `_route_to_successors` inline edge emission (3a).
- Delete inline writes in the 7 deferred use cases (3b).
- Delete `apply_state_updates` aggregate mutation (3b).
- Delete `ParityComparatorViewStore`, projector-drift router,
  `LiveRunContextViewStore`, tri-state `PROJECTOR_READ_MODE` (3b).
- Delete `run_lock_registry` after partition verification (3c).

## 5. Rollback

| Step | Rollback | Risk |
|---|---|---|
| 2a — each read-migration PR | Revert per-file; engine reads aggregate again. | Low. |
| 2b — `NodeOutputProduced.output_port` | Additive field; defaults to `""`; existing consumers ignore. Revert if needed. | None. |
| 2b — `ConditionEvaluated.taken_edge_ports` | Same. | None. |
| 2b — `DispatcherConsumer` | Flip `DISPATCHER_MODE=inline`; no authoritative write path. | Low. |
| 2b — child-workflow emission | Revert; parent fan-out falls back to inline `SuccessorProcessor.process`. | Low. |

## 6. Verification

| What | Where |
|---|---|
| Reads migrated (2a) | Full suite under `PROJECTOR_READ_MODE=projector`; shadow harness drift=0. |
| Dispatcher shadow parity (2b) | `tests/integration/test_dispatcher_shadow_parity.py` — new. |
| Branching extension (2b) | Extend `tests/unit/test_condition_handler.py`, `test_switch_handler.py`. |
| Redelivery invariance (2b) | New unit test in `tests/unit/test_dispatcher_consumer.py`. |
| Soak | `dispatcher_drift_total` counter zero over 7d staging + 14d prod. |

## 6A. Delivery status (2026-04-21)

Phase 2 landed in two halves over one session:

**2a — reads migration (shipped):**
- View-store helpers on `OrchestrationEngine` (`_view_completed`,
  `_view_skipped`, `_view_variables`) + analogous helper on
  `EventDrivenLoopDriver`, plus equivalent read-then-accumulate logic
  in `SuccessorProcessor._process_uniform`.
- All 14+ engine read sites migrated: cascade-skip, check-run
  completion, predecessor guard, body dispatch filter, branching +
  port-routed `_route_to_successors`, loop exit, child-workflow
  successor filter.
- Loop driver reads `variables` at three sites (body output, condition
  re-eval, items resolution) now go through the view-backed helper.
- `IRunContextViewStore` is threaded through the engine /
  successor / loop driver constructors; `bootstrap.py` wires it ahead
  of those services.

**2b — Dispatcher shadow (shipped partial):**
- `NodeOutputProduced` carries `output_port` and `taken_edge_ports`
  fields (additive, default empty).
- `DispatcherConsumer` + `DispatcherTeeingPublisher` +
  `DispatcherParityRecorder` live under
  `app/layer4_frameworks/dispatcher/`.
- `DISPATCHER_MODE={inline,shadow}` gates the consumer wiring in
  `bootstrap.py`. Default `inline` = no consumer constructed;
  `shadow` = consumer recorded via the publisher tee.
- 6 unit tests in `tests/unit/test_dispatcher_consumer.py` validate
  deterministic emissions, branching, redelivery invariance, and
  drift detection.
- Regression delta vs baseline: 1662 passed / 55 pre-existing failed
  / 107 skipped — zero new regressions, +6 new passes.

**2b — follow-up fixes shipped (same session):**
- **Child-workflow success path emission.** `handle_child_completed`
  at `orchestration_engine.py:2609` now routes the parent-variable
  write through `apply_state_updates_as_files(..., output_port="success")`
  so the projector rebuilds `parent.variables[child_node_id]` from
  the event log and the DispatcherConsumer fires on child-run
  completion.
- **Integration shadow parity.**
  `tests/integration/test_dispatcher_shadow_parity.py` — 3 tests: mode
  wiring, default-off, and a TRIGGER→COMPUTE happy path that asserts
  comparator drift=0 end-to-end. Inline side is fed from the
  `EventLogAppendingPublisher` event log (equivalent to a broker
  subscriber), dispatcher side is fed via the in-process tee.
- **Regression delta (post-fix):** 1665 passed (+9 new tests total) /
  55 pre-existing failed / 107 skipped — zero new regressions.

**2b — still deferred:**
- **Fail-skip child branch emission.** `fail_parent_tracking_task` at
  `orchestration_engine.py:2715-2723` mutates `completed_nodes` +
  `skipped_nodes` on the `on_failure=skip` branch without emitting
  `NodeOutputProduced` or `TaskCompleted`. Entangled with a
  pre-existing missing-TaskCompleted emission on that branch; tracked
  separately. Non-blocking for shadow rollout on happy-path /
  non-skip workflows.
- **Soak.** 7 days staging + 14 days prod at
  `DISPATCHER_MODE=shadow` before Phase 3 begins.

## 7. Related Documents

| Doc | Relevance |
|---|---|
| [engine-split-dispatcher-executor-projector.md](./engine-split-dispatcher-executor-projector.md) | Plan origin; §4 Phase 2 is this doc's source of truth |
| [phase-1b-authoritative-cutover.md](./phase-1b-authoritative-cutover.md) | §2.1 documents the aggregate-read blocker that 2a closes |
| [phase-0-event-id-audit.md](./phase-0-event-id-audit.md) | `deterministic_event_id` audit whose outputs make shadow parity byte-for-byte |
