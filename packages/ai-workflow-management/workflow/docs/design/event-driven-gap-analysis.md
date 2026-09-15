# Event-Driven Migration — Gap Analysis

> Snapshot of where `workflow-control-plane-service` stands today versus the target architecture in [`event-driven-orchestration.md`](./event-driven-orchestration.md). This document answers two questions: **what do we already have that fits the target?** and **what is the real cost of closing the gap?**
>
> Scope: the Python/FastAPI control-plane only. Worker-executor and worker-sdk are mentioned where they interact but are not the subject.
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. TL;DR

We're roughly **40% of the way to the event-driven target** — and the 40% we have is the hardest part to build from scratch: the realtime fan-out, the pluggable bus, the decorator-chained publishers, the lazy variable resolver, and the priority event queues are all production-grade today.

The remaining work concentrates in **one file** (`app/layer1_domain/services/orchestration_engine.py`, ~1600 LOC) plus two new concerns that don't exist yet: a **persistent event log** and **per-node failure policies**. The `NodeHandler` protocol, the 51-feature use-case layer, and the infrastructure adapters survive the migration mostly intact.

The single biggest architectural friction is that **BUILTIN node types (CONDITION, SWITCH, LOOP, PARALLEL, MERGE, TRIGGER, WORKFLOW, COMPUTE) run synchronously inline via direct Python recursion**. Flipping them to event-driven — especially LOOP, which today is a `while`-loop over live Python state — is where most of the design and testing effort would go.

---

## 2. Current State Snapshot

### 2.1 Overall shape

- Clean-architecture Python/FastAPI service in 4 layers.
- 51 auto-discovered use cases under `app/layer2_application/features/` wired via signature-introspection DI in `bootstrap.py`.
- Pluggable persistence (`INFRA_MODE`: mock / local / hana) and messaging (console / Kafka / SAP Event Mesh).
- Entry point `main.py` sets up FastAPI lifespan, Socket.IO server, and a conditional gRPC server.

### 2.2 Orchestration model — **DAG-walker, not event-driven**

Core engine: `app/layer1_domain/services/orchestration_engine.py`.

- **DAG is pre-validated.** `DAGValidator.validate(workflow)` runs before any entry node (lines 1088-1108). Acyclicity via Kahn's, connectivity BFS, port/type checks. Failing validation rejects the run.
- **Execution is recursive direct-call.** `dispatch_or_handle_task()` calls itself up to `MAX_RECURSION_DEPTH = 100` as it walks the graph. No events drive control flow.
- **Three node categories:**
  - **BUILTIN** — `CONDITION, SWITCH, LOOP, PARALLEL, MERGE, TRIGGER, WORKFLOW, COMPUTE`. Executed synchronously inline by their handlers. No bus involved in control flow.
  - **PENDING** — `INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD`. Pause the run, wait for external resume.
  - **TASK** — the only category dispatched asynchronously, via `task.dispatched` events consumed by the worker-executor service.
- **Skip propagation** in `dag_traversal.collect_downstream_nodes_to_skip()` — BFS that only skips a successor when all its predecessors are skipped. Merge-safe.
- **Per-run serialization** via `asyncio.Lock` keyed on `run_id` (line 87) prevents concurrent `orchestrate_start` / `orchestrate_continue` races in a single process.

### 2.3 Node handlers — 13 implementations

Path: `app/layer1_domain/services/node_handlers/`.

Files: `base`, `condition`, `switch`, `loop`, `parallel`, `merge`, `task`, `trigger`, `workflow`, `compute`, `input`, `human_action`, `data_edit`, `file_upload`.

All implement the `NodeHandler` protocol from `base.py` and return `NodeHandlerResult`:

```python
@dataclass
class NodeHandlerResult:
    tasks_to_dispatch: list[Task]
    skipped_node_ids: list[str]
    state_updates: dict
    loop_state_updates: dict | None
    events: list[DomainEvent]
    run_status_change: RunStatus | None
    requires_child_run: bool
```

**Edge-event quirk:** `ConditionNodeHandler` and `SwitchNodeHandler` emit their own `EdgeTraversed` events (since they pick the branch). The engine tracks this via `_HANDLER_EMITS_EDGE_EVENTS` and suppresses its own edge emission for those types.

### 2.4 Messaging & realtime — mature and already target-shaped

This is the layer that already matches the event-driven design most closely.

- **Pluggable publishers** in `app/layer4_frameworks/messaging/`:
  - `ConsoleEventPublisher` (mock)
  - `KafkaEventPublisher` (local)
  - `EventMeshEventPublisher` (sap, with circuit breaker)
- **Decorator chain** wired in `bootstrap.py` lines 90-110:
  ```
  RealtimeBroadcastingPublisher
      → EnrichingEventPublisher
          → Kafka | EventMesh | Console
  ```
- **Domain events** in `app/layer1_domain/events.py`:
  RunStarted/Completed/Failed/Cancelled/Paused/Resumed, TaskDispatched/Completed/Failed/Retrying/Skipped/Resumed/Rerun, ConditionEvaluated, LoopIterationCompleted, EdgeTraversed, InputRequested/Submitted, HumanActionRequested/Completed, DataEditRequested/Completed, ChildRunStarted/Completed, ArtifactAdded.
- **Event buffering with priority lanes** via `EventCollector` (contextvar-scoped per task):
  - `IMMEDIATE` — task.dispatched, input.requested, child_run.started, run terminal states (published without buffering).
  - `NORMAL` — edge.traversed, task.completed, condition.evaluated.
  - `TERMINAL` — run.completed/failed/cancelled.
  Drained in priority order; resolver caches cleared after drain.
- **Socket.IO realtime push** in `app/layer4_frameworks/realtime/`:
  - `SocketIORealtimeConsumer` subscribes to broker topic `realtime.events`.
  - Fan-out to three rooms per event: `run:{run_id}`, `run:{parent_run_id}`, `run:{root_run_id}`.
  - Three independent bounded queues with backpressure:
    - critical — 1000, never drop (HITL prompts, terminal events)
    - normal — 5000, drop on overflow (edge traversal, task completion)
    - bulk — 10000, drop freely (context updates, artifacts)
  - Redis adapter for horizontal Socket.IO scaling if `REDIS_URL` set.

### 2.5 Validation — hybrid

- **Pre-run DAG validation** — `DAGValidator` gates the run.
- **Per-node input validation at dispatch time** — `task_handler.py` lines 34-50 resolves input mappings and checks required keys; `SchemaValidator` checks input shape.
- **Expression evaluation is lazy** — `VariableResolver` uses a strategy chain:
  1. `FileBackedResolverStrategy` (CSV-backed data)
  2. `MetadataResolverStrategy` (workflow metadata)
  3. `TraversalResolverStrategy` (`{{$node1.data[0].key}}` walking run context)
  Expressions parse on first use via regex; caches cleared after each drain.
- **`ConditionEvaluator`** wraps the resolver for booleans; falls back to `False` on resolution error.

### 2.6 Persistence

- **mock** — in-memory repos
- **local** — SQLite via SQLAlchemy + aiosqlite
- **sap** — SAP HANA via pyodbc
- Repositories: `IWorkflowRepository`, `IRunRepository`, `ITaskRepository`, `IStageHistoryRepository`, `INodeDefinitionRepository`, `IWebhookRepository`.
- **No event log repository.** Events are broadcast, not persisted as a replayable log.

### 2.7 Feature surface (51 use cases)

`start_run, cancel_run, pause_run, resume_run, complete_task, fail_task, rerun_task, resume_pending_task, resume_input_node, edit_node_input, dry_run_workflow, test_full_workflow, test_node, sweep_timed_out_child_runs, get_run_context, get_run_status, get_node_input_data, get_node_output, get_variable_suggestions, validate_workflow, list_runs, list_workflows, list_node_palette, create/update/delete_workflow, create/update/delete_node_definition, create/update/delete_webhook, list_webhook_deliveries, store_output, get_output, add_node_artifact, get_socketio_config, get_expression_reference, clear_all_data, reset_test_run, set_main_flow, list_main_flows, get_workflow_node_tree, get_workflow_parameters …`

---

## 3. Target-vs-Current Mapping

Each row maps a section of [`event-driven-orchestration.md`](./event-driven-orchestration.md) to what exists today. Status column reflects work as of **2026-04-11** (Phases 0, 1, 2, 3, Fix A/B/C, Phase 4a, Phase 4b.1-4b.8, Phase 4c.1-4c.4, and Phase 5.1 of the execution plan have landed).

| Target concept | Status | Today | Gap |
|---|---|---|---|
| Pluggable event bus (§4) | Done | Console / Kafka / Event Mesh publishers, decorator-chained | None |
| Stateless Dispatcher / Executor / Projector split (§4.2) | **Done (Phases 6.1–6.3, 2026-04-12/13)** | Read side: `IRunContextView` shim + hybrid projector. Event log: shadow write-ahead + durable backends (memory/SQLite/HANA). Dispatcher: `compute_fanout` pure function. Projector: `apply_event` reducer. **Phase 6 uniform execution model**: all node types go through `_exec_uniform` → `_route_to_successors`. Legacy `_exec_builtin_queued`/`_exec_pending_queued`/`_exec_task_queued`/`_run_loop_iterations` **deleted** (Phase 6.3). Both `EVENT_DRIVEN_LOOP` and `UNIFORM_EXECUTION` flags default `true`. SuccessorProcessor simplified to thin `compute_fanout` wrapper. 1440 passed, 12 skipped. | Remaining: `dispatch_or_handle_task` inline-path migration (blocks handler `tasks_to_dispatch` removal). Remaining ~48 feature use cases → `IRunContextView`. |
| Read-side adapter-layer stability (`IRunContextView`) | **Done (Phase 4a, 2026-04-11)** | `IRunContextView` + `IRunContextViewStore` Protocols in `layer2_application/interfaces/`. `RunContextSnapshot` frozen dataclass in `layer1_domain/entities/`. `LiveRunContextViewStore` backed by `IRunRepository` in `layer4_frameworks/run_context/`. Wired in `bootstrap.py`. | Remaining ~48 feature use cases still consume repositories directly; they will migrate incrementally as Phase 4b lands. |
| Runtime-only validation (§8) | Partial | Expressions lazy; DAG validated pre-run | DAG validator needs to become advisory, not gating (Phase 5) |
| Event log as source of truth (§12.2) | Partial (all three backends production-wireable; read model still on live store) | Write-ahead shadow log + pure `apply_event` reducer that rebuilds lifecycle state AND `variables` from the log alone. **Phase 4b.6**: `SqliteEventLogRepository` durable file-backed backend. **Phase 4b.7**: bootstrap factory selects backend via `EVENT_LOG_BACKEND` env var. **Phase 4b.8**: `HanaEventLogRepository` — SAP BTP production backend using the shared `HanaConnectionManager` from `infra.db`; dedupe via HANA error code 301 on the `(RUN_ID, EVENT_ID)` unique index; migration `025_create_aiw_event_log.sql` picked up by the existing idempotent migrate() runner; conformance-tested via SQLite-backed stub that reuses the real `HanaConnectionManager._bind_params` placeholder translator. **Phase 4c.2**: `CachedProjectorRunContextViewStore` serves `IRunContextView` with O(1) warm gets. Read-model flip still pending `dag_info` fallback work. `MemoryEventLogRepository` + `LiveRunContextViewStore` remain defaults. | `dag_info` fallback for projector view store, feature-by-feature projector read-model flip, Phase 5.2 event-driven BUILTIN dispatch to make the projection authoritative on the write side. |
| Per-node failure policy (§8.2) | Partial | Retry and skip exist but are not declarative | Add policy fields + interpreter (Phase 5) |
| Named output ports + error routing (§7.3) | Partial | Ports exist; error-port wiring not first-class | Introduce `error` port convention + routing (Phase 5) |
| Socket.IO realtime (§13.1) | Done (better than design) | Three-queue backpressure, parent/root fan-out, Redis adapter | None |
| Priority event lanes (§6) | Done | `EventCollector` IMMEDIATE/NORMAL/TERMINAL | None |
| Lazy expression resolution (§10) | Done | `VariableResolver` + strategy chain, **now fully async down to the HTTP edge** (Phase 0) | None |
| **Async file-service + resolver chain (prerequisite for Phase 4)** | **Done (Phase 0, 2026-04-11)** | `HttpFileServiceClient` uses `httpx.AsyncClient` singleton; every resolver strategy, handler call site, and feature use case awaits through to the file service. Sync-in-async stall bug eliminated. | None |
| **Run-scoped materialize + OData cache (prerequisite for Phase 2/4)** | **Done (Phase 1, 2026-04-11)** | `FileBackedResolverStrategy` holds `_run_materialize_caches` and `_run_odata_caches` keyed by `run_id` contextvar. Dropped on terminal transitions (`check_run_completion` FAILED/COMPLETED branches; `cancel_run_use_case` parent + cascade). `drain_events()` no longer wipes caches per-pass. | LRU bound on the per-run bucket is deferred until Phase 2 metrics justify it. |
| Merge-safe skip propagation (§7.3 / §9) | Done | `dag_traversal.collect_downstream_nodes_to_skip()` | None |
| Sub-workflows with parent/root routing (§9.2) | Done | `WorkflowNodeHandler` + `ChildRunStarted/Completed` + 3-room fan-out | None |
| Per-run ordering via partitioning (§16.1) | Partial | `asyncio.Lock` per run in-process; not bus-level. **Post-Phase-0, the lock correctly serializes within a run only — no more cross-run blocking via sync I/O.** | Need Kafka partition-by-`run_id` for multi-replica (Phase 4) |
| Deterministic event ids for idempotency (§25.1.3) | Partial (helper + fan-out adoption across all post-BUILTIN sites done 2026-04-11) | `deterministic_event_id` helper in `layer1_domain/services/event_id_helper.py`. Pure `compute_fanout` generates stable `event_id`s for every `EdgeTraversed` and cascade-`TaskSkipped` it produces. `SuccessorProcessor.process` (both the main edge loop AND the post-handler BUILTIN loop — Phase 4b.4) and `ComputeNodeHandler.handle` (Phase 4b.4) stamp those ids on the emitted events. CONDITION/SWITCH taken_edge_ports path also gets deterministic ids. Event log repo dedupes redeliveries by `(run_id, event_id)`. | `TaskDispatched` / `TaskCompleted` / `RunStarted` / `RunCompleted` still use random UUID4 from `DomainEvent`'s default factory; these flow through `_publish_now` without a stable id. Phase 5.2 will make these deterministic as part of the event-driven BUILTIN dispatch rewrite. |
| Outbox pattern for side-effecting nodes (§25.3) | Missing | Not present | New `outbox` + `side_effect_journal` tables + shipper (Phase 5, selective) |
| Quorum buffering for multi-input nodes (§25.2) | Partial | `MergeNodeHandler` checks `completed_nodes` implicitly | Explicit buffers with policies and timeouts (Phase 5) |
| Cycles via runtime depth counter (§7.2) | Missing | Cycles rejected pre-run by `DAGValidator` | Remove rejection; add `loop_depth` per event (Phase 5) |
| **Correct child-workflow invocation semantics** | **Done (fixed 2026-04-11)** | Three targeted fixes landed: missing `await`s on `apply_state_updates_as_files` restored (Fix A), predecessor-readiness guard added in `_execute_node_inner` with loop-context exemption (Fix B), `_start_child_workflow` harmonized with `orchestrate_start` — contextvar scope, `drain_events`, `flush_events` (Fix C). 3 new regression tests cover the queued-exec path that was previously untested. | None. Phase 5 still provides the structural end-state but is no longer urgent for this bug class. |
| **Nested-loop support** | **Enabled behind `EVENT_DRIVEN_LOOP` flag (Phase 4c.4.2/3/4/5 + 5.2.1 + 7.2 complete, 2026-04-12)** | Phase 4c.4 landed the `LoopScope` / `derive_invocation_id` data-model foundation. Phase 4c.4.2 added the loop event types. Phase 4c.4.3 wired `LoopNodeHandler` to emit events + populate `scope_stack` on body tasks. Phase 4c.4.4 rewrote `MergeNodeHandler` for scope-aware quorum. Phase 4c.4.5 added the `LOOP_EXIT` BUILTIN node for cooperative break. **Phase 5.2.1** extracted the event-driven loop logic into `EventDrivenLoopDriver` and fixed event ordering. **Phase 7.1 (2026-04-12):** Loop output accumulation — `iteration_results` collected across all iterations, final output as `{"results": [...]}`. **Phase 7.2 (2026-04-12):** `LOOP_CONTINUE` node type for cooperative continue — skips rest of iteration, advances to next. Condition-false routing fix. Merge buffer pruning on `LoopCompleted`. **Break + continue control-flow primitives complete.** 55+ new tests across all loop phases. | Walker deletion once `EVENT_DRIVEN_LOOP` + `UNIFORM_EXECUTION` are default-on. Failed-iteration outcome propagation (deferred backlog). |
| **Queued-mode loop iteration** | **Closed (Phase 5.2.2, 2026-04-12)** | Phase 5.2.2 brought the pre-existing `orchestrate_loop_iteration` + `LoopIterationAction` dead code to life. The fix: a `maybe_enqueue_loop_iteration` body-completion hook on `OrchestrationEngine` detects when a loop body iteration finishes (via runtime BFS `is_iteration_done` over `dag_info.edge_map`) and enqueues `LoopIterationAction`. The worker dispatches it to `orchestrate_loop_iteration`, which advances the loop and enqueues the next body or exit task. Supporting infrastructure: `initialize_loop_queued` driver entry for queued-mode init, `scope_stack` contextvar propagation so deep body tasks carry loop scope, iter-boundary cleanup that BFS-wipes body nodes from `completed_nodes`/`skipped_nodes` for re-execution across iterations. Hook placed in 4 code paths: `_exec_builtin_queued`, `_exec_pending_queued`, `complete_task_use_case`, `resume_pending_task_use_case`. 32 new tests (24 unit BFS + 5 unit contextvar + 3 integration end-to-end with `MemoryOrchestrationQueue`). | **Remaining deferred:** parallel-mode per-iteration skip tracking (sequential mode is correct; parallel-mode has a latent false-positive risk from global `skipped_nodes`). Broker/Kafka end-to-end test (memory queue proves the mechanism). |
| **Merge quorum inside loop body** | **Closed behind `EVENT_DRIVEN_LOOP` (Phase 4c.4.4, 2026-04-11; buffer pruning added Phase 7.2, 2026-04-12)** | `MergeNodeHandler` now keys its quorum buffer on `(node_id, innermost_invocation_id, innermost_iter)` via a new `RunContext.merge_quorum_buffers: dict[tuple[str, str, int], set[str]]` field. When the completed task has a populated `scope_stack` (Phase 4c.4.3 event-driven path), each iteration gets its own buffer — parallel body iterations have independent quorum state, and nested loops get fresh buffers per outer iteration because `derive_invocation_id` guarantees different inner `invocation_id`s per outer iter. When the task has no scope (flag off, or merges outside any loop), the key falls back to the sentinel `(node.id, "", -1)` with buffer seeded from `run.context.completed_nodes` — behavior identical to pre-4c.4.4. **Phase 7.2 (2026-04-12):** `_emit_loop_completed_and_exit` now prunes `merge_quorum_buffers` entries matching the loop's `invocation_id` when `LoopCompleted` fires — prevents unbounded memory in million-iteration workflows. | `.discard` walker trick still in place for flag-off path; removable once `EVENT_DRIVEN_LOOP` is default-on. |

---

## 4. What's Already There (and Stays)

These components would survive the migration largely unchanged. They are the sunk-cost advantages that make the event-driven path cheaper than a rewrite.

### 4.1 Reusable without modification

| Component | Path | Why it fits |
|---|---|---|
| Pluggable messaging publishers | `app/layer4_frameworks/messaging/` | Already the pluggable event bus the target design calls for. |
| Decorator chain (Realtime → Enriching → Backend) | `bootstrap.py` lines 90-110 | Maps 1:1 to the target's publisher layering. |
| Socket.IO consumer + 3-queue backpressure | `app/layer4_frameworks/realtime/` | More sophisticated than what the design doc sketched. Keep as-is. |
| Room-based fan-out (run / parent / root) | `broadcasting_publisher.py` lines 156-179 | Solves sub-workflow realtime routing, which the design doc only mentions. |
| `VariableResolver` + strategy chain | `app/layer1_domain/services/variable_resolver.py` | Lazy-by-design; matches §10 of the design doc exactly. |
| `FileBackedResolverStrategy`, `MetadataResolverStrategy`, `TraversalResolverStrategy` | `app/layer1_domain/services/` | Unchanged; all three remain useful in the event-driven model. |
| `ConditionEvaluator` | `app/layer1_domain/services/condition_evaluator.py` | Wraps the resolver for booleans. Used by the new event-driven CONDITION handler as-is. |
| `dag_traversal.collect_downstream_nodes_to_skip()` | `app/layer1_domain/services/dag_traversal.py` | Merge-safe skip logic is exactly what the design doc §7.3 requires. |
| `EventCollector` priority lanes | contextvar-scoped helper | IMMEDIATE / NORMAL / TERMINAL lanes map onto the target's per-run event ordering. |
| Feature auto-discovery in `bootstrap.py` | `bootstrap.py` | Use-case layer unchanged; the engine underneath is what swaps. |
| Persistence adapters (memory / SQLite / HANA) | `app/layer4_frameworks/persistence/` | Same repositories; add one new one for the event log. |
| Domain events in `events.py` | `app/layer1_domain/events.py` | Superset of what the design doc's event catalog needs. |
| All 13 `NodeHandler` implementations (mostly) | `app/layer1_domain/services/node_handlers/` | The handler protocol and return type match the target. Their internals become pure functions of `(node.input, context) → NodeHandlerResult`. |

### 4.2 Near-reusable (light edits)

| Component | What changes |
|---|---|
| `NodeHandler` protocol | Add stateless guarantee; remove direct mutation of `WorkflowRun` from handlers; standardize on returning events instead of calling back into the engine. |
| `TaskHandler` | Keep input mapping + required-key check logic; wrap it as the Executor's pre-run validation step. |
| `ConditionNodeHandler` / `SwitchNodeHandler` | Already emit their own edge events — which is exactly what the target model requires. Small win. |

### 4.3 What `worker-executor-service` already contributes

The existing **worker-executor split is already event-driven** for TASK nodes: `task.dispatched` goes over the bus, workers consume, results come back. That half of the system is the template for what the control plane needs to become for BUILTIN and PENDING nodes too. No change needed on the worker side to support a migration.

---

## 5. The Real Gap

Everything below is net-new work or non-trivial rewrites.

### 5.1 Structural gap — engine rewrite

**File:** `app/layer1_domain/services/orchestration_engine.py` (~1600 LOC).

Today this file is a synchronous recursive DAG walker. Target: split into two stateless services.

- **Dispatcher role** — subscribe to `node.output`, load wire table, evaluate filters/maps, emit `node.input`. Today's logic for "find successors after a task completes" lives in `successor_processor.py` and the recursive path in `dispatch_or_handle_task()`. Both feed the new Dispatcher.
- **Executor role** — subscribe to `node.input`, validate, resolve expressions, call the handler, validate output, emit `node.output` or `node.failed`. Today's `dispatch_or_handle_task()` switch over node type (BUILTIN vs PENDING vs TASK) becomes the Executor's event handler.

The hard part is not the mechanical rewrite. It is that **BUILTIN nodes today run synchronously inline**, and two of them — LOOP and PARALLEL — hold live Python state:

- `LoopNodeHandler` uses `_run_loop_iterations()` (orchestration_engine.py lines 507-602) — a `while` loop over live Python state, iterating body tasks and feeding their completion back to the handler for the next iteration. Event-driven LOOP requires each iteration to be a separate `node.input` → `node.output` cycle, with iteration state carried in event metadata (`scope_stack` / `loop_iteration`).
- `ParallelNodeHandler` creates concurrent task nodes inline. Event-driven PARALLEL is simpler — fan-out happens naturally at the Dispatcher — but completion detection (knowing when to fire the downstream merge) needs the quorum buffering described in §25.2 of the design doc, which doesn't exist today.

### 5.2 Event log persistence — new component

Today the broadcast bus is write-only. Replay, fork, projection rebuild, and event-sourced run state all require an **append-only event log** ordered by `run_id`.

New work:
- `run_events` table (Postgres/HANA/SQLite) OR compacted Kafka topic partitioned by `run_id` OR NATS JetStream stream.
- `IEventLogRepository` protocol in `app/layer2_application/interfaces/`.
- Backend implementations per infra mode, alongside the existing repository adapters.
- A **Projector** component that consumes the log and writes materialized `RunState`. Today's `RunRepository` + the `WorkflowRun` entity become the Projector's output; the `RunState` materialization logic is new.
- Snapshot + replay primitives for recovery.

### 5.3 Per-node failure policy — new field + interpreter

Today retry lives in:
- `task_handler.py` (partial, for TASK nodes)
- Worker side (actual retry execution)
- Scattered skip logic in the engine

Target: a declarative policy field on every node descriptor:

```yaml
policy:
  on_validation_error: { mode: fail }
  on_exec_error:       { mode: retry, retry: { max: 3, backoff: exponential, base_ms: 200 } }
  on_timeout:          { mode: retry }
  on_output_validation_error: { mode: fail }
```

New work:
- Extend `WorkflowNode` / node descriptor schema to carry policy.
- Write a policy interpreter called by the new Executor when a handler raises or a schema check fails.
- Migrate existing retry paths to read from the policy field.
- Named output ports (`main`, `error`) as a first-class convention for `route_to_error` mode.

### 5.4 Runtime validation — remove the gate

- `DAGValidator` stays, but its caller changes. Today it gates `orchestrate_start()`. Target: it becomes an advisory linter exposed via the existing `validate_workflow` use case for the editor UI, but does not block a run.
- Cycles: remove the acyclicity check. Introduce `loop_depth` on every event; the Dispatcher increments it when re-entering a node already in the causal path; fail the specific node at `max_loop_depth` (default 100, mirroring today's recursion guard).
- Connectivity: unreachable nodes become a UI warning emitted by the Projector, not a run rejection.

### 5.5 Quorum buffering — new component

Today's `MergeNodeHandler` implicitly checks `completed_nodes`. Target: explicit per-node buffer keyed by `(run_id, node_id, invocation_id)` with quorum policies (`all` / `any` / `k_of_n` / `expression`) and timeouts. See §25.2 of the design doc.

New work:
- Buffer records in the state store with optimistic concurrency (CAS on version).
- Loop-scope-aware `invocation_id` derivation.
- Timeout scheduler (can reuse `sweep_timed_out_child_runs` feature as a model).
- Cancellation sweep on `run.cancel`.

### 5.6 Outbox pattern — new component

Only needed for node handlers that have side effects and emit events in the same step. Not all handlers need it; see §25.3.1 of the design doc.

New work:
- `outbox` + `side_effect_journal` tables per infra mode.
- Polling shipper (default) or CDC shipper (Debezium, future).
- Integration point in the new Executor for handlers that opt in.
- Retention and metrics.

### 5.7 Idempotency — deterministic event ids

Today's events use fresh UUIDs. Target: the Dispatcher generates `uuid5(source_event_id, wire_id)` so redelivery produces identical downstream events, and the Executor dedups by `event_id`.

New work:
- Deterministic id generation in the new Dispatcher.
- Executor dedup table (bounded LRU + persistent store for long-lived runs).
- Audit of every publisher to ensure ids are set correctly.

### 5.8 Per-run partitioning at the bus level

Today ordering is guaranteed by a single Python process holding an `asyncio.Lock` per `run_id`. Target: bus-level partitioning so multiple replicas of the Dispatcher/Executor can safely process different runs in parallel while each run stays ordered.

New work:
- Configure Kafka topics with `run_id`-based partitioning.
- Equivalent work for Event Mesh (which does not natively partition — likely need consumer-group-level routing).
- Remove the in-process asyncio lock once bus partitioning is authoritative.

---

## 6. Architectural Frictions

These are not line items; they are the points where migration design decisions get hard and where integration tests matter most.

### 6.1 LOOP is stateful in Python

`_run_loop_iterations()` iterates body tasks with live Python control flow. Event-driven LOOP requires:

- Each iteration = its own `node.input` → `node.output` cycle.
- Iteration state (counter, accumulator, current item) carried on events via `scope_stack`.
- Termination condition evaluated per iteration, not in a `while`.
- Each iteration creates fresh quorum buffers for any merge inside the loop body (see §25.2.5 of the design doc).

Migrating LOOP probably deserves its own design sub-document before code changes.

### 6.2 Inline BUILTIN execution vs event-driven dispatch

The current engine advantage of synchronous BUILTIN execution is that CONDITION/SWITCH/MERGE/etc. add zero bus overhead — they are pure CPU inside the engine process. Event-driven BUILTIN dispatch means every branch decision round-trips through the bus.

Implications:
- Latency per run increases by (events-per-run × one-way bus latency). For a small run, negligible. For a 500-node test workflow, measurable.
- Throughput cost is real: a workflow with 50 CONDITION nodes emits ~150 more events than today.
- Mitigation option: **batch BUILTIN evaluation** — a single Executor can drain a batch of consecutive BUILTIN-only events without ack'ing each one. This recovers most of today's inline speed. Worth benchmarking early.

### 6.3 Pre-run validation is a UX expectation

Users of the editor expect a red dot on a broken wire *now*, not on first run. Keeping `DAGValidator` as a linter (invoked by `validate_workflow` and via the editor API) preserves this UX while the runtime stops gating on it. The two roles need to be clearly separated in the codebase so the validator can't accidentally re-gate the run.

### 6.4 Event log growth

A persistent event log per run can get big (HITL workflows with long-lived waits, or loops with thousands of iterations). Mitigations:
- Externalize large payloads above threshold (256 KB default) — matches §10.1 of the design doc.
- Archive completed runs to cold storage after retention window.
- Compact event log for live-edit workflows.

### 6.5 Backward compatibility during migration

The feature layer (51 use cases) calls into the engine today. Changing the engine means either:
- Keeping the feature-layer contracts stable and changing only the engine internals. Preferred but requires carefully mapping today's `orchestrate_start()` / `orchestrate_continue()` API onto the new event-driven model.
- Or versioning the feature layer alongside the engine, running both for a window.

The 51 use cases are the surface area that must not break. Every migration PR should show which use cases it touches and how they behave afterwards.

---

## 7. Migration Path — Three Options

Presented in order of increasing ambition. Each is internally coherent; each has different risk/reward.

### Option A — Incremental, engine-in-place

**Idea:** keep `orchestration_engine.py` as the driver; introduce the event log, per-node policies, and runtime validation incrementally without splitting the engine.

- Week-scale changes:
  1. Add `IEventLogRepository` + backend implementations. Teach the existing publishers to also write to the log.
  2. Add per-node policy field. Move existing retry/skip logic to read from it.
  3. Demote `DAGValidator` from gating to advisory.
  4. Add deterministic event ids at the emission boundary.
- **What you get:** replay, per-node policy, runtime validation.
- **What you don't get:** Dispatcher/Executor split, horizontal scale beyond the existing worker split, LOOP-as-events.
- **Risk:** low. Nothing structural changes.

### Option B — Split Dispatcher + Projector, leave Executor shape

**Idea:** extract the wire-evaluation / fan-out logic (`successor_processor.py` + parts of `dispatch_or_handle_task`) into a Dispatcher service, and build a Projector that consumes the event log and rebuilds `RunState`. Keep BUILTIN execution inline for now.

- Gets you:
  - Stateless Dispatcher replica count → more runs/sec per pod.
  - Projector can be rebuilt from the log at will.
  - Event log is authoritative.
- Does not touch LOOP or PARALLEL internals.
- **Risk:** medium. Requires clean interfaces but no LOOP rewrite.

### Option C — Full event-driven per the design doc

**Idea:** the whole migration. BUILTIN becomes event-driven; LOOP and PARALLEL become scope-aware event sequences; quorum buffering and the outbox pattern get built; bus-level partitioning replaces the in-process lock.

- Gets you: everything in the design doc.
- **Risk:** high. Needs careful sequencing to keep the service running through the migration, and a dedicated design pass on LOOP before code changes begin.

### Recommended sequencing

If the team chooses to go all the way (Option C), do it in the order A → B → C. Option A's deliverables (event log, policies, demoted validator) are prerequisites for B, which are prerequisites for C. Each step is valuable on its own, so the migration can pause after A or B if priorities change.

---

## 8. Critical Files to Understand Before Starting

Anyone picking up this migration should read these first, in order:

1. `app/layer1_domain/services/orchestration_engine.py` — the thing being rewritten. Focus on `dispatch_or_handle_task`, `_run_loop_iterations`, and the `_HANDLER_EMITS_EDGE_EVENTS` set.
2. `app/layer1_domain/services/successor_processor.py` — the "what happens after a node completes" logic that becomes the Dispatcher.
3. `app/layer1_domain/services/node_handlers/base.py` — the `NodeHandler` protocol and `NodeHandlerResult` contract.
4. `app/layer1_domain/services/node_handlers/loop_handler.py` — the hardest handler to migrate.
5. `app/layer1_domain/services/dag_traversal.py` — the skip propagation logic that stays.
6. `app/layer1_domain/services/variable_resolver.py` — lazy expression resolution that stays.
7. `app/layer1_domain/events.py` — current event catalog to extend.
8. `app/layer4_frameworks/realtime/broadcasting_publisher.py` — realtime decorator; stays unchanged.
9. `app/layer4_frameworks/messaging/__init__.py` — pluggable publisher factory.
10. `bootstrap.py` — where the new services would wire in.

---

## 9. Open Questions Before Committing to a Path

Unresolved items that should be answered in a follow-up design pass:

- **LOOP semantics in event-driven form.** Specifically: how does `break` / `continue` propagate? How does `for-each` pass the current item? Where does the iteration counter live?
- **Event log backend per infra mode.** Is it SQL-backed in all modes, or does Kafka mode use the Kafka log directly? Affects replay performance and operational complexity.
- **Which handlers opt into the outbox pattern.** Most of today's handlers are pure orchestration (no side effects) — they don't need outbox. The TASK handler delegates to workers — those are where real side effects live. The list should be small.
- **Migration horizon.** If the plan is Option A only, the event-driven design document becomes aspirational reference material. If the plan is Option C, a dedicated migration team and timeline are needed.
- **Compatibility of existing workflows on disk.** If any production workflows rely on pre-run DAG validation to catch user errors, those users will see their errors move from edit time to run time. Acceptable? Needs product input.

---

## 10. Summary

**Keep:** messaging layer, realtime layer, variable resolver, skip propagation, priority lanes, sub-workflow routing, persistence adapters, feature auto-discovery, most node handlers. These are production-grade and target-shaped already.

**Rewrite:** `orchestration_engine.py` — the core control-flow loop. Specifically the recursive `dispatch_or_handle_task` path and the inline BUILTIN execution model.

**Add:** persistent event log, per-node policy fields, deterministic event ids, quorum buffers with timeouts, outbox pattern for side-effecting handlers, bus-level partitioning.

**Demote:** `DAGValidator` from a run gate to an editor linter.

**Hardest single piece:** LOOP (and, to a lesser extent, PARALLEL) — because they hold live Python state today and need to be reshaped as scoped event sequences.

The migration is **feasible, not free**. The cost is concentrated in one file plus two new components. The existing codebase has already solved the problems that would take the longest to build from scratch (realtime, pluggable bus, lazy resolution, priority queues). This is a refactor-in-place, not a rewrite.
