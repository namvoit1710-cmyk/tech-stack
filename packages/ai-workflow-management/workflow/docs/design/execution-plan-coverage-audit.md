# Event-Driven Execution Plan — Coverage Audit

**Date:** 2026-04-12
**Scope:** Cross-reference every requirement in `event-driven-execution-plan.md` against the current implementation in `workflow-control-plane-service/`.

---

## 1. Phase-by-Phase Coverage

### Phase 0 — Async HTTP Client Fix — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `httpx.AsyncClient` singleton replaces blocking `httpx.Client` | Yes | `layer4_frameworks/clients/http_file_service_client.py` |
| Async propagation through resolver chain | Yes | `FileBackedResolverStrategy`, `VariableResolver`, all handler call sites |
| No sync-in-async stall | Yes | Every resolver strategy, handler, and use case awaits through to file service |

**Coverage: 100%**

---

### Phase 1 — Run-Scoped Materialization Cache — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| Per-run materialize/OData caches via contextvar | Yes | `FileBackedResolverStrategy._run_materialize_caches`, `_run_odata_caches` |
| Cache dropped on terminal transitions | Yes | `check_run_completion` FAILED/COMPLETED branches; `cancel_run_use_case` |
| `drain_events()` no longer wipes caches | Yes | `orchestration_engine.py` drain_events |

**Coverage: 100%**

---

### Phase 2 — Handler Classification + Metrics — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `NodeHandlerShape` enum (PURE_INLINE, ASYNC_INLINE, DISPATCH_EXTERNAL, PENDING_USER) | Yes | `node_handlers/base.py:25-48` |
| Histogram instrumentation on handler duration | Yes | `invoke_handler` at `base.py:192-200` |
| Each handler declares its shape | Yes | All 14 handlers have `shape` class attribute |

**Coverage: 100%**

---

### Child-Workflow Hot-Fixes (Fix A/B/C) — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| Fix A: Missing `await`s on `apply_state_updates_as_files` | Yes | `orchestration_engine.py` |
| Fix B: Predecessor-readiness guard in `_execute_node_inner` | Yes | `orchestration_engine.py:1600-1642` |
| Fix C: `_start_child_workflow` harmonized with `orchestrate_start` | Yes | `orchestration_engine.py` |

**Coverage: 100%**

---

### Phase 3 — LOOP Design Spike — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| Research spike document delivered | Yes | `docs/design/loop-event-driven-spike.md` |
| Recommendation: go behind feature flag | Yes | `EVENT_DRIVEN_LOOP` flag in `app_config.py` |

**Coverage: 100%** (research deliverable)

---

### Phase 4a — Read-Side View Shim — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `IRunContextView` + `IRunContextViewStore` protocols | Yes | `layer2_application/interfaces/` |
| `RunContextSnapshot` frozen dataclass | Yes | `layer1_domain/entities/` |
| `LiveRunContextViewStore` backed by `IRunRepository` | Yes | `layer4_frameworks/run_context/` |
| 3 use cases migrated (`get_run_status`, `get_run_context`, `get_node_output`) | Yes | `layer2_application/features/` |

**Coverage: 100%**

---

### Phase 4b.1 — Event Log Foundation — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `IEventLogRepository` protocol | Yes | `layer2_application/interfaces/` |
| `MemoryEventLogRepository` | Yes | `layer4_frameworks/persistence/` |
| Shadow write-ahead log via `EventLogAppendingPublisher` | Yes | `layer4_frameworks/event_log/` |

**Coverage: 100%**

---

### Phase 4b.2 — Dispatcher Extraction (pure fan-out) — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `compute_fanout` pure function | Yes | `layer1_domain/services/dispatcher/fanout.py` |
| Port filter / cascade-skip / predecessor readiness | Yes | Same module |
| Deterministic event IDs on `EdgeTraversed` and `TaskSkipped` | Yes | Uses `deterministic_event_id` helper |

**Coverage: 100%**

---

### Phase 4b.3 — Projector Reducer — **DONE (partial)** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| Pure `apply_event` reducer | Yes | `layer1_domain/services/projector/run_state_projector.py` |
| Rebuilds lifecycle state from event log | Yes | Same |
| Rebuilds `variables` from event log | Yes | Same |
| Loop event type passthrough branches | Yes | Added in Phase 4c.4.2 |

**Coverage: 100%** (partial noted in plan due to variables gap, now closed)

---

### Phase 4b.4 — Post-BUILTIN Edge Migration — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| COMPUTE port fix | Yes | `compute_handler.py` |
| SuccessorProcessor uses `compute_fanout` | Yes | `successor_processor.py:143` |
| Deterministic IDs on CONDITION/SWITCH taken_edge_ports | Yes | Same |

**Coverage: 100%**

---

### Phase 4b.5 — Event Enrichment — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `EnrichingPublisher` decorator | Yes | `layer4_frameworks/event_enrichment/` |
| Sets `root_run_id` and `depth` on events | Yes | `orchestration_engine.py:320-327` |

**Coverage: 100%**

---

### Phase 4b.6 — SQLite Event Log Backend — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `SqliteEventLogRepository` | Yes | `layer4_frameworks/persistence/` |
| Durable file-backed log | Yes | Same |

**Coverage: 100%**

---

### Phase 4b.7 — Bootstrap Event Log Factory — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `EVENT_LOG_BACKEND` env var selection | Yes | `bootstrap.py:51-103` |
| memory / sqlite / hana backends | Yes | Same |

**Coverage: 100%**

---

### Phase 4b.8 — HANA Event Log Backend — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `HanaEventLogRepository` | Yes | `layer4_frameworks/persistence/` |
| Dedupe via HANA error code 301 on unique index | Yes | Same |
| Migration `025_create_aiw_event_log.sql` | Yes | `db/hdi/` |

**Coverage: 100%**

---

### Phase 4c.1 — Projector Run Context View Store — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `ProjectorRunContextViewStore` | Yes | `layer4_frameworks/run_context/projector_view_store.py` |

**Coverage: 100%**

---

### Phase 4c.2 — Cached Projector View Store — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `CachedProjectorRunContextViewStore` with O(1) warm gets | Yes | `layer4_frameworks/run_context/cached_projector_view_store.py` |
| Incremental per-run cache | Yes | Same |

**Coverage: 100%**

---

### Phase 4c.3 — `dag_info` Fallback + `USE_PROJECTOR_READ_MODEL` Flag — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| Hybrid read model (dag_info from workflow_repo) | Yes | `projector_view_store.py` |
| `get_by_root_id` via run_repo fallback | Yes | Same |
| `USE_PROJECTOR_READ_MODEL` flag | Yes | `app_config.py`, `bootstrap.py:106-153` |
| Per-view `dag_info` memoization | Yes | `ProjectedView._cached_dag_info` |

**Coverage: 100%**

---

### Phase 4c.4.1 — LoopScope Data Model — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `LoopScope` frozen dataclass | Yes | `layer1_domain/value_objects/loop_scope.py` |
| `derive_invocation_id` (uuid5-based) | Yes | Same |
| `Task.scope_stack: list[LoopScope] | None` | Yes | `layer1_domain/entities/task.py` |
| `Task.get_innermost_scope()` | Yes | `task.py:55` |
| `Task.with_pushed_scope()` | Yes | Same |

**Coverage: 100%**

---

### Phase 4c.4.2 — Loop Event Types + Reducer — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `LoopStarted` event | Yes | `events.py` |
| `LoopIterationStarted` event | Yes | `events.py` |
| `LoopIterationCompleted` event (extended) | Yes | `events.py` |
| `LoopCompleted` event | Yes | `events.py` |
| `LoopExitRequested` event | Yes | `events.py` |
| Projector reducer branches for loop events | Yes | `run_state_projector.py` |

**Coverage: 100%** (16 tests)

---

### Phase 4c.4.3 — Event-Driven LOOP Handler — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `LoopNodeHandler` wired behind `EVENT_DRIVEN_LOOP` flag | Yes | `loop_handler.py`, `bootstrap.py:173` |
| Emits loop events (LoopStarted, LoopIterationStarted, etc.) | Yes | Via `EventDrivenLoopDriver` delegation |
| Populates `scope_stack` on body tasks | Yes | `_dispatch_iteration_body` |
| Deterministic event IDs | Yes | `deterministic_event_id(run.id, ...)` |

**Coverage: 100%** (14 tests)

---

### Phase 4c.4.4 — Scope-Aware Merge Quorum — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `RunContext.merge_quorum_buffers` field | Yes | `workflow_run.py` |
| `MergeNodeHandler` keys by `(node_id, invocation_id, iter)` | Yes | `merge_handler.py` |
| Sentinel fallback `(node.id, "", -1)` for no-scope | Yes | Same |
| Parallel-iteration isolation | Yes | Independent quorum state |
| Nested-loop buffer freshness via `derive_invocation_id` | Yes | Different invocation_ids per outer iter |

**Coverage: 100%** (4 tests)

---

### Phase 4c.4.5 — LOOP_EXIT Break Node — **DONE** (2026-04-11)

| Requirement | Implemented | Location |
|---|---|---|
| `NodeType.LOOP_EXIT` enum value | Yes | `node_type.py` |
| `LoopExitNodeHandler` (PURE_INLINE shape) | Yes | `loop_exit_handler.py` |
| `_find_innermost_active_loop` scan | Yes | Same |
| Sets `break_requested=True` on loop_state | Yes | Same |
| Emits `LoopExitRequested` event | Yes | Same |
| Registered in `BUILTIN_NODE_TYPES` | Yes | `successor_processor.py` |

**Coverage: 100%** (10 tests)

---

### Phase 5.2.1 — Event-Driven Loop Driver (Inline) — **DONE** (2026-04-12)

| Requirement | Implemented | Location |
|---|---|---|
| `EventDrivenLoopDriver` class | Yes | `loop_driver/event_driven_loop_driver.py` |
| `initialize_loop` handler-interface method | Yes | Same |
| `iteration_complete` handler-interface method | Yes | Same |
| `run_loop` engine-facing inline method | Yes | Same |
| Event ordering fix (init events BEFORE body events) | Yes | `run_loop` publishes before dispatch |
| Engine routes LOOP nodes to driver when wired | Yes | `orchestration_engine.py:466-538` |
| `LoopNodeHandler` → thin delegation shim | Yes | `loop_handler.py` |
| Legacy walker unchanged for flag-off | Yes | `_run_loop_iterations` untouched |

**Coverage: 100%** (9 tests)

---

### Phase 5.2.2 — Queued-Mode Event-Driven Loops — **DONE** (2026-04-12)

| Requirement | Implemented | Location |
|---|---|---|
| Persistence fix (`self._run_repo.save(run)`) | Yes | `orchestration_engine.py:529` |
| Runtime BFS helpers (no DAGPreparer changes) | Yes | `event_driven_loop_driver.py:138-305` |
| `is_iteration_done` | Yes | Same, line 241 |
| `iterate_body_subgraph` | Yes | Same, line 203 |
| `_find_body_entry_node` | Yes | Same, line 155 |
| `_post_loop_reachable` | Yes | Same, line 176 |
| `_is_loop_exit_edge` / `_is_loop_body_edge` | Yes | Same, lines 138/148 |
| `scope_stack` contextvar propagation | Yes | `task_factory.py:32-55` |
| `invoke_handler` `current_scope_stack` kwarg | Yes | `base.py:147` |
| 9 invoke_handler call sites updated | Yes | `orchestration_engine.py` multiple lines |
| `initialize_loop_queued` method | Yes | `event_driven_loop_driver.py:592` |
| Engine queued-vs-inline routing | Yes | `orchestration_engine.py:492-525` |
| `maybe_enqueue_loop_iteration` hook | Yes | `orchestration_engine.py:121-207` |
| Hook in `_exec_builtin_queued` | Yes | `orchestration_engine.py:1788` |
| Hook in `_exec_pending_queued` | Yes | `orchestration_engine.py:1849` |
| Hook in `complete_task_use_case` | Yes | `complete_task_use_case.py` |
| Hook in `resume_pending_task_use_case` | Yes | `resume_pending_task_use_case.py` |
| Cascade-skip sweep in hook | Yes | `orchestration_engine.py:174-179` |
| Iter-boundary cleanup in `orchestrate_loop_iteration` | Yes | `orchestration_engine.py:2026-2051` |
| `_dispatch_iteration_body` scope_stack fix | Yes | `event_driven_loop_driver.py` |

**Coverage: 100%** (32 tests)

---

### Phase 6.1 — Uniform Execution Model (Foundation + Handler Migration) — **DONE** (2026-04-12)

| Requirement | Implemented | Location |
|---|---|---|
| `NodeHandlerResult` new fields (output_port, completion_mode, etc.) | Yes | `node_handlers/base.py` |
| `compute_fanout` `expected_port_override` parameter | Yes | `dispatcher/fanout.py` |
| `UNIFORM_EXECUTION` feature flag | Yes | `app_config.py`, `bootstrap.py` |
| `_exec_uniform` single entry point for all node types | Yes | `orchestration_engine.py` |
| `_route_to_successors` pure edge routing method | Yes | `orchestration_engine.py` |
| Gate in `_execute_node_inner` (flag on → uniform, flag off → legacy) | Yes | `orchestration_engine.py` |
| TRIGGER handler: `output_port="out_default"`, `completion_mode="immediate"` | Yes | `trigger_handler.py` |
| LOOP_EXIT handler: `output_port="out_default"`, `completion_mode="immediate"` | Yes | `loop_exit_handler.py` |
| COMPUTE handler: `output_port="success"`, `completion_mode="immediate"` | Yes | `compute_handler.py` |
| MERGE handler: `completion_mode="deferred"/"immediate"` | Yes | `merge_handler.py` |
| PARALLEL handler: `output_port="out_default"`, `completion_mode="immediate"` | Yes | `parallel_handler.py` |
| CONDITION handler: `completion_mode="immediate"` (uses `taken_edge_ports`) | Yes | `condition_handler.py` |
| SWITCH handler: `completion_mode="immediate"` (uses `taken_edge_ports`) | Yes | `switch_handler.py` |
| TASK handler: `completion_mode="dispatched"`, `resolved_input`, `worker_type` | Yes | `task_handler.py` |
| INPUT handler: `completion_mode="pending"` | Yes | `input_handler.py` |
| HUMAN_ACTION handler: `completion_mode="pending"` | Yes | `human_action_handler.py` |
| DATA_EDIT handler: `completion_mode="pending"` | Yes | `data_edit_handler.py` |
| FILE_UPLOAD handler: `completion_mode="pending"` | Yes | `file_upload_handler.py` |
| WORKFLOW handler: `completion_mode="child_run"` | Yes | `workflow_handler.py` |
| LOOP handler: `completion_mode="loop_body"/"immediate"` | Yes | `loop_handler.py` |

**Coverage: 100%** (1450 passed flag off; 54 integration tests passed flag on)

---

## 2. Gap Analysis Cross-Reference

| Gap Analysis Row | Status | Implementation Verified |
|---|---|---|
| Pluggable event bus | Done | Console / Kafka / Event Mesh publishers |
| Stateless Dispatcher/Executor/Projector split | **Substantial** | Phase 6.1 uniform execution model — all 14 node types behind `_exec_uniform` with `_route_to_successors`. Legacy paths still present behind flag guard. |
| Read-side adapter-layer (`IRunContextView`) | Done | 3 use cases migrated |
| Runtime-only validation | Partial | DAG validated pre-run; advisory mode deferred |
| Event log as source of truth | Partial | All 3 backends wireable; read model behind flag |
| Per-node failure policy | Partial | Retry/skip exist; declarative policies deferred |
| Named output ports + error routing | Partial | Ports exist; error-port not first-class |
| Socket.IO realtime | Done | 3-queue backpressure, parent/root fan-out |
| Priority event lanes | Done | EventCollector IMMEDIATE/NORMAL/TERMINAL |
| Lazy expression resolution | Done | VariableResolver + strategy chain |
| Async file-service + resolver chain | Done | Phase 0 |
| Run-scoped materialize + OData cache | Done | Phase 1 |
| Merge-safe skip propagation | Done | `collect_downstream_nodes_to_skip()` |
| Sub-workflows with parent/root routing | Done | WorkflowNodeHandler + 3-room fan-out |
| Per-run ordering via partitioning | Partial | asyncio.Lock in-process; bus-level deferred |
| Deterministic event IDs | Partial | Post-BUILTIN sites done; TASK/RUN events still UUID4 |
| Outbox pattern | Missing | Not present |
| Quorum buffering | Partial | MergeNodeHandler implicit; explicit buffers deferred |
| Cycles via runtime depth counter | Missing | DAGValidator rejects pre-run |
| Child-workflow invocation semantics | Done | Fix A/B/C |
| Nested-loop support | Enabled behind flag | Phase 4c.4 + 5.2.1 complete |
| Queued-mode loop iteration | **Closed** | Phase 5.2.2 |
| Merge quorum inside loop body | **Closed behind flag** | Phase 4c.4.4 |

---

## 3. Deferred Backlog Status (§2c)

| Item | Status | Notes |
|---|---|---|
| `LOOP_CONTINUE` + `LoopIterationSkipped` | Open | Medium priority — completes break+continue control-flow |
| Condition-false walker routing fix + merge buffer pruning | Open | High priority — latent bug with production exposure |
| Failed-iteration `outcome` propagation | Open | Low priority — nothing consumes `outcome` yet |
| ~~Queued-mode loop fix~~ | **Closed** | Superseded by Phase 5.2.2 |
| `handle_entry_nodes` unification | Open | Low priority — cosmetic cleanup |
| Walker removal | Subsumed by Phase 5.2 | Requires flag default-on in production first |
| Legacy `Task.loop_context` / `iteration_index` removal | Open | Post-flip cleanup; blocked by flag demotion |

---

## 4. Node Type Execution Coverage Matrix

All 14 node types and their execution paths. **With `UNIFORM_EXECUTION=true`**, all types go through the same `_exec_uniform` → handler → `_route_to_successors` path.

| Node Type | `completion_mode` | `output_port` | Legacy Path (flag off) | Uniform Path (flag on) | Tests |
|---|---|---|---|---|---|
| TRIGGER | `immediate` | `out_default` | `_exec_builtin_queued` | `_exec_uniform` → mark completed → `_route_to_successors` | Yes |
| COMPUTE | `immediate` | `success` | `_exec_builtin_queued` | `_exec_uniform` → mark completed → `_route_to_successors(port="success")` | Yes |
| CONDITION | `immediate` | (taken_edge_ports) | `_exec_builtin_queued` | `_exec_uniform` → mark completed → `_route_to_successors(taken_edge_ports)` | Yes |
| SWITCH | `immediate` | (taken_edge_ports) | `_exec_builtin_queued` | `_exec_uniform` → mark completed → `_route_to_successors(taken_edge_ports)` | Yes |
| LOOP | `loop_body`/`immediate` | `loop_complete` | `_exec_builtin_queued` / driver | `_exec_uniform` → enqueue body or mark completed → route exit | Yes |
| LOOP_EXIT | `immediate` | `out_default` | `_exec_builtin_queued` | `_exec_uniform` → mark completed → `_route_to_successors` | Yes |
| PARALLEL | `immediate` | `out_default` | `_exec_builtin_queued` | `_exec_uniform` → mark completed → `_route_to_successors` | Yes |
| MERGE | `deferred`/`immediate` | `out_default` | `_exec_builtin_queued` | `_exec_uniform` → deferred (noop) or mark completed → route | Yes |
| WORKFLOW | `child_run` | — | `_exec_builtin_queued` → child | `_exec_uniform` → start child run | Yes |
| TASK | `dispatched` | — | `_exec_task_queued` → worker | `_exec_uniform` → mark RUNNING → publish task.dispatched | Yes |
| INPUT | `pending` | — | `_exec_pending_queued` → pause | `_exec_uniform` → save PENDING → publish events | Yes |
| HUMAN_ACTION | `pending` | — | `_exec_pending_queued` → pause | `_exec_uniform` → save PENDING → publish events | Yes |
| DATA_EDIT | `pending` | — | `_exec_pending_queued` → pause | `_exec_uniform` → save PENDING → publish events | Yes |
| FILE_UPLOAD | `pending` | — | `_exec_pending_queued` → pause | `_exec_uniform` → save PENDING → publish events | Yes |

---

## 5. Event Flow Coverage

| Event Type | Emitted By | Consumed By | Deterministic ID |
|---|---|---|---|
| `RunStarted` | `start_run_use_case` | Projector, Realtime | No (UUID4) |
| `RunCompleted` | `check_run_completion` | Projector, Realtime | No (UUID4) |
| `RunFailed` | `check_run_completion` | Projector, Realtime | No (UUID4) |
| `TaskDispatched` | Engine buffer / immediate | Projector, Realtime | No (UUID4) |
| `TaskCompleted` | `complete_task_use_case` / engine | Projector, Realtime | No (UUID4) |
| `TaskSkipped` | `compute_fanout` | Projector, Realtime | Yes |
| `TaskFailed` | `_mark_task_failed` | Projector, Realtime | No (UUID4) |
| `EdgeTraversed` | `compute_fanout` / CONDITION/SWITCH handlers | Projector, Realtime | Yes |
| `NodeOutputProduced` | `SuccessorProcessor.process` | Projector, Realtime | Partial |
| `ContextUpdated` | `complete_task_use_case` | Projector, Realtime | No (UUID4) |
| `ChildRunStarted` | `_start_child_workflow` | Projector, Realtime | No (UUID4) |
| `ChildRunCompleted` | Child run completion | Projector, Realtime | No (UUID4) |
| `LoopStarted` | `EventDrivenLoopDriver` | Projector (passthrough), Realtime | Yes |
| `LoopIterationStarted` | `EventDrivenLoopDriver` | Projector (passthrough), Realtime | Yes |
| `LoopIterationCompleted` | `EventDrivenLoopDriver` | Projector (passthrough), Realtime | Yes |
| `LoopCompleted` | `EventDrivenLoopDriver` | Projector (passthrough), Realtime | Yes |
| `LoopExitRequested` | `LoopExitNodeHandler` | Projector (passthrough), Realtime | Yes |

---

## 6. Sequence Diagrams Reference

All sequence diagrams are in [`orchestration-sequence-diagrams.puml`](./orchestration-sequence-diagrams.puml):

| # | Diagram | Covers |
|---|---------|--------|
| 1 | `start_run_flow` | API → StartRunUseCase → Queue → Engine → Entry nodes |
| 2 | `builtin_node_inline` | BUILTIN node inline execution (CONDITION, SWITCH, COMPUTE, MERGE, PARALLEL, TRIGGER) |
| 3 | `builtin_node_queued` | BUILTIN node queued execution via ExecuteNodeAction |
| 4 | `task_node_flow` | TASK node: variable resolution → worker dispatch → CompleteTaskUseCase callback |
| 5 | `pending_node_flow` | PENDING node: pause → user input → ResumePendingTaskUseCase |
| 6 | `loop_inline_flow` | LOOP (event-driven, inline): driver.run_loop iteration cycle |
| 7 | `loop_queued_flow` | LOOP (event-driven, queued): initialize_loop_queued → LoopIterationAction cycle |
| 8 | `workflow_node_flow` | WORKFLOW: child run creation → independent execution → parent resumption |
| 9 | `condition_branch_flow` | CONDITION/SWITCH: evaluation → edge selection → cascade skip → branch dispatch |
| 10 | `event_publication_chain` | EventCollector → EventLogAppending → Enriching → RealtimeBroadcasting → Socket.IO |

---

## 7. Overall Assessment

**Phases completed: 31** (all phases through 5.2.2 + 6.1–6.3 + 7.1–7.7 are DONE)

**Test coverage: 1401 passed, 51 skipped**

**Remaining work:**
1. **`handle_entry_nodes` unification** — small, cosmetic; extract shared helpers
2. **ORM/HANA column migration** — drop `loop_context`/`iteration_index` columns from task table
3. **Future phases** — Outbox pattern, explicit quorum buffers, runtime validation demotion, bus-level partitioning, remaining deterministic event IDs

**Key architectural state:**
- **Uniform execution model** — all node types go through `_exec_uniform` → handler → `_route_to_successors`. Both flags default `true`.
- **All routing via `_route_to_successors`** — both `dispatch_or_handle_task` and `handle_entry_nodes` BUILTIN branches use it.
- **6 BUILTIN handlers cleaned** (Phase 7.5) — no `tasks_to_dispatch` or `compute_fanout`. TASK/PENDING/WORKFLOW/LOOP keep `tasks_to_dispatch` for non-routing uses.
- **Legacy Task fields removed** (Phase 7.6) — `loop_context`/`iteration_index` gone from entity + factory. All reads migrated to `scope_stack`.
- **LoopNodeHandler cleaned** (Phase 7.7) — 62-line driver delegation shim. ~320 lines of legacy methods deleted.
- **Failed-iteration outcome** (Phase 7.4) — `outcome="ok"/"failed"/"skipped"` on `LoopIterationCompleted`.
- Event log complete (memory/SQLite/HANA). Read model via `USE_PROJECTOR_READ_MODEL`.
- Loop features complete: output accumulation, max_concurrent, break, continue, merge pruning.
- **15 node types** (all handler-backed).
- **Total legacy code removed: ~1,700+ lines.**
