# Phase 3a — Dispatcher Authoritative

> Design doc for Phase 3a of the engine split
> ([engine-split-dispatcher-executor-projector.md](./engine-split-dispatcher-executor-projector.md) §4 Phase 2 exit).
>
> Phase 2b shipped the `DispatcherConsumer` in shadow mode and ran 7-day
> staging + 14-day prod soaks with drift=0. Phase 3a cashes in the soak
> by making the Dispatcher the sole producer of `EdgeTraversed` /
> `NodeInput` / `WireSkipped` / `WireDeadLetter` for the main fan-out
> path — the engine's inline `_route_to_successors` emissions become
> no-ops and can be deleted in a follow-up PR.

## 1. Scope

**3a ships:** Dispatcher owns emission for the `_route_to_successors`
fan-out path (branching + port-routed). Engine stops emitting the four
event types listed above from that method.

**3a does NOT ship (follow-ups):**
- **3a.2** — delete the now-dead inline emission code paths after
  one-week bake. One PR, low-risk.
- **Loop + child-workflow emission sites** at
  `orchestration_engine.py:456`, `:1912`, `:2271`, `:2296` stay
  inline. These edges are not driven by `NodeOutputProduced` emissions
  (loop body fan-out has no corresponding state-update event), so
  extending the Dispatcher to cover them requires either new events or
  a second consumer path. Scoped to **3a.3** — out of this plan.
- Phase 3b (delete Phase 1b-deferred inline writes, `apply_state_updates`
  aggregate mutation, `ParityComparatorViewStore`,
  `LiveRunContextViewStore`, tri-state `PROJECTOR_READ_MODE`) is a
  separate sequence dependent on 3a baking.
- Phase 3c (`run_lock_registry` deletion post bus-partitioning
  verification) is independent and unscheduled.

## 2. Changes

### 2.1 `DISPATCHER_MODE` tri-state

`app/layer4_frameworks/config/app_config.py` — add `"dispatcher"` to
the accepted values. Updated semantics:

| Value | Engine inline edge emission | Dispatcher publishes | Comparator recording |
|---|---|---|---|
| `inline` (default) | yes | no (not constructed) | no |
| `shadow` | yes | no — records to comparator only | yes |
| `dispatcher` | **no** (guarded) | **yes** (to the event bus) | optional (keep for one release) |

### 2.2 `DispatcherConsumer`

`app/layer4_frameworks/dispatcher/dispatcher_consumer.py`:

- Add `publisher: Any | None = None` and `mode: str = "shadow"` to
  `__init__`.
- In `on_node_output_produced` after computing emissions:
  - If `mode == "dispatcher"` and `publisher is not None`: for each
    `DispatcherEmission`, call `publisher.publish(emission.topic, emission.event)`.
  - If `mode == "shadow"`: record to the comparator (current
    behaviour).
  - Both sides guard against exceptions and never raise — dispatcher
    errors must never poison the publish chain of the triggering
    `NodeOutputProduced`.

The `publisher` reference is the outermost publisher in the chain
(the same one the engine uses), so dispatcher-published events flow
through `EventLogAppendingPublisher` → `RealtimeBroadcastingPublisher` →
broker. The `DispatcherTeeingPublisher` sits above it and no-ops on
non-`NodeOutputProduced` events, so there is no cycle when the
dispatcher publishes its own `EdgeTraversed`.

### 2.3 Engine guards

`app/layer1_domain/services/orchestration_engine.py` — accept a
`dispatcher_mode: str = "inline"` constructor parameter, store as
`self._dispatcher_mode`. Guard the four emission sites in
`_route_to_successors`:

| Line | Emission | 3a behaviour |
|---|---|---|
| 1994 | `EdgeTraversed` (branching path) | skip when `dispatcher_mode == "dispatcher"` |
| 2116 | `EdgeTraversed` (port-routed path) | skip |
| 2053 | `WireSkipped` (port-filter drops) | skip |
| 2079 | `WireDeadLetter` (no-fanout signal) | skip |

Task creation (`task_factory.create_task(...)`) and enqueue
(`_enqueue_or_dispatch(...)`) remain unconditional. Phase 3a does not
attempt to move task creation into the Dispatcher — that is Phase 3
master-doc "extract Executor" scope.

### 2.4 Bootstrap wiring

`workflow-control-plane-service/bootstrap.py`:

- Validate `DISPATCHER_MODE` against `{inline, shadow, dispatcher}`;
  fall back to `inline` with a warning on unknown values.
- For `dispatcher`: construct `DispatcherConsumer(publisher=event_publisher,
  mode="dispatcher", ...)`. Comparator retained for one release so
  operators can flip back to `shadow` at the first sign of trouble
  without redeploying.
- Pass `dispatcher_mode=settings.DISPATCHER_MODE` to
  `OrchestrationEngine.__init__`.

### 2.5 Test updates

- Unit: add `tests/unit/test_dispatcher_consumer.py::test_authoritative_mode_publishes`
  — fake publisher receives one `(topic, event)` per emission; recorder
  receives nothing.
- Integration: `tests/integration/test_dispatcher_authoritative.py` —
  `DISPATCHER_MODE=dispatcher` TRIGGER→COMPUTE, assert that:
  1. The event log contains exactly one `EdgeTraversed` per edge
     (not two — confirms engine did not double-emit).
  2. Each event's `event_id` matches
     `deterministic_event_id(causation_id, f"edge:{edge.id}:{target}")`
     — dispatcher-produced IDs.
  3. The run completes end-to-end without regressions.
- Regression: full suite under each of the three modes in CI.

## 3. Risks

### 3.1 Silent emission gap

Any `NodeOutputProduced` emission gap that shadow didn't surface (for
example, a rare node-type / config combination not exercised during
the 14-day soak) will now cause real missing events in prod rather
than drift in the comparator. Mitigation:
- Keep the comparator wired for one release. Alerts on
  `dispatcher_drift_total{kind}` stay active with the same paging
  rules.
- Canary the flip: roll `dispatcher` mode to one region / tenant
  first, bake 24h, expand.

### 3.2 Event log duplicates on rollback

If we flip `dispatcher → shadow` mid-run, the engine resumes emitting
`EdgeTraversed` for any in-flight fan-out; the dispatcher may have
already published for the same edge. Both events carry the same
deterministic `event_id` so the `EventLogAppendingPublisher` dedupes
them. Downstream consumers (Socket.IO, analytics) tolerate duplicates
by the same key. No data-loss risk; worst case is one redundant UI
event per in-flight fan-out at the moment of flip.

### 3.3 Ordering

Today the engine emits `EdgeTraversed` synchronously and then enqueues
`ExecuteNodeAction`. In dispatcher mode the dispatcher publishes
`EdgeTraversed` from `on_node_output_produced`, which runs
synchronously via the `DispatcherTeeingPublisher` tee, so the ordering
is preserved: `NodeOutputProduced` → dispatcher emits `EdgeTraversed`
→ engine continues → engine creates task + enqueues
`ExecuteNodeAction`. Downstream `TaskDispatched` still arrives after
`EdgeTraversed` for each successor.

### 3.4 Consumer wiring

The tee sits around the `event_publisher`, but
`event_publisher` is a local variable in `bootstrap.build_app_container`
— the engine holds a reference to the teed publisher, so engine
publishes flow through the tee (fine). The `DispatcherConsumer`'s
`publisher` reference in authoritative mode must also be the teed
publisher — otherwise dispatcher-published events skip the event log.
Implementation: construct the tee, construct the consumer with the
tee as its publisher, hand the tee to the engine. Circular in the
type graph but not at runtime because consumer publishes lazily, not
in `__init__`.

## 4. Rollout plan

1. **PR 1 — code + flag.** [Shipped] Lands `dispatcher` as a valid
   mode, consumer publishing, engine guards, tests. Default stayed
   `inline`. Merged after CI passed under all three modes.
2. **Staging flip.** [Done] Set `DISPATCHER_MODE=dispatcher` in
   staging for 48h. Observed `dispatcher_drift_total{kind}` and
   event-log spot-check clean.
3. **Prod canary.** [Done] One tenant / 24h bake.
4. **Prod full.** [Done] 1-week bake.
5. **PR 2 — 3a.2 cleanup.** [Shipped] Deleted the four inline
   emission blocks in ``_route_to_successors`` and the
   ``dispatcher_mode`` engine param / guards. Flipped default
   ``DISPATCHER_MODE=dispatcher``. Legacy ``inline`` and unknown
   values log a warning and upgrade to ``dispatcher``. Comparator
   wiring retained only under ``shadow`` as a one-release diagnostic
   safety net — the recorder captures dispatcher emissions for ops
   inspection rather than comparing two streams (inline is gone).
6. **PR 3 — 3a.3 loop coverage.** [Shipped] Added two new events
   `LoopBodyDispatched` and `LoopExitDispatched` (topics
   `loop.body.dispatched` / `loop.exit.dispatched`). Engine publishes
   them when it enqueues body / exit tasks; `DispatcherConsumer`
   consumes them via the teed publisher and emits `EdgeTraversed` for
   each `LOOP → body` / `LOOP → exit` edge with deterministic
   `event_id = deterministic_event_id(causation_id, "edge:<edge_id>:<target>")`.
   Engine retains task creation + enqueue (Phase 3 master-doc scope).
   Dead `_emit_edge_events` helper deleted (defined but never called).
   Dispatcher is now the sole emitter of `EdgeTraversed` for every
   fan-out path in the engine.

## 5. Rollback

**Pre-PR 2:** Flip `DISPATCHER_MODE=shadow` or `inline`. Engine
resumes inline emission. No data migration needed. `event_id`
determinism guarantees event-log dedupe absorbs any in-flight
duplicates.

**Post-PR 2:** Env-var rollback is no longer a path — inline engine
emission is deleted. Rolling back requires reverting PR 2 so the
guards and emission blocks come back. The revert is mechanical
(single-file, localised changes in `orchestration_engine.py` +
`bootstrap.py`) but coordinate the PR 2 merge with the 1-week
post-canary bake to minimise the window where this is needed.

After PR 2 (inline code deleted), rollback requires reverting PR 2.
The revert is mechanical — the code paths are easy to restore from
git — but coordinate the PR 2 merge with the 1-week post-canary bake
to minimize the window.

## 6. Exit criteria

- `DISPATCHER_MODE=dispatcher` green across full CI matrix under each
  mode.
- Prod `dispatcher_drift_total{kind}` stays zero for 7 days after full
  rollout.
- Event-log spot-check: `EdgeTraversed` emission count matches edge
  traversal count 1:1 (no duplicates from engine).
- PR 2 (inline code deletion) merged; orchestration_engine.py diff
  shows `_route_to_successors` no longer emits EdgeTraversed /
  WireSkipped / WireDeadLetter.

## 7. Critical files

- `workflow-control-plane-service/app/layer4_frameworks/config/app_config.py`
  — tri-state validation
- `workflow-control-plane-service/app/layer4_frameworks/dispatcher/dispatcher_consumer.py`
  — authoritative publish path
- `workflow-control-plane-service/app/layer1_domain/services/orchestration_engine.py`
  — `_route_to_successors` emission guards; `dispatcher_mode`
  constructor parameter
- `workflow-control-plane-service/bootstrap.py` — wire publisher into
  consumer; pass mode to engine
- `workflow-control-plane-service/tests/unit/test_dispatcher_consumer.py`
  — authoritative-mode unit test
- `workflow-control-plane-service/tests/integration/test_dispatcher_authoritative.py`
  — new end-to-end test

## 8. Open questions

1. **Comparator retention.** Keep `ParityComparatorViewStore`
   comparator active in `dispatcher` mode for continued observability,
   or delete after Phase 2b? Recommendation: retain for 1 release;
   delete in Phase 3b.
2. **Canary unit.** Tenant vs. region vs. rollout-percent flag. No
   existing primitive in the service. Recommendation: one tenant for
   this flip (simplest); build a proper percent-rollout primitive as
   separate work.
3. **Loop/child-workflow sites (3a.3).** Do we emit synthetic
   `NodeOutputProduced` for loop body fan-out (awkward — the loop
   node doesn't "produce output" for each iteration) or introduce a
   `LoopFanout` / `ChildWorkflowFanout` event the dispatcher
   consumes? Defer decision to 3a.3.

## 9. Related documents

| Doc | Relevance |
|---|---|
| [engine-split-dispatcher-executor-projector.md](./engine-split-dispatcher-executor-projector.md) | Master plan; §4 Phase 2 exit criterion is what 3a closes |
| [phase-2-dispatcher-extraction.md](./phase-2-dispatcher-extraction.md) | Phase 2a/2b delivery record; §4 "What Phase 2 does not do" lists the 3a deferred items |
| [phase-1b-authoritative-cutover.md](./phase-1b-authoritative-cutover.md) | Established the shadow-soak-then-authoritative rollout pattern 3a reuses |
