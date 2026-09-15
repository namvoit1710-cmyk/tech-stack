# Orchestration Engine Audit — workflow-control-plane-service

**Date:** 2026-04-16
**Scope:** `app/layer1_domain/services/orchestration_engine.py`, all node handlers, `VariableResolver` / `ConditionEvaluator`, realtime broadcasting, persistence, concurrency
**Type:** Issue / edge-case audit — no code changes
**Status:** Findings only. Fix planning deferred.

---

## Summary

| Severity | Count | Example |
|----------|-------|---------|
| Critical | 10    | Run-lock leak, missing cycle detection, concurrent MERGE race |
| High     | 10    | Late-task callback on terminal run, SWITCH vs CONDITION divergence |
| Medium   | 13    | `_run_locks` memory leak, Kafka bad-message drop |
| Low      | 10    | `is_empty` on `0`/`False`, whitespace in boolean coercion |

**Most urgent:** C1 (lock leak), C2 (no cycle detection), C3 (MERGE race), C4 (async repo misuse), C8 (LOOP+PENDING hang), C9 (COMPUTE no failure port).

---

## Critical — fix first

### C1. Run-lock leak on uncaught exception
- **File:** `app/layer1_domain/services/orchestration_engine.py` ~L1276–1298 (`orchestrate_execute_node`)
- **Issue:** Lock acquired at entry is not wrapped in `try/finally`. Any uncaught exception inside `_execute_node_inner` leaves the lock held forever.
- **Symptom:** The run becomes permanently unresponsive — no further task completions or cancels process for that `run_id`.

### C2. No intra-workflow cycle detection
- **File:** `app/layer1_domain/services/dag_preparer.py` L12–30
- **Issue:** `DAGPreparer.prepare()` builds adjacency/predecessors but never validates acyclicity. `_check_ancestry_cycle()` in the engine only covers cross-workflow cycles.
- **Symptom:** A cyclic definition → engine re-dispatches the same nodes until recursion depth 100 trips, then fails the run (or OOMs in pathological cases).

### C3. Concurrent MERGE fan-in without atomic quorum
- **File:** `orchestration_engine.py` L1701–1710 (fanout) and L462–622 (`check_run_completion`)
- **Issue:** Two predecessors completing simultaneously can both read the same `completed_nodes` snapshot and both dispatch the MERGE. The quorum buffer on `RunContext` (only `MergeNodeHandler` consults it) doesn't guard the fanout path.
- **Symptom:** Duplicate MERGE execution or a predecessor lost in the race → run hangs.

### C4. Sync repo methods call `run_until_complete` from async context
- **File:** `app/layer4_frameworks/repositories/run_repository.py` L18–24, L26–33
- **Issue:** `save()` / `get_by_id()` use `asyncio.get_event_loop().run_until_complete()`. Called from a FastAPI async handler this raises `RuntimeError: This event loop is already running`.
- **Symptom:** Hard failure on any path that uses this repo in prod (non-mock INFRA_MODE).

### C5. Event-log append vs. broker publish not atomic
- **File:** `app/layer4_frameworks/messaging/event_log_appending_publisher.py` L63–73
- **Issue:** Log append and inner publish are separate steps with no compensation. Append-succeeded / publish-failed leaves the projector holding an event that subscribers never received.
- **Symptom:** Frontend stale because a `RUN_COMPLETED` was persisted but never broadcast.

### C6. Realtime `partition_key=None` breaks per-run ordering
- **File:** `app/layer4_frameworks/realtime/broadcasting_publisher.py` L162–195
- **Issue:** `partition_key = root_run_id or run_id or workflow_id` can be `None` for events missing context. Kafka treats `None` as round-robin across partitions → events for the same run land on different partitions.
- **Symptom:** Out-of-order delivery (e.g., `INPUT_SUBMITTED` before `INPUT_REQUESTED` in HITL flows).

### C7. SWITCH silently stalls when no case matches
- **File:** `app/layer1_domain/services/node_handlers/switch_handler.py` L119–125
- **Issue:** No default branch + no matching case → handler logs a warning but produces zero successors. Run just stops.
- **Symptom:** Silent hang with no failure status.

### C8. LOOP body with PENDING node → infinite wait
- **File:** `loop_handler.py` L48–62
- **Issue:** Loop iteration accounting tracks counts only, not whether iterations can complete. A LOOP containing INPUT / HUMAN_ACTION waits forever if the external actor never resolves it — no stall detection.
- **Symptom:** Run hangs indefinitely with no diagnostic.

### C9. COMPUTE never routes to "failure" port on error
- **File:** `compute_handler.py` L161, L168–174
- **Issue:** On `ComputeExpressionError`, error is captured in output but `output_port` stays `"success"`. Downstream `failure` edges never fire.
- **Symptom:** Error-handling branches authored into the workflow are dead code.

### C10. MERGE quorum buffer not cleared between loop iterations
- **File:** `merge_handler.py` L72–86
- **Issue:** Buffer keyed by scope but not reset at iteration boundaries. Iteration N's tallies contaminate iteration N+1.
- **Symptom:** Quorum counted wrong inside loops → merge dispatches early or hangs.

---

## High

### H1. State mutation runs before exception is caught
`orchestration_engine.py` L1404–1415. `apply_state_updates_as_files()` runs at L1413 **before** the except at L1404. If it throws, exception propagates out, run goes FAILED without `check_run_completion`.

### H2. Late task completion after terminal status is dropped
`orchestration_engine.py` L1301–1303. Early return when run is terminal means a straggling worker callback marks the task COMPLETED but `check_run_completion` is never called. Dangling tasks, no event emitted.

### H3. Loop body re-dispatch hangs on deferred task
`orchestration_engine.py` L1337–1340, L1854–1869. If a loop-body task is deferred (predecessor not ready) and the iteration advances, the earlier `completed_nodes` entry for that node prevents re-dispatch.

### H4. `check_run_completion` ignores `scope_stack`
`orchestration_engine.py` L487–494. Dedup by `node_id` only. Iteration 5's COMPLETED task clobbers iteration 1's FAILED task → run advances despite failure.

### H5. SWITCH vs CONDITION coercion semantics diverge
`condition_handler.py` L181–272 vs `switch_handler.py` L201–253. Same operator text, different behavior (ConditionHandler warns on string fallback; SwitchHandler does it silently). `"5" == 5` gives different answers.

### H6. MERGE quorum key collision at root / parallel mix
`merge_handler.py` L68–76. Root MERGE with some branches providing `completed_task=None` falls back to `(node_id, "", -1)`; branches with a completed task use a different key. Quorum miscounted.

### H7. Realtime queue overflow can drop CRITICAL events
`app/layer4_frameworks/realtime/socketio_consumer.py` L75–77, L94–96. Fixed-capacity lanes catch `QueueFull` as a WARNING only. `INPUT_REQUESTED` can silently vanish.

### H8. Realtime silently drops events with empty `event_type` / `rooms`
`socketio_consumer.py` L214–215. Bare `return` on malformed event, no metric, no DLQ.

### H9. Memory repo is not thread-safe
`app/layer4_frameworks/persistence/memory/memory_run_repo.py`. Concurrent `save()` calls race (dict assign under no lock). Only dangerous if INFRA_MODE=memory leaks to prod.

### H10. `contains` operator uses `str()` coercion
`variable_resolver.py` L531–534. `contains` on a list does substring match on `repr(list)`. Right answer by accident for scalars, wrong for structured types.

---

## Medium

- **M1.** Predecessor-readiness guard bypassed for loop-body tasks breaks merges with external predecessors (`orchestration_engine.py` L1334–1351).
- **M2.** `_mark_task_failed` re-enters the state-update path; nested failure paints the run FAILED on an infra error rather than a logic error (`orchestration_engine.py` L1404–1410).
- **M3.** Stale `DAGInfo` cached on `run.context` if workflow mutated mid-run (`orchestration_engine.py` L473–474).
- **M4.** `_run_locks` accumulates forever — memory leak in long-lived deployments (`orchestration_engine.py` L257–260).
- **M5.** Cancel vs. complete_task race — both update run status with no atomic CAS (`cancel_run_use_case.py` L48–114).
- **M6.** `VariableResolver` recursion has no depth/visited guard (`variable_resolver.py` L402–440) → stack overflow via self-referential compute expressions.
- **M7.** Structured rules default to `"="` on missing `operation` key (`condition_handler.py` L157–178) — misconfigured rules evaluate wrong operator silently.
- **M8.** JSON stored as a string is not auto-parsed in `ConditionEvaluator` → operators applied to raw text (`variable_resolver.py` L538–556).
- **M9.** PARALLEL branch condition `VariableResolutionError` treated as permanent skip — no retry for transient failures (`parallel_handler.py` L40–62).
- **M10.** Ordered comparisons (`>`, `<`) silently fall back to string compare when both operands are non-numeric (`condition_handler.py` L228–259).
- **M11.** Kafka consumer auto-commits offsets; a single bad JSON message is logged ERROR and skipped forever (`kafka_broker_client.py` L202–208).
- **M12.** Event Mesh token refresh window — expiry between `_ensure_token()` and HTTP POST yields 401s until circuit breaker opens (`event_mesh_broker_client.py` L167–197).
- **M13.** `skipped_nodes` written in-memory then persisted; mid-save failure leaves in-memory/disk divergence (`orchestration_engine.py` L167–214).

---

## Low

- **L1.** `is_empty` treats `0`, `False`, `{}` inconsistently (`condition_handler.py` L269).
- **L2.** Variable-resolution errors masked behind "No strategy could resolve" when the real error was traversal-on-scalar (`variable_resolver.py` L195–204).
- **L3.** Artifact with `file_id=""` silently returns UNRESOLVED (`file_backed_resolver.py` L185–196).
- **L4.** OData `$skip` beyond row count falls back to full materialize — perf regression (`file_backed_resolver.py` L250–285).
- **L5.** Condition expression split on first `OR`/`AND` match → no parenthesis / precedence support (`variable_resolver.py` L486–497).
- **L6.** `" false "` (with spaces) not stripped in coerce_value (`variable_resolver.py` L298–307).
- **L7.** INPUT handler swallows invalid JSON schema → silent broken UI (`input_handler.py` L37–47).
- **L8.** Edge-event emission skipped for CONDITION/SWITCH when `taken_edge_ports` is empty — silent UI loss (`orchestration_engine.py` L1658–1683).
- **L9.** `LOOP_EXIT` / `LOOP_CONTINUE` mutate `loop_state` without CAS (idempotent today; fragile if handlers ever run concurrently).
- **L10.** Error context lost in `DATA_EDIT` artifact recovery (`data_edit_handler.py` L73–80).

---

## Not covered by this audit

- Performance profiling under load.
- Security review (auth, authz, input sanitation boundaries).
- Happy-path correctness — only failure modes and edges.
- Test coverage of the listed paths.

---

## Suggested verification once fixes land

- Add unit tests for each critical finding: cycle graph, cancel-vs-complete race, SWITCH no-match, LOOP+PENDING, COMPUTE failure port, MERGE across loop iterations.
- Run the control-plane test suite: `cd workflow-control-plane-service && python3 -m pytest tests/ -v`.
- Chaos test: spin up mock infra, launch a batch of concurrent runs including cancels mid-flight, assert no stuck runs after drain.
