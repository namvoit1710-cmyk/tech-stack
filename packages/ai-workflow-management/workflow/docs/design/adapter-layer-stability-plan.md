# Adapter Layer Stability Plan

> Plan for keeping the entire adapter layer (REST controllers, DTOs, Socket.IO, gRPC) **unchanged** through the event-driven migration. The feature layer (51 use cases) is the stability boundary — its contracts stay fixed while its internals switch from direct engine calls to command/event emission. Adapters never see the difference.
>
> Companion documents:
> - [`event-driven-orchestration.md`](./event-driven-orchestration.md) — target architecture
> - [`event-driven-execution-plan.md`](./event-driven-execution-plan.md) — phased migration plan
> - [`context-validator-resolver-migration.md`](./context-validator-resolver-migration.md) — RunContext / Validator / Resolver migration
> - [`runtime-validation-pipeline.md`](./runtime-validation-pipeline.md) — in-process validation pipeline
> - [`polyglot-engine-decision.md`](./polyglot-engine-decision.md) — Go engine decision
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. Goal

Through all phases of the event-driven migration, the following must remain **byte-identical**:

- Every REST endpoint (URL, method, status codes, headers).
- Every request DTO and response DTO (field names, types, nesting).
- Every Socket.IO event name, payload shape, and room convention.
- Every gRPC service definition and message type.
- Every OData query parameter behavior on `GET /runs`.
- Every error code and error response envelope.

**Frontend code and third-party clients must not need a single line change across the migration.** This includes the existing integration test suite at `tests/integration/test_api.py`, which becomes the primary regression gate.

---

## 2. Why the Adapter Layer Can Stay Stable

The codebase already enforces a clean boundary that makes this possible. Every REST controller method follows the same shape:

```python
@router.post("/runs")
async def start_run(
    payload: StartRunRequest,
    usecase: StartRunUseCase = Depends(_get_start_run),
):
    command = StartRunCommand(**payload.model_dump())
    result = await usecase.execute(command)
    if not result.id:
        return error_response("BAD_REQUEST", result.error)
    return RunOutputDto.from_dataclass(result)
```

(Simplified from `app/layer3_adapters/controllers/restful/v1/run_controller.py:238-247`.)

Three load-bearing facts:

1. **Controllers only know Commands and Results.** They build a `Command` dataclass from the request, call `usecase.execute(command)`, translate the result to a DTO. Nothing about engines, events, or buses reaches the controller.
2. **The feature layer already uses command dataclasses** (`StartRunCommand`, `CompleteTaskCommand`, `CancelRunCommand`, etc.). This is already a CQRS-ish pattern — it just happens to be implemented today with direct engine calls inside `execute()`.
3. **DI is per-feature** via `request.app.state.container["feature_name"]`. Swapping a use case implementation is a `bootstrap.py` change, not a controller change.

The stability boundary is therefore **the `UseCase.execute(command) -> result` signature of every feature**. If those signatures and their dataclasses remain byte-identical, the adapter layer survives untouched.

---

## 3. The Stability Contract

The following are **frozen** for the duration of the migration. Any PR that changes them requires a CODEOWNERS review and a migration note.

### 3.1 Frozen — REST

- All controller files under `app/layer3_adapters/controllers/restful/v1/`:
  - `run_controller.py`, `workflow_controller.py`, `node_definition_controller.py`, `expression_controller.py`, `file_controller.py`, `artifact_controller.py`, `output_controller.py`, `socketio_controller.py`, `webhook_controller.py`, `webhook_management_controller.py`, `health_controller.py`, `node_palette_controller.py`, `worker_function_controller.py`.
- All DTOs under `app/layer3_adapters/controllers/restful/v1/dtos/`:
  - `base_dto.py`, `odata_params.py`, `response_envelope.py`.
- All middleware under `app/layer3_adapters/controllers/restful/v1/middleware/`:
  - `auth.py`, `error_handler.py`, `response_wrapper.py`.
- Error codes in `app/layer2_application/errors.py` (`ErrorCode` enum — HTTP status mapping, error code strings).
- The OData parameter parsing contract (`parse_odata_params`, `parse_odata_filter`, `odata_page_response`).

### 3.2 Frozen — gRPC

- `app/layer3_adapters/controllers/grpc/run_event_servicer.py`
- `app/layer3_adapters/controllers/grpc/task_result_servicer.py`
- `app/layer3_adapters/controllers/grpc/server.py`
- All `.proto` files under `workflow_proto/` and `proto/`.

### 3.3 Frozen — Socket.IO events

- `app/layer4_frameworks/realtime/broadcasting_publisher.py`
- `app/layer4_frameworks/realtime/socketio_consumer.py`
- Room naming convention: `run:{run_id}`, `run:{parent_run_id}`, `run:{root_run_id}`.
- Topic normalization to dot notation (e.g., `runstarted` → `run.started`).
- The three-queue backpressure (critical / normal / bulk).
- Event payload shapes for every domain event in `app/layer1_domain/events.py`.

### 3.4 Frozen — feature-layer contract (the real boundary)

For every feature under `app/layer2_application/features/`:

- The `{PascalCase}UseCase` class name.
- The `execute(command) -> result` method signature.
- The `{PascalCase}Command` dataclass with its field names, types, defaults.
- The `{PascalCase}Result` (or similarly named) dataclass with its fields.
- The DI container key in `bootstrap.py` (`container["feature_name"]`).
- The feature's exceptions (raised to the adapter layer).

**Internal implementation of `execute()` is not frozen** — that's where all the migration work happens.

---

## 4. What Changes Inside the Feature Layer

All changes happen **below** the frozen contract. Feature use cases fall into four patterns.

### 4.1 Pattern A — Read-only

Features that return state today by reading `run.context`.

**Today:**
```python
class GetRunContextUseCase:
    def __init__(self, run_repo: IRunRepository): ...
    async def execute(self, cmd: GetRunContextCommand) -> GetRunContextResult:
        run = await self._run_repo.get_by_id(cmd.run_id)
        return GetRunContextResult(
            variables=run.context.variables,
            completed_nodes=list(run.context.completed_nodes),
            skipped_nodes=list(run.context.skipped_nodes),
            ...
        )
```

**Tomorrow:**
```python
class GetRunContextUseCase:
    def __init__(self, run_view: IRunContextView): ...  # ← changed dependency
    async def execute(self, cmd: GetRunContextCommand) -> GetRunContextResult:
        view = await self._run_view.get(cmd.run_id)
        return GetRunContextResult(
            variables=view.variables,
            completed_nodes=list(view.completed_nodes),
            skipped_nodes=list(view.skipped_nodes),
            ...
        )
```

**Command and Result dataclasses are byte-identical. Only the repository injected into the use case changes.** This is the most common pattern — the majority of the 51 features fit here.

### 4.2 Pattern B — Write-then-emit (synchronous feedback preservation)

Features that start something and whose caller expects the new state to be immediately queryable. `start_run` is the canonical example — the frontend calls `POST /runs`, then immediately calls `GET /runs/{id}` or subscribes via Socket.IO. The state must exist before the first response returns.

**Today:**
```python
class StartRunUseCase:
    async def execute(self, cmd: StartRunCommand) -> StartRunResult:
        run = WorkflowRun.create(workflow_id=cmd.workflow_id, ...)
        await self._run_repo.save(run)
        await self._engine.orchestrate_start(run)    # ← direct engine call
        return StartRunResult(id=run.id, status=run.status, ...)
```

**Tomorrow:**
```python
class StartRunUseCase:
    async def execute(self, cmd: StartRunCommand) -> StartRunResult:
        run_id = cmd.predefined_run_id or str(uuid.uuid4())

        # 1) Write the initial "pending" state so GET /runs/{id} works immediately
        initial = InitialRunState(
            id=run_id,
            workflow_id=cmd.workflow_id,
            status=RunStatus.PENDING,
            started_at=now_iso(),
            ...
        )
        await self._run_state_repo.save_initial(initial)

        # 2) Emit the command on the bus; Executor picks it up asynchronously
        await self._command_bus.send(
            "run.start",
            {"run_id": run_id, "workflow_id": cmd.workflow_id,
             "input_data": cmd.input_data, ...},
        )

        # 3) Return immediately with the same Result shape as today
        return StartRunResult(
            id=run_id,
            workflow_id=cmd.workflow_id,
            status="PENDING",
            started_at=initial.started_at,
        )
```

**Result shape is byte-identical.** The frontend sees a slightly different status progression (`PENDING` appears briefly before `RUNNING`), but `PENDING` is already a valid value in today's `RunStatus` enum, so no contract change.

Sync feedback invariant: **the initial state write happens before the function returns**, so any subsequent read by the same caller sees a consistent state. This is what makes the event-driven model transparent to callers.

### 4.3 Pattern C — Fire-and-forget command

Features that just acknowledge an intent and return success immediately. Examples: `cancel_run`, `pause_run`, `resume_run`, `resume_pending_task`.

**Tomorrow:**
```python
class CancelRunUseCase:
    async def execute(self, cmd: CancelRunCommand) -> CancelRunResult:
        await self._command_bus.send(
            "run.cancel",
            {"run_id": cmd.run_id, "reason": cmd.reason},
        )
        return CancelRunResult(run_id=cmd.run_id, accepted=True)
```

The caller doesn't need the cancellation to have been applied yet — only that it's been accepted. The Executor will process the `run.cancel` event, mark the run cancelled, and emit `run.cancelled` for the UI to observe. Same Result dataclass as today.

### 4.4 Pattern D — Read-modify-write via command

Features that need to atomically read existing state, mutate it, and trigger an effect. `edit_node_input` is the hard one: read the current file-backed data, apply edits, write back as a new file_ref, then optionally rerun the downstream.

**Tomorrow:**
```python
class EditNodeInputUseCase:
    async def execute(self, cmd: EditNodeInputCommand) -> EditNodeInputResult:
        # 1) Read current state from materialized view
        view = await self._run_view.get(cmd.run_id)
        current_ref = view.variables[cmd.node_id]

        # 2) Apply edits — this part stays in-process because it needs
        #    file-service access and the CSV edit library
        new_ref = await self._file_editor.apply_edits(
            current_ref, cmd.edits, cmd.new_rows, cmd.delete_row_indices, ...
        )

        # 3) Emit a command describing the edit
        await self._command_bus.send(
            "node.input.edited",
            {"run_id": cmd.run_id, "node_id": cmd.node_id,
             "new_file_ref": new_ref, "auto_rerun": cmd.auto_rerun},
        )

        return EditNodeInputResult(
            run_id=cmd.run_id,
            node_id=cmd.node_id,
            new_file_ref=new_ref.get("file_id", ""),
        )
```

The edit itself is a file-service operation that must complete before the Result returns (the caller needs the new `file_id`). The **effect** of the edit (re-running the downstream) is dispatched via command. The materialized view sees the new file_ref when the Projector processes the `node.input.edited` event.

This pattern is rare — only 4-5 features need it (`edit_node_input`, `rerun_task`, `add_node_artifact`, `store_output`, `set_main_flow`).

---

## 5. New Interfaces Added (All Below the Adapter Layer)

To support the patterns above, four new interfaces join the existing ones in `app/layer2_application/interfaces/`:

### 5.1 `ICommandBus`

```python
class ICommandBus(Protocol):
    async def send(self, topic: str, payload: dict[str, Any]) -> None: ...
```

A thin facade over the existing event publisher chain. Implementations:
- **In-process (memory bus)** — default for tests and single-node dev. Delivers to a local handler in the same process.
- **Kafka / NATS / Event Mesh** — production.

Today's `IEventPublisher` already provides the transport. `ICommandBus` is a semantic wrapper that makes the intent clear (a command is a request to do something, an event is a record that something happened). Reuses the same decorator chain (`RealtimeBroadcastingPublisher` → `EnrichingEventPublisher` → backend).

### 5.2 `IRunContextView` — **landed Phase 4a, 2026-04-11**

Implemented. Lives at `app/layer2_application/interfaces/run_context_view_interface.py`. Final field set is wider than the initial sketch because the existing read-only features already reached for more than just `RunContext` — `workflow_id`, `started_at`, `output_data`, etc. are properties of the run, not the context, but the shim consolidates them into a single read-only facade so feature-layer code only depends on one interface.

```python
class IRunContextView(Protocol):
    @property
    def run_id(self) -> str: ...
    @property
    def workflow_id(self) -> str: ...
    @property
    def workflow_version(self) -> str: ...
    @property
    def status(self) -> str: ...  # RunStatus.value already unwrapped
    @property
    def started_at(self) -> str: ...
    @property
    def completed_at(self) -> str | None: ...
    @property
    def error(self) -> str | None: ...
    @property
    def output_data(self) -> dict[str, Any] | None: ...
    @property
    def input_data(self) -> dict[str, Any]: ...
    @property
    def variables(self) -> Mapping[str, Any]: ...
    @property
    def completed_nodes(self) -> frozenset[str]: ...
    @property
    def skipped_nodes(self) -> frozenset[str]: ...
    @property
    def entry_nodes(self) -> tuple[str, ...]: ...
    @property
    def dag_info(self) -> DAGInfo: ...
    @property
    def child_runs(self) -> Mapping[str, str]: ...
    @property
    def parent_run_id(self) -> str | None: ...
    @property
    def root_run_id(self) -> str: ...
    @property
    def depth_level(self) -> int: ...


class IRunContextViewStore(Protocol):
    def get(self, run_id: str) -> IRunContextView | None: ...
    def get_by_root_id(self, root_id: str) -> list[IRunContextView]: ...
```

Default concrete implementation — `RunContextSnapshot` (`app/layer1_domain/entities/run_context_snapshot.py`): frozen dataclass with a `from_run(WorkflowRun)` classmethod that shallow-copies mutable collections so snapshot mutations cannot leak back into the live aggregate.

Backing stores (swapped by `bootstrap.py`):
- **Phase 4a (landed):** `LiveRunContextViewStore` wraps `IRunRepository`, materialises a `RunContextSnapshot` on each `get()`. See `app/layer4_frameworks/run_context/live_view_store.py`.
- **Phase 4b:** a projector-backed store reads from the event log. Feature-layer code does not change.

Migrated features as of 2026-04-11: `get_run_status`, `get_run_context`, `get_node_output`. Remaining read-only features on the matrix below will migrate incrementally as Phase 4b lands.

### 5.3 `IRunStateRepository`

Read-write access to the materialized view of run state. Used by `Pattern B — write-then-emit` to write the initial "pending" state before emitting the start command.

```python
class IRunStateRepository(Protocol):
    async def save_initial(self, initial: InitialRunState) -> None: ...
    async def get(self, run_id: str) -> RunStateSnapshot: ...
    async def list(self, filters: RunStateFilters) -> RunStateList: ...
```

This replaces direct use of `IRunRepository` from use cases that read/write run state for API responses. The `IRunRepository` stays as a Projector-side concept.

### 5.4 `IEventLogRepository` (already in the execution plan)

Append-only per `run_id`. Written by the Executor, read by the Projector. Not touched by the adapter or feature layers except for `get_run_events` if such a feature is added later.

---

## 6. Feature Migration Matrix

Every feature classified by pattern. Column `New deps` lists the shim/bus injections; column `Risk` flags anything subtle.

### 6.1 Read-only features (Pattern A)

Simply inject `IRunContextView` / `IRunStateRepository`. No behavior change.

| Feature | New deps | Risk |
|---|---|---|
| `get_run_status` | `IRunStateRepository` | — |
| `get_run_context` | `IRunContextView` | — |
| `list_runs` | `IRunStateRepository` | OData filtering must still work against the materialized view |
| `get_task` | `IRunStateRepository` | Task state must be projected into the view |
| `get_node_execution` | `IRunContextView` + `IRunStateRepository` | — |
| `get_node_output` | `IRunContextView` | Reads file_ref; materialization still happens lazily |
| `get_node_input_data` | `IRunContextView` | Uses resolver to build node input; resolver unchanged |
| `get_node_data_info` | `IRunContextView` | — |
| `get_variable_suggestions` | `IRunContextView` | Walks variables; purely read |
| `get_workflow_node_tree` | `IRunContextView` | — |
| `get_node_artifacts` | `IRunContextView` | — |
| `get_expression_reference` | workflow repo only | No run state involved |
| `get_workflow_parameters` | workflow repo only | — |
| `get_output` | `IRunStateRepository` | Reads stored final output |
| `get_socketio_config` | — | Pure config read |

### 6.2 CRUD features (no engine involvement at all)

These are workflow/definition/webhook management. They talk to the same repositories today and in the target model — nothing changes.

| Feature | Notes |
|---|---|
| `create_workflow`, `update_workflow`, `delete_workflow`, `get_workflow`, `list_workflows` | Workflow CRUD — unchanged |
| `create_node_definition`, `update_node_definition`, `delete_node_definition`, `get_node_definition`, `list_node_definitions`, `test_node_definition` | Definition CRUD — unchanged |
| `create_webhook`, `update_webhook`, `delete_webhook`, `get_webhook`, `list_webhooks`, `list_webhook_deliveries` | Webhook CRUD — unchanged |
| `list_node_palette` | Reads node definitions — unchanged |
| `set_main_flow`, `list_main_flows` | Workflow organization — unchanged |
| `validate_workflow` | **Stays as-is** — calls `DAGValidator` (now linter-only); API contract identical |
| `health` | Unchanged |

### 6.3 Write-then-emit features (Pattern B)

| Feature | New behavior | Risk |
|---|---|---|
| `start_run` | Write initial `PENDING` state, emit `run.start` command | Status briefly visible as `PENDING`; `PENDING` is already a valid `RunStatus` |

This is the only feature where the caller immediately expects to query the new state. Every other feature that creates state (workflows, definitions, webhooks) is a pure CRUD write with no engine involvement.

### 6.4 Fire-and-forget command features (Pattern C)

The Result dataclass stays the same. The use case emits a command and returns `accepted=True`. The actual effect is observed later via Socket.IO or polling.

| Feature | Emitted command | Risk |
|---|---|---|
| `cancel_run` | `run.cancel` | — |
| `pause_run` | `run.pause` | — |
| `resume_run` | `run.resume` | — |
| `complete_task` | `task.completed` | Worker callback path — idempotency via `idempotency_key` field |
| `fail_task` | `task.failed` | Same idempotency concern |
| `resume_pending_task` | `pending.resumed` | HITL resume |
| `resume_input_node` | `input.resumed` | HITL resume |
| `sweep_timed_out_child_runs` | `run.cancel` (per timed-out child) | Batch emission |
| `clear_all_data` | — | Admin/test endpoint; bypasses bus if in dev mode |
| `reset_test_run` | `run.reset` | Test mode |

### 6.5 Read-modify-write features (Pattern D)

The file-service mutation happens synchronously inside the use case; the downstream effect is emitted as a command.

| Feature | Synchronous work | Emitted command |
|---|---|---|
| `edit_node_input` | File service apply-edits (produces new file_ref) | `node.input.edited` (with `auto_rerun` flag) |
| `rerun_task` | Optionally update input | `task.rerun` |
| `add_node_artifact` | File service artifact upload | `node.artifact.added` |
| `store_output` | File service output upload | `run.output.stored` |

### 6.6 Test-mode features

These features don't start "real" runs — they execute in a synthetic context. They deserve bespoke handling.

| Feature | Strategy |
|---|---|
| `test_node` | Execute the node pipeline in-process against a synthetic context. Does not emit commands to the bus. Use an in-memory command bus implementation scoped to the test run. |
| `test_full_workflow` | Start a real run with `is_test=True`; the Executor respects the flag and skips certain persistence or side effects per run settings. |
| `dry_run_workflow` | Same as `test_full_workflow` but tagged `dry_run=True`; handlers that have side effects consult this flag and no-op. |

Today's `is_test` and `test_mode` fields on `WorkflowRun` already support this. The event-driven path preserves the same semantics — tests go through the same code path as production runs, just with a different flag.

---

## 7. Socket.IO Stability — Zero Changes

The Socket.IO integration is the most visible part of the adapter layer from a frontend perspective and the least in need of change.

- **Room conventions** (`run:{run_id}`, `run:{parent_run_id}`, `run:{root_run_id}`) stay identical.
- **Topic normalization** (camelCase → dot notation) stays identical.
- **Three-queue backpressure** (critical / normal / bulk with capacities 1000 / 5000 / 10000) stays identical.
- **The `RealtimeBroadcastingPublisher` decorator chain** stays identical — it's already in the right shape for the target architecture (gap analysis §2.4).
- **All event payload shapes** in `app/layer1_domain/events.py` stay identical.

The one subtle change: in Phase 5, events emitted by the Executor arrive at the `RealtimeBroadcastingPublisher` via the bus, not via direct engine-to-publisher calls. This is invisible to the frontend because the publisher doesn't care where events come from — it fans out whatever it receives.

**Frontend work required: zero.** The existing Socket.IO client code continues to subscribe to the same rooms and listen for the same event names.

---

## 8. gRPC Stability — Zero Changes

The gRPC servicers follow the same thin-adapter pattern:

- `run_event_servicer.py` — streams run events to clients.
- `task_result_servicer.py` — accepts task results from workers.

Both call feature use cases and translate between protobuf messages and command/result dataclasses. As long as the feature-layer contracts stay stable, the servicers don't change. The `.proto` files are frozen.

The task_result_servicer in particular handles the worker → control plane callback path. In the event-driven model, it becomes a thin translator: receive protobuf → build `CompleteTaskCommand` → call `CompleteTaskUseCase.execute` → which emits `task.completed` to the bus. No protocol change.

---

## 9. Error Contract — Zero Changes

Error responses go through `app/layer3_adapters/controllers/restful/v1/middleware/error_handler.py` and `dtos/response_envelope.py`. The `error_response` helper and `ErrorCode` enum are frozen.

New failure modes introduced by the event-driven model (e.g., "command bus unreachable," "Projector lag too high") map to existing error codes where possible:

| New failure mode | Existing error code |
|---|---|
| Command bus unreachable | `SERVICE_UNAVAILABLE` (HTTP 503) |
| Projector lag exceeds threshold when reading state | `SERVICE_UNAVAILABLE` or serve stale with a header |
| Initial state write fails in `start_run` | `INTERNAL_ERROR` (HTTP 500) |
| Materialized view missing for a `run_id` | `NOT_FOUND` (HTTP 404) — same as today |

If absolutely necessary, new error codes can be added to the `ErrorCode` enum without breaking existing ones. Clients that don't recognize a new code treat it as generic error. This is additive, not breaking.

---

## 10. Where New Behavior Becomes Observable

Three places where the migration is subtly visible to a very attentive client, none of them breaking:

1. **`start_run` response status.** Today returns a run that's already `RUNNING` (the engine has started dispatching inline). Tomorrow returns `PENDING` for a few milliseconds until the Executor picks up the `run.start` command. `PENDING` is already a valid `RunStatus`, so no contract change — clients that render status will show a brief `PENDING` → `RUNNING` transition.

2. **Event ordering via Socket.IO.** The existing priority lanes (IMMEDIATE / NORMAL / TERMINAL) already handle ordering. No change.

3. **Replay of events on reconnect.** Socket.IO clients that subscribe mid-run today miss events that fired before subscription. In the event-driven world, the event log is authoritative, so a new feature could let clients replay from any event_id. **This is an enhancement, not a breaking change** — existing clients don't replay and continue to work.

---

## 11. Phase Mapping

How the adapter-stability work slots into the execution plan:

| Phase | Adapter work | Feature-layer work |
|---|---|---|
| **0** — Async httpx fix | None | Await chain propagated through resolver-touching features |
| **1** — Run-scoped cache | None | None |
| **2** — Handler classification + metrics | None | None |
| **3** — LOOP design spike | None | None |
| **4** — Dispatcher + Projector split | **None** | **Introduce `ICommandBus`, `IRunContextView`, `IRunStateRepository`. Migrate all 51 features to use the new interfaces while engine still runs inline.** This is a pure refactor — no observable behavior change. Adapter contract identical. |
| **Phase 4.5** — Go extraction decision | None | None |
| **5** — Event-driven BUILTIN | **None** | Engine reads commands from bus; features continue to emit via `ICommandBus`. Nothing in the adapter layer notices the engine flipped. |

**Zero adapter-layer changes across all six phases.** Every migration PR that touches adapter code is a bug and should be rejected at review.

### 11.1 Phase 4 is where the feature-layer work lands

Phase 4 (Dispatcher + Projector split) is the right window to introduce the shim/bus infrastructure. At that phase:

- The Projector stands up, producing a materialized view.
- `IRunContextView` + `IRunStateRepository` get built.
- `ICommandBus` is introduced (in-process implementation initially).
- Each of the 51 feature use cases is migrated one at a time to use the new interfaces instead of the old `IRunRepository` + direct engine calls.
- Feature unit tests re-run unchanged.
- Integration tests (`tests/integration/test_api.py`) re-run unchanged.

Migration order within Phase 4 (safest first):
1. **Read-only features** (Pattern A, ~15 of them) — trivially swap repo injections.
2. **CRUD features** — no change needed; audit for hidden engine dependencies.
3. **Fire-and-forget command features** (Pattern C, ~10 of them) — wrap direct engine calls in command emissions.
4. **Write-then-emit** (`start_run`) — trickiest, ensure initial state write is atomic.
5. **Read-modify-write** (Pattern D, ~4 features) — bespoke per feature.
6. **Test-mode features** — last, with special handling.

Each step is a separate PR that keeps tests green.

---

## 12. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| **Initial state write races with command processing.** In `start_run`, if the Executor consumes the `run.start` command and emits `run.started` before the caller's initial state write completes, the Projector may apply events out of order. | The initial state write happens **before** the command emission. Use a transactional write (same DB transaction that emits the command for SQL bus, same acked produce for Kafka). Projector treats initial state as authoritative for `PENDING` status; subsequent events transition it. |
| **Socket.IO event ordering within a run.** The current priority lanes assume a single in-process `EventCollector`. In multi-replica Executors, events from the same run must still arrive in order. | Bus-level partitioning by `run_id` preserves order within a run. `EventCollector` stays in-process at each Executor replica for the duration of one event handler. |
| **Command bus down = inability to start runs.** Single point of failure on a critical path. | Same failure characteristics as today's messaging layer (Kafka/Event Mesh). Add a health check. Fall back to in-process bus for dev environments. |
| **Features silently reading from the wrong source.** A feature that grabs `run.context` directly during Phase 4 migration breaks as soon as the engine stops maintaining that object. | Grep audit during Phase 4: any `run.context.` access outside the shim is a CI failure. Introduce a type-check lint. |
| **Integration tests hardcoded to engine internals.** Some tests may construct `WorkflowRun` objects directly and assert against `run.context`. These tests break when `RunContext` becomes a projection. | Tests that use `run.context` directly are test-side code, not adapter code. Update test fixtures to use `RunContextView` mocks. Budget time for this in Phase 4. |
| **Long-running features that wait for completion.** If any feature synchronously awaits the run finishing (`test_full_workflow` might), the event-driven model forces them to become async waits with timeouts. | Inventory during Phase 4. For `test_full_workflow`, allow explicit `wait_for_completion: bool` flag on the command; implement the wait via an in-memory subscription to the run's event log. |
| **Frontend assumes `RUNNING` status immediately after `start_run`.** Any frontend that asserts `response.status == "RUNNING"` after POST breaks on the brief `PENDING` window. | Frontend already handles `PENDING` as a valid state in the run timeline. Document the change in the release notes. |

---

## 13. Verification

The adapter layer's stability is verified by **unchanged tests passing**:

- **`tests/integration/test_api.py`** — the primary regression gate. Exercises REST endpoints end-to-end. Must pass unchanged before and after Phase 4 and Phase 5.
- **Feature unit tests** — each feature's existing tests (`tests/unit/test_dry_run_workflow.py`, `test_edit_node_input.py`, etc.) must pass with only the repo/bus dependencies swapped in conftest.
- **Socket.IO broadcast tests** — `tests/unit/test_broadcasting_publisher.py` must pass unchanged; same event shapes, same rooms.
- **gRPC servicer tests** (if they exist) — pass unchanged.
- **Frontend manual smoke** — run the existing frontend against the Phase 4 build, then the Phase 5 build. No code changes, full workflow edit + run + observe lifecycle works.

A **"do not touch"** set of files gives concrete enforcement. Any migration PR modifying these files is a smell and should prompt review:

- `app/layer3_adapters/**` — all of it.
- `app/layer2_application/features/**/use_cases/*.py` — signatures and dataclass shapes only (internals are expected to change, the signatures are frozen).
- `app/layer1_domain/events.py` — additions OK, renames/removals not.
- `workflow_proto/**`, `proto/**` — all `.proto` files.
- `app/layer4_frameworks/realtime/broadcasting_publisher.py` — payload shapes.

---

## 14. Open Questions

- **Transactional outbox for `start_run`'s write-then-emit.** In SQL-backed deployments the initial state write and the `run.start` command emission should be atomic. Does Phase 4 introduce the outbox table now, or defer to Phase 5? Deferring risks a narrow crash window where state is written but command isn't emitted. Recommend introducing in Phase 4.
- **`test_full_workflow` wait semantics.** Does the synchronous wait stay in Phase 4 (engine still inline) and become an async wait in Phase 5? Or unify the implementation earlier? Small design call; handle in Phase 4.
- **Command bus reliability under Phase 4's in-process mode.** In-process mode has no retry. If a command handler throws, the command is lost. Should the in-process bus have a memory-bounded retry queue? Cheap to add; document the behavior.
- **Grace window for deprecated read patterns.** Should `run.context.variables[x]` access emit a deprecation warning during Phase 4, or a hard error? Warning is friendlier; error is safer. Lint it.
- **`rerun_task` and `edit_node_input` semantics on in-flight runs.** Today these mutate state in place. In the event-driven model, if the run is mid-execution, how do concurrent events interact with the edit? Needs a small design pass before Phase 5.

---

## 15. Summary

The adapter layer (REST + gRPC + Socket.IO + DTOs + middleware) does not change across any phase of the event-driven migration. The stability boundary is the feature layer's `UseCase.execute(command) -> result` contract. Every one of the 51 use cases keeps its signature, its command dataclass, and its result dataclass identical.

Internals change by pattern:
- **Read-only** (~15 features): swap the injected repository for a `RunContextView` shim.
- **CRUD** (~15 features): no change needed.
- **Fire-and-forget** (~10 features): emit a command via `ICommandBus` instead of calling engine methods.
- **Write-then-emit** (`start_run` only): write initial state synchronously, then emit command.
- **Read-modify-write** (~4 features): synchronous file-service work, then emit command.
- **Test mode** (~4 features): bespoke handling per feature.

New interfaces all live in `app/layer2_application/interfaces/`: `ICommandBus`, `IRunContextView`, `IRunStateRepository`, `IEventLogRepository`. None of them reach the adapter layer.

The work lands in Phase 4 of the execution plan, as a pure refactor with no observable behavior change. Phase 5 then flips the engine underneath without touching any feature or adapter code a second time.

**Regression gate:** `tests/integration/test_api.py` and all feature unit tests pass unchanged from Phase 0 through Phase 5. Any PR that modifies a file under `app/layer3_adapters/` during the migration is a review trigger.

The core insight: because the codebase already uses a clean command/use-case pattern (`StartRunCommand`, `CancelRunCommand`, etc.), the event-driven migration is a **below-the-use-case** refactor. The contract was built for this exact transition — we just haven't finished completing it yet.
