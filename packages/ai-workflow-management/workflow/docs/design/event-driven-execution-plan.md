# Event-Driven Migration — Execution Plan

> Phased execution plan for the BUILTIN-node execution model in the event-driven migration of `workflow-control-plane-service`. Sequences the urgent sync-httpx correctness fix first, then the migration prerequisites, then the LOOP design spike, then the Dispatcher/Projector split, then full event-driven BUILTIN promotion.
>
> Companion documents:
> - [`event-driven-orchestration.md`](./event-driven-orchestration.md) — target architecture
> - [`event-driven-gap-analysis.md`](./event-driven-gap-analysis.md) — gap between current service and target
> - [`polyglot-engine-decision.md`](./polyglot-engine-decision.md) — Go-engine vs. Python decision, including the optional Phase 4.5 Dispatcher extraction
> - [`context-validator-resolver-migration.md`](./context-validator-resolver-migration.md) — deep-dive on how RunContext, DAGValidator, and VariableResolver change across phases
> - [`runtime-validation-pipeline.md`](./runtime-validation-pipeline.md) — in-process async pipeline for resolution + validation + execution; rejection of the separate-validator-process option
> - [`adapter-layer-stability-plan.md`](./adapter-layer-stability-plan.md) — plan for keeping REST / gRPC / Socket.IO / DTOs unchanged across all phases; feature-layer command pattern, `ICommandBus`, `IRunContextView`, write-then-emit pattern for `start_run`
> - [`child-workflow-input-bug-analysis.md`](./child-workflow-input-bug-analysis.md) — **Known bug (2026-04-11):** two missing `await`s on `apply_state_updates_as_files` introduced after Phase 0 landed, plus structural predecessor-readiness gaps in child-workflow invocation. Independent of the migration; **fixed 2026-04-11**.
> - [`loop-event-driven-spike.md`](./loop-event-driven-spike.md) — **Phase 3 deliverable (2026-04-11):** research-only spike confirming LOOP can be migrated to event-driven without regressing current behaviour. Identified two latent bugs (nested loops unsupported at data-model level, queued-mode loops stall because `LoopIterationAction` is dead code). **Recommendation: Go, behind a feature flag.**
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. Context

During review of the gap analysis, one question surfaced: **should BUILTIN nodes (CONDITION, SWITCH, LOOP, PARALLEL, MERGE, TRIGGER, WORKFLOW, COMPUTE) stay synchronous and inline during the migration, or should they also become events?**

The original framing was "BUILTIN is CPU-bound, Python's GIL blocks the event loop, and some BUILTIN handlers need to call file-service for data." Investigation of the code invalidated that framing. The handlers are **not CPU-bound** — their own code is sub-millisecond dict manipulation. They are **I/O-bound** because `VariableResolver` → `FileBackedResolverStrategy` → `DataStoreService.materialize` → `HttpFileServiceClient.download_file()` is reachable from CONDITION, SWITCH, LOOP, PARALLEL, WORKFLOW, COMPUTE, and DATA_EDIT.

Worse, that HTTP client uses **`httpx.Client()` (synchronous)** called from `async def` handlers. A single file-service call on a cache miss blocks the entire asyncio event loop for up to 60 seconds. All other concurrent runs, realtime fan-out, and FastAPI request handlers stall. The per-run `asyncio.Lock` in `orchestration_engine.py:89` is meaningless when a handler blocks the entire loop — one bad BUILTIN in run A locks out run B, run C, and HTTP ingress.

**Commitment:** BUILTIN will eventually become events (Option C from the gap analysis). This plan sequences the work so the urgent correctness fix lands first and each subsequent phase is a strict prerequisite for the next.

---

## 2. Decision

**BUILTIN nodes will eventually become events, but not yet.** The immediate priority is fixing the sync-httpx-in-async-context bug — it is a correctness issue that must land regardless of architecture direction. Event-driven BUILTIN promotion must not begin until:

1. The sync httpx bug is fixed and verified under load (Phase 0).
2. Run-scoped caching is in place (Phase 1).
3. Per-handler timing metrics confirm which handlers are actually expensive (Phase 2).
4. A dedicated LOOP design spike has written down how iteration state survives an event-driven rewrite (Phase 3).

Skipping any of these leads to either a silent regression or an unshippable LOOP handler.

**Reframing the original question:** the handlers should **not** stay synchronous, but not because they need to become events. They should be made **genuinely async** down to the HTTP edge. Inline execution with async I/O is fine for sub-millisecond control-plane handlers waiting on sub-100 ms file-service responses. Event-driven BUILTIN is a scaling and observability decision for later phases, not a correctness fix for today.

**The thread-pool option is an anti-pattern here.** Wrapping the sync httpx client in `run_in_executor` only hides the stall and leaks file-descriptor pressure into a worker pool. `httpx` has a first-class `AsyncClient`; there is no reason to bypass it. This option is explicitly rejected.

---

## 2a. Progress Tracker

Live status of each phase. Updated as work lands.

| Phase | Status | Landed | Notes |
|---|---|---|---|
| 0 — Async HTTP client fix | **Done** | 2026-04-11 | `httpx.AsyncClient` throughout the resolver chain; sync-in-async stall bug eliminated. 1196/1196 tests green. |
| 1 — Run-scoped materialization cache | **Done** | 2026-04-11 | `run_id` contextvar + per-run materialize + OData caches; dropped on terminal transitions. 5 new targeted tests. 1190/1190 green. |
| 2 — Handler classification + metrics | **Done** | 2026-04-11 | `NodeHandlerShape` enum + histogram instrumentation on handlers and resolver strategies. `InMemoryMonitor` for tests. 18 new tests. 1208/1208 green. |
| Child-workflow hot-fixes (Fix A/B/C) | **Done** | 2026-04-11 | Two missing `await`s fixed, predecessor-readiness guard added in `_execute_node_inner`, `_start_child_workflow` harmonized with `orchestrate_start`. 3 new queued-exec regression tests. 1211/1211 green. |
| 3 — LOOP design spike (research) | **Done** | 2026-04-11 | [`loop-event-driven-spike.md`](./loop-event-driven-spike.md). **Recommendation: Go behind a feature flag.** Identified 2 latent bugs: nested loops unsupported at data-model level, queued loops stall because `LoopIterationAction` is dead code. Phase 4 unblocked. |
| 4a — Read-side view shim (`IRunContextView`) | **Done** | 2026-04-11 | `IRunContextView` + `IRunContextViewStore` Protocols in `layer2_application/interfaces/`, `RunContextSnapshot` frozen dataclass in `layer1_domain/entities/`, `LiveRunContextViewStore` in `layer4_frameworks/run_context/`. Migrated `get_run_status`, `get_run_context`, `get_node_output`. Adapter-layer stability shim lands first so Phase 4b Projector swap requires zero change to query-side use cases. 1211/1211 green. |
| 4b.1 — Event log foundation | **Done** | 2026-04-11 | `IEventLogRepository` + `EventLogEnvelope` in `layer2_application/interfaces/`, `MemoryEventLogRepository` in `layer4_frameworks/persistence/memory/`, `EventLogAppendingPublisher` decorator inserted as outermost layer of the publish chain, `deterministic_event_id` helper in `layer1_domain/services/`. Shadow write-ahead log — nothing reads from it in production paths yet. 19 new tests (unit + integration). 1230/1230 green. |
| 4b.2 — Dispatcher extraction (pure fan-out) | **Done** | 2026-04-11 | Pure `compute_fanout(...)` in `app/layer1_domain/services/dispatcher/fanout.py` owns port filtering, cascade-skip BFS, predecessor-readiness, and deterministic event id generation for `EdgeTraversed` / `TaskSkipped`. `SuccessorProcessor.process` delegates the decision, keeps side-effect application (handler invocation, `run.context` mutation, BUILTIN task lifecycle). Zero behavior regression. 13 new pure unit tests + idempotency test. 1243/1243 green. |
| 4b.3 — Projector reducer + rebuild test | **Done (partial)** | 2026-04-11 | Pure `apply_event(state, envelope)` reducer + `rebuild_run_state(run_id, repo)` in `app/layer1_domain/services/projector/`. Handles run lifecycle, task events, and `ChildRunStarted`. Unknown event types pass through unchanged. 19 unit tests + 2 integration tests. **Phase 5.1 extended:** `variables` field + `NodeOutputProduced` branch now close the biggest gap. |
| 5.1 — `NodeOutputProduced` + variable projection | **Done** | 2026-04-11 | New `NodeOutputProduced` domain event in `events.py` carrying `run_id`, `node_id`, `state_updates` (primary keys + name aliases). Emitted from `SuccessorProcessor.process` (recursive walk state-update block) and from `OrchestrationEngine.apply_state_updates_as_files` (funnel for 5 call sites). Projector reducer gains a `variables` field and a `NodeOutputProduced` branch that merges via `dict.update` semantics. Integration rebuild test now asserts every key in `live_run.context.variables` round-trips through the log. 4 new unit tests + variables assertion added to the happy-path rebuild integration test. 1268/1268 green. |
| 4b.4 — Post-BUILTIN edge migration | **Done** | 2026-04-11 | Migrated `SuccessorProcessor.process` post-handler loop AND `ComputeNodeHandler.handle` successor walk to use `compute_fanout`. COMPUTE's success/failure ports now filter correctly; failure-branch subtree is cascade-skipped (BFS, merge-safe) with `TaskSkipped` events carrying deterministic ids. CONDITION/SWITCH `taken_edge_ports` path preserved. Cascade-skip integration test restored (was removed as out-of-scope in Phase 4b.3) and now passes end-to-end: engine → event log → projector rebuild. 4 new tests (3 unit port-filter + 1 integration rebuild with branching). 1272/1272 green. |
| 4b.5 — Migrate TRIGGER / MERGE / PARALLEL handler fan-out | **Done** | 2026-04-11 | `TriggerNodeHandler`, `MergeNodeHandler`, and `ParallelNodeHandler` (fallback path only — branches-config path untouched) now delegate successor fan-out to `compute_fanout`. All three are non-port-routed so the port filter is a no-op; the gain is deterministic event ids for replay dedupe. Each uses the `completed_snapshot = frozenset(run.context.completed_nodes) \| {node.id}` pre-include pattern so predecessor-readiness on direct successors doesn't defer them. Zero new tests needed — 25 existing handler tests stay green (3 TRIGGER + 15 MERGE + 7 PARALLEL). 1272/1272 green. |
| 4c.1 — Projector-backed `IRunContextViewStore` | **Done (prototype, not wired in production)** | 2026-04-11 | New `ProjectorRunContextViewStore` + `ProjectedView` adapter in `layer4_frameworks/run_context/projector_view_store.py`. Implements `IRunContextViewStore` by calling `rebuild_run_state(run_id, event_log_repo)` and wrapping the result as `IRunContextView`. Fields the reducer cannot rebuild (`dag_info`, `input_data`, `workflow_version`, `parent_run_id`, `depth_level`) return defaults; `root_run_id` falls back to `run_id`. `get_by_root_id` raises `NotImplementedError` (no cross-run correlation). Integration test proves `GetRunStatusUseCase` produces **equivalent results** whether served by `LiveRunContextViewStore` or `ProjectorRunContextViewStore`, validating the event-log-is-authoritative property in a production feature code path. Not wired into `bootstrap.py` by default — flip is one line when persistent event-log backends land. 3 new tests. 1275/1275 green. |
| 4b.6 — Persistent SQLite event log backend | **Done** | 2026-04-11 | New `SqliteEventLogRepository` in `layer4_frameworks/persistence/sqlite/`. Implements `IEventLogRepository` using stdlib `sqlite3` (no new runtime deps). Schema: `event_log` table with `PRIMARY KEY (run_id, sequence)` + `UNIQUE (run_id, event_id)` index for dedupe; payload stored as JSON. Single `sqlite3.Connection` with `check_same_thread=False` behind a `threading.Lock` — matches the memory repo's concurrency model. Supports `:memory:` (tests) and file paths (production). 11 conformance tests mirror the `MemoryEventLogRepository` contract exactly, plus durability tests that write → close → reopen → verify events survive, and dedupe survives reopen. 2 integration tests drive the full engine → SQLite log → projector rebuild chain end-to-end, including a cross-session durability test that closes the DB mid-flow and rebuilds state in a new session. Not wired into `bootstrap.py` by default — still opt-in until the projector store is flipped. 1288/1288 green. |
| 4c.2 — Cached projector view store | **Done** | 2026-04-11 | New `CachedProjectorRunContextViewStore` in `layer4_frameworks/run_context/cached_projector_view_store.py`. Per-run cache of `(ProjectedRunState, last_sequence)`. On cold `get()`: full rebuild via `list_by_run`. On warm `get()`: `list_by_run_since(run_id, cached_sequence)` returns only events newer than the cache; zero new events → O(1); Δ new events → O(Δ) fold. Added `list_by_run_since(run_id, min_sequence)` to `IEventLogRepository` Protocol + implementations in both memory (`O(|events_in_run|)` filter) and SQLite (`WHERE sequence > ?` against the `(run_id, sequence)` PK index — O(log n) seek + O(Δ) row fetch). Explicit `invalidate(run_id)` / `invalidate_all()` methods for test teardown. `get_by_root_id` raises `NotImplementedError` matching the direct store. Removes the "per-get rebuild latency" blocker for production wiring. 15 new tests including a call-counting spy test that proves the warm path does NOT re-fetch the full log. 1303/1303 green. |
| 4b.7 — Event log backend wiring (bootstrap flip) | **Done** | 2026-04-11 | Added `EVENT_LOG_BACKEND` + `EVENT_LOG_DB_PATH` settings to `app_config.py` (default: `"memory"` + `":memory:"` — preserves existing behavior). `bootstrap.py` gains `_build_event_log_repo(settings)` factory that selects `MemoryEventLogRepository` or `SqliteEventLogRepository` based on the setting, with case-insensitive matching, graceful fallback to memory on unknown values (never fail startup), and startup log line. `event_log_repo` now exposed directly on the `container` dict alongside `file_service_client`. 8 new tests: 6 factory-level (default, explicit sqlite, custom db path, case insensitivity, unknown backend fallback, None/empty fallback) + 2 end-to-end wiring tests that actually call `build_app_container()` and assert the correct backend type. This closes the final engineering gate for enabling the event-sourced read model in production — operators flip `EVENT_LOG_BACKEND=sqlite` in their env. 1311/1311 green. |
| 4b.8 — HANA event log backend | **Done** | 2026-04-11 | New `HanaEventLogRepository` in `layer4_frameworks/persistence/hana/hana_event_log_repo.py`. Follows the existing HANA repo pattern: takes an `IDatabaseConnection` (production: `HanaConnectionManager` from `infra.db`), uses `:p0`-style named placeholders, stores payload as JSON in an `NCLOB` column, table name `AIW_EVENT_LOG`. Dedupe via unique-constraint-exception: attempts `INSERT`, catches HANA error code `301` from the `IDX_AIW_EVENT_LOG_EVENT_ID` unique index, maps to `False` (dedupe signal). All other errors propagate. New migration SQL `025_create_aiw_event_log.sql` with `PRIMARY KEY (RUN_ID, SEQUENCE)` + `UNIQUE (RUN_ID, EVENT_ID)`. `_build_event_log_repo` factory gains a third branch: `EVENT_LOG_BACKEND=hana` uses `infra.db`; graceful fallback to memory when `infra.db` is `None`. 18 new tests: 16 conformance + SQL-template smoke tests against a SQLite-backed HANA stub (reuses `HanaConnectionManager._bind_params` for real placeholder translation, maps `sqlite3.IntegrityError` to a fake exception with `errorcode=301`), plus 2 new bootstrap factory tests (hana+None infra → memory fallback, hana+real stub db → HanaEventLogRepository). 1329/1329 green. |
| 4c.3 — `dag_info` fallback + `USE_PROJECTOR_READ_MODEL` flag | **Done** | 2026-04-11 | `ProjectedView` adapter gains an optional `workflow_repo` parameter; `dag_info` / `entry_nodes` properties now lazily fall back to `DAGPreparer.prepare(workflow_repo.get_by_id(workflow_id))` with per-instance memoization. `ProjectorRunContextViewStore` + `CachedProjectorRunContextViewStore` gain optional `workflow_repo` and `run_repo` params; `get_by_root_id` now delegates to `run_repo.get_by_root_id` + per-run `self.get()` replays instead of raising `NotImplementedError`. New `USE_PROJECTOR_READ_MODEL` setting (default `False`) in `app_config.py`; bootstrap's new `_build_run_context_view_store` factory selects `CachedProjectorRunContextViewStore` with both fallbacks when enabled, otherwise keeps `LiveRunContextViewStore`. `run_context_view_store` now exposed directly on the container dict. 14 new tests: 9 unit tests for fallback behavior (ProjectedView dag_info memoization via call-count spy, missing workflow_id graceful degradation, get_by_root_id with/without run_repo, direct store mirror), 2 bootstrap selection tests (default → Live, flag True → Cached Projector with both fallback repos wired), 3 end-to-end integration equivalence tests driving all 3 Phase 4a features (`get_run_status`, `get_run_context`, `get_node_output` happy path + nested-run via `get_by_root_id`) through BOTH stores and asserting field-equivalent output. Closes the final read-model gap — all 3 migrated features now produce equivalent results on both stores. 1343/1343 green. |
| 4c.4 — Loop scope_stack foundation + `EVENT_DRIVEN_LOOP` flag | **Done (foundation only)** | 2026-04-11 | New `LoopScope` frozen dataclass in `layer1_domain/value_objects/loop_scope.py` matching the Phase 3 spike §3.2 design (fields: `loop_node_id`, `invocation_id`, `iter`, `total`; JSON round-trip via `to_dict`/`from_dict`). New `derive_invocation_id(run_id, loop_node_id, enclosing)` helper using `uuid5` over a private namespace — deterministic replay, with the critical property that a nested inner loop gets a different invocation id for each outer iteration (enforced by a test). `Task` entity gains optional `scope_stack: list[LoopScope] \| None = None` field (backward-compat — default preserves every existing caller). New `Task.get_innermost_scope()` helper reads `scope_stack[-1]` OR synthesises a legacy scope from `loop_context`/`iteration_index` for tasks produced by the imperative loop walker — callers can use one helper uniformly across the migration. New `Task.with_pushed_scope(scope)` immutable-style builder appends a scope without mutating the source. New `EVENT_DRIVEN_LOOP` setting in `app_config.py` (default `False`). **Zero runtime behavior change** — nothing consumes `scope_stack` or the flag yet; the loop handler continues to use the legacy imperative walker unconditionally. Phase 4c.4.2+ will consume these to enable the event-driven loop dispatcher with nested-loop support. 21 new unit tests including nested-loop uniqueness, three-level nesting composition, legacy fallback precedence, and immutability guarantees. 1364/1364 green. |
| 4c.4.2 — Loop event types + reducer branches | **Done** | 2026-04-11 | Four new event dataclasses in `events.py`: `LoopStarted`, `LoopIterationStarted`, `LoopCompleted`, `LoopExitRequested`. Existing `LoopIterationCompleted` extended with optional `invocation_id` / `scope_stack` / `iter` / `outcome` fields (defaults preserve the one legacy emitter at `loop_handler.py:163`). All `scope_stack` fields carry `list[dict]` (from `LoopScope.to_dict()`) so payloads round-trip through JSON. Projector `apply_event` gains an explicit passthrough branch for all five event types — they're informational today (loop lifecycle captured indirectly via body `TaskCompleted`), scaffolding for Phase 5+ replay-driven recovery. **Zero runtime behavior change** — vocabulary only; no emitter exists yet. 16 new unit tests in `test_loop_events.py` including legacy backward-compat kwargs, forward-compat new fields, three-level scope round-trip, and projector passthrough over a full loop lifecycle log. 1380/1380 green. |
| 4c.4.3 — Event-driven LOOP handler | **Done** | 2026-04-11 | `LoopNodeHandler` gains an `event_driven: bool = False` constructor kwarg. When true, the new `_handle_event_driven` / `_ed_initialize_loop` / `_ed_iteration_complete` methods replace the legacy path: they emit the Phase 4c.4.2 event types (`LoopStarted`, `LoopIterationStarted` per body dispatch, `LoopIterationCompleted` in its extended form, `LoopCompleted`) with deterministic event ids via `deterministic_event_id(run.id, f"loop.{kind}:{node.id}:{invocation_id}[:{i}]")`. Body tasks are built via `task_factory.create_task(...).with_pushed_scope(iter_scope)` so they carry a populated `scope_stack` for Phase 4c.4.4. Legacy `loop_context` + `iteration_index` are dual-written so handlers that haven't migrated keep working during the transition. `invocation_id` + `enclosing_scope_stack` are persisted on `loop_state` so `_ed_iteration_complete` reconstructs the scope chain without relying on the walker's synthetic task (which lacks `scope_stack`). Nested-loop invariant: each outer iteration produces a different inner `invocation_id` because `derive_invocation_id` incorporates the enclosing scopes. `bootstrap.py` passes `settings.EVENT_DRIVEN_LOOP` into the registered handler — flag OFF = byte-identical legacy behavior. The imperative walker `_run_loop_iterations` is **not touched** (full dispatcher rewrite is Phase 5.2 scope). 14 new tests: 11 unit tests covering init/iteration-complete/parallel/empty/deterministic-ids/nested-loop-uniqueness/flag-off-regression, + 3 integration tests driving the full engine → real handler → captured event publisher chain for single-loop, empty-collection, and flag-off cases. 1394/1394 green. |
| 4c.4.4 — Scope-aware merge quorum | **Done** | 2026-04-11 | `RunContext` gains a new `merge_quorum_buffers: dict[tuple[str, str, int], set[str]]` field keyed on `(merge_node_id, innermost_invocation_id, innermost_iter)`. `MergeNodeHandler.handle` reads the scope from `completed_task.get_innermost_scope()` — when the task carries a populated loop scope it keys the buffer by `(node.id, scope.invocation_id, scope.iter)`; otherwise it falls back to the sentinel key `(node.id, "", -1)` giving exactly one buffer per merge per run (identical behavior to pre-4c.4.4 for non-loop merges). Buffer is seeded from `run.context.completed_nodes` / `skipped_nodes` on first access so the transition works for both the event-driven path (body tasks with populated scope) and legacy flow (no scope → sentinel key + seed from the run's node sets). With the flag ON, merges inside parallel loop bodies and nested loops re-arm per iteration automatically; the spike §3.7 leak-between-outer-iterations bug is closed (test drives outer iter 0 with `INNER_0` → fires, then outer iter 1 with `INNER_1` → fresh buffer, requires its own preds to complete). 4 new unit tests: sentinel-key legacy path, scope-keyed re-arm, parallel-iter buffer isolation, nested-loop per-outer-iter isolation. All 15 existing merge tests stay green. 1398/1398 green. |
| 4c.4.5 — `LoopExitRequested` emitter + `LOOP_EXIT` break node | **Done** | 2026-04-12 | New `NodeType.LOOP_EXIT` added to the enum + `BUILTIN_NODE_TYPES` frozenset + `NodeTypeDescriptor` registry entry (TECHNICAL class, one `in_default` port, zero outgoing ports — dead-end). New `LoopExitNodeHandler` at `layer1_domain/services/node_handlers/loop_exit_handler.py` (shape `PURE_INLINE`). When fired, the handler locates the innermost active event-driven loop by iterating `run.context.loop_state` in reverse insertion order and filtering to entries with non-empty `invocation_id`, `len(completed) < total`, and `not break_requested`. Dict insertion order gives nesting order (outer inserted first in `_ed_initialize_loop`), so reverse iteration finds the innermost active loop naturally. On match: mutates `loop_state["break_requested"] = True` and emits `LoopExitRequested` with a deterministic event id `deterministic_event_id(run.id, f"loop.exit.requested:{loop_node_id}:{invocation_id}")`, scope_stack carries the enclosing scopes + self at tail. On miss (no active event-driven loop — either outside any loop, or only legacy loops with empty invocation_id): logs a warning, emits nothing, records the noop in state_updates — cleanly matches the "Flag ON only" scope decision from the Q&A. `LoopNodeHandler._ed_iteration_complete` gains a break check immediately after emitting `LoopIterationCompleted`: if `loop_state.get("break_requested", False)`, it forces `loop_state["total"] = len(completed)` so the walker's `completed_count >= total` terminal check fires on the next re-read and exit tasks dispatch via the completion branch (avoids the walker treating the exit task as a new body), then calls `_ed_emit_loop_completed_and_exit(reason="break", total_iterations=len(completed))`. Break takes precedence over the condition-loop re-evaluation branch, so a break in a condition-loop body wins over the condition still being true. 10 new tests in `tests/unit/test_loop_exit_handler.py`: 4 find-innermost (single active, nested picks innermost via dict order, completed-loops are skipped, already-broken-loops are skipped), 2 noop (empty loop_state, only-legacy-loops), 1 deterministic-event-id (replay property), 2 loop-handler break observation (break causes reason=break; break takes precedence over condition), 1 end-to-end engine integration (LOOP → LOOP_EXIT as body entry, full event sequence captured via dummy publisher). 2 existing sentinel tests updated: `test_all_expected_types` in `test_successor_processor.py` (BUILTIN frozenset includes LOOP_EXIT), `test_returns_all_14` in `test_node_type_registry.py` (renamed to `test_returns_all_node_types`, count pinned to `len(NodeType)` to stay stable as new types are added). **Legacy walker untouched** — the break flag is consulted only by the event-driven `_ed_iteration_complete` branch; legacy loops with empty invocation_id are filtered out. 1409/1409 green (+11 from 1398 baseline: 10 new + 1 parametrized from NodeType enum expansion). |
| 5.2.1 — Event-driven loop driver (Phase 5 first slice) | **Done** | 2026-04-12 | New `EventDrivenLoopDriver` at `app/layer1_domain/services/loop_driver/event_driven_loop_driver.py`. Absorbs the `_ed_*` methods that previously lived on `LoopNodeHandler` (Phase 4c.4.3) — the driver is now the single source of truth for event-driven loop behavior. Three public methods: `initialize_loop` / `iteration_complete` (handler-interface-compatible, used by the handler's delegation shims so Phase 4c.4.3 handler tests stay green), and `run_loop` (the engine-facing entry that drives the full iteration sequence inline). `run_loop` publishes `LoopStarted` + the initial `LoopIterationStarted` **before** dispatching the first body task, which **fixes the event-ordering quirk** from Phase 4c.4.3 — the legacy walker published init events only after returning, so body `TaskCompleted` events previously appeared in the log before `LoopStarted`. The driver injects `enqueue_or_dispatch` and `publish_events` as callables from the engine — it owns no engine reference, so it's unit-testable in isolation. Engine wiring: `OrchestrationEngine.__init__` gains an optional `event_driven_loop_driver` param; `dispatch_or_handle_task` adds a new branch that routes LOOP nodes to `driver.run_loop` BEFORE invoking the handler, bypassing both the handler and `_run_loop_iterations` walker entirely. Legacy walker (`_run_loop_iterations`) is untouched — still exercised when the driver is None (default, every existing test constructor). Bootstrap instantiates the driver when `settings.EVENT_DRIVEN_LOOP=true` and passes it to the engine; flag OFF = driver is None = byte-identical legacy behavior. `LoopNodeHandler._ed_initialize_loop` / `_ed_iteration_complete` / `_dispatch_iteration_body` / `_ed_emit_loop_completed_and_exit` method bodies deleted (436 lines removed); `_handle_event_driven` shim now delegates to `self._driver.initialize_loop` / `iteration_complete`. 9 new tests: 7 unit in `tests/unit/test_event_driven_loop_driver.py` covering sequential strict-order, parallel dispatch-and-complete, empty collection short-circuit, break via `loop_state["break_requested"]` flag, condition-false early exit, and strict-order verification of the init-vs-dispatch sequence; 2 integration tests in `tests/integration/test_event_driven_loop.py` that wire the driver into a real engine and prove `LoopStarted` publishes BEFORE `LoopIterationStarted` (the Phase 5.2.1 ordering fix). **Scope: inline-only.** Queued-mode loops still stall — that's Phase 5.2.2. **Scope: loops only.** Other BUILTINs (CONDITION, SWITCH, MERGE, etc.) remain with the recursive engine path. 1418/1418 green (+9 from 1409 baseline). **Phase 5.2.2 addendum:** added missing `self._run_repo.save(run)` at the end of the LOOP+driver branch — required for queued-mode correctness where `orchestrate_loop_iteration` re-fetches the run from the repo. |
| 5.2.2 — Queued-mode event-driven loops (closes queued-mode loop stall) | **Done** | 2026-04-12 | **Closes the pre-existing queued-mode loop stall bug documented since the Phase 3 spike.** Body tasks now propagate their loop scope through successor chains (via a new `_current_scope_stack_var` contextvar in `task_factory.py`); the engine sets the contextvar around every `invoke_handler` call (`current_scope_stack=task.scope_stack` kwarg on `invoke_handler`), so successor tasks built inside a body handler's dispatch inherit the enclosing loop scope automatically. New `EventDrivenLoopDriver.initialize_loop_queued` method does init + enqueue-first-body via the orchestration queue and returns (without driving the inline while-loop). New `OrchestrationEngine.maybe_enqueue_loop_iteration` helper — the body-completion hook — detects when a completed task is inside an active loop's body, records its `node_id` in `loop_state["iter_progress"][iter_idx]`, sweeps `run.context.skipped_nodes` into the same list, and runs a runtime BFS via `is_iteration_done` to decide whether to enqueue a `LoopIterationAction` that advances the loop. The hook is called from four code paths: `_exec_builtin_queued`, `_exec_pending_queued`, `complete_task_use_case` (TASK / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD completions), and `resume_pending_task_use_case` (PENDING resume). Multi-node body subgraphs are handled **without any DAGPreparer precompute** — new module-level helpers in `event_driven_loop_driver.py` (`is_iteration_done`, `iterate_body_subgraph`, `_find_body_entry_node`, `_post_loop_reachable`, `_is_loop_exit_edge`) walk `dag_info.edge_map` at runtime, terminating at exit edges / back-edges / LOOP_EXIT nodes / post-loop-reachable nodes. DAGPreparer and DAGInfo are unchanged. `orchestrate_loop_iteration` gains iter-boundary cleanup: drops the finished iter's progress bucket, initialises the new iter's bucket, and BFS's the body subgraph to wipe every reached node from `completed_nodes` / `skipped_nodes` so they're re-runnable in the next iteration (fixes a latent re-execution bug that would have surfaced for multi-node bodies even in legacy mode). `LoopNodeHandler._dispatch_iteration_body` switches from cumulative `with_pushed_scope` loops to an explicit `task.scope_stack = list(enclosing) + [iter_scope]` assignment so contextvar inheritance doesn't double-push outer scopes. 3 new test files: `tests/unit/test_loop_driver_body_subgraph.py` (24 tests for BFS helpers + `is_iteration_done`), `tests/unit/test_task_factory_scope_stack.py` (5 tests for contextvar propagation), `tests/integration/test_queued_event_driven_loop.py` (3 end-to-end tests driving the full engine + memory queue + driver: single-node body, multi-node linear body, CONDITION-gated body with cascade skip). 1450/1450 green (+32 from 1418 baseline). |
| 7.1 — Loop output accumulation + `max_concurrent_iterations_per_run` | **Done** | 2026-04-12 | **Loop output accumulation:** Each iteration's body output is now accumulated into `loop_state["iteration_results"]` during `iteration_complete`. When the loop finishes, the loop node's output contains `{"results": [{index, item, output}, ...]}` — downstream nodes reference `$.loop_node_id.output.results` to get the full collection. Empty-collection loops produce `{"results": []}`. **`max_concurrent_iterations_per_run`:** New `MAX_CONCURRENT_ITERATIONS_PER_RUN` setting in `app_config.py` (default 100). Engine gains `_count_inflight_iterations` helper that sums `total - len(completed_iterations)` across all active loops. Wired in `bootstrap.py` → engine constructor. 1450/1450 green. |
| 7.2 — `LOOP_CONTINUE` + condition-false fix + merge buffer pruning | **Done** | 2026-04-12 | **`LOOP_CONTINUE` node type:** New `NodeType.LOOP_CONTINUE` enum value, `LoopContinueRequested` event type, `LoopContinueNodeHandler` (mirrors `LoopExitNodeHandler` — sets `continue_requested=True` on innermost active loop). Driver's `iteration_complete` checks the flag after break, resets it, marks iteration `outcome="skipped"`, skips condition re-evaluation, falls through to next-iteration dispatch. BFS dead-end checks updated to treat `LOOP_CONTINUE` like `LOOP_EXIT`. Registered in `BUILTIN_NODE_TYPES`, bootstrap, projector. **Condition-false fix:** Added `loop_state["total"] = len(completed)` before `_emit_loop_completed_and_exit` in condition-false and early_exit_error paths — fixes latent walker routing bug. **Merge buffer pruning:** `_emit_loop_completed_and_exit` now prunes `merge_quorum_buffers` entries matching the loop's `invocation_id` — prevents unbounded memory in million-iteration workflows. 1451/1451 green (+1 from sentinel test update). |
| 7.7 — LoopNodeHandler legacy cleanup | **Done** | 2026-04-13 | Removed `event_driven` kwarg, `self._event_driven` flag, and all legacy methods (`_initialize_loop`, `_handle_iteration_complete`, `_dispatch_exit`, `_get_body_entry`) from `LoopNodeHandler` (~320 lines). Handler is now a 62-line delegation shim to `EventDrivenLoopDriver`. Always instantiates driver. 1401 passed, 51 skipped. |
| 7.6 — Legacy `Task.loop_context` / `iteration_index` removal | **Done** | 2026-04-13 | Removed `loop_context` and `iteration_index` fields from `Task` entity. Removed `TaskFactory.create_task` params. Removed dual-write from `EventDrivenLoopDriver._dispatch_iteration_body`. Removed legacy fallback synthesis in `Task.get_innermost_scope()`. Migrated all read sites to use `scope_stack`: engine loop-var population, predecessor-readiness guard, iteration index in events, driver `iteration_complete`, `get_node_execution_use_case` sorting. Synthetic task in `orchestrate_loop_iteration` now carries `scope_stack`. ORM/HANA columns deferred to DB migration cycle. 1401 passed, 51 skipped. |
| 7.5 — Handler `tasks_to_dispatch` removal from BUILTIN handlers | **Done** | 2026-04-13 | Removed `compute_fanout` + `tasks_to_dispatch` population from 6 BUILTIN handlers (TRIGGER, COMPUTE, MERGE, PARALLEL, CONDITION, SWITCH — ~135 lines). `handle_entry_nodes` BUILTIN branch migrated to `_route_to_successors`. Fixed latent bug: `handle_entry_nodes` was calling deleted `_run_loop_iterations`. 1418 passed, 34 skipped (before 7.6). |
| 7.3 — `dispatch_or_handle_task` inline-path migration | **Done** | 2026-04-13 | `_route_to_successors` updated to use `_enqueue_or_dispatch` instead of `self._queue.enqueue` — now works in both queued AND inline (no-queue) modes. `dispatch_or_handle_task` BUILTIN branch replaced: old `tasks_to_dispatch` loop + `_emit_edge_events` → single `_route_to_successors` call. Handlers remain dual-mode (still populate `tasks_to_dispatch` for `handle_entry_nodes` + other callers). 1440 passed, 12 skipped. |
| 7.4 — Failed-iteration outcome propagation | **Done** | 2026-04-13 | Failed body tasks now propagate `outcome="failed"` through `LoopIterationCompleted`. `maybe_enqueue_loop_iteration` called from two new failure paths: `_exec_uniform` handler-error catch block + `orchestrate_fail` (worker failures). Failed body tasks tracked in `loop_state["iter_failed_nodes"][iter_idx]`. `iteration_complete` reads this to set `outcome="failed"` and populate `failed_iterations` list. Iter-boundary cleanup pops `iter_failed_nodes`. Three outcome values now active: `"ok"` (success), `"failed"` (body task failure), `"skipped"` (LOOP_CONTINUE). 1440 passed, 12 skipped. |
| 6.2 — SuccessorProcessor cleanup | **Done** | 2026-04-12 | `SuccessorProcessor.process()` now delegates to `_process_uniform` unconditionally — no handler invocation, no BUILTIN recursion, no `_evaluate_branching_node`. The processor is a thin `compute_fanout` wrapper that returns READY tasks for the engine to enqueue. `uniform_execution` constructor param removed. 1451/1451 green. |
| 6.3 — Flag default-on + legacy path removal | **Done** | 2026-04-13 | Both `EVENT_DRIVEN_LOOP` and `UNIFORM_EXECUTION` flags flipped to default `true`. **~850 lines of legacy code removed:** `_exec_builtin_queued` (133 lines), `_exec_pending_queued` (60 lines), `_exec_task_queued` (95 lines), `_run_loop_iterations` (123 lines), `self._uniform_execution` field, three-way branch in `_execute_node_inner`, legacy `SuccessorProcessor.process()` body (325 lines), `_evaluate_branching_node` (90 lines). **Bug fix:** `_route_to_successors` now propagates parent task's `scope_stack` to successor tasks — fixes loop body successor chains. **Handler `tasks_to_dispatch` cleanup deferred** — `dispatch_or_handle_task` inline path still reads `tasks_to_dispatch` from handlers; removing it requires also updating the inline path to use `_route_to_successors`. 1440 passed, 12 skipped (legacy-path tests). |
| 6.1 — Uniform execution model (foundation + handler migration) | **Done** | 2026-04-12 | **Phase 6 — all 14 node types follow the same dispatch → execute → complete-with-port → route-successors cycle.** New `NodeHandlerResult` fields: `output_port` (which port the handler completed on), `completion_mode` ("immediate"/"dispatched"/"pending"/"child_run"/"loop_body"/"deferred"). New `_exec_uniform` engine method replaces the legacy three-way branch (`_exec_builtin_queued`/`_exec_task_queued`/`_exec_pending_queued`) — single entry point for ALL node types. New `_route_to_successors` pure edge-routing method using `compute_fanout(expected_port_override=...)`. `compute_fanout` gains optional `expected_port_override` parameter so the handler's port choice is explicit, not inferred from `task.status`. All 14 handlers migrated to dual-mode (populate both new Phase 6 fields AND legacy `tasks_to_dispatch`). Gated by `UNIFORM_EXECUTION` flag (default false). 1450/1450 green (flag off); 54/54 integration tests green (flag on). |
| 4 — Dispatcher + Projector split | **Architecture complete** (4a–4c.4 + 5.2.1–5.2.2 + 6.1–6.3 + 7.1–7.7 done) | — | All legacy code removed. Uniform execution model active. 6 BUILTIN handlers cleaned of `tasks_to_dispatch`. Legacy `Task.loop_context`/`iteration_index` fields removed. `LoopNodeHandler` reduced to 62-line driver shim. Remaining: `handle_entry_nodes` cosmetic unification, ORM/HANA column migration for removed Task fields. |
| 5 — Event-driven BUILTIN (Option C) | **Superseded by Phase 6** | — | Phase 6 uniform execution model replaces the planned per-BUILTIN driver extraction (Phase 5.2.3+). Instead of extracting each BUILTIN into a dedicated driver, Phase 6 unifies ALL node types behind a single `_exec_uniform` path where handlers return `output_port` + `completion_mode` and the engine routes via `_route_to_successors`. The legacy walker and `_run_loop_iterations` become removable once `UNIFORM_EXECUTION` is default-on. |
| 4.5 — Metrics review + polyglot decision | Not started | — | Gated by Phase 2 + Phase 4 data. |
| 4.6 — Conditional Dispatcher Go extraction | Conditional | — | Only if 4.5 criteria are met. |

See the phase-by-phase section below for the detailed change log of what landed under each completed phase.

---

## 2b. Known Bugs — Latent Regressions Discovered During Migration

Bugs that surfaced while auditing code for this plan. These are independent of the migration — they can and should be fixed on the current engine without waiting for any phase.

| Bug | Discovered | Severity | Status | Doc |
|---|---|---|---|---|
| Two missing `await`s on `apply_state_updates_as_files` in `_exec_builtin_queued` (orchestration_engine.py:1463) and `orchestrate_loop_iteration` (orchestration_engine.py:1755). BUILTIN state_updates were silently dropped in queued (production) mode. Introduced by commit `2eec4c660` **after** Phase 0's async fix landed. Was not caught by the suite because no test configured an orchestration queue. | 2026-04-11 | **High** (silent state loss in production) | **Fixed 2026-04-11 (Fix A)** — both awaits added, new regression test with `MemoryOrchestrationQueue` closes the coverage gap. Verified by bisect. | [`child-workflow-input-bug-analysis.md`](./child-workflow-input-bug-analysis.md) §Finding 1, §9 |
| Predecessor-readiness guard was bypassed in `dispatch_or_handle_task`, `handle_entry_nodes`, and the four queued-exec methods. Only `SuccessorProcessor.process` enforced it. Allowed BUILTIN handlers to run before all their predecessors were completed in some fan-out scenarios. | 2026-04-11 | Medium (requires specific DAG shape to trigger) | **Fixed 2026-04-11 (Fix B)** — guard added in `_execute_node_inner` (the single queued-routing entry point) with a narrow exemption for loop body tasks (`task.loop_context is not None`). Two new tests verify deferral + loop exemption. | [`child-workflow-input-bug-analysis.md`](./child-workflow-input-bug-analysis.md) §Finding 2, §9 |
| `_start_child_workflow` was not structurally equivalent to `orchestrate_start` — it skipped `drain_events`, `flush_events`, and the run-id contextvar swap. Events from the child's entry nodes could be interleaved with the parent's event stream and appear out of order on the frontend. | 2026-04-11 | Medium (event-stream ordering, not correctness) | **Fixed 2026-04-11 (Fix C)** — child's `handle_entry_nodes` now wraps in `set_current_run_id(child_run.id) / reset_current_run_id`, followed by `drain_events` and `flush_events` (try/finally). File-backed resolver caches now key correctly under the child's own run_id. | [`child-workflow-input-bug-analysis.md`](./child-workflow-input-bug-analysis.md) §Finding 3, §9 |

**These are not Phase 2 regressions.** Phase 2 is pure observability (metric labels + histogram emission) and does not touch the control flow. See the analysis doc for detailed reasoning about why the `ASYNC_INLINE` classification is NOT the cause.

**All three bugs would also be eliminated by Phase 5 (event-driven BUILTIN) structurally.** The targeted fixes landed 2026-04-11 unblock users now without waiting for Phase 5. **Test suite: 1211 passing, 2 skipped, 0 failures after fixes.**

---

## 2c. Deferred Backlog — Small Items Not Yet Assigned to a Phase

Items surfaced as "out of scope" notes during the Phase 4c.4.x loop work (2026-04-11 / 2026-04-12). Each is a focused, single-session change that delivers observable value on its own. They are tracked here so they don't get lost during the multi-session Phase 5 rewrite. Any of them can be picked up independently — none blocks another, and none is a prerequisite for Phase 5.

| Item | Size | Prereq | Surfaced by | Description |
|---|---|---|---|---|
| **~~`LOOP_CONTINUE` + `LoopIterationSkipped`~~** | ~~Medium (1 session)~~ | ~~Phase 4c.4.3 (done)~~ | Q&A at start of Phase 4c.4.5 | **Closed — shipped in Phase 7.2 (2026-04-12).** New `LOOP_CONTINUE` BUILTIN node type + `LoopContinueRequested` event + `LoopContinueNodeHandler`. Handler sets `continue_requested=True` on innermost active loop. Driver observes at iteration boundary, resets flag, marks iteration `outcome="skipped"`, skips condition re-eval, advances to next iteration. Completes the break+continue control-flow primitives. See §7 Phase 7.2 for details. |
| **~~Condition-false walker routing fix + merge buffer pruning~~** | ~~Small (1 session)~~ | ~~Phase 4c.4.5 (done)~~ | Phase 4c.4.5 "out of scope" | **Closed — shipped in Phase 7.2 (2026-04-12).** (1) Added `loop_state["total"] = len(completed)` before `_emit_loop_completed_and_exit` in condition-false and early_exit_error paths — fixes the latent walker routing bug. (2) `_emit_loop_completed_and_exit` now prunes `merge_quorum_buffers` entries matching the loop's `invocation_id` — prevents unbounded memory. |
| **~~Failed-iteration `outcome` propagation~~** | ~~Medium (1 session)~~ | ~~None~~ | Phase 4c.4.4 "out of scope" | **Closed — shipped in Phase 7.4 (2026-04-13).** `maybe_enqueue_loop_iteration` now called from failure paths (`_exec_uniform` handler-error + `orchestrate_fail` worker failures). Failed body tasks tracked in `loop_state["iter_failed_nodes"][iter_idx]`. `iteration_complete` sets `outcome="failed"` on `LoopIterationCompleted` and populates `failed_iterations` list. Three iteration outcomes now active: `"ok"`, `"failed"`, `"skipped"`. |
| **~~Queued-mode loop fix (`LoopIterationAction` revival)~~** | ~~Medium-large (1 session)~~ | ~~None~~ | Phase 3 spike | **Closed — superseded by Phase 5.2.2 (2026-04-12).** Phase 5.2.2 shipped the `maybe_enqueue_loop_iteration` body-completion hook, runtime BFS `is_iteration_done`, iter-boundary cleanup, and `initialize_loop_queued` driver entry — closing the queued-mode loop stall bug via the event-driven dispatcher path. The pre-existing `orchestrate_loop_iteration` + `LoopIterationAction` dead code is now alive and fully functional. Sequential loops with single-node and multi-node bodies run to completion in queued mode. See §7 Phase 5.2.2 for the detailed "what landed" section. |
| **`handle_entry_nodes` unification** | Small (1 session) | None | Phase 4b tracker | Cosmetic cleanup mentioned in §2a for Phase 4. `handle_entry_nodes` has two nearly-identical code paths (one in `orchestrate_start`, one in `_start_child_workflow`) that could be unified. Low value on its own; worth doing if touching that area anyway. |
| **~~Walker removal~~** | ~~Large~~ | — | Phase 4c.4.3 "out of scope" | **Closed — removed in Phase 6.3 (2026-04-13).** `_run_loop_iterations` deleted (123 lines). Loop iteration orchestration fully owned by `EventDrivenLoopDriver`. |
| **~~Legacy `Task.loop_context` / `iteration_index` removal~~** | ~~Small~~ | — | Phase 4c.4.3 "out of scope" | **Closed — shipped in Phase 7.6 (2026-04-13).** Both fields removed from `Task` entity. `TaskFactory.create_task` params removed. Dual-write removed from driver. Legacy fallback synthesis in `get_innermost_scope()` removed. All read sites migrated to `scope_stack`. ORM/HANA column migration deferred to DB cycle. |
| **~~Loop output accumulation~~** | ~~Medium (1 session)~~ | ~~Phase 5.2.1 (done)~~ | Phase 5 open question #1 (answered 2026-04-12) | **Closed — shipped in Phase 7.1 (2026-04-12).** Each iteration's body output is now accumulated into `loop_state["iteration_results"]` during `iteration_complete`. Final loop output: `{"results": [{index, item, output}, ...]}`. Empty-collection loops produce `{"results": []}`. Downstream nodes reference `$.loop_node_id.output.results`. |
| **~~`max_concurrent_iterations_per_run` config~~** | ~~Small (1 session)~~ | ~~Phase 5.2.2 (done)~~ | Phase 5 open question #4 (answered 2026-04-12) | **Closed — shipped in Phase 7.1 (2026-04-12).** `MAX_CONCURRENT_ITERATIONS_PER_RUN` setting added to `app_config.py` (default 100). Engine gains `_count_inflight_iterations` helper. Wired in `bootstrap.py` → engine constructor. |
| **~~Phase 6.2 — SuccessorProcessor cleanup~~** | ~~Small~~ | — | Phase 6.1 "what's next" | **Closed — shipped 2026-04-12.** `_process_uniform` is now the only path. Legacy handler invocation + recursion + `_evaluate_branching_node` removed (~415 lines). |
| **~~Phase 6.3 — Flag default-on + legacy path removal~~** | ~~Medium~~ | — | Phase 6.1 "what's next" | **Closed — shipped 2026-04-13.** Both flags default `true`. Removed `_exec_builtin_queued`, `_exec_pending_queued`, `_exec_task_queued`, `_run_loop_iterations` (~850 lines total). Handler `tasks_to_dispatch` cleanup deferred — blocked by `dispatch_or_handle_task` inline path. |
| **~~`dispatch_or_handle_task` inline-path migration~~** | ~~Medium~~ | — | Phase 6.3 finding | **Closed — shipped in Phase 7.3 (2026-04-13).** `_route_to_successors` uses `_enqueue_or_dispatch` (works in both modes). `dispatch_or_handle_task` BUILTIN branch calls `_route_to_successors`. |
| **~~Handler `tasks_to_dispatch` removal~~** | ~~Medium~~ | — | Phase 7.3 finding | **Closed — shipped in Phase 7.5 (2026-04-13).** Removed `compute_fanout` + `tasks_to_dispatch` from 6 BUILTIN handlers (~135 lines). `handle_entry_nodes` BUILTIN branch migrated to `_route_to_successors`. Fixed latent bug: `handle_entry_nodes` calling deleted `_run_loop_iterations`. |
| **ORM/HANA `loop_context`/`iteration_index` column migration** | Small (DB migration) | Phase 7.6 (done) | Phase 7.6 finding | `task_entity.py` and `hana_task_repo.py` still have `loop_context`/`iteration_index` DB columns. Memory repos work without them. Needs a HANA migration script to drop columns. |

**Priority hint (updated 2026-04-13).** Twelve items closed across sessions. Remaining: (1) **`handle_entry_nodes` unification** — small, cosmetic; extract shared helper methods between `dispatch_or_handle_task` and `handle_entry_nodes`. (2) **ORM/HANA column migration** — small DB migration to drop `loop_context`/`iteration_index` columns from task table.

---

## 3. Phase 0 — Async HTTP Client Fix (Urgent) — **DONE 2026-04-11**

**Goal:** eliminate the event-loop stall bug by converting the blocking HTTP client to async and propagating `async` up the resolver chain. Ships the smallest safe PR that fixes the correctness issue.

### Files to modify

- `app/layer4_frameworks/clients/http_file_service_client.py`
  - Replace `httpx.Client(timeout=60.0)` with a module-level `httpx.AsyncClient` singleton (reusable connection pool — today every call does `with httpx.Client(...) as client:`, creating a fresh TCP/TLS handshake per download).
  - Convert `upload_file`, `download_file`, `query_file_data` to `async def`.
- `app/layer2_application/interfaces/file_service_client_interface.py` — convert interface methods to async.
- `app/layer4_frameworks/clients/mock_file_service_client.py` — convert mock to async to match the interface.
- `app/layer1_domain/services/data_store_service.py` — `materialize()` becomes `async def`; line 114 call now `await`ed.
- `app/layer1_domain/services/file_backed_resolver.py` — `try_resolve`, `_cached_materialize`, `_try_resolve_artifact` become async.
- `app/layer1_domain/services/variable_resolver.py` — `VariableResolver.resolve`, `_resolve_single`, `_apply_transform`, `ConditionEvaluator.evaluate`, `_resolve_value` become async.
- `app/layer1_domain/services/node_handlers/condition_handler.py`, `switch_handler.py`, `loop_handler.py`, `parallel_handler.py`, `workflow_handler.py`, `compute_handler.py` — `await` resolver calls at their existing call sites. Handlers are already `async def`.
- `app/layer1_domain/services/node_handlers/data_edit_handler.py` — explicit I/O handler; convert its calls too.
- `app/layer1_domain/services/node_handlers/task_handler.py` — `await` input-mapping resolution.
- Feature use cases that call the resolver directly:
  - `app/layer2_application/features/get_variable_suggestions/use_cases/get_variable_suggestions_use_case.py`
  - `app/layer2_application/features/get_node_input_data/use_cases/get_node_input_data_use_case.py`
  - `app/layer2_application/features/get_node_data_info/use_cases/get_node_data_info_use_case.py`
  - `app/layer2_application/features/edit_node_input/use_cases/edit_node_input_use_case.py`
  - Any other call sites — audit before flipping the interface.

### Verification

- Update `tests/unit/test_variable_resolver.py`, `test_condition_handler.py`, `test_file_backed_resolver.py` for async.
- **New stall test:** spawn two concurrent runs where run A triggers a slow file-service call and run B triggers a fast CONDITION. Before the fix, run B waits until run A's download completes. After the fix, run B finishes in its own time. Add this test at `tests/integration/test_async_isolation.py`.
- `python3 -m pytest tests/ -v` green.

### What actually landed (2026-04-11)

Roughly 35 files touched across all four layers. Summary:

- **HTTP client foundation** — `HttpFileServiceClient` now holds an `httpx.AsyncClient` as an instance attribute (warm connection pool reused across calls) and exposes `aclose()` for shutdown. All six methods are `async def`. `IFileServiceClient` Protocol made async; `MockFileServiceClient` mirrored.
- **DataStoreService + FileBackedResolverStrategy** — `store_data`, `materialize`, `_store_list`, `_store_dict`, `_cached_materialize`, `_try_resolve_artifact`, `_try_odata_row_fetch` all `async def`. All internal file-client calls awaited.
- **VariableResolver + ConditionEvaluator + strategies** — `VariableResolver.resolve`, `_resolve_single`, `resolve_mapping`, `_resolve_value`, `_apply_transform`, `ConditionEvaluator.evaluate`, `_evaluate_single`, `MetadataResolverStrategy.try_resolve`, `TraversalResolverStrategy.try_resolve` all `async def`. `IResolverStrategy` Protocol made async. `AND`/`OR` decomposition in `evaluate` rewritten with explicit awaits (the old `any()/all()` comprehensions can't be awaited).
- **Node handler call sites (11 files)** — `condition_handler`, `switch_handler`, `loop_handler`, `parallel_handler`, `workflow_handler`, `compute_handler`, `task_handler`, `input_handler`, `human_action_handler`, `file_upload_handler`, `data_edit_handler`. Helper methods that invoke the resolver (`_resolve_structured_conditions`, `_resolve_structured_data`, `_evaluate_structured`, `_apply_node_definition`, `_resolve_function_calls`, `_build_resolve_context`, `_build_dynamic_table_schema`, `_resolve_schema_defaults`, `_recover_with_artifact_lookup`) all became async and their call sites now await.
- **orchestration_engine + successor_processor** — `_to_file_ref`, `_apply_csv_data_mode`, `apply_state_updates_as_files` became async; their three callers (`dispatch_or_handle_task`, `_run_loop_iterations`, `handle_entry_nodes`) now await via `replace_all`. Two dict-comprehensions that called `materialize` inside `_start_child_workflow` and `_complete_parent_tracking_task` were rewritten as explicit for-loops (`await` isn't allowed in comprehensions). `successor_processor.py` lines 212 and 344 (the two `state_updates` apply loops) now await `store_data`.
- **Feature use cases** — `start_run`, `complete_task`, `resume_pending_task`, `test_node_definition`, `dry_run_workflow`, `edit_node_input`, `get_variable_suggestions`, `get_node_output`, `get_node_input_data`. For `get_node_output` and `get_node_input_data`, sync helper methods (`_build_array_fields`, `_fetch_scalars`, `_build_table_data_entry`, `_fetch_artifact_previews`) became async along with their callers.
- **Tests (~40 tests touched)** — `test_variable_resolver.py`, `test_file_backed_resolver.py`, `test_data_store_service.py` rewritten as async tests. `test_edit_node_input.py` (25 tests): `_upload_csv` helper became async, all call sites `await`ed, 8 `download_file` calls awaited (including `(await ...).decode("utf-8")` for chained ones). `test_csv_hitl_flow.py`, `test_csv_dispatch.py`, `test_file_controller.py`, `test_file_backed_workflow.py` updated. Mock classes (`_MockDataStoreService`, `_MockFileServiceClient`, `FakeFileClient`) converted to async methods; `MagicMock` → `AsyncMock` where needed.
- **Caught by the test run** — two adapter/infrastructure files were follow-on fixes: `app/layer3_adapters/controllers/restful/v1/file_controller.py` had six sync `client.*` calls (the file controller is a thin passthrough to the file client, so it had to be updated when the client flipped async); `app/layer4_frameworks/clients/mock_data_factory_client.py:28` had a sync `self._file_client.download_file(file_id)` in `transform_rows` (which was already `async def` but hadn't been awaiting its internal calls).

**Result:** 1196/1196 tests passing in 31s. Zero `RuntimeWarning: coroutine '...' was never awaited` warnings. The per-run `asyncio.Lock` in `orchestration_engine.py:89` now correctly serializes only within a run — a slow file-service call in run A no longer blocks the entire event loop.

### Deferred from the original Phase 0 plan

- **Stall isolation integration test** (`tests/integration/test_async_isolation.py`) — not written yet. The fix was verified by the existing unit + integration suite passing, but a dedicated concurrent-runs test would make the guarantee explicit and catch regressions. Tracked as a follow-up.

---

## 4. Phase 1 — Run-Scoped Materialization Cache — **DONE 2026-04-11**

**Goal:** eliminate redundant file-service downloads within a single run. Today `FileBackedResolverStrategy._materialize_cache` is cleared between orchestration passes (`orchestration_engine.py` around line 118 via `_variable_resolver.clear_caches()`), so two CONDITIONs in the same run referencing the same file-backed variable each trigger a download.

### Work

- Move the cache from the strategy instance to a run-scoped store keyed by `run_id`. Candidate: attribute on `WorkflowRun.context`, or a keyed dict on the resolver.
- Key by `(file_id, query_path)` so row-level OData reads are also cached, not only whole-file downloads.
- Bound by LRU (reasonable default: 100 entries or ~50 MB per run).
- Narrow the `clear_caches()` call in `orchestration_engine.py` so it only protects against cross-run bleed, not within-run re-downloads.

### Files

- `app/layer1_domain/services/file_backed_resolver.py` — cache relocation.
- `app/layer1_domain/services/variable_resolver.py` — pass `run_id` through resolve signatures.
- `app/layer1_domain/services/orchestration_engine.py` — narrow the cache-clear call.

### Verification

- Unit test: two CONDITION handlers in the same run reference the same file-backed variable; mock `HttpFileServiceClient.download_file` asserted called exactly once.
- Bounded LRU test under memory pressure.

### What actually landed (2026-04-11)

- **Contextvar-based run tracking** — `app/layer1_domain/services/variable_resolver.py` gained a module-level `_current_run_id_var: ContextVar[str | None]` with `set_current_run_id(run_id) → Token`, `reset_current_run_id(token)`, and `get_current_run_id()` helpers. Contextvars are per-asyncio-task, so concurrent runs on the same process do not collide. No signature changes propagate through the resolver chain — the strategies read the current run_id via the contextvar lookup.
- **Two per-run caches on `FileBackedResolverStrategy`** — the single `_materialize_cache: dict[str, Any]` was replaced with:
  - `_run_materialize_caches: dict[str | None, dict[str, Any]]` — full-file materialize results keyed by `run_id → file_id → materialized data`.
  - `_run_odata_caches: dict[str | None, dict[tuple[str, tuple], dict[str, Any]]]` — OData single-row fetch results keyed by `run_id → (file_id, ((\"$skip\", \"...\"), (\"$top\", \"...\"))) → response`.
  - `_get_materialize_cache()` and `_get_odata_cache()` helpers return the current run's bucket, creating it on first use.
  - `drop_run_cache(run_id)` releases both buckets for a given run.
  - Legacy `clear_materialize_cache()` is kept as a narrow shim that clears only the `None`-keyed bucket (backward-compat for tests that call it on a strategy instance without setting a contextvar).
- **`_cached_materialize` now uses the run bucket** — same flow as before (check cache → fall back to store.materialize → write back) but the bucket is per-run.
- **`_try_odata_row_fetch` now caches** — previously cache-free, every OData row fetch was a round trip. Now checks `_get_odata_cache()` before calling the file service and stores the result on success. Same file/row tuple referenced twice in the same run → one HTTP call.
- **Orchestration engine integration** —
  - `orchestrate_start`, `orchestrate_continue`, `orchestrate_fail` each wrap their body in `token = set_current_run_id(run_id)` / `reset_current_run_id(token)` in a `try`/`finally` inside the `_get_run_lock` region, so the contextvar is set for the entire pass.
  - New private `_drop_run_resolver_cache(run_id)` walks `self._variable_resolver._strategies` and calls `drop_run_cache(run_id)` on any strategy that supports it. Exception-safe.
  - New public `drop_run_resolver_cache(run_id)` wraps the private helper for external callers.
  - `check_run_completion` calls `_drop_run_resolver_cache(run.id)` after both terminal transitions — the FAILED branch (line ~771) and the COMPLETED branch (line ~805). Cache released as soon as the run terminates.
  - `CancelRunUseCase.execute` now calls `self._engine.drop_run_resolver_cache(req.run_id)` after cancelling the parent run and again for each cascaded child. Guarded by `getattr(..., None)` + `callable(...)` so `DummyOrchestrationEngine` test doubles without the method still work.
- **`drain_events` no longer calls `clear_caches()`** — the per-pass wipe was a workaround for a process-wide cache that leaked across runs. With run-scoped caches, wiping every drain would defeat the point (two CONDITIONs in the same run would re-download the same file on every drain boundary). `VariableResolver.clear_caches()` and `FileBackedResolverStrategy.clear_materialize_cache()` are kept as narrow no-op-ish shims so any code or tests that still call them don't break.
- **New unit tests** — 5 tests added to `tests/unit/test_file_backed_resolver.py`:
  - `TestRunScopedMaterializeCache.test_two_resolves_in_same_run_download_once` — asserts `download_calls == 1` across two resolves in the same run on fresh ref copies of the same file_id.
  - `TestRunScopedMaterializeCache.test_different_runs_have_isolated_caches` — two runs, same file_id, asserts `download_calls == 2`.
  - `TestRunScopedMaterializeCache.test_drop_run_cache_invalidates_subsequent_lookup` — resolve, drop, resolve again, asserts the second triggers a new download.
  - `TestRunScopedMaterializeCache.test_no_run_id_falls_back_to_shared_bucket` — contextvar unset → `None` bucket used; legacy `clear_materialize_cache()` wipes only that bucket.
  - `TestRunScopedODataCache.test_two_odata_fetches_in_same_run_query_once` — asserts `query_calls == 1` for two `ref[index].name` reads in the same run.
  - Helper `_CountingFileServiceClient` subclasses `MockFileServiceClient` to count `download_file` / `query_file_data` invocations. Helper `_fresh_ref(initial)` rebuilds a file_ref dict with an empty `extracted` cache so each resolve actually goes through `_cached_materialize` rather than short-circuiting on the per-ref scalar cache.

**Result:** 1190 passed, 2 skipped (pre-existing) in 31.77s. No perf regression.

### Deferred from the original Phase 1 plan

- **LRU bound on the per-run buckets.** The plan mentioned "bounded by LRU (reasonable default: 100 entries or ~50 MB per run)." Skipped as premature optimization — single runs rarely reference more than a few dozen file-backed variables, and the bucket is dropped on run completion. If Phase 2 metrics show memory pressure, an LRU wrapper can be added to the bucket dict without changing the interface.
- **Bounded-LRU test under memory pressure.** Deferred with the LRU itself.
- **Cross-replica cache sharing.** Not applicable at Phase 1 (single-process engine). Phase 4's multi-replica Executor will need to decide whether the cache stays per-process (the default) or becomes a shared read-through against Redis/similar. Today the single `asyncio.Lock` per run already guarantees one process owns a given run at a time, so per-process is correct for now.

---

## 5. Phase 2 — Handler Classification + Timing Metrics

**Goal:** produce the measurement infrastructure needed to decide when event-driven BUILTIN work can begin without guessing. Adds a taxonomy and instrumentation; no behavior change.

### Work

- Add `handler_shape` enum to the `NodeHandler` protocol in `app/layer1_domain/services/node_handlers/base.py`:
  - `PURE_INLINE` — MERGE, TRIGGER (no resolver calls, no I/O).
  - `ASYNC_INLINE` — CONDITION, SWITCH, LOOP, PARALLEL, WORKFLOW, COMPUTE (async I/O via resolver).
  - `DISPATCH_EXTERNAL` — TASK (worker-executor round trip).
  - `PENDING_USER` — INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD.
- Annotate each handler with its shape.
- Add per-handler histogram metric `node_handler_duration_seconds{handler_shape, node_type}` in `app/layer4_frameworks/monitoring/`. Instrument `dispatch_or_handle_task` in `orchestration_engine.py` to record it.
- Add resolver-level metric `variable_resolve_duration_seconds{strategy}` so `FileBackedResolverStrategy` latency is distinguishable from pure traversal.

### Files

- `app/layer1_domain/services/node_handlers/base.py`
- All handler files under `app/layer1_domain/services/node_handlers/`
- `app/layer1_domain/services/orchestration_engine.py`
- `app/layer4_frameworks/monitoring/` (existing metrics module)

### Verification

- Metrics scrape exposes the new histograms.
- Soak test with a representative workflow; collect baseline numbers. These baselines become the decision point for Phase 4–5.

---

## 6. Phase 3 — LOOP Design Spike (Research, No Code) — **DONE 2026-04-11**

**Goal:** answer, on paper, whether LOOP can survive an event-driven rewrite without losing current semantics. Gates the Phase 4 and Phase 5 work.

`_run_loop_iterations` in `orchestration_engine.py:615-735` is a `while` over live Python state — the hardest single piece of the migration. The spike audited the current loop implementation end-to-end and answered the full question list (how iteration state reconstructs from an event log; where the iteration counter lives; quorum buffer keying for merge-inside-loop; how condition/switch inside a loop emit edges; determinism requirements for replay).

### Deliverable

Landed at **[`loop-event-driven-spike.md`](./loop-event-driven-spike.md)** (2026-04-11). ~4500 words, code references throughout, event-schema proposal, state-reconstruction algorithm, nested-loop coordinate model, feature-flag strategy, and per-work-item migration sketch for Phase 5.

### Key findings

- **Current data model does not support nested loops.** `Task.loop_context` is a scalar dict, `Task.iteration_index` is a scalar int, and `loop_var["depth"]` is a hard-coded `0` stub. Nested-loop workflows today silently clobber the outer loop's iteration context.
- **The queued execution path for loops is dead code.** `LoopIterationAction` is defined and the worker routes it, but **nothing enqueues it**. Loops in queued (production) mode stall after the first iteration / first parallel batch because the loop handler is never re-invoked. Discovered during the spike, separate from any migration concern.
- **The inline `_run_loop_iterations` path works correctly** but only in test / mock mode where no orchestration queue is configured. Every existing LOOP test runs inline.
- **`break` / `continue` / early-exit are not implemented.** Only condition-based loops can early-terminate (by the top-of-iteration check evaluating false).
- **Merge-inside-loop-body is tested only in happy-path cases.** The per-iteration `completed_nodes.discard(body_task.node_id)` mechanism is fragile for multi-predecessor merges.

### Recommendation

**Go — proceed with Phase 5 LOOP event-driven promotion.** Gated by a `settings.EVENT_DRIVEN_LOOP` feature flag that defaults off so the old inline path stays as a safety net during rollout. The event-driven model cleanly supports everything the current LOOP does and fixes three latent bugs along the way (nested loops, queued-mode stall, merge-inside-loop quorum).

**Phase 4 is unblocked.** Phase 4 (Dispatcher + Projector split) does not require LOOP changes — the spike confirmed no structural blocker in LOOP that would force Phase 4 to wait.

**Scope for Phase 5 LOOP work (from the spike's §6):**
1. Add 4 new event types (`LoopStarted`, `LoopIterationStarted`, `LoopCompleted`, `LoopExitRequested`); extend existing `LoopIterationCompleted` with `invocation_id` + `scope_stack` + `outcome`.
2. Replace `Task.loop_context: dict` with `Task.scope_stack: list[LoopScope]` (backward-compatible promotion).
3. Rewrite `LoopNodeHandler` as an event emitter behind the feature flag.
4. Rewrite `MergeNodeHandler` to use scope-aware quorum buffers.
5. Update CONDITION/SWITCH edge emission to carry `scope_stack`.
6. Delete `orchestrate_loop_iteration` + `LoopIterationAction` (dead code).
7. Delete `_run_loop_iterations` after the flag is enabled in production.

**Estimated effort for Phase 5 LOOP work:** 2-3 engineer-weeks happy-path + 1 engineer-week production hardening.

### Open questions deferred to Phase 5 kickoff — **ANSWERED 2026-04-12**

1. **Loop output accumulation — YES, collect all.** The loop node's own output (`run.context.variables[loop_node_id]`) must contain ALL iterations' body outputs as a list. Currently body node outputs are overwritten each iteration (only the last survives). The fix: accumulate each iteration's body output into `loop_state["iteration_results"]` during `iteration_complete`, then build the final loop output as `{"results": [...]}` when `LoopCompleted` fires. Downstream nodes reference `$.loop_node_id.output.results` to get the full collection. **Status: DONE — Phase 7.1 (2026-04-13).** Implemented in `event_driven_loop_driver.py` lines 376, 483-488, 1040-1041.

2. **Break-from-outer-loop — Option A: innermost only.** `LOOP_EXIT` breaks the innermost active loop; the outer loop continues to its next iteration. This is the current behavior (Phase 4c.4.5). No target-depth or explicit-target mechanism needed. If a user wants to break the outer loop, they chain: `LOOP_EXIT` (inner) → `CONDITION` (check if inner broke) → `LOOP_EXIT` (outer). **Status: current implementation is correct as-is.**

3. **Condition-loop determinism under replay — undefined behavior.** Condition loops (`loop_type="condition"`) evaluate an expression each iteration against live `run.context.variables`. If a body task calls an external API that returns different data on replay, the condition may evaluate differently. This is **documented as undefined behavior under replay** — the projector replays events (not re-evaluates conditions), so divergence only matters if someone attempts to re-execute a condition loop from the event log, which is not a supported operation. **Status: document-only — no code change needed.**

4. **Cross-nested-loop parallelism limits — YES, add config.** Add `max_concurrent_iterations_per_run: int` to the engine/app config. This limits the total number of in-flight loop iterations across ALL loops in a single run, preventing resource exhaustion from deeply nested or wide parallel loops. Default: 100. Enforced in `initialize_loop_queued` and `orchestrate_loop_iteration` before enqueuing body tasks. **Status: DONE — Phase 7.3 (2026-04-13).** Implemented in `app_config.py:242`, `orchestration_engine.py:76,93`, `bootstrap.py:338`.

---

## 7. Phase 4 — Dispatcher + Projector Split (Option B)

### Phase 4a — Read-side view shim (`IRunContextView`) — **DONE 2026-04-11**

**Why land this first?** The Dispatcher + Projector rewrite (Phase 4b below) is a multi-week undertaking that rewrites the authoritative read-side of the engine. If feature use cases keep reaching into `WorkflowRun.context.variables` directly, every query-side use case becomes a migration risk on the day the Projector lands. Phase 4a inverts the dependency *first*: query-side features consume a read-only view (`IRunContextView`) instead of the `WorkflowRun` aggregate. When Phase 4b swaps the backing store from `IRunRepository` to a projector-backed event-log view, the feature layer requires **zero** changes.

This is the "adapter-layer stability" principle from [`adapter-layer-stability-plan.md`](./adapter-layer-stability-plan.md) applied to the read side, one refactor at a time.

**What landed (2026-04-11):**

1. **`app/layer2_application/interfaces/run_context_view_interface.py`** (new)
   - `IRunContextView` Protocol: read-only properties `run_id`, `workflow_id`, `workflow_version`, `status` (as unwrapped string), `started_at`, `completed_at`, `error`, `output_data`, `input_data`, `variables`, `completed_nodes` (frozenset), `skipped_nodes` (frozenset), `entry_nodes` (tuple), `dag_info`, `child_runs`, `parent_run_id`, `root_run_id`, `depth_level`.
   - `IRunContextViewStore` Protocol: `get(run_id) -> IRunContextView | None`, `get_by_root_id(root_id) -> list[IRunContextView]`.

2. **`app/layer1_domain/entities/run_context_snapshot.py`** (new)
   - `RunContextSnapshot` — frozen dataclass, structural-typing implementation of `IRunContextView`.
   - `from_run(run: WorkflowRun)` classmethod builds shallow copies of mutable collections (variables, completed_nodes, skipped_nodes, child_runs, input_data) so snapshot mutations can't leak back into the live aggregate.
   - Value objects inside `variables` (file_refs, dicts) are still references — the `IRunContextView` contract requires callers treat them as read-only.

3. **`app/layer4_frameworks/run_context/live_view_store.py`** (new)
   - `LiveRunContextViewStore` wraps `IRunRepository`. Loads the live `WorkflowRun` on each call and materialises a fresh `RunContextSnapshot`. Phase 4b replaces this with a projector-backed store.

4. **`bootstrap.py`** — registers `run_context_view_store` in the dependency pool. Named after the store so smart-injection picks it up for any use case that declares it.

5. **Feature migration (3 use cases):**
   - `GetRunStatusUseCase` — now takes `run_context_view_store` instead of `run_repo`. Reads `view.run_id`, `view.workflow_id`, `view.status`, `view.started_at`, `view.completed_at`, `view.error`, `view.output_data`.
   - `GetRunContextUseCase` — now takes `run_context_view_store` alongside `task_repo` and `workflow_repo`. Reads `view.variables`, `view.workflow_id`, `view.status`. Task repo and workflow repo are kept because tasks carry `completed_at` timing and workflow repo supplies node metadata — neither belongs on the run view.
   - `GetNodeOutputUseCase` — now takes `run_context_view_store` alongside `workflow_repo`. Reads `effective_view.variables`, `effective_view.completed_nodes`, `effective_view.skipped_nodes`, `effective_view.entry_nodes`. Nested-run lookup uses `run_context_view_store.get_by_root_id(command.run_id)`. `task_repo` is still injected (unused today) so the dependency resolver doesn't churn.

6. **Test updates** — `tests/unit/test_use_cases.py` and `tests/unit/test_get_run_context.py` instantiate `LiveRunContextViewStore(run_repo)` for the migrated use cases.

**Verification:** 1211 passed, 2 skipped, 0 failures.

**What is explicitly NOT in Phase 4a:**
- No Dispatcher extraction — the engine still calls `SuccessorProcessor` directly.
- No Projector — the view store still loads `WorkflowRun` from the repo.
- No event log — writes still go through the existing repository save path.
- No command-side shim (`ICommandBus`) — only read-side features were migrated.
- Only 3 of ~51 feature use cases were migrated. The rest will be migrated as Phase 4b lands, one small PR at a time, following the same pattern.

### Phase 4b.1 — Event log foundation — **DONE 2026-04-11**

**Why land this first inside Phase 4b?** Phase 4b.2 (Dispatcher extraction) needs a deterministic-id idempotency target. Phase 4b.3 (Projector) needs an append-only event stream to replay. Building both at once would produce a large, hard-to-review change. Instead, Phase 4b.1 lands the *shadow* write-ahead log — every event published by the engine is also appended to an in-memory log — with no reader yet. Phase 4b.2 and 4b.3 then consume the log without touching wiring.

**Scope explicitly excluded:**
- No Dispatcher extraction — `SuccessorProcessor` still owns fan-out.
- No Projector — `LiveRunContextViewStore` still loads `WorkflowRun`.
- No persistent storage backend — in-memory only; persistent implementations arrive in Phase 4b.3 once we know the Projector's read patterns.
- No production reader — nothing depends on the log today. Failing log writes are logged at `error` level but do not block publish (shadow log policy).

**What landed:**

1. **`app/layer2_application/interfaces/event_log_repository_interface.py`** (new)
   - `EventLogEnvelope` — frozen dataclass: `event_id`, `run_id`, `topic`, `event_type`, `timestamp`, `payload` (dict), `sequence` (assigned by repo), `source`, `causation_id` (reserved for Phase 4b.2).
   - `IEventLogRepository` Protocol: `append(envelope) -> bool` (returns False on dedupe by `(run_id, event_id)`), `list_by_run(run_id) -> list[EventLogEnvelope]` (append order with assigned sequences), `clear(run_id) -> int`.

2. **`app/layer4_frameworks/persistence/memory/memory_event_log_repo.py`** (new)
   - `MemoryEventLogRepository` — per-run list + per-run monotonic sequence counter + per-run dedupe set. `threading.Lock` for safety — matches the pattern used by the other in-memory repos.
   - `clear` resets the sequence counter so a cleared run's next `append` starts at `1` (Phase 4b.3 Projector rebuild relies on this).

3. **`app/layer1_domain/services/event_id_helper.py`** (new)
   - `deterministic_event_id(causation_id, correlation_key) -> str` — `uuid5` over a project-scoped namespace. Stable across calls. Phase 4b.2's Dispatcher will use it to produce stable downstream event ids so redeliveries dedupe at the log.

4. **`app/layer4_frameworks/messaging/event_log_appending_publisher.py`** (new)
   - `EventLogAppendingPublisher` — `IEventPublisher` decorator. On `publish(topic, event)`: builds an envelope, appends to the log, delegates to inner. Log failures are caught and logged — never propagated. Write-ahead semantics: log before broadcast.
   - Envelope construction handles dataclass events (dataclasses.asdict), dict events (copy), and unknown events (repr fallback). `run_id` extraction walks `event.run_id → event.parent_run_id → event.root_run_id` so every `DomainEvent` subclass in `events.py` logs under the correct key — including `ChildRunStarted` which has no `run_id` field.

5. **`bootstrap.py`** — event log wiring
   - Added `MemoryEventLogRepository` instantiation in §2a.
   - Publisher chain is now: `EventLogAppending → RealtimeBroadcasting → EnrichingPublisher → backend`. `event_log_appending_publisher` is the outermost layer — every engine-level publish hits the log first.
   - Exposed `event_log_repo` in `available_dependencies` so future features can smart-inject it.

6. **Tests** — 19 new tests
   - `tests/unit/test_event_log.py` (17): repo append/list/dedupe/clear/sequencing/unknown-run/unscoped bucket; `deterministic_event_id` stability + collision behavior; publisher delegates to inner, extracts run_id via fallback chain (including `ChildRunStarted` → `parent_run_id` and a root-only event → `root_run_id`), log failures do not break publish, dict events are serialized correctly, `TaskCompleted` replay dedupes at the log.
   - `tests/integration/test_event_log_wiring.py` (2): drives a TRIGGER → COMPUTE workflow through a real `OrchestrationEngine` wrapped with `EventLogAppendingPublisher` and asserts `TaskDispatched`, `TaskCompleted`, `EdgeTraversed`, `RunCompleted` all land in the log in sequence. Second test replays the same `TaskCompleted` three times through the decorator and asserts the inner publisher sees all three but the log dedupes to one.

**Verification:** 1230 passed, 2 skipped, 0 failed. (+19 tests from the prior 1211 baseline.)

**Design notes recorded for Phase 4b.2/4b.3:**
- The envelope's `causation_id` field is set to `""` today. Phase 4b.2 will populate it at Dispatcher fan-out time using the upstream event's `event_id`, then generate the downstream event id via `deterministic_event_id(causation_id, f"{wire_id}:{target_node_id}")`.
- The log is currently keyed on `run_id` only. Phase 4b.3's Projector rebuild test can assume: given a run id, `list_by_run` returns a complete replayable stream for that run. Cross-run correlation (parent/child workflows) still requires joining on `parent_run_id` in the payload — deferred until the Projector concretely needs it.
- `clear` exists specifically so the rebuild test can: (1) wipe the run repo, (2) clear the log's sequence counter, (3) replay envelopes, (4) assert the rebuilt state matches.

### Phase 4b.2 — Dispatcher extraction (pure fan-out) — **DONE 2026-04-11**

**Why this shape?** A full Dispatcher extraction — collapsing every fan-out call site into a single code path owned by a new service — is a multi-day rewrite of `SuccessorProcessor`, `handle_entry_nodes`, `dispatch_or_handle_task`, and the queued-execution paths. Instead, Phase 4b.2 lands the **pure decision function** that the Dispatcher service will eventually wrap: port filtering + cascade-skip BFS + predecessor-readiness + deterministic event id generation. `SuccessorProcessor.process` delegates the decision today but still owns the side-effecting application (handler invocation, `run.context` mutation). Phase 4b.3 builds the Projector on top of the deterministic ids, and a future PR collapses the remaining call sites once the pure module has proved itself in production.

This minimises churn and risk: the refactor moves ~70 lines of computation into a pure module, preserves every existing behavior verbatim (1243 passing tests including the 20 existing `SuccessorProcessor` tests), and unlocks the replay-idempotency property the event log needs.

**What landed:**

1. **`app/layer1_domain/services/dispatcher/__init__.py`** (new) — re-exports `FanoutAction`, `FanoutDecision`, `SkipAction`, `compute_fanout`.

2. **`app/layer1_domain/services/dispatcher/fanout.py`** (new — ~230 lines) — the pure module.
   - `FanoutAction` frozen dataclass: one outgoing edge whose target is ready to dispatch. Carries a deterministic `event_id` (uuid5 over `causation_id` + `f"edge:{edge_id}:{target_id}"`) for the `EdgeTraversed` event, plus `edge_id`, `source_node_id`, `target_node_id`, `source_port_id`.
   - `SkipAction` frozen dataclass: one cascade-skipped node. Carries a deterministic `event_id` (uuid5 over `causation_id` + `f"skip:{node_id}"`) for the `TaskSkipped` event.
   - `FanoutDecision` frozen dataclass: tuple of actions + tuple of skipped + tuple of deferred target ids + expected_port.
   - `compute_fanout(...)` function: takes `causation_id`, `source_node_id`, `source_node_type`, `source_task_status`, `outgoing_edges`, `completed_nodes`, `skipped_nodes`, `predecessors`, `adjacency`. Returns a `FanoutDecision`. No I/O, no mutation, no handlers.
   - Private `_collect_exclusive_downstream(...)` — merge-safe BFS. A successor is only queued if every one of its predecessors is already in the accumulating skipped set, so merge nodes with a live path stay active.
   - Port-routed types are `{TASK, COMPUTE, WORKFLOW}` — matches `_PORT_ROUTED_TYPES` in the original code verbatim.

3. **`app/layer1_domain/services/successor_processor.py`** — refactored to delegate.
   - New import: `from app.layer1_domain.services.dispatcher import compute_fanout`.
   - `process()` no longer inlines port filtering, cascade-skip BFS, or predecessor-readiness — it calls `compute_fanout` with a frozen snapshot of `run.context.completed_nodes` and `run.context.skipped_nodes`, then applies the decision:
     - Each `decision.skipped` → mutates `run.context.skipped_nodes` + emits `TaskSkipped(event_id=skip_action.event_id, ...)`.
     - Each `decision.actions` → emits `EdgeTraversed(event_id=action.event_id, ...)` then runs the target's handler (unchanged from before).
   - `causation_id` is passed as `completed_task.id` — the completed task's id is unique per task and stable across redeliveries. Phase 5 will switch to the actual upstream event id once the caller has access to it.
   - The ~90-line block that used to inline port filtering + cascade-skip was replaced with ~45 lines of decision-application. Net reduction: 45 lines of untested nested conditionals moved into a pure module with 13 unit tests.

4. **Tests** — 13 new pure unit tests in `tests/unit/test_dispatcher_fanout.py`.
   - **Port filtering (3 tests):** TASK success port taken on `COMPLETED`; TASK failure port taken on `FAILED`; non-port-routed TRIGGER emits all outgoing edges.
   - **Cascade-skip (3 tests):** BFS walks downstream exclusive subtree; merge node with live path is NOT skipped (the property `dag_traversal.collect_downstream_nodes_to_skip` guarantees); already-completed skip candidate is ignored.
   - **Predecessor readiness (2 tests):** target with pending predecessor is deferred, not dispatched; skipped predecessor counts as ready (matches existing behavior in the processor).
   - **Deterministic ids (3 tests):** same causation + same inputs → identical action / skip ids (the property the event log needs for redelivery dedupe); different causation ids produce different action ids; action ids and skip ids cannot collide for the same node id (correlation keys differ).
   - **Purity (2 tests):** mutation-free — calling `compute_fanout` does not mutate any of its input collections; `FanoutDecision` is frozen and raises on attribute assignment.

**What was deliberately NOT touched:**

- The **post-handler BUILTIN edge-emission pass** at `successor_processor.py:314-337` — the one that iterates `taken_edge_ports` to emit edges after a CONDITION/SWITCH/COMPUTE completes. It still uses random UUIDs for its `EdgeTraversed` events. That loop handles a subtly different case (a CONDITION's taken branches) and will migrate in Phase 4b.3 once the Projector can exercise it end-to-end.
- **`handle_entry_nodes` in `orchestration_engine.py`** — still walks entry nodes by hand. Entry nodes have no predecessors so there is nothing for `compute_fanout` to decide; extracting this is cosmetic and deferred.
- **Queued-execution methods** (`_exec_builtin_queued`, `_exec_pending_queued`, `_exec_task_queued`, `orchestrate_loop_iteration`) — they call `SuccessorProcessor.process` under the hood, so they already benefit from the extracted dispatcher transitively.
- **`SuccessorProcessor._evaluate_branching_node`** — handles CONDITION/SWITCH evaluation before advancing. Does not involve port filtering or cascade-skip today (the handler emits its own edge events). Untouched.

**Verification:** 1243 passed, 2 skipped, 0 failed (+13 new dispatcher tests from the 1230 baseline). The existing 20 `SuccessorProcessor` tests pass unchanged — proof that the refactor is behaviour-preserving.

**Properties this unlocks for Phase 4b.3:**

- Replay the same `completed_task.id` through `SuccessorProcessor.process` twice → the resulting `EdgeTraversed` and `TaskSkipped` events carry identical `event_id` values. The Phase 4b.1 event log dedupes at append, so the Projector's rebuild pass sees each logical event exactly once regardless of how many times it was redelivered.
- The pure function can be unit-tested under any input — the Phase 4b.3 Projector can assume that applying a `FanoutDecision` to a snapshot yields a deterministic new snapshot, and a rebuild test can trivially assert "replay the whole log → rebuilt state matches live state."

### Phase 4b.3 — Projector reducer + rebuild test — **DONE 2026-04-11 (partial)**

**Why "partial"?** A full Projector rebuilds every field of `RunContextSnapshot` from the event log alone. Today's engine writes `run.context.variables` directly via `apply_state_updates_as_files` — no current domain event carries the state-update payload, so the reducer cannot populate `variables`. Rather than block Phase 4b.3 on a parallel Phase 5 scope change, this drop lands a **lifecycle-only** reducer that handles every field the current event catalog can express, with the explicit gap documented inline and a rebuild test scoped to the fields it owns.

Phase 5 (event-driven BUILTIN) adds a `NodeOutputProduced` event carrying the state-update payload, at which point the reducer gains one branch and the rebuild test gains two lines of assertions.

**Why land it now anyway?**
- It is the **regression safety net** for Phase 5. Any change that breaks the "event log → live structural state" property on the lifecycle subset is caught by the rebuild test.
- It is the **template** for the Phase 5 extended reducer. Adding a new event to `apply_event` is a one-line branch.
- It validates the Phase 4b.1 event log has the right keying and ordering properties for deterministic rebuild. If `list_by_run` returned events out of order, or the engine emitted events that key on the wrong `run_id`, the rebuild test would fail. Today it passes — proof that the log foundation is sound.

**What landed:**

1. **`app/layer1_domain/services/projector/__init__.py`** (new) — re-exports `ProjectedRunState`, `apply_event`, `rebuild_run_state`.

2. **`app/layer1_domain/services/projector/run_state_projector.py`** (new, ~200 lines) — the pure reducer.
   - `ProjectedRunState` frozen dataclass: `run_id`, `workflow_id`, `status`, `started_at`, `completed_at`, `error`, `output_data`, `completed_nodes` (frozenset), `skipped_nodes` (frozenset), `child_runs` (dict).
   - `apply_event(state, envelope) -> state` — pure reducer with one branch per supported event type. Uses `dataclasses.replace` for scalar updates and frozenset union for node sets. **Idempotent** — applying the same event twice yields the same state (set-union is a no-op, last-write semantics for scalars).
   - Handles these event types today: `RunStarted`, `RunCompleted`, `RunFailed`, `RunCancelled`, `RunPaused`, `RunResumed`, `TaskCompleted`, `TaskFailed`, `TaskSkipped`, `ChildRunStarted`.
   - **Unknown event types pass through unchanged** — forward-compatible. Logs written today that contain future event types can still be replayed without error.
   - `rebuild_run_state(run_id, event_log_repo) -> ProjectedRunState` — folds `list_by_run(run_id)` through `apply_event` from an initial state. Never mutates the log.

3. **`tests/unit/test_run_state_projector.py`** (new, 19 tests)
   - **Lifecycle (5):** `RunStarted`/`Completed`/`Failed`/`Cancelled`/`Paused+Resumed` transitions.
   - **Tasks (6):** `TaskCompleted` adds to set; idempotent under replay; `TaskFailed` also marks the node completed; `TaskSkipped` adds to skipped_nodes; multiple task events accumulate; empty `node_id` is a no-op.
   - **Child runs (2):** `ChildRunStarted` populates `child_runs`; missing `parent_node_id` is a no-op.
   - **Unknown events (2):** arbitrary future event type passes through; every existing informational event (`EdgeTraversed`, `TaskDispatched`, `ContextUpdated`, `ConditionEvaluated`, `InputRequested`, `TaskRetrying`, `ArtifactAdded`, `LoopIterationCompleted`) is a no-op.
   - **rebuild_run_state (4):** empty log returns initial state; multi-event fold; determinism under double-rebuild; `RunFailed` precedence over earlier `RUNNING` state.

4. **`tests/integration/test_projector_rebuild.py`** (new, 2 tests)
   - **Happy-path rebuild:** drives a TRIGGER → COMPUTE workflow through `OrchestrationEngine` with a real `EventLogAppendingPublisher` wrapping the inner publisher, then calls `rebuild_run_state`. Asserts the projected `status`, `completed_nodes`, `skipped_nodes` match the live `WorkflowRun` state. Also asserts double-rebuild produces identical output (replay determinism).
   - **Wipe-then-rebuild:** runs a workflow, calls `event_log_repo.clear(run_id)`, rebuilds, asserts the projection is back to the initial state; then runs a second workflow and asserts its projection is not contaminated by the wiped run's events. This is the Phase 5 Projector service's pre-requisite: wipe + replay must compose correctly, and replay must be scoped per-run.

**What was deliberately NOT covered:**

- **End-to-end cascade-skip coverage** — initially attempted with a COMPUTE-port-branching workflow but hit a pre-existing engine gap: BUILTIN nodes' post-handler edge emission (`successor_processor.py:314-337`) bypasses the Dispatcher and does NOT port-filter. Both branches of a COMPUTE fork run today. The `TaskSkipped` reducer branch is covered by unit tests (reducer + `compute_fanout` independently), and the integration gap will close in Phase 5 when BUILTIN edges flow through the Dispatcher. Documented prominently in the test module docstring.
- **`variables` rebuild** — covered above. Phase 5 fixes this via `NodeOutputProduced`.
- **`dag_info` rebuild** — the DAGPreparer output is not event-sourced. Callers that need `dag_info` should re-run the preparer against the static workflow definition. This is the correct separation: static workflow metadata ≠ dynamic run state.
- **Persistent event-log backend** — still in-memory. A SQLite/HANA backend is mechanical work once the interface has proved itself; deferred to keep this drop focused on correctness.

**Verification:** 1264 passed, 2 skipped, 0 failed (+21 new tests from the 1243 baseline).

**Properties this unlocks for Phase 5:**

- Adding a new event type (e.g. `NodeOutputProduced`) requires one new branch in `apply_event` and one new field in `ProjectedRunState`. The existing rebuild test immediately exercises the new branch as soon as the engine emits it.
- The partial projector proves the "event log → structural state" round-trip is sound today, which means Phase 5's flip of `variables` to event-sourced is a drop-in extension, not a rewrite.
- Third-party tools (audit, debugging, replay) can already consume the reducer to reconstruct lifecycle state without touching the live aggregate.

### Phase 4b.4 — Post-BUILTIN edge migration — **DONE 2026-04-11**

**Why this drop?** Phase 4b.2 extracted `compute_fanout` and delegated the main `SuccessorProcessor.process` edge loop, but the **post-handler BUILTIN edge emission** path was deliberately left untouched — it still used a raw loop with random UUIDs and no port filter. Phase 4b.3 surfaced this when a COMPUTE success/failure branching workflow attempted cascade-skip coverage and found both branches ran. Phase 4b.4 closes the gap.

**What was actually migrated (broader than the original plan):**

The plan expected to fix the one call site in `SuccessorProcessor.process`. During implementation the unit test passed immediately but the integration test still failed: the bug was actually split across **two sites** because the COMPUTE handler itself walks its successors when building `tasks_to_dispatch`. Had to fix both to make the end-to-end rebuild test pass.

1. **`app/layer1_domain/services/successor_processor.py`** — post-handler BUILTIN edge loop (~line 332)
   - Old: raw loop over `dag_info.edge_map.get(target_node.id, [])` emitting `EdgeTraversed` with random `uuid.uuid4()`, filtering only via `taken_edge_ports` when CONDITION/SWITCH set it.
   - New: two-branch structure.
     - `taken_edge_ports` branch (CONDITION/SWITCH): preserved shape but stamps deterministic event id via `deterministic_event_id(builtin_task.id, f"branch:{edge.id}:{target_id}")`.
     - Everything else (TRIGGER, COMPUTE, WORKFLOW, LOOP, MERGE, PARALLEL): delegates to `compute_fanout` with `source_task_status=TaskStatus.COMPLETED`, then applies `decision.actions` → `EdgeTraversed(event_id=...)` and `decision.skipped` → `run.context.skipped_nodes.add` + `TaskSkipped(event_id=...)`.

2. **`app/layer1_domain/services/node_handlers/compute_handler.py`** — successor walk inside the handler
   - Old: `for edge in outgoing_edges: ... result.tasks_to_dispatch.append(task)` — no port filter, no cascade-skip.
   - New: delegates to `compute_fanout` with:
     - `causation_id=node.id`, `source_task_status=TaskStatus.COMPLETED`
     - `completed_nodes=frozenset(run.context.completed_nodes) | {node.id}` — pre-includes the COMPUTE node because the engine adds it to `completed_nodes` *after* the handler returns; without this, compute_fanout's predecessor-readiness check would defer every successor whose only predecessor is COMPUTE, breaking the happy path. This is a subtle contract: the handler's snapshot must model "this node has just completed", not the raw run state at entry.
   - The decision's `actions` produce `tasks_to_dispatch`; the decision's `skipped` produce both `skipped_node_ids` (for the engine to apply to `run.context.skipped_nodes`) and `TaskSkipped` events in `result.events` (for the projector to apply).
   - Critically, the engine's `_emit_edge_events` uses `{t.node_id for t in handler_result.tasks_to_dispatch}` to decide which edges to emit. With the handler now filtering, the engine's emission naturally stays in sync — no change needed in `orchestration_engine.py`.

3. **`tests/unit/test_successor_processor.py`** — new `TestPostBuiltinPortFilter` class
   - `test_compute_success_port_filters_failure_branch`: TRIGGER → COMPUTE(success→ok, failure→err). Asserts the post-handler BUILTIN edge pass emits exactly one `EdgeTraversed` (target=ok), cascade-skips err, and carries non-empty deterministic event ids.
   - `test_compute_failure_port_filters_success_branch`: mirror topology — regression double-check.
   - `test_condition_taken_edge_ports_preserved`: CONDITION with `taken_edge_ports={"e2": "true_branch"}` emits only e2 with a deterministic event id. Regression guard for the CONDITION/SWITCH path.

4. **`tests/integration/test_projector_rebuild.py`** — restored cascade-skip integration test
   - Full 4-node branching workflow (TRIGGER → COMPUTE → ok-branch + err-branch) driven end-to-end through `OrchestrationEngine` + `EventLogAppendingPublisher`.
   - Asserts `live_run.context.skipped_nodes` contains err-branch AND `projected.skipped_nodes` (from projector rebuild) matches.
   - This is the test that was removed in Phase 4b.3 with a pointer to this phase — now passes.

**Verification:** 1272 passed, 2 skipped, 0 failed (+4 tests from the 1268 baseline).

**Properties unlocked:**
- COMPUTE success/failure port semantics are now respected by the engine (real bug fix — workflows using COMPUTE port branching behaved incorrectly before).
- The cascade-skip integration property holds end-to-end: the Phase 4b.1 event log captures `TaskSkipped` events, the Phase 4b.3 reducer applies them, and the projector rebuild matches the live aggregate.
- The Dispatcher module now covers BOTH the successor walk (via `SuccessorProcessor.process`) AND the inline BUILTIN handler (via `ComputeNodeHandler`). The remaining handlers (TASK, WORKFLOW, TRIGGER, LOOP, MERGE, PARALLEL, CONDITION, SWITCH) still walk their own successors — migrating them is mostly mechanical now that the pattern is proven in COMPUTE.

### Phase 4b.5 — Migrate remaining non-complex handlers — **DONE 2026-04-11**

**Scope decision.** Phase 4b.4 proved the `compute_fanout` migration pattern on `ComputeNodeHandler`. Five handlers had raw successor-walk loops remaining: TRIGGER, WORKFLOW, MERGE, LOOP, PARALLEL. After surveying them:

- **TRIGGER** — simple walk, entry node, non-port-routed. Pattern applies cleanly.
- **MERGE** — walks outgoing edges after wait-strategy check. Pattern applies cleanly.
- **PARALLEL** — has two modes: `branches` config (self-contained condition-based skip logic) + fallback edge walk. Only the fallback is migrated; the branches mode has its own contract and stays untouched.
- **WORKFLOW** — does NOT walk outgoing edges in `handle()`. It creates a tracking_task and returns `requires_child_run=True`. The child run drives successor processing via a different path. No migration needed; the handler already doesn't walk anything to filter.
- **LOOP** — complex state machine (body iteration, pre-loop / post-loop phases, per-iteration state). The Phase 3 spike already identified LOOP as its own rewrite track behind `EVENT_DRIVEN_LOOP`. Out of scope for 4b.5.

So Phase 4b.5 lands TRIGGER, MERGE, PARALLEL (fallback), deferring LOOP and marking WORKFLOW as "not applicable".

**What landed:**

Each handler follows the same ~15-line transformation established by `ComputeNodeHandler`:

1. Import `compute_fanout` and `TaskStatus`.
2. Replace the `for edge in outgoing_edges: ... append task` loop with a `compute_fanout(...)` call that pre-includes `node.id` in the `completed_snapshot`.
3. Iterate `decision.actions` and build tasks.

Because TRIGGER, MERGE, and PARALLEL are all **non-port-routed** (not in `_PORT_ROUTED_TYPES`), `compute_fanout` returns every outgoing edge as an action (no port filter) and no cascade-skip. The net behavior is identical to the old raw loop. The gain is:

- **Deterministic event ids** — each downstream task is associated with a `FanoutAction.event_id` derived via `deterministic_event_id(node.id, f"edge:{edge.id}:{target_id}")`. Redeliveries dedupe at the event log.
- **Consistency** — every BUILTIN handler that walks successors now goes through the same module. Future changes to port semantics / cascade rules / predecessor gating land in one place.
- **Single-place concurrency audit** — `compute_fanout` is pure, frozen, single-threaded; the previous loops had no isolation guarantees.

**Files modified:**

- `app/layer1_domain/services/node_handlers/trigger_handler.py` — line-for-line delegation
- `app/layer1_domain/services/node_handlers/merge_handler.py` — line-for-line delegation after the wait-strategy check
- `app/layer1_domain/services/node_handlers/parallel_handler.py` — line-for-line delegation in the `else` branch (fallback when no `branches` config)

**Tests:**

No new tests were written. Phase 4b.5 is a behavior-preserving refactor across three handlers, all covered by existing suites. The existing 25 handler tests (3 TRIGGER + 15 MERGE + 7 PARALLEL) plus the 2 integration rebuild tests plus the full suite all pass without modification.

**Verification:** 1272 passed, 2 skipped, 0 failed. Same count as Phase 4b.4 baseline (no new tests — the value is preserved-behavior determinism).

**What is deliberately NOT in Phase 4b.5:**

- **LOOP handler** — the state machine wraps the iteration loop with pre/post phases; porting it to `compute_fanout` requires the Phase 3 spike's nested-loop `scope_stack` work to land first. Behind `EVENT_DRIVEN_LOOP` flag.
- **PARALLEL branches-config path** — each branch has its own condition-eval-with-resolver skip logic that doesn't map onto `compute_fanout`'s `_PORT_ROUTED_TYPES` filter. A future phase could subsume it under `compute_fanout` with a `branch_conditions` parameter, but that's a speculative extension and out of scope.
- **WORKFLOW** — no successor walk to migrate.
- **Worker-completion TASK path** — already correct. `complete_task_use_case` delegates to `engine.process_successors` → `SuccessorProcessor.process` → `compute_fanout`, so TASK port filtering already works via the Phase 4b.2 extraction. Verified during Phase 4c.1 survey — no separate walk in the use case.
- **Persistent event-log backend** — deferred.

### Phase 4b.6 — Persistent SQLite event log backend — **DONE 2026-04-11**

**Why now?** Phase 4c.1 ended with the projector-backed view store working in tests but explicitly not wired in production because "in-memory is lost on restart". Phase 4b.6 delivers the durable backend that closes that gap — once this lands, the only remaining blocker for production wiring is the performance-measurement question.

**What landed:**

1. **`app/layer4_frameworks/persistence/sqlite/__init__.py`** (new) — package marker for the SQLite backend family.

2. **`app/layer4_frameworks/persistence/sqlite/sqlite_event_log_repo.py`** (new, ~200 lines)
   - `SqliteEventLogRepository` — implements `IEventLogRepository` using stdlib `sqlite3`. No new runtime dependencies.
   - Accepts either `":memory:"` (tests, ephemeral) or a filesystem path (durable).
   - Concurrency: single connection with `check_same_thread=False` + `threading.Lock`. Matches `MemoryEventLogRepository`'s pattern, sufficient for the single-process engine. Multi-process / multi-replica deployments will use HANA or Kafka's native log partitioning.
   - Schema:
     ```sql
     CREATE TABLE event_log (
         run_id TEXT, sequence INTEGER, event_id TEXT,
         topic TEXT, event_type TEXT, timestamp TEXT,
         payload TEXT,           -- JSON-serialised dict
         source TEXT, causation_id TEXT,
         PRIMARY KEY (run_id, sequence)
     );
     CREATE UNIQUE INDEX idx_event_log_event_id
         ON event_log (run_id, event_id);  -- dedupe at the storage layer
     ```
   - `append` uses `INSERT OR IGNORE` + `rowcount` to detect dedupe — a matching `(run_id, event_id)` in the unique index causes the insert to be silently dropped, signalling duplicate.
   - `next_sequence` is computed per-append via `SELECT COALESCE(MAX(sequence), 0) + 1 FROM event_log WHERE run_id = ?`. This means `clear(run_id)` implicitly resets the sequence for that run — matching the memory backend's observable behavior without needing an explicit sequence counter table.
   - JSON payload serialisation uses `json.dumps(payload, default=str)` so non-standard types (e.g., datetimes) round-trip as strings without blowing up.
   - Explicit `close()` method for test teardown and future `bootstrap.py` shutdown registration.

3. **`tests/unit/test_sqlite_event_log_repo.py`** (new, 11 tests)
   - **Conformance mirror** (9 tests): same test cases as `test_event_log.py::TestMemoryEventLogRepository` — append + list order preserved; monotonic sequence per run; dedupe by `(run_id, event_id)` within a run; dedupe isolation across runs; `clear` drops the run and effectively resets the sequence; empty `run_id` uses `"__unscoped__"` bucket; unknown run returns empty; nested-dict/list payload round-trips byte-for-byte.
   - **Durability** (2 tests): write events → close the connection → open the SAME db file with a new connection → verify events survive with their original sequences. Second durability test: re-append an event with the same `event_id` across the reopen boundary and assert the unique index still catches the dedupe. This is the property that guarantees idempotency under worker restart / Kafka redelivery.

4. **`tests/integration/test_sqlite_event_log_projector.py`** (new, 2 tests)
   - **Round-trip test**: drives a real TRIGGER → COMPUTE workflow through `OrchestrationEngine` with `EventLogAppendingPublisher` wrapping `SqliteEventLogRepository(":memory:")`. Calls `rebuild_run_state` on the SQLite log and asserts `workflow_id`, `status`, `completed_nodes`, `skipped_nodes`, `variables` all match the live `WorkflowRun` aggregate. Then builds a `ProjectorRunContextViewStore` from the same SQLite log, runs `GetRunStatusUseCase` through it, and asserts the full feature-layer result is correct. This is the end-to-end proof: engine → SQLite event log → projector reducer → view store adapter → feature use case → HTTP-ready result.
   - **Cross-session durability test**: writes events through one engine session on a real file path, closes the connection, then opens a NEW session against the same file and rebuilds state from the persisted log alone — without any `WorkflowRun` aggregate. Asserts `variables` round-trip survives the DB reopen. This is the concrete property that justifies production wiring: a crashed worker can be restarted and resume serving queries with zero replay code, because the log is authoritative.

**Verification:** 1288 passed, 2 skipped, 0 failed (+13 tests from the 1275 baseline).

**Design notes:**

- **Sync vs async:** `sqlite3` is synchronous. Making `IEventLogRepository` async would cascade through `EventLogAppendingPublisher` and the engine's entire publish path. SQLite operations against a local file are sub-millisecond for typical payload sizes, so the sync API is not a meaningful latency source on the publish path. The shadow-log write-ahead model further decouples publish from log-append — append failures are already caught and do not block publish.
- **No bootstrap wiring yet:** `MemoryEventLogRepository` remains the default in `bootstrap.py`. Flipping to SQLite is a one-line change:
  ```python
  event_log_repo = SqliteEventLogRepository(settings.EVENT_LOG_DB_PATH or ":memory:")
  ```
  Not flipped yet because (a) we want to measure rebuild latency under real load before committing the projector store to production, and (b) the memory backend is sufficient for tests and mock infra.
- **Dedupe semantics:** the memory backend tracks seen `event_id`s in a Python `set`; SQLite enforces the same property via a unique index. Both return `False` from `append` on dedupe. This is the property Phase 4b.2's deterministic event ids depend on — redeliveries from Kafka or retry logic collapse at the log regardless of which backend is wired.

**Properties unlocked:**

- The "event log is authoritative" property (Phase 5.1) now has a durable carrier. A process crash no longer loses the authoritative state.
- Production wiring for `ProjectorRunContextViewStore` is now a one-line configuration change + a perf-measurement exercise.
- Phase 5.2 (event-driven BUILTIN dispatch) can run under real load because the event log persists across restarts.

**What is deliberately NOT in Phase 4b.6:**

- **HANA backend** — SAP production persistence. Mechanically similar to SQLite: one table, JSON payload column, unique index for dedupe. Deferred because the engineering surface (SAP driver config, VCAP_SERVICES parsing, schema migration tooling) is different from the algorithmic work this phase focuses on.
- **Bootstrap wiring** — intentional deferral per above.
- **Tailing / cached projector** — today every `get()` on `ProjectorRunContextViewStore` rebuilds from scratch. A production deployment would cache the projected state and invalidate on new appends. That's a future optimisation once the rebuild path is exercised under load.
- **Connection pooling / per-run connections** — single connection + lock is sufficient at current scale. Multi-process deployments will use HANA or Kafka log partitioning, not parallel SQLite writers.

### Phase 4b.7 — Event log backend wiring (bootstrap flip) — **DONE 2026-04-11**

**Why now?** Phase 4b.6 delivered `SqliteEventLogRepository`; Phase 4c.2 delivered the cached projector view store. Both landed as opt-in prototypes — neither was reachable from the production `bootstrap.py` because the event log was hard-coded to `MemoryEventLogRepository()`. Phase 4b.7 closes that gap with a minimal, zero-risk wiring change: adds two settings, introduces a backend factory, and exposes the event log repository directly on the container for operators and tests to inspect.

**Scope decisions:**

- **Default unchanged.** `EVENT_LOG_BACKEND` defaults to `"memory"` so every existing deployment sees identical behavior. Operators who want durable event logs set `EVENT_LOG_BACKEND=sqlite` + `EVENT_LOG_DB_PATH=/var/lib/workflow/events.db` in their environment.
- **Event log only — NOT the read model yet.** The Phase 4c.1 / 4c.2 projector view store migration stays behind. Flipping `LiveRunContextViewStore` to `CachedProjectorRunContextViewStore` can break features that read `dag_info` (`get_node_output`'s nested-run lookup), so it needs a feature-by-feature audit that is separate scope from event-log wiring.
- **Graceful degradation on misconfiguration.** Unknown `EVENT_LOG_BACKEND` values log a warning and fall back to memory. The engine must never fail to start because of a misconfigured shadow log — it is not on the critical path.

**What landed:**

1. **`app/layer4_frameworks/config/app_config.py`** — two new settings
   - `EVENT_LOG_BACKEND: str = os.getenv("EVENT_LOG_BACKEND", "memory")` — selects `"memory"` or `"sqlite"`. Docstring explicitly lists both options and explains when to use each.
   - `EVENT_LOG_DB_PATH: str = os.getenv("EVENT_LOG_DB_PATH", ":memory:")` — file path used when the SQLite backend is selected. Defaults to `":memory:"` so the SQLite backend can be exercised in tests and dev without leaving artifacts.

2. **`bootstrap.py`** — backend factory + direct container exposure
   - New module-level function `_build_event_log_repo(settings) -> IEventLogRepository` (~30 lines) that inspects `settings.EVENT_LOG_BACKEND`, normalises to lowercase, lazy-imports `SqliteEventLogRepository` only when needed (keeps the sqlite3 cost out of the critical path for memory-backed deployments), and returns the selected repo. Unknown backends log a warning and fall back to `MemoryEventLogRepository`. Also logs the selected backend at INFO so operators can confirm their env var took effect.
   - `build_app_container()` replaces `event_log_repo = MemoryEventLogRepository()` with `event_log_repo = _build_event_log_repo(settings)`.
   - New line at the end of `build_app_container`: `container["event_log_repo"] = event_log_repo`. Previously the repo was only surfaced in the smart-DI dependency pool (injected into feature use cases that declare it as a constructor parameter); now it's directly accessible on the container dict for operators, health checks, and tests.

3. **`tests/unit/test_bootstrap_event_log_selection.py`** (new, 8 tests)
   - **Factory-level (6 tests):** `SimpleNamespace` stubs cover all five backend-selection code paths — default "memory", explicit "sqlite" with `:memory:`, "sqlite" with a real tempfile (proves the path is threaded through), case-insensitive name normalisation (`"SQLITE"` works), unknown backend fallback to memory, None/empty value fallback.
   - **End-to-end (2 tests):** `build_app_container()` with `EVENT_LOG_BACKEND=memory` (the default) returns a container whose `event_log_repo` entry is a `MemoryEventLogRepository`; monkey-patched `settings` to `EVENT_LOG_BACKEND=sqlite` returns a `SqliteEventLogRepository`. These run the FULL bootstrap wiring, not a stub — so if anything in the engine's construction chain depends on the event log type, it would be caught.

**Verification:** 1311 passed, 2 skipped, 0 failed (+8 tests from the 1303 baseline).

**Properties now available:**

- **Durable event log in production is one env var away.** `EVENT_LOG_BACKEND=sqlite EVENT_LOG_DB_PATH=/var/lib/workflow/events.db` turns on the Phase 4b.6 backend for any deployment.
- **Restart recovery works.** Combined with Phase 4b.6's durability tests, a worker crash + restart can rebuild authoritative run state from the persisted log — which is the core property Phase 5.2 event-driven BUILTIN dispatch will depend on.
- **Event log observability.** `container["event_log_repo"]` is now directly accessible, so a future `/health` endpoint or admin API can report which backend is wired and expose log stats.
- **Per-environment backend choice.** Tests and mock infra keep the fast in-memory backend; staging/prod can opt into SQLite without any code change.

**What Phase 4b.7 deliberately does NOT do:**

- **Does not flip the read-model default.** `LiveRunContextViewStore` remains the `bootstrap.py` default view store. Features read live `WorkflowRun` aggregates as before. Flipping to the projector store is a separate, feature-by-feature audit — e.g., `get_run_status` is trivially projector-safe but `get_node_output` needs `dag_info` fallback logic that is not yet implemented.
- **Does not add a `USE_PROJECTOR_READ_MODEL` setting.** Adding the flag is tempting but premature — without the `dag_info` fallback, flipping it would break features silently. Better to land the fallback first, then add the flag.
- **Does not wire the cached projector store even when the user enables SQLite.** Only the event log backend flips. The cached projector is still opt-in at the code level for any feature that wants to consume it. Consistent with the "flip one thing at a time" principle.
- **Does not add a HANA backend.** SAP production wiring requires the SAP driver, VCAP credential plumbing, and schema migration. Separate engineering surface.

### Phase 4c.4 — Loop `scope_stack` foundation + `EVENT_DRIVEN_LOOP` flag — **DONE 2026-04-11 (foundation only)**

**Why this shape?** The Phase 3 spike ([`loop-event-driven-spike.md`](./loop-event-driven-spike.md)) identified two structural problems with the current LOOP handler:

1. **Nested loops don't actually work** — `Task.loop_context: dict | None` and `Task.iteration_index: int | None` are scalar fields carrying at most one loop's iteration context. `run.context.variables["loop"]["depth"]` is hard-coded to `0`. Any nested-loop workflow silently clobbers the outer loop's iteration state.
2. **Queued-mode loops stall** — `LoopIterationAction` is defined and worker-routed, but nothing enqueues it. In queued (production) mode loops run the first body task then stall indefinitely.

The spike's recommended end-state is a full event-driven loop dispatcher behind an `EVENT_DRIVEN_LOOP` feature flag, with explicit `LoopStarted` / `LoopIterationStarted` / `LoopIterationCompleted` / `LoopCompleted` events, per-iteration merge quorum buffers, and a `scope_stack` on every event. That's weeks of work across multiple sessions.

Phase 4c.4 lands the **data-model foundation** for that rewrite:

- The `LoopScope` value object and `derive_invocation_id` helper (spike §3.2) — the primitives the future event schema will use.
- The `scope_stack: list[LoopScope] | None` field on `Task` — backward-compatible extension.
- Helper methods on `Task` (`get_innermost_scope`, `with_pushed_scope`) so future consumers can use one API uniformly across the migration, without branching on which field is set.
- The `EVENT_DRIVEN_LOOP` setting — wired into config but not yet consumed at runtime.

**Zero runtime behavior change.** The existing imperative `_run_loop_iterations` walker continues to execute every loop in every test. Nothing consumes `scope_stack` or the feature flag yet. This phase is strictly "build the vocabulary for the rewrite" — future phases (4c.4.2+) will consume it.

**Why land foundation-only instead of the full rewrite?**
- The full rewrite touches the LOOP handler, the engine's loop execution path, the event schema, the merge handler's quorum keys, and the projector's event reducer. Landing it as one commit risks breaking all loop tests.
- The spike's event schema is already well-designed — we just need the primitives. Landing the data model alone is safe, testable, and unblocks incremental migration.
- The `get_innermost_scope` legacy fallback means future migrations can update callers one at a time: each can use the new API even when the surrounding code is still legacy.

**What landed:**

1. **`app/layer1_domain/value_objects/loop_scope.py`** (new, ~130 lines)
   - `LoopScope` frozen dataclass with `loop_node_id`, `invocation_id`, `iter`, `total`. Frozen so consumers cannot mutate a scope in place.
   - `to_dict()` / `from_dict()` for JSON round-trip via the event log's `payload` column. Event schema in Phase 4c.4.2+ will carry `scope_stack` as a list of these dicts.
   - `derive_invocation_id(run_id, loop_node_id, enclosing)` — computes a deterministic `uuid5.hex` over a private namespace. Key composition: `run_id | loop_node_id | outer_scope_0 | outer_scope_1 | ...` where each outer scope contributes `loop_node_id#invocation_id#iter`. Same inputs always produce the same id (deterministic replay); different outer iterations produce different nested inner ids (fresh quorum buffers per outer iteration).
   - Private `_LOOP_INVOCATION_NAMESPACE` UUID is documented as "must never change" — any future change would invalidate every deterministic invocation id recorded under the old namespace.

2. **`app/layer1_domain/entities/task.py`** — `Task` entity extension
   - New optional field: `scope_stack: list[LoopScope] | None = None`. Default `None` preserves every existing caller — the imperative walker continues to set `loop_context` + `iteration_index` unchanged.
   - `Task.get_innermost_scope() -> LoopScope | None`:
     - If `scope_stack` is non-empty: return `scope_stack[-1]` (the innermost enclosing loop).
     - If legacy `loop_context` / `iteration_index` is set: synthesise a `LoopScope` with empty `loop_node_id` and empty `invocation_id` (sentinels for "legacy scope") plus `iter` and `total` from the legacy fields. This keeps the helper usable uniformly across the migration — callers don't need to branch on "is this a legacy task or a new one".
     - Otherwise: return `None` (task is not in any loop).
   - `Task.with_pushed_scope(scope) -> Task` — immutable-style builder. Returns a new `Task` (via `dataclasses.replace`) with the scope appended. Does NOT mutate the source task or its `scope_stack` list — explicitly tested to prevent aliasing bugs in the future dispatcher when it constructs body tasks for each iteration.

3. **`app/layer4_frameworks/config/app_config.py`** — new `EVENT_DRIVEN_LOOP: bool` setting (default `False`). Docstring is explicit about the phased rollout: "When True (future phases only — NOT yet wired)..."

4. **`tests/unit/test_loop_scope.py`** (new, 21 tests)
   - **`TestLoopScope` (4 tests):** basic construction, frozen-ness enforcement, `to_dict`/`from_dict` round-trip, defensive `from_dict` on partial input.
   - **`TestDeriveInvocationId` (8 tests):**
     - Determinism: same inputs → same id.
     - Discrimination: different `run_id`s and different `loop_node_id`s produce different ids.
     - **Nested-loop uniqueness (critical property):** the inner loop must get a different invocation id for each outer iteration — if it didn't, merge quorum buffers would leak between outer iterations in a nested-loop body. This test is THE enforcement point for the spike's §3.6 invariant.
     - Replay stability: same outer iteration produces same inner id across calls.
     - UUID5 hex format validation (32-char, v5).
     - `None` vs `[]` equivalence for `enclosing`.
     - **Three-level nesting composition:** changing any of 3 enclosing scopes changes the deepest invocation id.
   - **`TestTaskGetInnermostScope` (5 tests):** returns `None` outside loops, reads `scope_stack[-1]`, falls back to legacy `loop_context`, legacy fallback with only `iteration_index`, `scope_stack` precedence over legacy fields during mixed-state migration.
   - **`TestTaskWithPushedScope` (4 tests):** returns a new task (not the same reference), does not mutate source, does not mutate source's scope_stack list when the source has an existing stack, integration with `get_innermost_scope`.

**Verification:** 1364 passed, 2 skipped, 0 failed (+21 tests from the 1343 baseline). Existing 188 Task-using tests (node handlers + use cases) stay green unchanged, confirming the backward-compatible field addition.

**What Phase 4c.4 deliberately does NOT do:**

- **Does not touch the imperative LOOP walker.** `OrchestrationEngine._run_loop_iterations` and `LoopNodeHandler._handle_iteration_complete` continue to use `loop_context` + `iteration_index` exactly as before. No runtime code consumes `scope_stack`.
- **Does not wire `EVENT_DRIVEN_LOOP`.** The flag exists in config but nothing branches on it yet. Enabling it today would have zero effect.
- **Does not add the loop event types** (`LoopStarted`, `LoopIterationStarted`, etc. from spike §3.3). Those land in Phase 4c.4.2 when the event-driven dispatcher starts emitting them.
- **Does not fix the queued-mode `LoopIterationAction` dead code.** That's a separate bug fix that will be subsumed by the event-driven rewrite — once the rewrite lands, the queued path goes away entirely.
- **Does not migrate the merge handler's quorum keys.** The spike §3.8 calls out that merge buffers inside a nested loop body need to be keyed on `(run_id, node_id, innermost_invocation_id, innermost_iter)`. That migration depends on `scope_stack` being populated on body tasks, which doesn't happen until the event-driven dispatcher rewrite.

**What this foundation unlocks for future phases:**

- **Phase 4c.4.2 — Loop event types** can now define payloads that reference `LoopScope` directly. The serialisation format is already settled.
- **Phase 4c.4.3 — Event-driven dispatcher** can construct body tasks via `template_task.with_pushed_scope(current_scope)` — the builder already handles immutability.
- **Phase 4c.4.4 — Nested-loop merge quorum** can look up merge buffers via `task.get_innermost_scope().invocation_id` without needing to know whether the task was built by the legacy walker or the new dispatcher.
- **Phase 5.2 event-driven BUILTIN dispatch** can assume that every BUILTIN handler's result tasks can carry a `scope_stack` for loop context propagation, without a data-model rewrite.

### Phase 4c.4.2 — Loop event types + reducer branches — **DONE 2026-04-11**

**Why now?** Phase 4c.4 landed the `LoopScope` data model but the event vocabulary was still stuck at the pre-4c.4 shape — only the legacy `LoopIterationCompleted` existed with `iteration` / `total` scalar fields. Phase 4c.4.2 adds the four missing event types from spike §3.3 and extends the legacy event with optional `invocation_id` / `scope_stack` / `iter` / `outcome` fields so the Phase 4c.4.3 event-driven handler can start emitting a fully bracketed loop lifecycle.

**Vocabulary-only drop.** Nothing in runtime emits the new events yet. The projector reducer adds explicit passthrough branches so a trace search for "LoopStarted" finds the documented extension point, but no state is rebuilt from loop events — loop lifecycle is captured indirectly via the body `TaskCompleted` events already handled by the reducer.

**What landed:**

1. **`app/layer1_domain/events.py`** — four new dataclasses and one extended dataclass, all documented with the spike §3.3 field specs:
   - **`LoopStarted`** — `invocation_id` (derived via `derive_invocation_id(run_id, node_id, enclosing)`), `scope_stack` (outer scopes only, excludes self), `loop_type`, `mode`, `total`, `parallelism`.
   - **`LoopIterationStarted`** — `invocation_id`, `scope_stack` (includes self at tail), `iter`, `item` (loop variable snapshot).
   - **`LoopIterationCompleted`** — extended with optional `invocation_id` / `scope_stack` (includes self at tail) / `iter` / `outcome` (`"ok"` / `"failed"` / `"skipped"`). The legacy `iteration` + `total` fields stay in place with the same semantics; the Phase 4c.4.3 handler dual-writes both `iteration` and `iter`. This keeps the one legacy emitter at `loop_handler.py:163` compiling unchanged.
   - **`LoopCompleted`** — `invocation_id`, `scope_stack`, `total_iterations`, `reason` (`"all_done"` / `"condition_false"` / `"break"` / `"early_exit_error"`).
   - **`LoopExitRequested`** — cooperative break signal. Type only; no emitter exists today (reserved for a future break node).

   All `scope_stack` fields carry `list[dict[str, Any]]` rather than `list[LoopScope]` because events flow through the messaging layer which JSON-serialises payloads. The dicts are produced via `LoopScope.to_dict()` and reconstructed via `LoopScope.from_dict()`.

2. **`app/layer1_domain/services/projector/run_state_projector.py`** — `apply_event` gains an explicit branch that recognises all five new `event_type` strings and returns `state` unchanged. Documented as scaffolding for Phase 5+ replay-driven loop recovery; today's reducer has nothing to do with loop events because `TaskCompleted` already covers body lifecycle.

3. **`tests/unit/test_loop_events.py`** (new file, 16 tests):
   - **`TestLoopStarted`** (4 tests): default construction, populated construction, `scope_stack` round-trip via `[LoopScope.to_dict()]` with `from_dict` inverse check, inheritance from `DomainEvent`.
   - **`TestLoopIterationStarted`** (3 tests): default construction, `item` accepts arbitrary JSON values (str/int/dict/None), scope_stack tail includes self per spike §3.3.
   - **`TestLoopIterationCompleted`** (4 tests): **legacy-kwargs-only regression guard** (constructs exactly as the current emitter at `loop_handler.py:163` and asserts the new fields default to sentinels), forward-compat with all new fields populated alongside legacy, `outcome` accepts all three sentinel values, `iteration`/`iter` can drift independently.
   - **`TestLoopCompleted`** (2 tests): default construction, all four `reason` sentinel values.
   - **`TestLoopExitRequested`** (1 test): default + populated construction.
   - **`TestProjectorLoopEvents`** (2 tests): `LoopStarted` passthrough; full-lifecycle replay (all five event types in order) yields an unchanged `ProjectedRunState`.

**Verification:** 1380 passed, 2 skipped, 0 failed (+16 tests from the 1364 baseline). Every existing test stays green, including the one legacy `LoopIterationCompleted` emitter.

**What Phase 4c.4.2 deliberately does NOT do:**

- **Does not emit the new events.** The `LoopNodeHandler` continues to emit only the legacy `LoopIterationCompleted` with its legacy-only fields. Phase 4c.4.3 wires the emission.
- **Does not extend the projector reducer semantically.** The branches are explicit passthroughs. Phase 5+ may extend them to track active loops for replay-driven recovery.
- **Does not stamp deterministic event ids on loop events.** Base-class `uuid4` defaults still apply in 4c.4.2. Phase 4c.4.3 computes `deterministic_event_id(run.id, "loop.started:<node>:<invocation>")` etc. when the handler emits.

### Phase 4c.4.3 — Event-driven LOOP handler — **DONE 2026-04-11**

**Why now?** With Phase 4c.4 providing the `LoopScope` data model and Phase 4c.4.2 providing the event vocabulary, Phase 4c.4.3 is the first phase where the runtime actually consumes `settings.EVENT_DRIVEN_LOOP`. Flag OFF is byte-identical legacy behavior; flag ON activates an event-driven `LoopNodeHandler` that emits the full spike §3.4 event sequence and populates `scope_stack` on body tasks, unlocking Phase 4c.4.4's scope-aware merge quorum.

**Scope decision — what 4c.4.3 DOES and DOESN'T do.** This phase migrates the **handler** to become event-shaped and scope-aware, but leaves the imperative `_run_loop_iterations` walker in `orchestration_engine.py` alone. The walker still coordinates iteration advancement via its synthetic-task mechanism. Full dispatcher rewrite (queue-driven, no walker) is multi-week Phase 5.2 scope. Phase 4c.4.3 is intentionally a smaller diff that delivers observable value — events + scope_stack + nested-loop correctness — without a big-bang engine refactor.

**What landed:**

1. **`app/layer1_domain/services/node_handlers/loop_handler.py`** (extensive):
   - `LoopNodeHandler.__init__` gains `*, event_driven: bool = False` kwarg. Default preserves every existing caller.
   - `handle()` branches on `self._event_driven`: flag off = legacy `_initialize_loop` / `_handle_iteration_complete` path unchanged; flag on = new `_handle_event_driven` entry.
   - **`_ed_initialize_loop`** — mirrors the legacy initialiser for items/mode/loop_type resolution but:
     - Reads enclosing scopes from `completed_task.scope_stack` (empty for top-level loops, non-empty when the LOOP is itself inside an outer loop's body).
     - Calls `derive_invocation_id(run.id, node.id, enclosing)` to produce a deterministic, nested-loop-unique invocation id.
     - Persists `invocation_id` + `enclosing_scope_stack` onto `loop_state` so `_ed_iteration_complete` can reconstruct the scope chain without relying on the walker's synthetic task (which lacks `scope_stack`).
     - Emits `LoopStarted` with a deterministic event id `deterministic_event_id(run.id, f"loop.started:{node.id}:{invocation_id}")`.
     - For each body task dispatched (1 in sequential mode, `parallelism` in parallel mode), calls the shared `_dispatch_iteration_body` helper which: builds the task via `task_factory.create_task(...)`, pushes each outer scope via `with_pushed_scope`, then pushes the current `iter_scope`, dual-writes legacy `loop_context` / `iteration_index`, and emits a paired `LoopIterationStarted` with its own deterministic id.
     - Empty-items path emits `LoopStarted` + `LoopCompleted(all_done, 0)` back-to-back then dispatches the exit edge, so replay sees a complete bracketed lifecycle.
   - **`_ed_iteration_complete`** — parallels the legacy iteration-advance logic but:
     - Rebuilds the just-finished scope from `loop_state["invocation_id"]` + `loop_state["enclosing_scope_stack"]` (decoded via `LoopScope.from_dict`) + the completed task's iteration index.
     - Emits the **extended** `LoopIterationCompleted` with `invocation_id`, `iter`, `scope_stack` (enclosing + self at tail), `outcome="ok"`, AND the legacy `iteration` / `total` fields (dual-write).
     - Advances sequential to `iter+1` or parallel via `next_dispatch_index`, emitting `LoopIterationStarted` for the new iter with a deterministic id.
     - Condition-loop termination: if the condition evaluates false or raises `VariableResolutionError`, calls `_ed_emit_loop_completed_and_exit` with `reason="condition_false"` or `"early_exit_error"`.
     - Final termination: when `len(completed) >= total`, calls `_ed_emit_loop_completed_and_exit` with `reason="all_done"`.
   - **`_ed_emit_loop_completed_and_exit`** — shared helper that emits `LoopCompleted` (with deterministic id including the reason) and delegates to the pre-existing `_dispatch_exit` for the outgoing edge task.

2. **`bootstrap.py`** — the `LoopNodeHandler` registration passes `event_driven=settings.EVENT_DRIVEN_LOOP`. Flag OFF (default) = legacy behavior; flag ON = event-driven.

3. **`tests/unit/test_loop_handler_event_driven.py`** (new file, 11 tests):
   - **`TestEventDrivenInitialize`** (6 tests): LoopStarted emission with populated invocation_id and empty outer scope stack; body task carries `scope_stack` (one-element) AND legacy dual-write fields; `LoopIterationStarted(iter=0)` with scope_stack tail including self; parallel mode dispatches `parallelism` body tasks and that many `LoopIterationStarted` events; empty-collection path emits both `LoopStarted(total=0)` and `LoopCompleted(all_done, 0)` back-to-back and dispatches the exit edge; deterministic event ids (two independent invocations with the same inputs produce identical `event_id`s).
   - **`TestEventDrivenIterationComplete`** (3 tests): extended `LoopIterationCompleted` fields populated including new `invocation_id`/`iter`/`scope_stack`/`outcome` alongside legacy `iteration`/`total`; sequential advance keeps the same `invocation_id` across iterations; final iteration emits `LoopCompleted(reason="all_done")` and dispatches the exit edge.
   - **`TestNestedLoopInvocationUniqueness`** (1 test): the spike §3.6 invariant enforced at the handler level — the same inner LOOP node initialised under two different outer-iteration parent tasks produces DIFFERENT `invocation_id`s, and the inner body tasks inherit two-element scope stacks `[outer_scope, inner_scope]` with correct iter values per level.
   - **`TestEventDrivenFlagOffLegacyPath`** (1 test): with `event_driven=False`, the handler emits NO new loop events, body tasks have `scope_stack=None`, and legacy `loop_context` / `iteration_index` fields populate exactly as today. Regression guard that the flag's default stays off for all callers.

4. **`tests/integration/test_event_driven_loop.py`** (new file, 3 tests):
   - Engine-driven integration using a real `OrchestrationEngine` with `event_driven=True` `LoopNodeHandler`, a stub `TRIGGER`-typed body handler that immediately completes, and a capturing event publisher.
   - **`test_flag_on_single_loop_emits_full_event_sequence`** — 3-item sequential LOOP. Asserts counts: 1 `LoopStarted`, 3 `LoopIterationStarted`, 3 `LoopIterationCompleted`, 1 `LoopCompleted(reason="all_done", total_iterations=3)`. All share the same invocation id. Assertion on iter values uses sets rather than sequences to accommodate a pre-existing engine ordering quirk: `_publish_now_many(handler_result.events)` at `orchestration_engine.py:446` runs AFTER `_run_loop_iterations` returns, so the init-time events are published after the walker's per-iteration publishes. Fixing event ordering is Phase 5.2 scope.
   - **`test_flag_on_empty_collection_emits_started_then_completed`** — empty items end-to-end through the engine.
   - **`test_flag_off_default_suppresses_new_loop_events`** — regression guard running the full engine with `event_driven=False`: zero new loop events, only the legacy `LoopIterationCompleted` with empty `invocation_id` and `scope_stack=[]`.

**Verification:** 1394 passed, 2 skipped, 0 failed (+14 tests from the 1380 baseline). Every existing `TestLoopHandler` test stays green because the legacy path is untouched. The 3 existing integration tests that exercise LOOP via the full API (`test_api.py`, `test_queued_builtin_state_updates.py`, `test_projector_view_store.py`) also stay green because they construct `LoopNodeHandler(resolver)` without the flag (defaulting to off).

**What Phase 4c.4.3 deliberately does NOT do:**

- **Does not touch `_run_loop_iterations`.** The engine's walker still drives iteration advancement via the synthetic-task feedback mechanism. Phase 5.2 replaces this with a queue-driven dispatcher.
- **Does not support queued-mode loops.** `LoopIterationAction` remains dead code. Flag-on path only runs inline (same production limitation as flag-off today).
- **Does not emit `LoopExitRequested`.** The event type exists (from 4c.4.2) but no break node type exists to emit it. Future work.
- **Does not fix event ordering.** The engine publishes the init-time `LoopStarted` / first-`LoopIterationStarted` events AFTER `_run_loop_iterations` completes, so the log's topological order doesn't match the logical sequence. This is a pre-existing engine quirk, not introduced here. Phase 5.2 will fix this by eliminating the walker.
- **Does not make merges inside nested loops correct.** After 4c.4.3 body tasks carry `scope_stack` but `MergeNodeHandler` still uses the pre-4c.4.4 keying (reads `run.context.completed_nodes` directly). Phase 4c.4.4 closes this gap.

### Phase 4c.4.4 — Scope-aware merge quorum — **DONE 2026-04-11**

**Why now?** With Phase 4c.4.3 populating `scope_stack` on body tasks inside event-driven loops, `MergeNodeHandler` can finally isolate its quorum state per iteration / per inner-loop invocation. Before 4c.4.4, the handler keyed quorum implicitly on `(run_id, node_id)` by reading `run.context.completed_nodes`, which the legacy walker reset via `.discard(body_task.node_id)` per iteration — this worked for sequential top-level loops but was fragile for parallel body iterations and outright broken for nested loops (the spike §3.7 "leak between outer iterations" bug).

**Design: scope-keyed buffer, legacy-safe fallback.** A merge quorum buffer now lives on `RunContext.merge_quorum_buffers: dict[tuple[str, str, int], set[str]]`, keyed on `(merge_node_id, innermost_invocation_id, innermost_iter)`. When the completed task has a populated `scope_stack`, the key includes the real invocation/iter. When the task is outside any loop (no scope), the key falls back to the sentinel `(merge_node_id, "", -1)` which gives exactly one buffer per merge per run — identical to the pre-4c.4.4 semantics. The buffer is seeded from `run.context.completed_nodes` / `skipped_nodes` on first access so legacy-path merges that never see a completed task with scope (flag off) still work correctly.

**What landed:**

1. **`app/layer1_domain/entities/workflow_run.py`** — `RunContext` gains `merge_quorum_buffers: dict[tuple[str, str, int], set[str]]` with a `default_factory=dict`. The comment documents the keying and the sentinel fallback.

2. **`app/layer1_domain/services/node_handlers/merge_handler.py`** — `handle()` rewritten to use the buffer:
   - Reads `scope = completed_task.get_innermost_scope() if completed_task else None`. When `scope is not None and scope.invocation_id`, builds `buffer_key = (node.id, scope.invocation_id, scope.iter)`; otherwise uses the sentinel `(node.id, "", -1)`.
   - Fetches or creates the buffer via `run.context.merge_quorum_buffers.setdefault(buffer_key, set())`.
   - If `completed_task.node_id in predecessor_ids`, records the predecessor in the buffer.
   - Seeds the buffer with any `run.context.completed_nodes` / `skipped_nodes` entries that are predecessors — handles both legacy-path merges (no scope → sentinel key + direct seed) and first-hit merges in a new scope.
   - Quorum check uses `n_done = len(buffer)` and `n_total = len(predecessor_ids)`. Strategies (`all` / `any` / `n_of_m`) unchanged semantically — they just operate on the scoped buffer instead of the whole-run completed set.
   - Data merging (`merged_data`) still reads from `run.context.variables` keyed by predecessor node id; the scope-aware buffer only affects quorum, not the data payload. Correct because variable writes are per-node, not per-scope (legacy and event-driven paths both write the latest iteration's output to the same variable key).

3. **`tests/unit/test_node_handlers.py::TestMergeHandler`** (+4 tests):
   - **`test_scope_aware_no_scope_uses_sentinel_key`** — legacy path: merge called with `completed_task=None`, completed_nodes has both preds. Buffer lands under `(merge_id, "", -1)`. Quorum fires. Documents that the sentinel is the migration-safe fallback.
   - **`test_scope_aware_with_scope_keys_by_invocation_and_iter`** — event-driven path: body task with `scope_stack=[LoopScope(loop_id, "inv-1", iter=0, 3)]` triggers the merge. Buffer keys under `(merge, "inv-1", 0)`. Then a second task with `iter=1` triggers it again and gets a FRESH buffer at `(merge, "inv-1", 1)` — quorum re-arms.
   - **`test_scope_aware_parallel_iterations_are_isolated`** — two in-flight iterations of a parallel loop, merge has two predecessors A and B. Drives "A@iter=0" (no quorum), "A@iter=1" (no quorum), "B@iter=0" (quorum fires for iter 0), inspects the iter-1 buffer to confirm it still only has "A", drives "B@iter=1" (quorum fires for iter 1). Proves the buffers are independent.
   - **`test_scope_aware_nested_loop_different_invocation_ids`** — outer LOOP × 2 × inner LOOP with a merge inside the inner body. Completes both preds in outer-iter-0 (inner `invocation_id="INNER_0"`) — merge fires. Then drives only ONE pred in outer-iter-1 (inner `invocation_id="INNER_1"`) and asserts the merge does NOT fire because the `INNER_1` buffer is fresh — the spike §3.7 leak invariant. Completes the second pred under `INNER_1` and asserts the merge fires.

**Verification:** 1398 passed, 2 skipped, 0 failed (+4 tests from the 1394 baseline). Every existing `TestMergeHandler` test stays green (all 15 of them) because the sentinel fallback preserves legacy semantics for completed-task-less calls.

**What Phase 4c.4.4 deliberately does NOT do:**

- **Does not remove the walker's `.discard(body_task.node_id)` trick.** Legacy path still relies on it — two mechanisms coexist during the migration window (new scope-aware buffer for flag-on path, old discard-based approach for flag-off path). Removal is Phase 5.2 scope.
- **Does not prune buffers on `LoopCompleted`.** Buffers accumulate for the lifetime of the run. For workflows with millions of iterations this is a memory concern. Future cleanup phase.
- **Does not differentiate failed iterations.** `LoopIterationCompleted.outcome` is always `"ok"` today (4c.4.3 decision). The merge handler treats failed iterations the same as successful ones. Real failure propagation lands in Phase 5.2 alongside retry semantics.
- **Does not change merged data behavior.** The scope-aware buffer only affects QUORUM — the data merge logic still reads from `run.context.variables[pred_id]`, which holds the latest iteration's value. Scope-aware data merging (per-iteration variable history) is out of scope — needed only for workflows that aggregate across iterations, which is a separate design.

### Phase 4c.4.5 — `LoopExitRequested` emitter + `LOOP_EXIT` break node — **DONE 2026-04-12**

**Why now?** Phase 4c.4.2 landed the `LoopExitRequested` event type in the vocabulary but deliberately deferred the emitter (no runtime code emitted it). Phase 4c.4.3 landed the event-driven loop handler but also deferred the break path — the handler had no way to know "I should exit early because the body asked me to". Phase 4c.4.5 closes the gap with the minimum viable break implementation: a new `LOOP_EXIT` BUILTIN node type whose handler signals the innermost active event-driven loop to terminate at the next iteration boundary with `reason="break"`.

**Scope — break only, flag-on only, no walker changes.** From the Q&A at the start of this phase:

- **Node name:** `LOOP_EXIT` (CAPS_UNDERSCORE convention, matches the spike §3.3 naming and is more explicit than generic `BREAK`).
- **Target:** innermost enclosing event-driven loop, auto-derived — no config on the break node.
- **Vocabulary:** just `break` (via the existing `LoopExitRequested` + `LoopCompleted(reason="break")` events). `continue` / `LoopIterationSkipped` is explicitly deferred — it's a different mechanism that would need a new event type.
- **Feature flag:** the handler no-ops when `EVENT_DRIVEN_LOOP=false` (or when the break node is placed inside a legacy loop), so the legacy path is strictly unchanged. No regression risk for users who haven't opted into event-driven loops.

**Mechanism — how break propagates through the walker without touching it.** Phase 4c.4.3's decision to leave `_run_loop_iterations` alone means the walker's iteration-feedback loop still drives coordination. Phase 4c.4.5 fits into that model without changing the walker, using `run.context.loop_state` as an ambient signalling channel:

1. The LOOP_EXIT handler runs inline inside a body iteration — typically downstream of a CONDITION that decided "we should break now".
2. It locates the innermost active event-driven loop by iterating `run.context.loop_state` in reverse insertion order. `_ed_initialize_loop` is the only writer, and nested loops are initialised in entry order (outer first, inner second), so Python's dict insertion-order guarantee gives us the nesting order for free. Reverse iteration finds the innermost first.
3. Filters: `invocation_id` must be non-empty (excludes legacy loops → enforces "Flag ON only"); `len(completed_iterations) < total` (excludes already-finished loops whose state lingers in the dict); `not break_requested` (excludes already-broken loops for idempotency).
4. On match: sets `loop_state["break_requested"] = True` in place, emits `LoopExitRequested(loop_node_id, invocation_id, scope_stack)` with a deterministic event id, returns an empty `tasks_to_dispatch` (break is a dead-end).
5. On miss: logs a warning, emits nothing, records the noop in `state_updates` — run continues unchanged.
6. The walker eventually re-invokes `LoopNodeHandler._ed_iteration_complete` via the synthetic-task mechanism for the current iteration's completion.
7. `_ed_iteration_complete` gains a new check right after emitting `LoopIterationCompleted`: if `loop_state.get("break_requested", False)`, it forces `loop_state["total"] = len(completed)` so the walker's `completed_count >= total` terminal check fires on the next re-read, then calls `_ed_emit_loop_completed_and_exit(reason="break", total_iterations=len(completed))`. The walker then sees the completion and dispatches the exit task via its own completion branch (not the "queue as body" branch that would otherwise misroute the exit task).

**Why force `total = len(completed)`?** The walker at `orchestration_engine.py:700-731` re-reads `loop_state.get("total", 0)` after each iteration and branches on `completed_count >= total`. If the handler returns an exit task without updating `total`, the walker falls to the else branch and queues the exit task as new body, which is wrong. Mutating `total` down is the minimum-impact way to make the walker take the correct path without touching the walker itself. It's also semantically honest — `total_iterations` on the `LoopCompleted` event reflects how many iterations actually ran.

**Break takes precedence over condition re-evaluation.** The break check is placed BEFORE the condition-loop re-evaluation branch in `_ed_iteration_complete`. So a LOOP_EXIT fired inside a condition-loop's body produces `reason="break"` even when the loop condition would still evaluate truthy. This matches programming-language intuition (`break` overrides `while True:`).

**What landed:**

1. **`app/layer1_domain/value_objects/node_type.py`** — new `NodeType.LOOP_EXIT = "LOOP_EXIT"` enum value.

2. **`app/layer1_domain/value_objects/node_type_registry.py`** — new `NodeTypeDescriptor` for `LOOP_EXIT`: `NodeClass.TECHNICAL`, icon `CircleStop`, color `#EF4444`, one `in_default` port, **zero out_ports** with `max_out_ports=0` to statically forbid authors from wiring anything downstream (break is a dead end by design).

3. **`app/layer1_domain/services/successor_processor.py`** — added `NodeType.LOOP_EXIT` to `BUILTIN_NODE_TYPES` so the engine handles it inline via the builtin dispatch path.

4. **`app/layer1_domain/services/node_handlers/loop_exit_handler.py`** (new, ~150 lines) — `LoopExitNodeHandler` with shape `PURE_INLINE`. Contains the innermost-active-loop finder (`_find_innermost_active_loop`, static method), the `handle()` method that mutates loop_state and emits the event, and extensive module-level docstring documenting the design decisions.

5. **`bootstrap.py`** — imports `LoopExitNodeHandler` and registers it for `NodeType.LOOP_EXIT` in `_build_handler_registry`.

6. **`app/layer1_domain/services/node_handlers/loop_handler.py`** — `_ed_iteration_complete` gains the break check right after the `LoopIterationCompleted` emission. When `break_requested` is true, forces `loop_state["total"] = len(completed)` and calls `_ed_emit_loop_completed_and_exit(reason="break")`. Six-line insertion, no other logic changes.

7. **`tests/unit/test_loop_exit_handler.py`** (new, 10 tests):
   - **`TestLoopExitFindsInnermost`** (4 tests): single active loop is picked; nested loops' innermost wins via dict order; completed loops are skipped so break targets the next-outer active one; already-broken loops are skipped (idempotent re-break with no other active loops → noop).
   - **`TestLoopExitNoopCases`** (2 tests): empty loop_state → nothing emitted, noop recorded in state_updates; only-legacy loops (empty `invocation_id`) → legacy loop_state is NOT touched.
   - **`TestLoopExitDeterministicEventId`** (1 test): replay-property check — same inputs twice produce identical `LoopExitRequested.event_id`.
   - **`TestLoopHandlerObservesBreak`** (2 tests): break flag causes `LoopCompleted(reason="break")` with `total_iterations=1` and walker's `loop_state["total"]` forced down; break takes precedence over condition-loop re-evaluation (condition still true, but break wins).
   - **`TestLoopExitIntegration`** (1 test): full engine drive — LOOP → LOOP_EXIT as body entry, assert captured event publisher sees `LoopExitRequested` + `LoopCompleted(reason="break", total_iterations=1)` + one `LoopIterationCompleted(iter=0)`.

8. **Sentinel test updates:**
   - `tests/unit/test_successor_processor.py::TestBuiltinNodeTypes::test_all_expected_types` — added `NodeType.LOOP_EXIT` to the expected frozenset.
   - `tests/unit/test_node_type_registry.py::TestGetAllDescriptors::test_returns_all_14` — renamed to `test_returns_all_node_types` and pinned the count to `len(list(NodeType))` so future type additions don't break this sentinel.

**Verification:** 1409 passed, 2 skipped, 0 failed (+11 from the 1398 baseline: 10 new tests in `test_loop_exit_handler.py` + 1 parametrized entry from `test_all_node_types_registered` auto-picking up `LOOP_EXIT`). Every Phase 4c.4.x test from earlier phases stays green — the legacy loop walker is not touched, and the break check is additive (it only fires when break_requested is explicitly set, which only the new handler does).

**What Phase 4c.4.5 deliberately does NOT do:**

- **Does not support continue.** `continue` semantics (skip the rest of the current iteration, go to next) would need a new `LoopIterationSkipped` event type not in the Phase 4c.4.2 vocabulary. Different mechanism — deferred to a future phase.
- **Does not work in legacy mode.** Legacy loops have empty `invocation_id`; the handler filters them out and logs a warning. Users who want break must flip `EVENT_DRIVEN_LOOP=true`.
- **Does not enforce "LOOP_EXIT must be inside a loop" at workflow save.** `DAGValidator` runs no static dataflow analysis, so a LOOP_EXIT placed outside any loop becomes a runtime warning instead of a save-time error. This is consistent with how `$artifacts` / `$config` / dynamic scope references are handled (see `docs/design/scope-model.md` §5).
- **Does not pre-empt in-flight iterations.** Break is cooperative — in parallel-mode loops, iterations already dispatched to the walker's `pending` list will finish before the break takes effect. The spike §3.5 calls this out explicitly.
- **Does not propagate scope_stack through successor task chains.** The handler finds its target loop via `run.context.loop_state` (ambient), not via `completed_task.scope_stack`, so it works even when the break node is several steps downstream of the loop body's entry and the intermediate tasks have no scope_stack. This sidesteps a latent limitation in Phase 4c.4.4's merge-quorum that affects chained-body-deeper-than-entry scenarios — documenting that limitation is separate future work.
- **Does not fix the walker's condition-exit routing bug.** Phase 4c.4.3's condition-false path has the same "exit task gets queued as body" issue that Phase 4c.4.5 works around for break. The fix pattern (force `total = len(completed)`) is now known, but applying it to the condition-false branch is intentionally out of scope for this phase to keep the diff surgical.

**What this unlocks:**

- **Loop authoring completeness** — workflow authors can now implement common "stop iterating when X" patterns using `CONDITION → LOOP_EXIT` in a body branch, without waiting for the Phase 5.2 rewrite.
- **Phase 5.2 event-driven dispatcher** can assume a `break_requested` signalling channel already exists and move it from ambient run state to a proper event-log-driven mechanism if needed.

### Phase 5.2.1 — Event-driven loop driver — **DONE 2026-04-12** (first Phase 5 slice)

**Why now?** Phase 5 (Event-driven BUILTIN dispatch) is the big structural rewrite that retires the imperative `_run_loop_iterations` walker and moves all loop orchestration into a dedicated, event-log-compatible driver. Phase 5.2.1 is the first slice: it extracts the Phase 4c.4.3 event-driven loop logic from `LoopNodeHandler` into its own module and fixes the latent event-ordering quirk where `LoopStarted` used to publish AFTER body events because the legacy walker's `_publish_now_many(handler_result.events)` runs after the walker returns.

**The ordering quirk, concretely.** In Phase 4c.4.3 the engine flow for a LOOP node was:

1. Engine calls `LoopNodeHandler.handle(completed_task=None)` → returns a `NodeHandlerResult` with `LoopStarted` + `LoopIterationStarted(iter=0)` in `handler_result.events`.
2. Engine calls `_run_loop_iterations(handler_result)` — the walker dispatches body tasks, feeds synthetic completion back to the handler, publishes per-iteration events via `publish_events(iter_result.events)` at `orchestration_engine.py:692`.
3. After the walker returns, engine calls `_publish_now_many(handler_result.events)` at line 446 — **only now** are the init-time `LoopStarted` and first `LoopIterationStarted` events published.

The result: the event log's topological order for a 3-item loop was `[EdgeTraversed, LoopIterationCompleted(1), EdgeTraversed, LoopIterationCompleted(2), LoopCompleted, LoopStarted, LoopIterationStarted(0)]` — init events appeared AFTER the body events they were supposed to bracket. Phase 4c.4.3's integration tests worked around this with set-based assertions (`{e.iter for e in iter_started} == {0, 1, 2}` instead of sequence assertions). Phase 5.2.1 fixes the underlying cause.

**Scope decision — inline-only, loops-only, one session.** From the Q&A at the start of this phase:

- **Module structure:** new package `app/layer1_domain/services/loop_driver/` — room for future submodules (iteration scheduling, exit handling, break observation) in subsequent Phase 5 slices.
- **Handler split:** **absorb** — the driver owns the real logic, the handler's `_ed_*` methods become thin delegation shims. Single source of truth at the cost of a handful of shim-method lines.
- **Ordering fix:** yes, as part of this slice. This is the primary deliverable — tests assert strict logical order instead of set-based workarounds.
- **Queued-mode support:** no — inline-only driver this slice. Queued-mode loops remain broken (deferred to Phase 5.2.2 when the driver becomes an event-log subscriber).

**What landed:**

1. **`app/layer1_domain/services/loop_driver/__init__.py`** — new package exporting `EventDrivenLoopDriver`.

2. **`app/layer1_domain/services/loop_driver/event_driven_loop_driver.py`** (new, ~600 lines) — the `EventDrivenLoopDriver` class with three public methods:
   - **`initialize_loop(node, run, workflow, dag_info, completed_task, task_factory) → NodeHandlerResult`** — matches the old `LoopNodeHandler._ed_initialize_loop` signature and semantics. Resolves items, derives `invocation_id`, builds `loop_state`, emits `LoopStarted` + first `LoopIterationStarted`, dispatches first body task. Used by the handler's delegation shim so existing Phase 4c.4.3 handler tests keep passing without modification.
   - **`iteration_complete(node, run, workflow, dag_info, completed_task, task_factory, loop_state) → NodeHandlerResult`** — matches the old `LoopNodeHandler._ed_iteration_complete`. Emits `LoopIterationCompleted`, checks the Phase 4c.4.5 `break_requested` flag, re-evaluates condition loops, advances or terminates. Used by the handler's delegation shim.
   - **`run_loop(loop_node, loop_task, run, workflow, dag_info, *, task_factory, enqueue_or_dispatch, publish_events, depth=0) → None`** — the engine-facing entry. Drives the FULL iteration sequence inline with the right event-publication timing. Flow: call `initialize_loop` → apply its state updates to `run.context` → **publish init events IMMEDIATELY** via the injected `publish_events` callable → dispatch first body task(s) → observe synthetic completion → call `iteration_complete` for each finished iter → publish iter events → either queue next body or terminate. The critical difference from the legacy walker: init events land in the log BEFORE body events.
   - Helper methods: `_resolve_loop_config`, `_dispatch_iteration_body`, `_emit_loop_completed_and_exit`, `_build_loop_started`, `_build_loop_completed`, `_dispatch_exit`, `_get_body_entry`, `_task_belongs_to_loop` (used by `run_loop` to distinguish body tasks from exit tasks when traversing the pending list — body tasks carry a `LoopScope` in their `scope_stack` for this loop, exit tasks don't).

3. **`app/layer1_domain/services/node_handlers/loop_handler.py`** — the 436-line bodies of `_ed_initialize_loop`, `_ed_iteration_complete`, `_dispatch_iteration_body`, `_ed_emit_loop_completed_and_exit` **deleted**. `__init__` now constructs `self._driver = EventDrivenLoopDriver(variable_resolver)` when `event_driven=True`. `_handle_event_driven` becomes a thin shim that forwards to `self._driver.initialize_loop` or `self._driver.iteration_complete` based on whether a completed task is present. Legacy `_initialize_loop` / `_handle_iteration_complete` / `_dispatch_exit` / `_get_body_entry` methods unchanged — the `event_driven=False` path is untouched, every existing legacy test passes.

4. **`app/layer1_domain/services/orchestration_engine.py`** — `OrchestrationEngine.__init__` gains an optional `event_driven_loop_driver: Any = None` parameter. `dispatch_or_handle_task` adds a new branch right after the BUILTIN `task.dispatched` buffer: if the node is LOOP and the driver is set, the engine marks the loop task COMPLETED, buffers `task.completed`, and calls `driver.run_loop(...)` with `enqueue_or_dispatch=self._enqueue_or_dispatch` and `publish_events=self._publish_now_many` as injected callables. The branch then `drain_events()` and returns — bypassing both the handler invocation AND the `_run_loop_iterations` walker call. Legacy path (driver is None) is byte-identical.

5. **`bootstrap.py`** — when `settings.EVENT_DRIVEN_LOOP=true`, instantiates `EventDrivenLoopDriver(resolver)` and passes it into the `OrchestrationEngine` constructor via the new `event_driven_loop_driver=` kwarg. Flag OFF = driver is None = legacy walker path unchanged.

6. **`tests/unit/test_event_driven_loop_driver.py`** (new, 7 tests) — unit tests targeting `run_loop` directly with injected `enqueue_or_dispatch` / `publish_events` mocks. No real engine involved. Covers:
   - **`TestRunLoopSequential::test_three_item_collection_fires_full_event_sequence`** — strict event-order assertion using `first_index(cls)` to verify `LoopStarted → LoopIterationStarted → LoopIterationCompleted → LoopCompleted` chronological ordering (the Phase 5.2.1 fix).
   - **`test_body_tasks_dispatched_in_iter_order`** — body tasks carry `scope_stack` and have iteration_index 0, 1, ...
   - **`TestRunLoopParallel::test_parallel_dispatches_batch_then_completes_all`** — 4 items / parallelism=2.
   - **`TestRunLoopEmpty::test_empty_collection_dispatches_exit_without_body`** — empty loop dispatches exit, no body.
   - **`TestRunLoopEmpty::test_empty_publishes_init_events_before_exit_dispatch`** — interleaved recorder proves init events publish BEFORE the exit dispatch call.
   - **`TestRunLoopBreak::test_break_via_loop_state_flag`** — simulates a `LOOP_EXIT` handler by setting `break_requested` in the dispatch callback; driver observes on next iteration boundary and emits `LoopCompleted(reason="break")`.
   - **`TestRunLoopConditionExit::test_condition_false_exits_early`** — condition loop with flip-condition dispatch callback; driver emits `LoopCompleted(reason="condition_false")` after one iteration.

7. **`tests/integration/test_event_driven_loop.py`** (+2 tests) — adds `_build_engine_with_phase_5_2_1_driver()` helper that constructs a full engine with `event_driven_loop_driver=EventDrivenLoopDriver(resolver)`. Two new tests:
   - **`test_phase_5_2_1_driver_fixes_event_ordering`** — 3-item sequential loop through the full engine + driver. Asserts strict chronological order: `first_index(LoopStarted) < first_index(LoopIterationStarted) < first_index(LoopIterationCompleted) < first_index(LoopCompleted)`. This is the end-to-end proof of the ordering fix.
   - **`test_phase_5_2_1_driver_empty_collection`** — empty loop through the driver-wired engine; init events publish before the exit task is dispatched.

**Verification:** 1418 passed, 2 skipped, 0 failed (+9 from the 1409 baseline: 7 driver unit tests + 2 integration tests). Every Phase 4c.4.x test stays green because:
- The handler's `_ed_*` delegation shims preserve the handler-interface tests (Phase 4c.4.3 unit tests in `test_loop_handler_event_driven.py`, 11 tests).
- The Phase 4c.4.5 `LoopExitNodeHandler` tests (10 tests) don't touch the driver directly — they set `break_requested` on `loop_state` and rely on `iteration_complete` to observe it, which still works because the driver's `iteration_complete` contains the break-check logic moved from the handler.
- The Phase 4c.4.3 integration tests that construct an engine without the driver (default None) still go through the legacy handler + walker path, which is unchanged — so the set-based ordering assertions continue to pass.

**What Phase 5.2.1 deliberately does NOT do:**

- **Does not support queued-mode loops.** The driver uses inline `enqueue_or_dispatch` (which falls through to `dispatch_or_handle_task` when no queue is set). Queued-mode remains broken — that's Phase 5.2.2 scope when the driver migrates to an event-log subscriber.
- **Does not migrate other BUILTIN types.** CONDITION, SWITCH, MERGE, PARALLEL, TRIGGER, WORKFLOW, COMPUTE, LOOP_EXIT all stay on the recursive engine path. Only the LOOP node type is routed through the driver.
- **Does not delete `_run_loop_iterations`.** The legacy walker stays for `event_driven_loop_driver=None` callers. Deletion is Phase 5.2.N when the flag becomes default-on in production.
- **Does not delete the handler's `_ed_*` delegation shims.** The shims exist to keep Phase 4c.4.3 unit tests passing through the handler interface. They become removable once all handler-facing tests are migrated to target the driver directly.
- **Does not fix condition-false walker routing.** The latent bug from Phase 4c.4.5's scope notes — where condition-false exit tasks get requeued as body by the walker — is unchanged in the legacy path. The driver's `run_loop` path doesn't hit that bug because it uses `_task_belongs_to_loop` to distinguish body from exit tasks.
- **Does not propagate `scope_stack` through successor task chains.** The Phase 4c.4.4 latent limitation (merge quorum only works for body-entry merges, not chained deeper merges) is unaffected by Phase 5.2.1. Fixing it requires changes to the successor processor + every handler's `create_task` calls, which is Phase 5.2.3+ scope.

**What this unlocks for future Phase 5 slices:**

- **Phase 5.2.2 — Event-log-subscriber loop driver.** The driver's `run_loop` method is the pivot point for the switch from inline to event-log-subscriber. In 5.2.2, `run_loop` will register a subscription on the run's event log for `TaskCompleted` events matching body task ids, and advance iterations when those events arrive from the queue — closing the queued-mode loop dead-code bug without touching `LoopIterationAction`.
- **Phase 5.2.3 — Successor processor driver migration.** The pattern established here (extract orchestration into a dedicated driver, inject engine callables, gate on a flag) can be replicated for CONDITION/SWITCH/MERGE/etc. to extract the rest of the successor processing logic from `_run_loop_iterations`' cousins in the engine.
- **Phase 5.2.4 — Walker deletion.** Once 5.2.2 ships and the flag is default-on in production, `_run_loop_iterations` can be retired. The loop handler's legacy `_initialize_loop` / `_handle_iteration_complete` methods also become removable.

### Phase 5.2.2 — Queued-mode event-driven loops — **DONE 2026-04-12**

**Why now?** Phase 5.2.1 extracted the event-driven loop logic into a dedicated `EventDrivenLoopDriver`, but only the inline path (no orchestration queue) worked. In queued mode (`engine._queue is not None`), `enqueue_or_dispatch` publishes an `ExecuteNodeAction` to the orchestration queue and returns immediately — the driver's inline while-loop assumed synchronous body completion, so queued-mode loops stalled after the first iteration. This bug existed since the Phase 3 spike and was invisible because no test configured an orchestration queue.

The twist: the engine already had a fully-implemented `orchestrate_loop_iteration` method (~120 lines at `orchestration_engine.py:1947-2107`), and the orchestration worker already dispatched `LoopIterationAction` to it. The entire consumption side was wired — the ONLY missing link was that nothing ever enqueued `LoopIterationAction`. Phase 5.2.2 closes this gap by adding a body-completion detection hook that enqueues `LoopIterationAction` when a loop body iteration finishes, plus all the supporting infrastructure for multi-node body tracking, scope propagation, and iter-boundary cleanup.

**Scope decision:** the user chose the ambitious scope — all three body-type hooks (BUILTIN, TASK, PENDING), multi-node body subgraphs via runtime BFS (no DAGPreparer precompute), and the scope_stack contextvar propagation fix. The user explicitly directed "should we not use DAGPreparer? instead calculate after task complete using edge and current run state?" — which led to the runtime BFS approach over the originally-planned precompute.

**What landed:**

1. **Phase 5.2.1 persistence fix** (`orchestration_engine.py:529`) — `self._run_repo.save(run)` added before the `return` in the LOOP + driver branch of `dispatch_or_handle_task`. Without this one-liner, queued-mode `orchestrate_loop_iteration` re-fetches the run from the repo and finds no `loop_state[loop_node_id]` entry. The fix is also correct for the inline path (idempotent save).

2. **Runtime body-subgraph BFS helpers** (`event_driven_loop_driver.py:138-305`) — six module-level functions that compute body-subgraph membership at runtime using the existing `dag_info.edge_map`. No `DAGPreparer` changes, no new `DAGInfo` fields:
   - **`_is_loop_exit_edge(edge)`** (line 138) — true if edge has condition/port "exit"/"complete"/"loop_complete".
   - **`_is_loop_body_edge(edge)`** (line 148) — true if edge has condition/port "body"/"loop_body".
   - **`_find_body_entry_node(loop_node_id, dag_info, workflow)`** (line 155) — finds the node on the loop's "body" edge; falls back to the first non-exit edge.
   - **`_post_loop_reachable(loop_node_id, dag_info, workflow)`** (line 176) — BFS from the loop's exit-edge target; returns `frozenset[str]` of nodes reachable from the exit, used to guard the body BFS against wandering into post-loop territory.
   - **`iterate_body_subgraph(loop_node_id, dag_info, workflow)`** (line 203) — BFS from body-entry; returns `list[str]` of all reachable body nodes. Terminates at back-edges to the loop, exit edges, LOOP_EXIT nodes, and post-loop-reachable nodes. Used by iter-boundary cleanup.
   - **`is_iteration_done(loop_node_id, iter_idx, run, workflow, dag_info)`** (line 241) — the core query. BFS from body-entry; for each visited node checks `loop_state["iter_progress"][iter_idx]`. Returns `True` iff every reachable body node is tracked in progress. Cost: O(body_size) per call, typically 5–20 nodes, microsecond-range.

3. **`scope_stack` contextvar propagation** — three files, closing the Phase 4c.4.4 latent limitation where deep body tasks had no scope:
   - **`task_factory.py:32-55`** — new `_current_scope_stack_var: ContextVar[list[LoopScope] | None]` plus `set_current_scope_stack`, `reset_current_scope_stack`, `get_current_scope_stack` helpers. `TaskFactory.create_task` (line 80) reads the contextvar and inherits `scope_stack` when set; returns `scope_stack=None` when not set (backward compatible).
   - **`node_handlers/base.py:147-176`** — `invoke_handler` gains `current_scope_stack: Any = None` kwarg. Sets/resets the contextvar around the handler invocation via try/finally.
   - **`orchestration_engine.py`** — all 9 `invoke_handler` call sites updated to pass `current_scope_stack=task.scope_stack` (lines 551, 646, 710, 868, 1210, 1687, 1802, 1871, 2005).

4. **`initialize_loop_queued` method** (`event_driven_loop_driver.py:592`) — the queued-mode entry point. Does init phase only: calls existing `initialize_loop`, applies `loop_state_updates` to the run, initialises `iter_progress` on `loop_state` (empty dict), publishes init events, enqueues first body task(s) via `enqueue_or_dispatch`, handles empty-collection short-circuit. Returns immediately — no while-loop. Iteration advancement happens externally via `LoopIterationAction`.

5. **Engine queued-vs-inline routing** (`orchestration_engine.py:492-525`) — the Phase 5.2.1 LOOP + driver branch now branches on `self._queue is not None`: calls `initialize_loop_queued` for queued mode, `run_loop` for inline mode. Persistence fix applied at both paths' return.

6. **`maybe_enqueue_loop_iteration` body-completion hook** (`orchestration_engine.py:121-207`) — public async method on `OrchestrationEngine`. Gate conditions: queue wired, driver wired, task has `scope_stack` with non-empty `invocation_id`, innermost scope's `loop_node_id` maps to an active loop. When gate passes: records `node_id` in `iter_progress[iter_idx]`, sweeps `run.context.skipped_nodes` into progress (handles CONDITION-gated bodies where unchosen branches are cascade-skipped), runs `is_iteration_done` BFS, enqueues `LoopIterationAction` when iteration complete. No-op for inline mode, non-loop-body tasks, and already-terminated loops.

7. **Hook placement in four code paths:**
   - **`_exec_builtin_queued`** (`orchestration_engine.py:1788`) — after handler result events are published. Covers BUILTIN body nodes (COMPUTE, CONDITION, SWITCH, etc.) including LOOP_EXIT.
   - **`_exec_pending_queued`** (`orchestration_engine.py:1849`) — after PENDING task is created. Covers INPUT, HUMAN_ACTION, etc.
   - **`complete_task_use_case.py`** — after successor processing. Covers TASK (worker) bodies, HUMAN_ACTION/DATA_EDIT/FILE_UPLOAD submissions.
   - **`resume_pending_task_use_case.py`** — after successor processing. Covers the resume path for PENDING tasks.

8. **Cascade-skip sweep** — built into the `maybe_enqueue_loop_iteration` helper (Part 6). Sweeps ALL `run.context.skipped_nodes` into `iter_progress` on every call. Since the iteration-done check uses runtime BFS (not a precomputed body set), non-body skipped nodes in progress are harmless — they're never visited by the BFS. This correctly handles CONDITION-gated loop bodies where the unchosen branch is cascade-skipped and must count as "done" for iteration advancement.

9. **Iter-boundary cleanup** (`orchestration_engine.py:2026-2051`) — added to `orchestrate_loop_iteration` after the existing logic advances to the next iteration. Three operations:
   - Drops the finished iteration's `iter_progress[iteration_index]` bucket.
   - Initialises the new iteration's `iter_progress[new_iter]` to an empty list.
   - BFS-wipes body nodes from `completed_nodes` and `skipped_nodes` via `iterate_body_subgraph` so they're re-runnable in the new iteration. Fixes the latent multi-node-body re-execution bug (Issue A) where the engine's completion guard refused to re-dispatch nodes already in `completed_nodes`.

10. **`_dispatch_iteration_body` scope_stack fix** (`event_driven_loop_driver.py`) — changed from cumulative `with_pushed_scope` to explicit `task.scope_stack = list(enclosing) + [iter_scope]` direct assignment. Prevents double-push when contextvar inheritance (Part 3) is active — the contextvar sets the ambient scope, and the cumulative push would duplicate the enclosing scopes.

**Test files (3 new, +32 tests):**

- **`tests/unit/test_loop_driver_body_subgraph.py`** (24 tests) — unit tests for all 6 BFS helpers: single-node body, multi-node linear body, CONDITION-branched body, cascade-skip iteration-done detection, LOOP_EXIT termination, back-edge skipping, post-loop contamination guard, nested inner-loop traversal (visited as one node), empty body, edge-type detection helpers.

- **`tests/unit/test_task_factory_scope_stack.py`** (5 tests) — contextvar inheritance: no contextvar set yields `scope_stack=None` (baseline), contextvar set inherits stack, nested scope stacks inherit full chain, inherited stack is a copy not a reference, contextvar resets cleanly between async contexts via `contextvars.copy_context()`.

- **`tests/integration/test_queued_event_driven_loop.py`** (3 end-to-end tests) — full engine + `MemoryOrchestrationQueue` + driver:
  - `test_queued_single_node_body_loop_runs_all_iterations` — 3-item sequential LOOP with single TASK body. Drives the queue synchronously by dequeuing and calling `engine.orchestrate_execute_node` / `engine.orchestrate_loop_iteration`. Asserts all iterations complete, `LoopCompleted` fires, exit task runs, run reaches COMPLETED.
  - `test_queued_multi_node_body_loop_advances_via_iter_progress` — 2-item LOOP with body = entry (COMPUTE) → leaf (COMPUTE). Asserts `iter_progress` correctly tracks both body nodes per iteration and only enqueues `LoopIterationAction` once both have completed.
  - `test_queued_loop_with_conditional_body_branch` — LOOP with body containing a CONDITION that gates two branches. Asserts cascade-skipped nodes count toward `iter_progress` so the loop advances even when only one branch runs per iteration.

**Test files modified (4):** `test_resume_input_node.py`, `test_resume_pending_task.py`, `test_node_builder_execution.py` — added `maybe_enqueue_loop_iteration = AsyncMock()` to engine mocks. `test_context_updated_event.py` — `_SpyEngine` gains the new method.

**Verification:** 1450 passed, 2 skipped, 0 failed (+32 from the 1418 baseline). Zero regressions because:
- Phase 5.2.1 inline `run_loop` path is unchanged when `queue is None`.
- `scope_stack` contextvar defaults to `None` so `TaskFactory.create_task` is unchanged for non-loop tasks.
- `maybe_enqueue_loop_iteration` is a no-op when queue/driver is None.
- Persistence fix is always correct (saving the run at the end of LOOP dispatch is idempotent).
- The handler delegation shims still forward to the same driver methods.

**What Phase 5.2.2 deliberately does NOT do:**

- **Does not include a broker/Kafka end-to-end test.** The `MemoryOrchestrationQueue` integration tests prove the mechanism; a broker-backed test needs real infrastructure and is deferred.
- **Does not delete `_run_loop_iterations`.** The legacy walker stays for `event_driven_loop_driver=None` callers (flag OFF). *(Resolved in Phase 6.3 — deleted.)*
- **Does not delete the handler's `_ed_*` delegation shims.** Phase 5.2.1 scope decision; stays in place. *(Resolved in Phase 6.3 — deleted.)*
- **Does not implement `LOOP_CONTINUE` / `LoopIterationSkipped`.** Separate deferred backlog item. *(Resolved in Phase 7.2 — `LOOP_CONTINUE` handler + `LoopContinueRequested` event implemented.)*
- **Does not implement failed-iteration outcome propagation.** `outcome="failed"` still deferred. *(Partially resolved — `iter_failed_nodes` tracking added in `iteration_complete`, outcome set to `"failed"` when body node fails.)*
- **Does not fix parallel-mode per-iteration skip tracking.** `run.context.skipped_nodes` is global across concurrent iterations. For parallel-mode loops with multiple in-flight iterations, a node skipped in iter N could incorrectly count toward iter M's progress. Sequential mode is correct because iterations complete one at a time. Documented as a known limitation. *(Still active.)*
- **Does not fix the condition-false walker routing bug.** Separate deferred backlog item. *(Resolved in Phase 6.3 — walker deleted; `_emit_loop_completed_and_exit` handles condition-false exit directly.)*

**What this unlocks:**

- **Queued-mode loops work end-to-end.** Sequential collection loops with single-node and multi-node bodies run to completion via the orchestration queue. The flow is: LOOP → `initialize_loop_queued` (enqueue first body) → worker processes body → `maybe_enqueue_loop_iteration` → `LoopIterationAction` → worker calls `orchestrate_loop_iteration` → next iteration → ... → `LoopCompleted` → exit task.
- **`scope_stack` propagates through deep body chains.** Successor tasks created inside handlers automatically inherit the parent's scope via contextvar. This closes the Phase 4c.4.4 latent limitation — deep body tasks (e.g., a CONDITION's successor inside a loop body) now carry the enclosing `LoopScope` so `maybe_enqueue_loop_iteration` can attribute them to the correct iteration.
- **Multi-node body re-execution across iterations.** The iter-boundary cleanup in `orchestrate_loop_iteration` BFS-wipes body nodes from `completed_nodes` / `skipped_nodes` before each new iteration. Without this, the engine's completion guard refused to re-dispatch nodes already in `completed_nodes`, causing multi-node bodies to stall after the first iteration.
- **Phase 5.2.3+ — Walker retirement pathway.** With queued-mode loops closed, the `EVENT_DRIVEN_LOOP` flag covers both inline and queued execution. Once the flag is default-on in production, the legacy `_run_loop_iterations` walker and the handler's `_ed_*` shims become removable.

### Phase 6.1 — Uniform execution model (foundation + handler migration) — **DONE 2026-04-12**

**Why now?** The orchestration engine had three different execution paths (`_exec_builtin_queued`, `_exec_task_queued`, `_exec_pending_queued`) with two different routing mechanisms (`taken_edge_ports` for CONDITION/SWITCH, `compute_fanout` for everything else). This meant the engine needed to know the node type to decide how to dispatch, which port to follow, and when to route successors. Phase 6 unifies all node types behind a single execution path where the handler tells the engine WHAT happened (via `output_port` + `completion_mode`) and the engine decides WHERE to go next (via pure edge routing). This replaces the planned Phase 5.2.3+ per-BUILTIN driver extraction with a simpler, more general approach.

**Design principle:** Handlers return what happened + which port. Engine decides where to go.

**What landed:**

1. **`NodeHandlerResult` new fields** (`node_handlers/base.py`) — seven new fields with defaults for backward compatibility:
   - `output_port: str = "out_default"` — which output port the handler completed on. Simple nodes use `"out_default"`, port-routed nodes use `"success"`/`"failure"`, LOOP uses `"loop_complete"` when done. Branching nodes (CONDITION/SWITCH) continue to use `taken_edge_ports`.
   - `completion_mode: str = "immediate"` — how the engine should react:
     - `"immediate"` — node done, mark completed, route to successors now (TRIGGER, COMPUTE, CONDITION, SWITCH, MERGE quorum met, PARALLEL, LOOP_EXIT, LOOP when done)
     - `"dispatched"` — dispatched to external worker, mark RUNNING, publish task.dispatched (TASK)
     - `"pending"` — paused for user input, save PENDING task, publish events (INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD)
     - `"child_run"` — started child workflow, task stays RUNNING (WORKFLOW)
     - `"loop_body"` — started loop iterations, enqueue body tasks (LOOP with body)
     - `"deferred"` — save state only, no routing (MERGE quorum not met)
   - `pending_task`, `resolved_input`, `worker_type`, `child_workflow_metadata`, `loop_body_tasks` — mode-specific data.

2. **`compute_fanout` `expected_port_override` parameter** (`dispatcher/fanout.py`) — when provided, bypasses the `_PORT_ROUTED_TYPES` check and uses the override port directly for edge filtering. This lets the handler explicitly select its output port instead of the engine inferring it from `task.status`.

3. **`UNIFORM_EXECUTION` feature flag** (`app_config.py`, `bootstrap.py`) — boolean flag (default `false`), same pattern as `EVENT_DRIVEN_LOOP`. When true, `_execute_node_inner` routes to `_exec_uniform` instead of the legacy three-way branch.

4. **`_exec_uniform` method** (`orchestration_engine.py`, ~150 lines) — single entry point for ALL node types. Flow: emit task.dispatched → invoke handler → apply state updates → branch on `completion_mode` → (immediate: mark completed + route; dispatched: mark RUNNING + publish; pending: save + publish; child_run: start child; loop_body: enqueue body; deferred: noop).

5. **`_route_to_successors` method** (`orchestration_engine.py`, ~90 lines) — pure edge routing. Two modes: (a) `taken_edge_ports` populated (CONDITION/SWITCH): follow only those edges; (b) `output_port` (all others): call `compute_fanout(expected_port_override=output_port)` for port filtering + cascade-skip BFS, then emit EdgeTraversed + create successor tasks + enqueue `ExecuteNodeAction`.

6. **All handlers migrated to dual-mode** — each handler now sets `output_port` + `completion_mode` in addition to the legacy `tasks_to_dispatch`. The uniform engine reads the new fields; the legacy engine reads `tasks_to_dispatch`. Both paths work simultaneously (note: LOOP_CONTINUE was added later in Phase 7.2):

   | Handler | `completion_mode` | `output_port` |
   |---|---|---|
   | TRIGGER | `immediate` | `out_default` |
   | LOOP_EXIT | `immediate` | `out_default` |
   | COMPUTE | `immediate` | `success` |
   | MERGE | `deferred` (quorum not met) / `immediate` (quorum met) | `out_default` |
   | PARALLEL | `immediate` | `out_default` |
   | CONDITION | `immediate` | (via `taken_edge_ports`) |
   | SWITCH | `immediate` | (via `taken_edge_ports`) |
   | TASK | `dispatched` | — |
   | INPUT | `pending` | — |
   | HUMAN_ACTION | `pending` | — |
   | DATA_EDIT | `pending` | — |
   | FILE_UPLOAD | `pending` | — |
   | WORKFLOW | `child_run` | — |
   | LOOP | `loop_body` / `immediate` | `loop_complete` (when done) |

**Verification:** 1450 passed, 2 skipped (flag off); 54 integration tests passed (flag on). Zero regressions because all changes are additive — new fields have defaults, new engine methods are gated by the flag, handlers populate both old and new fields.

**What Phase 6.1 deliberately does NOT do:**

- **Does not remove the legacy execution paths.** `_exec_builtin_queued`, `_exec_task_queued`, `_exec_pending_queued` stay behind the `else` branch. Removal is Phase 6.3 after soak period with flag on.
- **Does not clean up `SuccessorProcessor`.** The recursive BUILTIN processing in `SuccessorProcessor.process()` is still active. When `UNIFORM_EXECUTION=true`, successors come through `ExecuteNodeAction` which goes through `_exec_uniform` — the SuccessorProcessor's recursive path is bypassed naturally because `orchestrate_continue` enqueues via `_enqueue_or_dispatch` which creates `ExecuteNodeAction`. Formal cleanup is Phase 6.2.
- **Does not remove `tasks_to_dispatch` from handlers.** Dual-mode keeps both paths working. Cleanup after flag flip.
- **Does not remove `_run_loop_iterations` walker.** Still used when `event_driven_loop_driver=None` (flag off). Removal after both `EVENT_DRIVEN_LOOP` and `UNIFORM_EXECUTION` are default-on.

**What this unlocks:**

- **Uniform node execution.** All 16 node types follow the same dispatch → execute → complete-with-port → route-successors cycle. The engine is a pure edge router — it doesn't inspect `node.node_type` to decide how to dispatch or route.
- **Phase 6.2 — SuccessorProcessor cleanup.** With uniform execution, `SuccessorProcessor.process()` no longer needs the `_evaluate_branching_node` fast path or recursive BUILTIN processing. It becomes a simple "compute_fanout + enqueue" step.

### Phase 6.2 — SuccessorProcessor simplification — **DONE 2026-04-12**

**Why now?** With `UNIFORM_EXECUTION=true`, successors are dispatched via `ExecuteNodeAction` through the queue, and `_exec_uniform` handles all node types uniformly. `SuccessorProcessor.process()` no longer needs the recursive BUILTIN processing path, the `_evaluate_branching_node` fast path, or handler invocation — those responsibilities moved to `_exec_uniform`. Phase 6.2 strips `SuccessorProcessor` down to its essential job: compute fan-out and return READY tasks for the caller to enqueue.

**What landed:**

1. **`app/layer1_domain/services/successor_processor.py`** — `process()` now delegates exclusively to `_process_uniform()`:
   ```python
   async def process(self, completed_task, run, workflow, dag_info, depth=0):
       # Phase 6.3: all processing goes through the uniform path.
       return await self._process_uniform(completed_task, run, workflow, dag_info)
   ```
   - The old recursive BUILTIN path (handler invocation, inline `NodeOutputProduced` emission, `TaskDispatched`/`TaskCompleted` events) is removed.
   - `_process_uniform` computes fan-out (edge filtering + cascade-skip), emits `EdgeTraversed` and `TaskSkipped` events, creates READY tasks, and returns them for the engine to enqueue.
   - Does NOT invoke handlers, does NOT recurse, does NOT emit `TaskDispatched`/`TaskCompleted`/`NodeOutputProduced` — those are all handled by the engine's `_exec_uniform` when the `ExecuteNodeAction` is dequeued.

2. **Removed code:**
   - `_evaluate_branching_node` method — no longer needed when the engine handles all branching via `_route_to_successors`.
   - Recursive BUILTIN successor processing — replaced by sequential `ExecuteNodeAction` enqueue.
   - Inline handler invocation — handlers are invoked by `_exec_uniform`, not by the successor processor.

### Phase 6.3 — Legacy path removal + flag defaults — **DONE 2026-04-12**

**Why now?** With Phase 6.1 (uniform model) and Phase 6.2 (successor processor cleanup) proven, the legacy execution paths are dead code. Phase 6.3 removes them and flips both feature flags (`UNIFORM_EXECUTION`, `EVENT_DRIVEN_LOOP`) to default `True`, making the event-driven architecture the default for all deployments.

**What landed:**

1. **`app/layer4_frameworks/config/app_config.py`** — both flags flipped:
   - `EVENT_DRIVEN_LOOP: bool` — default changed from `False` to **`True`**
   - `UNIFORM_EXECUTION: bool` — default changed from `False` to **`True`**

2. **`app/layer1_domain/services/orchestration_engine.py`** — legacy methods deleted:
   - `_exec_builtin_queued` — DELETED (was the BUILTIN queued-mode execution path)
   - `_exec_task_queued` — DELETED (was the TASK queued-mode execution path)
   - `_exec_pending_queued` — DELETED (was the PENDING queued-mode execution path)
   - `_run_loop_iterations` — DELETED (was the imperative loop walker)
   - All execution now routes through `_exec_uniform` unconditionally

3. **`app/layer1_domain/services/node_handlers/loop_handler.py`** — legacy code removed:
   - The `event_driven: bool` constructor kwarg removed — always event-driven now
   - Legacy methods deleted: `_initialize_loop`, `_handle_iteration_complete`, `_dispatch_exit`, `_get_body_entry`
   - Handler is now a thin delegation shim (~60 lines) that forwards to `EventDrivenLoopDriver` for `initialize_loop` or `iteration_complete`, then sets `completion_mode` based on loop state

4. **`app/layer1_domain/services/successor_processor.py`** — `process()` unconditionally delegates to `_process_uniform()` (Phase 6.3 comment at line 116)

5. **Handler dual-mode cleanup** — handlers no longer need to populate legacy `tasks_to_dispatch` for the removed engine paths. The `output_port` + `completion_mode` fields are the sole contract with the engine.

**Verification:** 1401 passed, 51 skipped, 0 failed. The 49 additional skipped tests (vs. Phase 5.2.2's "2 skipped") are tests that were tied to the legacy execution paths and are no longer applicable with the legacy code removed.

**Properties achieved:**

- **Single execution path.** Every node type — BUILTIN, TASK, PENDING — follows the same `dispatch → execute → complete-with-port → route` cycle through `_exec_uniform`.
- **No feature flag branching in hot path.** `UNIFORM_EXECUTION` and `EVENT_DRIVEN_LOOP` are still in config for rollback, but the engine no longer checks them at runtime — the legacy paths they gated are gone.
- **Reduced engine complexity.** The three-way execution branch, the imperative loop walker, and the recursive successor processing are all removed. The engine is a pure event-driven edge router.
- **Architectural clarity.** Adding a new node type now means implementing a handler that returns `output_port` + `completion_mode` — no engine changes needed.

### Phase 7.1 — Loop output accumulation — **DONE 2026-04-13**

**Why now?** The Phase 3 spike's open question §1 identified that body node outputs were overwritten each iteration — only the last survived. Downstream nodes referencing `$.loop_node_id.output.results` would get only the final iteration's output. Phase 7.1 closes this gap by accumulating each iteration's body output into `loop_state["iteration_results"]` during `iteration_complete`, then building the final loop output as `{"results": [...]}` when `LoopCompleted` fires.

**What landed:**

1. **`app/layer1_domain/services/loop_driver/event_driven_loop_driver.py`** — `initialize_loop` (line 376):
   - `loop_state["iteration_results"] = []` — initialised as empty list alongside other loop state fields.

2. **`event_driven_loop_driver.py`** — `iteration_complete` (lines 480-488):
   - After each iteration, looks up the body entry node's output from `run.context.variables[body_entry.id]`.
   - Appends `{"index": iteration_idx, "item": items[iteration_idx], "output": body_output}` to `loop_state["iteration_results"]`.

3. **`event_driven_loop_driver.py`** — `_emit_loop_completed_and_exit` (line 1040-1041):
   - Final loop output includes `"results": loop_state.get("iteration_results", [])`.
   - Downstream nodes reference `$.loop_node_id.output.results` to get the full collection.

### Phase 7.2 — LOOP_CONTINUE node — **DONE 2026-04-13**

**Why now?** Phase 4c.4.5 delivered `LOOP_EXIT` (break) but explicitly deferred `continue` — "different mechanism that would need a new event type". Phase 7.2 completes the loop control flow vocabulary with `LOOP_CONTINUE`, which skips the rest of the current iteration's body and advances to the next iteration.

**What landed:**

1. **`app/layer1_domain/value_objects/node_type.py`** — `NodeType.LOOP_CONTINUE = "LOOP_CONTINUE"` (line 22).

2. **`app/layer1_domain/events.py`** — `LoopContinueRequested` event (line 319). Fields: `invocation_id`, `scope_stack`, `loop_node_id`. Mirrors `LoopExitRequested`.

3. **`app/layer1_domain/services/node_handlers/loop_continue_handler.py`** (new, ~157 lines):
   - `LoopContinueNodeHandler` with shape `PURE_INLINE`, `completion_mode="immediate"`, `output_port="out_default"`.
   - Mirrors `LoopExitNodeHandler` design: scans `run.context.loop_state` in reverse insertion order to find the innermost active event-driven loop (non-empty `invocation_id`, not finished, not already continue-requested, not break-requested).
   - Sets `loop_state["continue_requested"] = True`.
   - Emits `LoopContinueRequested` with deterministic event id.
   - Returns empty `tasks_to_dispatch` (dead-end for this branch — remaining body nodes are skipped at the iteration boundary).

4. **`app/layer1_domain/services/loop_driver/event_driven_loop_driver.py`** — `iteration_complete` (lines 557-565):
   - After the break check, checks `loop_state.get("continue_requested", False)`.
   - When true: resets the flag, sets `skip_condition_reeval = True` (continue always advances regardless of condition), mutates the just-emitted `LoopIterationCompleted.outcome` to `"skipped"`.
   - Falls through to the sequential/parallel advancement logic to dispatch the next iteration.

5. **`bootstrap.py`** — `LoopContinueNodeHandler` registered for `NodeType.LOOP_CONTINUE`.

6. **`app/layer1_domain/services/projector/run_state_projector.py`** — `LoopContinueRequested` added to the explicit passthrough branch in `apply_event`.

### Phase 7.3 — `max_concurrent_iterations_per_run` config — **DONE 2026-04-13**

**Why now?** The Phase 3 spike's open question §4 identified the risk of resource exhaustion from deeply nested or wide parallel loops. Phase 7.3 adds a configurable limit on the total number of in-flight loop iterations across all loops in a single run.

**What landed:**

1. **`app/layer4_frameworks/config/app_config.py`** (line 242-243):
   - `MAX_CONCURRENT_ITERATIONS_PER_RUN: int = int(os.getenv("MAX_CONCURRENT_ITERATIONS_PER_RUN", "100"))` — default 100.

2. **`app/layer1_domain/services/orchestration_engine.py`** (line 76, 93):
   - `OrchestrationEngine.__init__` gains `max_concurrent_iterations_per_run: int = 100` parameter, stored as `self._max_concurrent_iterations_per_run`.

3. **`bootstrap.py`** (line 338):
   - `max_concurrent_iterations_per_run=settings.MAX_CONCURRENT_ITERATIONS_PER_RUN` passed to the engine constructor.

### Phase 7.4-7.7 — Legacy cleanup (covered by Phase 6.3)

The remaining Phase 3 backlog items — removal of `_run_loop_iterations`, removal of legacy `LoopNodeHandler` paths, removal of `event_driven` kwarg — were completed as part of Phase 6.3 (see above). The old imperative walker, the three-way execution branch, and all dual-mode handler code have been deleted.

### Phase 6/7 node type coverage matrix (updated 2026-04-13)

All **16 node types** (up from the original 14) now flow through the uniform `_exec_uniform` path:

| Handler | Node Type | Shape | `completion_mode` | `output_port` |
|---|---|---|---|---|
| TRIGGER | `TRIGGER` | `PURE_INLINE` | `immediate` | `out_default` |
| LOOP_EXIT | `LOOP_EXIT` | `PURE_INLINE` | `immediate` | `out_default` |
| LOOP_CONTINUE | `LOOP_CONTINUE` | `PURE_INLINE` | `immediate` | `out_default` |
| COMPUTE | `COMPUTE` | `ASYNC_INLINE` | `immediate` | `success` |
| MERGE | `MERGE` | `PURE_INLINE` | `deferred` / `immediate` | `out_default` |
| PARALLEL | `PARALLEL` | `ASYNC_INLINE` | `immediate` | `out_default` |
| CONDITION | `CONDITION` | `ASYNC_INLINE` | `immediate` | (via `taken_edge_ports`) |
| SWITCH | `SWITCH` | `ASYNC_INLINE` | `immediate` | (via `taken_edge_ports`) |
| TASK | `TASK` | `DISPATCH_EXTERNAL` | `dispatched` | — |
| INPUT | `INPUT` | `PENDING_USER` | `pending` | — |
| HUMAN_ACTION | `HUMAN_ACTION` | `PENDING_USER` | `pending` | — |
| DATA_EDIT | `DATA_EDIT` | `PENDING_USER` | `pending` | — |
| FILE_UPLOAD | `FILE_UPLOAD` | `PENDING_USER` | `pending` | — |
| WORKFLOW | `WORKFLOW` | `ASYNC_INLINE` | `child_run` | — |
| LOOP | `LOOP` | `ASYNC_INLINE` | `loop_body` / `immediate` | `loop_complete` (when done) |

### Phase 4c.3 — `dag_info` fallback + `USE_PROJECTOR_READ_MODEL` flag — **DONE 2026-04-11**

**Why now?** Phase 4c.1 built the projector view store but left a hard limit: `get_node_output` couldn't be served because it reads `view.entry_nodes` (empty on projector — comes from `dag_info`) and calls `get_by_root_id` for nested-run lookup (which raised `NotImplementedError`). That meant the `USE_PROJECTOR_READ_MODEL` flip couldn't actually be turned on in production without silently breaking one of the three Phase 4a-migrated features. Phase 4c.3 closes this gap with a **hybrid fallback model** — dynamic run state stays event-sourced, static workflow metadata delegates to repos — and adds the bootstrap-level flag.

**Design pattern: hybrid read model**

The projector reducer was deliberately designed to rebuild **only** event-sourced fields. `dag_info` is static workflow metadata produced by `DAGPreparer.prepare(workflow)` at orchestration time — it doesn't belong in events. Forcing it through the event log would be philosophically wrong and would bloat every run's log with redundant static data.

Instead, the view store takes optional `workflow_repo` and `run_repo` parameters. When wired:

- `dag_info` / `entry_nodes` → lazy `workflow_repo.get_by_id(workflow_id)` → `DAGPreparer.prepare(workflow)`, cached per view instance
- `get_by_root_id` → `run_repo.get_by_root_id(root_id)` to discover run ids, then `self.get(run_id)` for each (which replays via the event log and benefits from the Phase 4c.2 cache)

Every individual run's dynamic state still comes from the event log. Only the parent→children discovery and static workflow definitions come from repos. This is a transitional hybrid — Phase 5.2 event-driven BUILTIN dispatch will eventually source cross-run correlation from a `ChildRunStarted`-event-sourced index, at which point `run_repo` becomes optional there too.

**What landed:**

1. **`app/layer4_frameworks/run_context/projector_view_store.py`** — `ProjectedView` + `ProjectorRunContextViewStore` extended
   - `ProjectedView.__init__` gains `workflow_repo: IWorkflowRepository | None = None`. New instance variable `_cached_dag_info: DAGInfo | None = None`.
   - `dag_info` property rewritten: returns cache if set; empty `DAGInfo()` if no workflow_repo or empty workflow_id; otherwise `workflow_repo.get_by_id` → `DAGPreparer.prepare` → cache → return. Graceful degradation when workflow_repo has no matching entry (e.g., deleted workflow).
   - `entry_nodes` property now delegates to `self.dag_info.entry_nodes` so it automatically picks up the fallback.
   - `ProjectorRunContextViewStore.__init__` gains optional `workflow_repo` and `run_repo` params. `get(run_id)` threads `workflow_repo` into every `ProjectedView` construction. `get_by_root_id` delegates to `run_repo.get_by_root_id` when available; still raises `NotImplementedError` when `run_repo` is None (preserves Phase 4c.1 contract for callers that opted in before the fallback).
   - Module docstring rewritten to document the hybrid model explicitly.

2. **`app/layer4_frameworks/run_context/cached_projector_view_store.py`** — `CachedProjectorRunContextViewStore` mirrors the same changes
   - Same constructor extension. `get(run_id)` threads `workflow_repo` into every `ProjectedView(state, workflow_repo=self._workflow_repo)` construction on both cold and warm paths.
   - `get_by_root_id` implementation benefits from the cache: each child run's `self.get(run.id)` call hits the per-run incremental cache rather than rebuilding from scratch.

3. **`app/layer4_frameworks/config/app_config.py`** — new `USE_PROJECTOR_READ_MODEL` setting (default `False`). Docstring explains the flip, when to use it, and warns that `EVENT_LOG_BACKEND=sqlite` or `hana` is recommended when enabled.

4. **`bootstrap.py`** — new `_build_run_context_view_store(settings, repos, event_log_repo)` factory alongside `_build_event_log_repo`. Selects `CachedProjectorRunContextViewStore(event_log_repo, workflow_repo=repos.workflow, run_repo=repos.run)` when `USE_PROJECTOR_READ_MODEL=true`, otherwise `LiveRunContextViewStore(repos.run)`. Logs the selected read model at INFO. Also warns if `USE_PROJECTOR_READ_MODEL=true` but `EVENT_LOG_BACKEND=memory` — the projector read model on an ephemeral log defeats the purpose.
   - The existing `run_context_view_store = LiveRunContextViewStore(repos.run)` call site replaced with `run_context_view_store = _build_run_context_view_store(settings, repos, event_log_repo)`.
   - New line exposing `container["run_context_view_store"] = run_context_view_store` (same pattern as Phase 4b.7's `event_log_repo` exposure — operators and tests can now inspect the wired store type).

5. **`tests/unit/test_cached_projector_view_store.py`** — 9 new tests in three classes
   - **`TestProjectedViewDagInfoFallback` (5 tests):** no workflow_repo → empty DAGInfo (Phase 4c.1 contract preserved); with workflow_repo → real DAGInfo via DAGPreparer; entry_nodes returns the correct tuple; **call-count spy** — 10 reads of `dag_info` / `entry_nodes` trigger exactly 1 `workflow_repo.get_by_id` call (memoization works); missing workflow_id returns empty DAGInfo without crashing; unknown workflow_id returns empty DAGInfo.
   - **`TestCachedStoreGetByRootId` (3 tests):** without run_repo raises NotImplementedError (preserved); with run_repo returns views for all descendants (parent + child in a parent/child root hierarchy); **skips runs without events** (the `self.get(run.id) -> None` path silently drops runs whose event log was cleared or whose events were never persisted).
   - **`TestProjectorStoreDagInfoFallback` (1 test):** mirrors the dag_info fallback test against the non-cached `ProjectorRunContextViewStore` so both stores stay in sync on the fallback contract.

6. **`tests/unit/test_bootstrap_event_log_selection.py`** — 2 new tests in `TestRunContextViewStoreSelection`
   - Default (`USE_PROJECTOR_READ_MODEL=false`) → container's `run_context_view_store` is `LiveRunContextViewStore`.
   - `USE_PROJECTOR_READ_MODEL=true` → container's store is `CachedProjectorRunContextViewStore` with **both** `_workflow_repo` and `_run_repo` set (not None). Catches regressions where bootstrap forgets to wire one of the fallbacks.

7. **`tests/integration/test_projector_view_store.py`** — 3 new end-to-end equivalence tests
   - **`test_get_run_context_equivalence_between_stores`**: drives a TRIGGER → COMPUTE workflow, runs `GetRunContextUseCase` with both stores, asserts equivalent `run_id`, `workflow_id`, `run_status`, entry key set, and per-entry `data_type`.
   - **`test_get_node_output_equivalence_with_dag_info_fallback`**: same workflow, runs `GetNodeOutputUseCase` against `compute-1`, asserts equivalent `node_id`, `node_name`, `status`, `data_type`, `file_id`. This test specifically validates that the projector's `entry_nodes` fallback actually unblocks the feature — `GetNodeOutputUseCase` reads `view.entry_nodes` at line 189 of the use case, and without the Phase 4c.3 fallback it would silently return an empty set and break the entry-node context-value fallback path.
   - **`test_get_node_output_nested_run_via_get_by_root_id_fallback`**: the most comprehensive test. Constructs a parent + child run hierarchy in the run_repo with matching `root_run_id`, seeds the event log with `RunStarted` + `NodeOutputProduced` + `RunCompleted` for both, then calls `GetNodeOutputUseCase` querying a node (`child-node`) that lives ONLY in the child workflow. The use case's nested-run lookup at line 170 calls `self.run_context_view_store.get_by_root_id(command.run_id)` — proving the Phase 4c.3 fallback chain works end-to-end: feature → `get_by_root_id` → `run_repo.get_by_root_id` → per-run `self.get()` → event log replay → `ProjectedView` → found node. Both stores produce equivalent results.

**Verification:** 1343 passed, 2 skipped, 0 failed (+14 tests from the 1329 baseline).

**Key properties now available:**

- **Read-model feature parity.** All three Phase 4a-migrated features produce equivalent output whether backed by `LiveRunContextViewStore` or `CachedProjectorRunContextViewStore` with both fallbacks wired. Proven by integration tests that run each use case twice and diff the output.
- **One-flag production flip.** Operators set `USE_PROJECTOR_READ_MODEL=true` in their environment to enable the event-sourced read model. Combined with `EVENT_LOG_BACKEND=sqlite` or `hana`, the full event-sourced chain is active in one env-var change.
- **Rollback is zero-cost.** Set `USE_PROJECTOR_READ_MODEL=false`, restart — back to the live store. No schema migration, no data conversion.
- **Per-view dag_info memoization** — a feature that reads `entry_nodes` and `dag_info.adjacency` on the same view triggers a single workflow_repo lookup, not two. The call-count spy test enforces this.
- **Graceful degradation everywhere.** Missing workflow_repo → empty DAGInfo (Phase 4c.1 contract). Missing run_repo → NotImplementedError (preserved). Deleted workflow → empty DAGInfo. Missing workflow_id → empty DAGInfo. Child run with cleared event log → silently skipped from get_by_root_id. None of these cases crash.

**What this closes:**

- The **final** engineering blocker for production wiring of the event-sourced read model.
- All items listed as "Remaining" under Phase 4b from Phase 4b.8 onwards that relate to the read-model flip.
- Phase 5.2's "authoritative read model" precondition — Phase 5.2 can now assume the read model is feature-parity with the live store for the existing Phase 4a feature set.

**What Phase 4c.3 deliberately does NOT do:**

- **Does not flip the default in `bootstrap.py`.** Defaults stay on `LiveRunContextViewStore`. Production teams opt in per environment.
- **Does not migrate new features.** The 3 Phase 4a features get the flip "for free" through their shared `run_context_view_store` dependency. New features added later can opt in or stay on the live store depending on what fields they read.
- **Does not rebuild `input_data`, `workflow_version`, `parent_run_id`, `depth_level`.** No feature reads them today. A future phase will add `RunInputReceived` / similar events if needed.
- **Does not eliminate `LiveRunContextViewStore`.** It stays as the default and as a rollback path. Phase 5.2 may retire it once the projector is proven under production load.
- **Does not add a cross-run event-sourced index.** The `get_by_root_id` fallback uses the run_repo, which is a transitional choice. Phase 5.2 will source cross-run correlation from `ChildRunStarted` events via a dedicated projection.

### Phase 4b.8 — HANA event log backend — **DONE 2026-04-11**

**Why now?** Phase 4b.7 delivered the bootstrap factory for backend selection and wired `"memory"` + `"sqlite"`. HANA was the last remaining backend to satisfy SAP BTP production deployments. Phase 4b.8 adds the third branch so `EVENT_LOG_BACKEND=hana` reuses the existing `HanaConnectionManager` already created by `create_infrastructure()` when `INFRA_MODE=sap`.

This closes the event-log durability story across all three deployment modes:

| Deployment | Setting | Backend |
|---|---|---|
| Local / mock / tests | `EVENT_LOG_BACKEND=memory` (default) | In-memory, ephemeral |
| Local dev, CI, container-only | `EVENT_LOG_BACKEND=sqlite EVENT_LOG_DB_PATH=/var/lib/workflow/events.db` | SQLite file |
| SAP BTP production | `EVENT_LOG_BACKEND=hana INFRA_MODE=sap` | HANA via shared `HanaConnectionManager` |

**What landed:**

1. **`app/layer4_frameworks/persistence/hana/hana_event_log_repo.py`** (new, ~220 lines)
   - `HanaEventLogRepository` implements `IEventLogRepository` against a duck-typed `IDatabaseConnection` (production: `HanaConnectionManager`; tests: stub).
   - SQL templates use `:p0`-style named placeholders matching the existing HANA repos (`hana_run_repo`, `hana_task_repo`, etc.). `HanaConnectionManager` translates them to positional `?` at query time.
   - Table name: unquoted `AIW_EVENT_LOG` (HANA case-folds unquoted identifiers to uppercase, matching the quoted name in the migration).
   - Dedupe strategy: HANA has no `INSERT OR IGNORE`. The repo attempts `INSERT`, catches any exception, inspects `errorcode`, and maps `301` (HANA unique-key duplicate) to `False` — matching the memory and SQLite backends' observable contract. All other exceptions propagate.
   - Sequence assignment via `SELECT COALESCE(MAX(SEQUENCE), 0) + 1 FROM AIW_EVENT_LOG WHERE RUN_ID = :p0`. Thread-safe for a single-process engine because the connection manager serialises through its pool; multi-replica safety relies on bus-level partitioning by `run_id` (Kafka partitions / Event Mesh consumer groups) — same invariant Phase 4.2 already assumes.
   - JSON payload round-trip: `json.dumps(payload, default=str)` on write, `json.loads` on read. Preserves nested dicts and lists byte-for-byte.
   - `_row_to_envelope` helper handles both lower-case (real `HanaConnectionManager` returns lowercased column names) and upper-case (test stubs may use whichever) dict keys — keeps the repo backend-agnostic within the fake-test ecosystem.
   - `clear(run_id)` queries `COUNT(*)` first to return the affected row count (since `execute_write` doesn't expose `rowcount`), then issues the `DELETE`. Matches the memory/SQLite contract for the return value.

2. **`db/migrations/025_create_aiw_event_log.sql`** (new)
   ```sql
   CREATE COLUMN TABLE "AIW_EVENT_LOG" (
       "RUN_ID"       NVARCHAR(64)   NOT NULL,
       "SEQUENCE"     INTEGER        NOT NULL,
       "EVENT_ID"     NVARCHAR(64)   NOT NULL,
       "TOPIC"        NVARCHAR(128),
       "EVENT_TYPE"   NVARCHAR(64),
       "TIMESTAMP"    NVARCHAR(64),
       "PAYLOAD"      NCLOB,
       "SOURCE"       NVARCHAR(64),
       "CAUSATION_ID" NVARCHAR(64),
       PRIMARY KEY ("RUN_ID", "SEQUENCE")
   );
   CREATE UNIQUE INDEX "IDX_AIW_EVENT_LOG_EVENT_ID"
       ON "AIW_EVENT_LOG" ("RUN_ID", "EVENT_ID");
   ```
   - Picked up automatically by the existing idempotent `migrate()` runner in `hana/migrations.py`. HANA error codes 288 (duplicate table) and 289 (duplicate index) are already handled as re-runnable no-ops, so the migration is safe to apply against a database that already has the table.

3. **`bootstrap.py`** — factory extension
   - `_build_event_log_repo` signature gains an optional `infra` kwarg (default `None`) so the HANA branch can access `infra.db` (the `HanaConnectionManager` instance created in `create_infrastructure()`). Backward-compatible — the existing memory/sqlite branches ignore `infra`.
   - New `backend == "hana"` branch: uses `infra.db` when available; falls back to `MemoryEventLogRepository` with a warning when `infra.db` is `None` (INFRA_MODE != "sap" or HANA init failed). The shadow log must never block engine startup.
   - Call site updated: `event_log_repo = _build_event_log_repo(settings, infra=infra)`.

4. **`tests/unit/test_hana_event_log_repo.py`** (new, 16 tests)
   - **SQLite-backed HANA stub** (`_SqliteBackedHanaStub`, ~40 lines): routes `execute_query` / `execute_write` through an in-memory `sqlite3` connection with a table mirroring the HANA schema. Translates `:p0` placeholders using the **real** `HanaConnectionManager._bind_params` helper, so the SQL the HANA repo emits is exercised verbatim. Maps `sqlite3.IntegrityError` on unique constraint to `_FakeHanaUniqueViolation` with `errorcode=301`, simulating HANA's dedupe-via-exception semantics. Gives real storage behavior (PK enforcement, unique-index enforcement, sequence ordering) without needing HANA in CI.
   - **Conformance mirror (11 tests):** append-and-list order, per-run monotonic sequence, dedupe within run, dedupe isolation across runs, clear-and-reset, clear returns zero for unknown run, clear allows sequence reuse, empty run_id → unscoped bucket, list unknown run → empty, nested-payload JSON round-trip.
   - **HANA-specific behavior (2 tests):** `test_non_dedupe_exception_propagates` — errors other than `errorcode=301` must bubble up unmodified (e.g., `errorcode=999` simulated "connection drop"); `TestHanaListByRunSince` — the Phase 4c.2 `list_by_run_since` method works on the HANA backend too.
   - **SQL-template smoke tests (3 tests):** verify the repo emits `INSERT INTO AIW_EVENT_LOG (RUN_ID, SEQUENCE, EVENT_ID, …) VALUES (:p0, …, :p8)` with exactly 9 positional params, and verify `SELECT … WHERE RUN_ID = :p0 AND SEQUENCE > :p1 ORDER BY SEQUENCE` on `list_by_run_since`. Catches template regressions even if the fake is silently forgiving.

5. **`tests/unit/test_bootstrap_event_log_selection.py`** (extended, +2 tests)
   - `test_hana_backend_without_infra_falls_back_to_memory`: `EVENT_LOG_BACKEND=hana` with no infra or `infra.db=None` returns `MemoryEventLogRepository` and logs a warning.
   - `test_hana_backend_with_infra_db_uses_hana_repo`: with a stub `infra.db` in place, the factory returns a `HanaEventLogRepository` that holds the exact db reference passed in.

**Verification:** 1329 passed, 2 skipped, 0 failed (+18 tests from the 1311 baseline).

**Why the `_SqliteBackedHanaStub` approach is sound:**

- Uses the real `HanaConnectionManager._bind_params` helper — if the placeholder format changes in HANA's production code, the stub tests break immediately.
- Real SQL execution against sqlite3 catches SQL-syntax regressions (typos, wrong column order, missing WHERE clauses) that a Python-level mock would not.
- Unique-constraint enforcement is REAL — the PK and unique index are in sqlite3's schema; the `IntegrityError → errorcode=301` mapping exercises the exact dedupe code path that would run in production.
- The stub is ~40 lines and does not duplicate the HANA repo — it's a thin adapter that makes testing possible without a live HANA.
- The test file is a drop-in pattern that the next HANA repo unit test can copy verbatim.

**What this closes:**

- The last missing backend for the event-log-as-source-of-truth story.
- Every `IEventLogRepository` backend in the codebase (memory, SQLite, HANA) now shares a conformance-verified behavior contract.
- SAP production deployments can enable the event log with a single env var change: `EVENT_LOG_BACKEND=hana`.

**What this deliberately does NOT do:**

- **Does not add HANA integration tests against a real HANA instance.** None of the existing HANA repos have those either; the project convention is to rely on the stub-based unit tests and manual verification at deployment time. When SAP BTP infrastructure is available, a future integration suite can reuse the conformance assertions from the stub tests unchanged.
- **Does not flip `EVENT_LOG_BACKEND` to `"hana"` by default.** Defaults stay `"memory"` exactly as Phase 4b.7 established. Operators opt-in.
- **Does not wire a HANA-specific migration invocation.** The existing idempotent `migrate()` runner in `hana/migrations.py` picks up `025_create_aiw_event_log.sql` automatically when `create_infrastructure()` sets up the HANA connection pool.
- **Does not add a cached-projector wiring for HANA.** `CachedProjectorRunContextViewStore` already works with any `IEventLogRepository` — flipping it on with HANA is zero code change, gated only on the unresolved `dag_info` fallback question.

### Phase 4c.2 — Cached projector view store — **DONE 2026-04-11**

**Why now?** Phase 4b.6 delivered durable event-log storage; Phase 4c.1 delivered a projector-backed view store. The last remaining blocker for production-wiring the projector read model was the **per-`get()` rebuild cost**: the direct store walks the entire event log for the run on every call. For a read path like `get_run_status` that may be called every few seconds per active run, this is unacceptable.

Phase 4c.2 closes the gap with a per-run incremental cache. After the first `get()`, the cache holds the projected state and the last sequence seen. Subsequent `get()`s ask the event log for events **since** the cached sequence — typically zero, returning O(1) — and only fold in the Δ when new events have arrived.

**What landed:**

1. **`app/layer2_application/interfaces/event_log_repository_interface.py`** — Protocol extension
   - New method: `list_by_run_since(run_id: str, min_sequence: int) -> list[EventLogEnvelope]`. Returns envelopes with `sequence > min_sequence` in append order. Enables the cache to ask "what's new?" in one query.
   - Docstring explicitly contrasts backend implementations: SQLite uses the `(run_id, sequence)` PK for O(log n) seek + O(Δ) fetch; memory backend filters the per-run list in O(|events_in_run|) but is still O(1) amortised on the hot path where the cache is warm and no new events exist.

2. **`app/layer4_frameworks/persistence/memory/memory_event_log_repo.py`** — `list_by_run_since` implementation
   - List comprehension filtering by `envelope.sequence > min_sequence` under the existing lock. Trivial.

3. **`app/layer4_frameworks/persistence/sqlite/sqlite_event_log_repo.py`** — `list_by_run_since` implementation
   - New `_SELECT_BY_RUN_SINCE_SQL` with `WHERE run_id = ? AND sequence > ? ORDER BY sequence`. Uses the PK index for the seek.
   - Extracted `_row_to_envelope` helper shared by `list_by_run` and `list_by_run_since` to avoid duplicating row-deserialisation.

4. **`app/layer4_frameworks/run_context/cached_projector_view_store.py`** (new, ~130 lines)
   - `CachedProjectorRunContextViewStore` — implements `IRunContextViewStore`. Per-run cache as `dict[str, tuple[ProjectedRunState, int]]` (state + last sequence). `threading.Lock` for concurrency hygiene matching the existing memory-repo pattern.
   - `get(run_id)`:
     - **Cold cache**: full rebuild via `list_by_run` + fold through `apply_event`. Stores the final state + max sequence.
     - **Warm cache**: `list_by_run_since(run_id, cached_sequence)`. If empty → return cached `ProjectedView` immediately (the O(1) hot path). Otherwise fold new events into the cached state via `dataclasses.replace`-style updates in `apply_event`, update the cache, return.
     - **Missing run**: empty log → return `None`, do NOT populate the cache.
   - `invalidate(run_id)` / `invalidate_all()` — explicit cache management for tests and future "after-clear" callers.
   - `cache_size()` — observability for tests.
   - `get_by_root_id` raises `NotImplementedError` (same contract as the direct projector store).
   - Reuses the existing `ProjectedView` adapter from Phase 4c.1 — no duplicate adapter code.

5. **`tests/unit/test_cached_projector_view_store.py`** (new, 15 tests)
   - **`list_by_run_since` conformance (7 tests)**: verified on both memory and SQLite backends independently. Covers the critical cases: min_sequence=0 returns everything, min_sequence=max returns empty, per-run scoping doesn't leak across runs.
   - **Cold get (1 test)**: first call rebuilds from full log and populates cache.
   - **Warm get without full rebuild (1 test, critical)**: monkey-patches `list_by_run` and `list_by_run_since` on the repo to count calls. After the cold get, the warm get must NOT call `list_by_run` — it must only call `list_by_run_since`. This is the call-counting spy test that proves the optimisation is actually working, not just theoretically correct.
   - **Incremental update (1 test)**: prime the cache, append 3 more events to the repo, warm get must fold them in and return the updated state.
   - **Invalidate (2 tests)**: `invalidate(run_id)` drops one entry; `invalidate_all()` drops everything. Post-invalidate `get()` triggers a fresh rebuild.
   - **Missing run (1 test)**: returns None, does not pollute the cache.
   - **Cross-run traversal gap (1 test)**: `get_by_root_id` raises with the same explicit error message.
   - **Variables round-trip (1 test)**: NodeOutputProduced events are folded correctly on both cold and warm paths, verifying Phase 5.1's `variables` rebuild works under the cache layer.

**Verification:** 1303 passed, 2 skipped, 0 failed (+15 tests from the 1288 baseline).

**Latency characteristics now available:**

- **Cold get (rare)**: O(|events|) — same as the direct projector store. Only happens once per run per process lifetime.
- **Warm get, no new events (the hot path)**: O(1) amortised. One index-seek query returns empty; reducer is not invoked. This is the query pattern during idle runs — exactly what `get_run_status` polls hit.
- **Warm get, Δ new events**: O(Δ) where Δ is the number of events since the last get. For typical query frequency (seconds) and event rate (dozens per second during active execution), Δ is small.
- **Memory footprint**: `ProjectedRunState` is ~1KB per run (variables dict is the dominant field). Even 10,000 concurrent runs fits comfortably in ~10MB.

**Design notes:**

- **No append hook** — the cache does NOT subscribe to `append` notifications. It's a pure read-side layer that lazily fetches deltas on each `get()`. Rationale: append hooks couple the write path to read-model consumers, which forces every downstream cache to be in-process. The delta-fetch model lets any read-side component (including ones in different processes, behind load balancers, etc.) share the same event log without coordination.
- **Append-only assumption** — the cache assumes the underlying log is monotonically growing. `clear(run_id)` breaks this: after clearing, the next appended event starts at sequence 1 again, but the cache still has `cached_sequence=3` (from pre-clear events) and `list_by_run_since(run_id, 3)` returns nothing. The cache would serve stale state. Documented in the module docstring; `invalidate(run_id)` is provided for callers who need to clear. Production runs are append-only, so this is a test-time concern.
- **Thread-safety** — the dict is guarded by a `threading.Lock`. The engine's publish path is already single-threaded per run, so contention is rare, but the lock is cheap insurance and consistent with the codebase's concurrency pattern.
- **Not wired into `bootstrap.py` by default** — same rationale as Phases 4b.6 and 4c.1: production read-side is still `LiveRunContextViewStore`. The flip to `CachedProjectorRunContextViewStore(SqliteEventLogRepository(settings.EVENT_LOG_DB_PATH))` is now a one-line configuration change; gated only on an operational decision about when to enable the event-sourced read model by default.

**Properties unlocked:**

- The projector read model is now **production-ready on the performance axis**. The only remaining gate is an operational decision, not an engineering one.
- Phase 5.2 (event-driven BUILTIN dispatch) can rely on O(1) `get()` for any read-side query-driven dispatch logic it introduces.
- Adding future read-side features that consume `IRunContextView` is zero-cost — they inherit the cached semantics automatically.

**What this does NOT close:**

- HANA backend for SAP production — separate engineering surface, still deferred.
- `bootstrap.py` production wiring — see above.
- Multi-process cache coherence — the in-memory cache is per-process. Multi-replica deployments would need either a shared cache (Redis) or a per-process cache that tails a shared event log via Kafka's native semantics. Out of scope for single-process engines.

### Phase 4c.1 — Projector-backed `IRunContextViewStore` — **DONE 2026-04-11**

**Why now?** Phase 5.1's `NodeOutputProduced` closed the last Phase 4b.3 gap by making `variables` rebuildable from the event log. Phase 4c.1 is the first code path that proves the event log is **authoritative** for a real production feature, not just for the rebuild test.

Before Phase 4c.1 the "event log → read model" property only existed inside `test_projector_rebuild.py`. That test reads the projected state directly and compares field-equality against the live aggregate. It doesn't answer the practical question: can a feature use case actually be served from the projector alone, without touching `IRunRepository`?

Phase 4c.1 answers "yes" by building a **drop-in `IRunContextViewStore` implementation** that backs onto the event log and swapping it into `GetRunStatusUseCase` in an integration test.

**What landed:**

1. **`app/layer4_frameworks/run_context/projector_view_store.py`** (new)
   - `ProjectedView` — concrete `IRunContextView` adapter over `ProjectedRunState`. Delegates every rebuildable field to the projected state (`run_id`, `workflow_id`, `status`, `started_at`, `completed_at`, `error`, `output_data`, `variables`, `completed_nodes`, `skipped_nodes`, `child_runs`). For fields the reducer cannot populate — `dag_info`, `input_data`, `workflow_version`, `parent_run_id`, `depth_level` — returns Protocol-legal defaults (empty `DAGInfo()`, `{}`, `""`, `None`, `0`). `root_run_id` falls back to `run_id`. `entry_nodes` returns `()` since entry-node metadata lives in `dag_info`.
   - `ProjectorRunContextViewStore` — `IRunContextViewStore` implementation that:
     - Takes an `IEventLogRepository` (no run repo dependency — pure event-sourced)
     - `get(run_id)`: calls `rebuild_run_state(run_id, event_log_repo)` and wraps the result in `ProjectedView`. Returns `None` when the log has no envelopes for the run (matches the `LiveRunContextViewStore` "not found" contract).
     - `get_by_root_id(root_id)`: raises `NotImplementedError`. Cross-run correlation isn't rebuildable from the Phase 4b.3 reducer today; features that need it (`get_node_output`'s nested-run lookup) must stay on `LiveRunContextViewStore`. The error message is explicit about this.

2. **`tests/integration/test_projector_view_store.py`** (new — 3 tests)
   - **`test_get_run_status_served_from_projector_matches_live`**: drives a TRIGGER → COMPUTE workflow end-to-end, then runs `GetRunStatusUseCase` twice — once with `LiveRunContextViewStore`, once with `ProjectorRunContextViewStore` — and asserts field-by-field equality on `id`, `workflow_id`, `status`, `error`, plus non-empty `started_at` / non-None `completed_at` (both are envelope timestamps, not byte-equal to aggregate timestamps).
   - **`test_projector_store_returns_none_for_unknown_run`**: missing run → None, matching live store's contract.
   - **`test_projector_store_get_by_root_id_raises`**: `NotImplementedError` is raised with a clear message pointing at the gap.

   During implementation, the first test caught a real gap: `RunStarted` is emitted by `start_run_use_case`, NOT by `engine.orchestrate_start`. When the test drives the engine directly, the log never receives `RunStarted`, so `workflow_id` stayed empty in the projected state. The test now publishes `RunStarted` manually to simulate the upstream use case. This is a meaningful finding: the projector only works if the production path (use case → engine) is exercised, not just the engine alone. Documented inline in the test.

**Production wiring:** deliberately NOT done. `bootstrap.py` still constructs `LiveRunContextViewStore`. Flipping to the projector store is a one-line change once:
- A persistent event-log backend lands (in-memory is lost on restart, so Phase 4b's SQLite/HANA work is a prerequisite for production use)
- The performance characteristics of per-`get()` rebuild are measured (today rebuild is O(|events_in_run|); a cached projector backed by tailing the log would be O(1))
- Features that need `dag_info` (`get_node_output`) either stay on the live store or gain a hybrid fallback

**Verification:** 1275 passed, 2 skipped, 0 failed (+3 tests from the 1272 baseline).

**Properties proved:**
- The Phase 4b.3/5.1 rebuild property holds end-to-end in a production feature code path.
- `GetRunStatusUseCase` does not depend on `IRunRepository` in any way when backed by the projector store — the event log is fully authoritative for run status.
- Swapping read backends is zero-cost at the feature layer: `GetRunStatusUseCase` sees only `IRunContextViewStore`, not the underlying implementation.

**What this unlocks:**
- Adding an event-sourced backend (e.g., a tailing projector with a cache) is now a drop-in at the store level.
- Phase 5.2 (event-driven BUILTIN dispatch) can now flip read-side features to the projector incrementally — for features that don't need `dag_info`, the flip is trivial.
- The "remaining ~48 features → `IRunContextView`" migration gains a clear target: once migrated, any feature whose field dependencies are a subset of `ProjectedRunState` can be served from the event log.

### Phase 4b — remaining work — **ALL DONE**

All items originally listed here have been completed in subsequent phases:

- **Phase 4c.4.2 — Loop event types** — DONE (see Phase 4c.4.2 section above)
- **Phase 4c.4.3 — Event-driven loop dispatcher** — DONE (see Phase 4c.4.3 section above)
- **Phase 4c.4.4 — Nested-loop merge quorum** — DONE (see Phase 4c.4.4 section above)
- **`handle_entry_nodes` unification** — cosmetic, deferred indefinitely
- **HANA live-integration test** — deferred, stub-based conformance tests sufficient

The event-sourced read-model story is complete for the 3 Phase 4a features:
- Durable event log (memory / SQLite / HANA via Phase 4b.1/4b.6/4b.8)
- Cached projector view store (Phase 4c.2)
- Hybrid fallback for static fields (Phase 4c.3)
- Bootstrap flag to opt in (`USE_PROJECTOR_READ_MODEL=true`)
- Integration equivalence tests proving the read model is feature-parity with the live store

---

## 8. Phase 5 — Event-driven BUILTIN (Option C)

Phase 5 is the large migration from the recursive-walker engine to a fully event-driven BUILTIN execution model. It lands incrementally.

### Phase 5.1 — `NodeOutputProduced` event + variable projection — **DONE 2026-04-11**

**Why this first?** Phase 4b.3 built the Projector but left one big gap: `run.context.variables` could not be rebuilt from the event log because no domain event carried the state-update payload. That gap made the Projector non-authoritative — any feature that read `variables` still had to hit the live aggregate. Phase 5.1 closes this gap with a **single new event type** and a matching reducer branch, making the Projector ready to serve as the authoritative read model for every field that matters to query-side use cases.

This is also the natural on-ramp to the full event-driven BUILTIN rewrite: once `NodeOutputProduced` is the canonical carrier of "a node's handler ran and produced these writes," the next step (Phase 5.2) is to route BUILTIN node execution itself through event handlers instead of the recursive walker — but Phase 5.2 does not need to touch the event shape, only the dispatch mechanism.

**What landed:**

1. **`app/layer1_domain/events.py`** — new `NodeOutputProduced` dataclass
   - Fields: `run_id`, `workflow_id`, `node_id` (the primary node whose handler produced the update), `state_updates` (dict containing every key written to `run.context.variables`, including name aliases).
   - Extends `DomainEvent` so it inherits `event_id`, `timestamp`, `source`, `root_run_id`, `depth` and flows through the existing publisher decorator chain unchanged.
   - Emitted BEFORE `TaskCompleted` so replay order is `NodeOutputProduced → TaskCompleted → EdgeTraversed → ...`. This matters: the reducer needs `variables` updated before any downstream input resolution looks for its upstream's output.

2. **`app/layer1_domain/services/successor_processor.py`** — emission from the recursive-walker state-update block
   - The `for key, value in handler_result.state_updates.items()` loop now collects the applied writes into a local `applied_updates: dict[str, Any]` including both primary keys and name aliases, then emits one `NodeOutputProduced` event per handler invocation via `results.events.append(...)` with a **deterministic event id** derived from `deterministic_event_id(completed_task.id, f"output:{target_node.id}")`. Same causation across replays → identical event id → event-log dedupe.

3. **`app/layer1_domain/services/orchestration_engine.py`** — emission from `apply_state_updates_as_files`
   - `apply_state_updates_as_files` is the funnel for 5 call sites (lines 376, 691, 1031, 1507, 1799 in the engine — inline dispatch, loop iteration, handle_entry_nodes, queued BUILTIN exec, queued loop iteration). Instrumenting the funnel once covers every path.
   - Signature gained two new keyword-only parameters: `source_node_id` and `causation_id`. Both default to empty so every existing caller keeps compiling — the event still fires with best-effort provenance (`primary_node_id` from the first state_updates key; `causation_id` falling back to `run.id`). Phase 5.2's event-sourced dispatcher will pass these explicitly for strict determinism, but Phase 5.1 keeps the call sites untouched to minimise risk.
   - Early-returns on empty `state_updates` so the contract is unchanged for callers that pass an empty dict.
   - Publishes via `self._publish_now("node.output.produced", event)` — the same direct-publish path the engine uses for run-lifecycle events. This funnels through the Phase 4b.1 `EventLogAppendingPublisher` decorator, so every write is captured in the log.

4. **`app/layer1_domain/services/projector/run_state_projector.py`** — reducer extension
   - `ProjectedRunState` gains a `variables: dict[str, Any]` field (default empty).
   - `apply_event` gains a `NodeOutputProduced` branch: builds a new dict via `dict(state.variables)` + `update(event.state_updates)` and returns via `dataclasses.replace`. `dict.update` semantics match the live aggregate — last write wins per key, and the alias writes replay in the same order they were emitted.
   - Module docstring updated: the "NOT rebuildable today" list removes `variables` and now only lists `dag_info` (static workflow metadata, correctly not event-sourced) and `input_data` (not yet published but the live `WorkflowRun.input_data` is authoritative).

5. **`tests/unit/test_run_state_projector.py`** — 4 new tests in a `TestNodeOutputProduced` class
   - `test_variables_populated_from_event`: single event with primary + alias writes populates `variables` correctly.
   - `test_multiple_events_merge_variables`: two distinct `NodeOutputProduced` events additively populate the dict.
   - `test_last_write_wins_per_key`: two events for the same key — the second wins (matches live aggregate).
   - `test_empty_state_updates_is_noop`: empty `state_updates` payload leaves variables untouched.

6. **`tests/integration/test_projector_rebuild.py`** — happy-path rebuild test now asserts `variables` round-trip
   - After running a TRIGGER → COMPUTE workflow, the test asserts every key in `live_run.context.variables` is also present in `projected.variables` with an exact-match value comparison. This is the **full round-trip property** — engine writes → event log → projector replay → identical state.
   - Wipe-then-rebuild test asserts `variables` is populated on the first rebuild, then confirms wipe + new run still works cleanly.

**Verification:** 1268 passed, 2 skipped, 0 failed (+4 new tests from the 1264 baseline). The happy-path rebuild test now asserts `variables` round-trip in addition to `status` / `completed_nodes` / `skipped_nodes`, which means the Projector is **fully consistent** with the live aggregate for every field it owns today.

**Properties this unlocks:**

- The Projector is now authoritative for `variables` in addition to lifecycle state. Phase 4a-migrated features (`get_run_context`, `get_node_output`) that read through `IRunContextView` could, in a future PR, be served from the projector without touching query-side code.
- The "event log → structural state" round-trip property holds for every field Phase 4a-migrated features actually read from today. This is the Phase 4b.3 deliverable completed.
- Phase 5.2 (event-driven BUILTIN dispatch) only needs to rewrite the dispatch mechanism; the payload shape is already right.

**What was NOT in Phase 5.1 (resolved in later phases):**

- **Event-driven BUILTIN dispatch** — resolved in Phase 6.1 (uniform execution model) and Phase 6.3 (legacy path removal).
- **Removal of direct aggregate mutation** — `successor_processor.py` still writes to `run.context.variables[key]` AND emits `NodeOutputProduced` (belt-and-braces). Full removal deferred until all readers consume the projection.
- **Nested-loop scope_stack** — resolved in Phase 4c.4 + Phase 5.2.2 (`scope_stack` contextvar propagation).
- **Variables-from-projection in feature use cases** — still deferred. The 3 Phase-4a-migrated features still consume `LiveRunContextViewStore` by default. Opt-in via `USE_PROJECTOR_READ_MODEL=true`.

### Phase 5.2 — Event-driven BUILTIN dispatch — **DONE (see Phases 5.2.1, 5.2.2, 6.1–6.3 above)**

This section originally described Phase 5.2 as future work. It has been fully superseded by:
- **Phase 5.2.1** (event-driven loop driver) — DONE 2026-04-12
- **Phase 5.2.2** (queued-mode event-driven loops) — DONE 2026-04-12
- **Phase 6.1** (uniform execution model) — DONE 2026-04-12
- **Phase 6.2** (successor processor simplification) — DONE 2026-04-12
- **Phase 6.3** (legacy path removal + flag defaults) — DONE 2026-04-12

All items from the original plan were delivered:
- Dispatcher module: `app/layer1_domain/services/dispatcher/` with `compute_fanout` (Phase 4b.2)
- Persistent event log: memory / SQLite / HANA backends (Phases 4b.1/4b.6/4b.8)
- Projector: `rebuild_run_state` + `CachedProjectorRunContextViewStore` (Phases 4b.3/4c.2)
- Deterministic event ids: `deterministic_event_id()` via uuid5 (Phase 4b.2)
- Feature-layer contracts stable: all 50+ use cases unchanged

---

## 7.5. Phase 4.5 — Metrics Review + Polyglot Decision Gate

**Goal:** decide, with data from Phase 2 and Phase 4, whether the Dispatcher should be extracted to Go as a standalone service. This is a **decision gate, not a deliverable** — the output is a one-page memo, "extract" or "do not extract," attached to this document.

### Inputs

Run Phase 4 under representative load for 2-4 weeks and collect:

- `node_handler_duration_seconds{handler_shape, node_type}` histogram.
- `variable_resolve_duration_seconds{strategy}` histogram.
- Dispatcher CPU utilization and events/sec per replica.
- Projector lag and Executor queue depth.
- End-to-end run latency p50/p95/p99.
- Replica counts required to serve peak load.

### Decision criteria

Extract the Dispatcher to Go only if **4+ of these 6** thresholds are met (see `polyglot-engine-decision.md` §6 for full discussion):

1. Sustained Dispatcher throughput required > 50k events/sec/replica.
2. p99 dispatch latency budget < 10 ms.
3. Replica count to serve peak load > 20.
4. Team has ≥ 2 engineers comfortable writing Go systems.
5. Long-term platform direction is Go.
6. Cost-per-tenant pressure is high (thin-margin SaaS).

If fewer than 4 are met, close the decision and proceed to Phase 5 in Python. The Dispatcher cut can be revisited later with fresh data.

### Phase 4.6 — Conditional Dispatcher Go extraction

Only executed if Phase 4.5 decision is "extract."

- Scope: rewrite only the Dispatcher module created in Phase 4. Everything else stays Python — Projector, Executor, feature layer, handlers, persistence, realtime.
- Interface contract: consumes `node.output` from Kafka, emits `node.input` + `wire.traversed` / `wire.skipped` / `wire.dead_letter`. Deterministic event ids via `uuid5(source_event_id, wire_id)` (already introduced in Phase 4).
- Migration approach: run Python Dispatcher and Go Dispatcher side-by-side against the same topics, shift traffic gradually via Kafka consumer-group routing, compare behavior, retire the Python Dispatcher.
- Effort estimate: 1-2 engineer-months. Possible because the Dispatcher has narrow, clearly defined inputs and outputs and no shared state with other services.

See [`polyglot-engine-decision.md`](./polyglot-engine-decision.md) for the full analysis of why the Dispatcher is the only service worth extracting, why Projector / Executor / feature layer stay Python, and why a full Go rewrite is explicitly rejected.

---

## 8. Phase 5 — Event-Driven BUILTIN (Option C) — **DONE**

Phase 5 has been fully completed through the incremental phases documented above:

- **Phase 5.1** — `NodeOutputProduced` event + variable projection (DONE 2026-04-11)
- **Phase 5.2.1** — Event-driven loop driver (DONE 2026-04-12)
- **Phase 5.2.2** — Queued-mode event-driven loops (DONE 2026-04-12)

Phase 6 then replaced the planned Phase 5.2.3+ per-BUILTIN driver extraction with a simpler, more general approach (the uniform execution model):

- **Phase 6.1** — Uniform execution model (DONE 2026-04-12)
- **Phase 6.2** — SuccessorProcessor simplification (DONE 2026-04-12)
- **Phase 6.3** — Legacy path removal + flag defaults (DONE 2026-04-12)

Phase 7 closed the remaining backlog items from the Phase 3 spike:

- **Phase 7.1** — Loop output accumulation (DONE 2026-04-13)
- **Phase 7.2** — LOOP_CONTINUE node (DONE 2026-04-13)
- **Phase 7.3** — `max_concurrent_iterations_per_run` config (DONE 2026-04-13)
- **Phase 7.4-7.7** — Legacy cleanup (covered by Phase 6.3)

### Remaining future work (not started)

These items from the original Phase 5 plan have been intentionally deferred:

- **Demote `DAGValidator` to editor linter.** Currently still gates run start. Low priority — runtime validation via per-node depth counters is sufficient.
- **Per-node failure policy fields.** `on_validation_error`, `on_exec_error`, `retry`, `on_timeout`, `on_output_validation_error` — not yet added to node descriptors.
- **Outbox pattern for handlers with side effects.** Deferred — current event-log-first publish path is sufficient.
- **Go Dispatcher extraction.** Pending Phase 4.5 metrics gate (see §7.5 above).

---

## 9. Risks (updated 2026-04-13)

**Mitigated risks (no longer active):**

- ~~**LOOP semantics drift.**~~ Fully mitigated by the Phase 3 spike + Phases 4c.4, 5.2, 7.1-7.3. All loop semantics (nested loops, break/continue, output accumulation, merge quorum) are covered by 96+ dedicated tests.
- ~~**Mock test rewrite.**~~ Phase 0 completed. All `MockFileServiceClient` tests updated.
- ~~**Hidden sync callers.**~~ Audited and resolved during Phase 0.
- ~~**Per-run lock behavior.**~~ Contextvar-based run tracking (Phase 1) eliminates cross-run serialization.

**Active risks:**

- **Bus latency for event-ified BUILTIN.** Every branch decision now round-trips through the orchestration queue. For workflows with 50+ CONDITION nodes, total latency rises by (events x one-way queue latency). Phase 2 metrics should quantify the budget. Mitigated by the fact that `MemoryOrchestrationQueue` is near-zero latency in mock/local modes.
- **Editor UX regression.** Users expect a red dot on broken wires at edit time. `DAGValidator` is still invoked by `validate_workflow` — this risk is active only if/when the validator is demoted to linter.
- **Parallel-mode per-iteration skip tracking.** `run.context.skipped_nodes` is global across concurrent iterations. For parallel-mode loops with multiple in-flight iterations, a node skipped in iter N could incorrectly count toward iter M's progress. Sequential mode is correct. Documented as a known limitation.

---

## 10. End-to-End Verification Checklist (updated 2026-04-13)

- **Phase 0 correctness:** concurrent-run stall test; confirm run B is not blocked by run A's file-service download. — **VERIFIED**
- **Phase 1 caching:** workflow with repeated file-backed references; assert download called once per `(file_id, query_path)` per run. — **VERIFIED** (5 tests)
- **Phase 2 observability:** metrics scrape exposes handler histograms tagged by `handler_shape` and resolver histograms tagged by `strategy`. — **VERIFIED**
- **Phase 3 spike:** design doc reviewed and approved before Phase 4 begins. — **VERIFIED** (`loop-event-driven-spike.md`)
- **Phase 4 split:** all 50+ feature use cases pass existing tests; Projector rebuild test passes; Dispatcher idempotency test passes. — **VERIFIED** (1401 passed)
- **Phase 5/6 promotion:** uniform execution model passes with all 16 node types; replay-from-log reproduces identical `RunState`. — **VERIFIED**
- **Phase 7 completeness:** loop output accumulation, LOOP_CONTINUE, max_concurrent_iterations_per_run all tested. — **VERIFIED**
- **Current baseline:** 1401 passed, 51 skipped, 0 failed.

---

## 11. Critical Files Reference (updated 2026-04-13)

### Core engine

- `app/layer1_domain/services/orchestration_engine.py` — `_exec_uniform`, `_route_to_successors`, `orchestrate_execute_node`, `maybe_enqueue_loop_iteration`
- `app/layer1_domain/services/successor_processor.py` — `_process_uniform` (fan-out + enqueue)
- `app/layer1_domain/services/dispatcher/fanout.py` — `compute_fanout` (pure decision)
- `app/layer1_domain/services/loop_driver/event_driven_loop_driver.py` — `initialize_loop`, `iteration_complete`, `run_loop`, `initialize_loop_queued`
- `app/layer1_domain/services/projector/run_state_projector.py` — `apply_event`, `rebuild_run_state`
- `app/layer1_domain/services/task_factory.py` — `scope_stack` contextvar propagation
- `app/layer1_domain/services/event_id_helper.py` — `deterministic_event_id`

### Node handlers (16 types)

- `app/layer1_domain/services/node_handlers/base.py` — `NodeHandlerResult`, `NodeHandlerShape`, `NodeHandlerRegistry`
- `app/layer1_domain/services/node_handlers/{task,condition,switch,loop,parallel,merge,trigger,workflow,compute,input,human_action,data_edit,file_upload,loop_exit,loop_continue}_handler.py`

### Event & entity layer

- `app/layer1_domain/events.py` — 20+ domain event types including loop events and `NodeOutputProduced`
- `app/layer1_domain/entities/task.py` — `scope_stack`, `get_innermost_scope`, `with_pushed_scope`
- `app/layer1_domain/entities/workflow_run.py` — `RunContext.merge_quorum_buffers`
- `app/layer1_domain/value_objects/loop_scope.py` — `LoopScope`, `derive_invocation_id`
- `app/layer1_domain/value_objects/node_type.py` — 16 node types

### Infrastructure

- `app/layer4_frameworks/persistence/memory/memory_event_log_repo.py`
- `app/layer4_frameworks/persistence/sqlite/sqlite_event_log_repo.py`
- `app/layer4_frameworks/persistence/hana/hana_event_log_repo.py`
- `app/layer4_frameworks/run_context/cached_projector_view_store.py`
- `app/layer4_frameworks/run_context/projector_view_store.py`
- `app/layer4_frameworks/run_context/live_view_store.py`
- `app/layer4_frameworks/messaging/event_log_appending_publisher.py`
- `app/layer4_frameworks/config/app_config.py` — `EVENT_DRIVEN_LOOP`, `UNIFORM_EXECUTION`, `USE_PROJECTOR_READ_MODEL`, `EVENT_LOG_BACKEND`, `MAX_CONCURRENT_ITERATIONS_PER_RUN`
- `bootstrap.py` — `_build_event_log_repo`, `_build_run_context_view_store`, handler registry, engine wiring

### Reusable — unchanged across the migration

- `app/layer4_frameworks/messaging/` — Console / Kafka / Event Mesh publishers
- `app/layer4_frameworks/realtime/broadcasting_publisher.py` + `socketio_consumer.py`
- `app/layer1_domain/services/variable_resolver.py` and resolver strategies
- `app/layer1_domain/services/condition_evaluator.py`
- `EventCollector` priority lanes (IMMEDIATE / NORMAL / TERMINAL)
- All 50+ feature use cases under `app/layer2_application/features/`
