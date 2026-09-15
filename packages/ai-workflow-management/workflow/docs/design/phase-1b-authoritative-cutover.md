# Phase 1b — Authoritative Projector Cutover

> Progress record for Phase 1b of the engine split
> ([engine-split-dispatcher-executor-projector.md](./engine-split-dispatcher-executor-projector.md)).
> Phase 1a landed the shadow comparator + four emission gaps; after
> successful staging (7d) + prod (14d) soak with drift=0 the default
> read mode was flipped to `projector`.
>
> This document records: (a) what's shipped; (b) what remains of
> Option B from the original plan; (c) why the remaining work is
> deferred to Phase 2 prep rather than finished here.

## 1. Shipped

- **Default read mode flipped** from `live` to `projector` at
  `app/layer4_frameworks/config/app_config.py`. All three
  Phase 4a-migrated features (`get_run_status`, `get_run_context`,
  `get_node_output`) now serve from the event-sourced
  `CachedProjectorRunContextViewStore` out of the box.
- **Bootstrap tests updated** to verify both the new default and the
  legacy-compat `live` mode for local dev with ephemeral event logs.
- **Shadow comparator retained** — stays in code for one more phase
  so operators can flip back to `shadow` if any regression surfaces.
- **No new failures** — full suite runs 1656 passed / 55 failed / 107
  skipped; the 55 failures are pre-existing baseline unrelated to the
  engine split.

## 2. Deferred — and why

The original Phase 1b plan also called for:

1. Deleting inline state writes in seven use cases (`cancel_run`,
   `complete_task`, `fail_task`, `edit_node_data`, `resume_run`,
   `resume_pending_task`, `sweep_timed_out_child_runs`).
2. Deleting `apply_state_updates`'s aggregate mutation in the engine.
3. Deleting `ParityComparatorViewStore`, `projector_drift_controller`,
   `LiveRunContextViewStore`, and the `shadow`/`live` branches in
   `PROJECTOR_READ_MODE`.

These are **deferred to Phase 2 prep** for the following reason:

### 2.1 The blocker: engine still reads the aggregate

`OrchestrationEngine` reads run state directly from the in-memory
`WorkflowRun` aggregate passed into most orchestration methods:

- `run.context.completed_nodes` — consulted every time successor
  fanout runs.
- `run.context.variables` — read by `VariableResolver` to resolve
  node-input expressions.
- `run.status` — consulted before dispatching to avoid re-running a
  cancelled / completed run.
- `task.status` — consulted similarly.

These reads happen intraprocess against the aggregate that the engine
itself mutates during orchestration. If we delete the use-case writes
but keep the engine's direct reads, the engine sees stale state for
state transitions that came from outside its own loop (user cancels a
run, HITL resume, scheduled sweep).

Concretely, the scenario that breaks:

```
1. Cancel use case emits RunCancelled event (projector now has CANCELLED)
2. Cancel use case drops run.status = CANCELLED + run_repo.save
3. Engine's in-flight orchestration pass reloads run via run_repo.get_by_id
4. Live aggregate still says RUNNING
5. Engine dispatches the next task and the run keeps running
```

The fix requires routing the engine's reads through
`IRunContextViewStore` (the projector-backed view). That's a much
bigger change than Phase 1b was scoped for — it touches
`orchestration_engine.py`, every node handler's expression resolution
path, `successor_processor.py`, `loop_driver/event_driven_loop_driver.py`,
and `variable_resolver.py` at minimum.

### 2.2 Correct phasing

Per the design doc §4 Phase 2 ("Extract the Dispatcher"), the
Dispatcher extraction pulls fan-out decisions out of the engine
entirely and serves them from event-consuming projections. That's the
natural home for the engine-reads-go-through-projector work because
the Dispatcher itself has to read state through the bus anyway.

Doing that migration inside Phase 1b would duplicate effort with
Phase 2, and the intermediate state (engine reading from ViewStore but
still inline-executing) is harder to test in isolation than either
pure live or pure projector.

## 3. Recommended next action

Proceed to **Phase 2 — Extract the Dispatcher**. When the Dispatcher
ships:

1. Engine reads migrate to the ViewStore as a prerequisite.
2. Inline use-case writes become truly dead code — delete them
   alongside the dispatcher extraction.
3. Live store, comparator, and shadow/live branches in
   `PROJECTOR_READ_MODE` can be deleted in the same PR (or a cleanup
   PR immediately after).

The retained shadow comparator is the safety net: if anything in
Phase 2 goes wrong, operators can flip back to `shadow` and watch the
drift log until the issue is localized.

## 4. Exit criteria for Phase 1b (re-scoped)

Phase 1b as shipped meets these exit criteria:

| Target | Status |
|---|---|
| Reads authoritative via projector by default | Done |
| Shadow comparator available for emergency fallback | Retained |
| Shadow harness test asserts drift=0 on happy path | Passing |
| Full suite under `projector` mode shows zero new regressions | Confirmed (1656 pass / 55 pre-existing fail) |
| "Deleting `apply_state_updates` is a no-op" | **Deferred** to Phase 2 |
| `run_repo.save(run)` calls zero outside projector | **Deferred** to Phase 2 |

The deferred items were unachievable without the Phase 2 Dispatcher
extraction that replaces engine-internal aggregate reads with
view-store reads.

## 5. Related Documents

| Doc | Relevance |
|---|---|
| [engine-split-dispatcher-executor-projector.md](./engine-split-dispatcher-executor-projector.md) | Plan origin; §4 Phase 2 defines the next milestone |
| [phase-0-event-id-audit.md](./phase-0-event-id-audit.md) | Remaining non-deterministic emission sites to fix alongside Phase 2 |
| `tests/integration/test_shadow_parity_harness.py` | Drift=0 regression guard |
