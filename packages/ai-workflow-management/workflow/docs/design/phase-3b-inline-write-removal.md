# Phase 3b — Inline Write Removal

> Delete the last aggregate writes left by Phase 1b's partial rollout,
> and retire the shadow / live / comparator read-path machinery now
> that Phase 2a + 3a have made the projector the only authoritative
> store of run state.

## 1. Scope

Explicit list (matches the Phase 1b deferred bucket + Phase 3a design
doc §4):

| Target | Type | Location |
|---|---|---|
| Engine `apply_state_updates_as_files` aggregate write | 1 line | `orchestration_engine.py:2314` |
| `cancel_run` status + task status writes | 1 use case | `cancel_run_use_case.py:77, :110` |
| `complete_task` fallback writes | 1 use case | covered by Phase 1a routing via `apply_state_updates_as_files`; audit only |
| `fail_task` fallback writes | 1 use case | `fail_task_use_case.py:162-164` (else branch) |
| `edit_node_data` direct variable writes | 1 use case | `edit_node_data_use_case.py:320, :876` |
| `resume_run` status write | 1 use case | `resume_run_use_case.py:73` |
| `resume_pending_task` status + variable writes | 1 use case | `resume_pending_task_use_case.py:112, :348, :351` |
| `sweep_timed_out_child_runs` status writes | 1 use case | `sweep_timed_out_child_runs_use_case.py:99, :150` |
| `LiveRunContextViewStore` | delete file | `app/layer4_frameworks/run_context/live_view_store.py` |
| `ParityComparatorViewStore` | delete file | `app/layer4_frameworks/run_context/parity_comparator_view_store.py` |
| `ProjectorDriftRecorder` | delete file | `app/layer4_frameworks/monitoring/projector_drift_metrics.py` |
| Projector drift router | delete file | `app/layer3_adapters/controllers/restful/v1/projector_drift_controller.py` |
| `PROJECTOR_READ_MODE` tri-state | simplify | `app_config.py` + `bootstrap.py` |

**Out of scope for 3b** (deferred or unrelated):
- `start_run`, `trigger_node`, `pause_run`, `rerun_task`, `rerun_run`,
  `reset_test_run`, `test_node`, `test_full_workflow` inline writes.
  These were never in the "Phase 1b deferred" bucket — they run before
  the engine loop starts, or are test-only, and do not block
  projector authoritative state.
- `apply_state_updates` static method itself — one legit external
  caller remains (`test_node_use_case.py:175`). Leave in place; Phase
  3c or later can fold it in.

## 2. Sub-PR sequence

**3b.1 — Engine aggregate write removal.** [Shipped 2026-04-22]
1. Deleted `self.apply_state_updates(wrapped, run, workflow)` inside
   `apply_state_updates_as_files` — `NodeOutputProduced` is now the
   sole carrier of the write-set. The `apply_state_updates` static
   method itself stays for the one legit external caller
   (`test_node_use_case.py:175`).
2. New `tests/helpers/event_capture.CapturingEventPublisher` — a test
   publisher that records every `publish(topic, event)` call and
   reconstructs the projector-equivalent `variables` dict from the
   captured `NodeOutputProduced` events. Replaces the pattern of
   reading `run.context.variables` after the fact.
3. Seven failing tests migrated to assert via captured events:
   - `test_phase_0_engine_prep` — `TestNodeOutputProducedOrderingBeforeMutation`
     renamed to `TestNodeOutputProducedCarriesWriteSet`; the
     mutation-ordering invariant is vacuous after 3b.1 and is
     replaced by the "event carries the full alias-propagated
     write-set, aggregate untouched" contract.
   - `test_queued_builtin_state_updates` × 3 (including the guard
     tests) — projected-variables assertion against the capturing
     publisher.
   - `test_use_cases::TestCompleteTaskUseCase` × 4 — same pattern,
     using the `engine._test_publisher` that
     `_build_real_engine` now exposes.

**3b.2a — Low-risk use-case write removal.** [Shipped 2026-04-22]
1. Deleted `edit_node_data_use_case.py:320` and `:876` — the
   `run.context.variables[cmd.node_id] = new_ref` aggregate writes.
   Both sites already call `emit_node_data_edited` immediately
   before, and the projector reducer handles `NodeDataEdited` with
   the same write-set semantics as `NodeOutputProduced`.
2. Deleted `fail_task_use_case.py:161-164` — the `else:` fallback
   that wrote `variables[task.node_id] = failure_info` when
   engine/workflow were missing. Production wiring always provides
   both, so the fallback was dead code.
3. Regression delta: 1654 passed / 59 baseline failed — zero new
   regressions, no test migration required (no test asserted on the
   aggregate after either write).

**3b.2b — Pending-node output event plumbing.** [Shipped 2026-04-22]
1. `resume_pending_task_use_case.py:348-359` (the
   `_resume_human_interaction_task` variable write): replaced the
   direct `run.context.variables[node.id] = node_output` (+ name
   alias) with a call to
   `self._orchestration_engine.apply_state_updates_as_files(...)`
   passing `output_port="success"` and `causation_id=task.id`. The
   helper emits `NodeOutputProduced` which the projector reducer
   already consumes — variables rebuild from the event log without
   extending the reducer or the handler-specific completion events.
2. `tests/unit/test_resume_pending_task.py::_mock_orchestration_engine`
   gained a test-only mirror of `apply_state_updates_as_files` that
   mutates `run.context.variables` (mirrors the Phase 1a-style
   test shim used in `test_node_builder_execution.py`) so the
   existing aggregate-read assertions stay green without a
   per-test migration.
3. `sweep_timed_out_child_runs_use_case.py` had no variable writes
   — only status writes at `:99` (`child_run.status = FAILED`) and
   `:150` (`grandchild_run.status = CANCELLED`). Those belong in
   3b.2c.

**3b.2c — Cross-use-case status-write removal.** [Shipped 2026-04-22]
1. New helper `app/layer2_application/run_status_reader.py`:
   `read_authoritative_status(run, view_store) -> str`. Prefers the
   projector-backed view store (authoritative post-3b.2c) and falls
   back to the aggregate for runs with no projected events.
2. Injected `run_context_view_store` + switched seven use cases to
   the helper:
   - `cancel_run_use_case.py:57,:105` — parent + child status
     validation.
   - `resume_run_use_case.py:66` — PAUSED validation.
   - `resume_pending_task_use_case.py:102,:110` — RUNNING/PENDING
     validation + `was_pending` flag.
   - `sweep_timed_out_child_runs_use_case.py:79,:140` — child +
     grandchild terminal-status check.
   - `pause_run_use_case.py:47` — RUNNING validation.
   - `rerun_task_use_case.py:100,:175` — blocked-status check +
     FAILED/COMPLETED→RUNNING revive branch.
   - `trigger_node_use_case.py:113` — SUSPENDED validation.
3. Deleted the five status writes:
   - `cancel_run_use_case.py:77` (`run.status = CANCELLED`) and
     `:110` (child `status = CANCELLED`).
   - `resume_run_use_case.py:73` (`run.status = RUNNING`) plus the
     now-redundant `self.run_repo.save(run)` on the next line.
   - `resume_pending_task_use_case.py:112` (`run.status = RUNNING`
     + redundant `run_repo.save`).
   - `sweep_timed_out_child_runs_use_case.py:99` (child
     `status = FAILED`) and `:150` (grandchild
     `status = CANCELLED`). The aggregates still persist
     `completed_at` + `error` because no event carries those fields;
     status is rebuilt from `RunCancelled` / `RunFailed` events.
4. `tests/conftest.py::DummyOrchestrationEngine` gained an
   `emitted: dict[str, list]` call recorder + hooks on
   `emit_run_cancelled` / `emit_run_resumed` so tests can assert
   status transitions via events.
5. Two aggregate-status tests migrated to event assertions
   (`test_use_cases.py::TestCancelRun::test_cancel_running_run`,
   `TestResumeRunUseCase::test_resume_paused_run`).

**Regression delta (3b.2c):** 1654 passed / 59 baseline / 107
skipped — zero new regressions.

All Phase 3b inline-write removal work is shipped. Next scope item
is Phase 3c (`run_lock_registry` deletion after bus partitioning
verification) — tracked separately.

**3b.3 — Read-path cleanup.** [Shipped 2026-04-22]
1. Deleted `LiveRunContextViewStore`, `ParityComparatorViewStore`,
   `ProjectorDriftRecorder`, `projector_drift_controller.py`,
   `tests/unit/test_parity_comparator_view_store.py`,
   `tests/integration/test_shadow_parity_harness.py`.
2. Collapsed `_build_run_context_view_store` — always returns
   `CachedProjectorRunContextViewStore`. `monitor` / `drift_recorder`
   kwargs retained for signature compat.
3. Removed `PROJECTOR_READ_MODE` setting from `app_config.py` and
   the `projector_drift_recorder` / `/internal/projector-drift`
   wiring from `bootstrap.py` and the v1 router module.
4. New `tests/helpers/view_store_doubles.LiveViewStoreDouble` — test-
   only aggregate-backed view store for fixtures that pre-populate
   a `MemoryRunRepository`. Three test files migrated to the helper:
   `test_get_run_context.py`, `test_use_cases.py`,
   `test_projector_view_store.py`.
5. `test_bootstrap_event_log_selection.py::TestRunContextViewStoreSelection`
   rewritten to assert a single wiring outcome rather than three modes.

## 3. Risks and mitigations

### 3.1 Test fixtures that construct `WorkflowRun` directly

Many unit tests build a `WorkflowRun` by hand, mutate
`run.context.variables`, and expect the engine to read from the
aggregate. After 3b the engine always reads through the view store,
which for those tests is backed by an empty projector (no event log
replay). Tests that don't populate state via events will see missing
variables.

Mitigation: Phase 2a already addressed this via
``_view_completed(run, extra=…)`` in-pass helpers and by
``LiveRunContextViewStore`` fallback. After 3b.3
`LiveRunContextViewStore` is gone — but `CachedProjectorRunContextViewStore`
falls back to the workflow repo + run repo when the event log is
empty. For surviving test compatibility we keep that fallback.

### 3.2 HITL resume race

Between the engine emitting `InputSubmitted` / `HumanActionCompleted`
and the projector applying the event, the engine might re-read the
view store and see stale status. Phase 2a accepted this as
2a-interim; Phase 3b closes the window because the aggregate is no
longer a separate source — everything goes through the event hop.
The latency window is bounded by the in-process projector's
synchronous apply, which is sub-millisecond.

### 3.3 Rollback

After 3b.1 / 3b.2: revert the specific PR; aggregate write returns,
engine tolerates having two sources (projector + aggregate) briefly.

After 3b.3: the live / comparator stores are deleted. Reverting
requires re-checking out those files. Coordinate 3b.3 with a release
boundary; avoid shipping it mid-cycle.

## 4. Verification

Per sub-PR:
- Full regression suite runs under default mode (projector). Zero
  new failures vs. current 59-failure baseline (55 pre-existing + 4
  upstream socketio).
- Dispatcher shadow parity test (`test_dispatcher_shadow_parity.py`)
  continues to pass: dispatcher emissions unchanged by this work.
- Target use-case tests pass (`tests/unit/test_{cancel_run,fail_task,
  edit_node_data,resume_run,resume_pending_task,
  sweep_timed_out_child_runs,complete_task}*.py`).

## 5. Critical files

- `app/layer1_domain/services/orchestration_engine.py`
- `app/layer2_application/features/{cancel_run,fail_task,edit_node_data,resume_run,resume_pending_task,sweep_timed_out_child_runs}/use_cases/*.py`
- `app/layer4_frameworks/run_context/live_view_store.py`
- `app/layer4_frameworks/run_context/parity_comparator_view_store.py`
- `app/layer4_frameworks/monitoring/projector_drift_metrics.py`
- `app/layer3_adapters/controllers/restful/v1/projector_drift_controller.py`
- `app/layer4_frameworks/config/app_config.py`
- `bootstrap.py`

## 6. Related documents

| Doc | Relevance |
|---|---|
| [phase-1b-authoritative-cutover.md](./phase-1b-authoritative-cutover.md) | Named these 7 use cases in §2 "Deferred items" |
| [phase-2-dispatcher-extraction.md](./phase-2-dispatcher-extraction.md) | §4 "Deferred to Phase 3" bucket |
| [phase-3a-dispatcher-authoritative.md](./phase-3a-dispatcher-authoritative.md) | Completed the dispatch-side authoritative work |
