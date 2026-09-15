# Context, Validator, and Resolver in the Event-Driven Migration

> Deep-dive on the three runtime subsystems that the event-driven migration affects most: the **run context** (shared mutable state), the **validators** (DAG and schema), and the **variable resolver** (expression + file-backed data). Explains how each works today, how the event-driven model changes it, and the specific downstream impacts that need handling.
>
> Companion documents:
> - [`event-driven-orchestration.md`](./event-driven-orchestration.md) — target architecture
> - [`event-driven-gap-analysis.md`](./event-driven-gap-analysis.md) — current vs. target gap
> - [`event-driven-execution-plan.md`](./event-driven-execution-plan.md) — phased migration plan
> - [`polyglot-engine-decision.md`](./polyglot-engine-decision.md) — Go engine decision
> - [`runtime-validation-pipeline.md`](./runtime-validation-pipeline.md) — in-process async pipeline for resolution + validation + execution
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. Why This Deep-Dive Exists

The execution plan names Phases 0–5 but does not explain, concretely, what happens to the **run context object**, the **DAGValidator + SchemaValidator**, and the **VariableResolver + strategy chain** when the engine flips from DAG-walker to event-driven. These three subsystems are where the current architecture's assumptions are load-bearing, and where most of the subtle behavior lives.

Specifically:

- `WorkflowRun.context` is a **live mutable object** shared between the engine and every handler. Event-driven execution has no such shared object — state is derived from the event log. This is the single biggest mental-model flip in the migration.
- `DAGValidator` **gates** run start today. Event-driven execution accepts invalid topologies at runtime.
- `VariableResolver` reads from `run.context.variables` directly. That dict no longer exists in the new model.

At least **15 feature use cases** read `run.context` directly. Getting this subsystem migration wrong breaks the public API.

---

## 2. How Things Work Today

### 2.1 RunContext — the live mutable object

Source: `app/layer1_domain/entities/workflow_run.py`.

```python
@dataclass(kw_only=True)
class RunContext:
    variables: dict[str, Any] = field(default_factory=dict)
    loop_state: dict[str, Any] = field(default_factory=dict)
    completed_nodes: set[str] = field(default_factory=set)
    current_node: str = field(default_factory=str)
    skipped_nodes: set[str] = field(default_factory=set)
    child_runs: dict[str, str] = field(default_factory=dict)
    dag_info: DAGInfo = field(default_factory=DAGInfo)
```

Each field plays a distinct role:

| Field | Populated by | Read by |
|---|---|---|
| `variables` | `successor_processor.py:213` after every handler returns `state_updates` | Resolver, all handlers, 15+ features |
| `loop_state` | `loop_handler.py` mutates directly; `successor_processor.py:218-219` merges `loop_state_updates` | `loop_handler`, `_run_loop_iterations` in `orchestration_engine.py:507-602` |
| `completed_nodes` | `successor_processor.py:231` + `orchestration_engine.py:1257` when a task finishes | DAG traversal, skip propagation, merge quorum checks |
| `skipped_nodes` | `successor_processor.py:206` + `dag_traversal.collect_downstream_nodes_to_skip()` | Skip propagation, `get_run_context` feature |
| `current_node` | Set transiently per handler invocation | Mostly telemetry |
| `child_runs` | `orchestration_engine.py:1484-1486` when a WORKFLOW node completes | `workflow_handler`, parent/child run linking |
| `dag_info` | `DAGPreparer.prepare(workflow)` — built once in `orchestrate_start` | Engine recursion, skip propagation |

**Critical behavior:** `state_updates` values that are `dict` or `list` are **not stored inline**. They are uploaded to the file-service via `DataStoreService.store_data()` (lines 47–103) and replaced with a lightweight file-reference dict:

```python
{
  "__file_ref__": True,
  "file_id": "abc-123",
  "row_count": 500,
  "columns": ["id", "name", ...],
  "extracted": { "path.to.scalar": {"value": 42} },  # pre-extracted scalar cache
  "artifacts": [...]  # array-field artifact CSVs
}
```

So `run.context.variables` is a **mix** of scalar values, small dicts, and file-ref dicts pointing at CSVs on the file-service. The `extracted` scalar cache is the fast path — if a downstream resolver needs only a scalar value that's pre-extracted, no HTTP call to the file-service is needed.

Nodes are aliased by both ID and by human-readable name in `variables` (successor_processor.py:215-217), so `{{$fetch_user.body.id}}` and `{{$node_abc123.body.id}}` both resolve.

**Concurrency model:** one `asyncio.Lock` per `run_id` in `orchestration_engine.py:87` serializes all operations on the same run's context within the process. Different runs don't serialize each other unless one blocks the event loop (see the gap analysis for the sync-httpx bug).

### 2.2 DAGValidator — the pre-run gate

Source: `app/layer1_domain/services/dag_validator.py`.

`DAGValidator.validate(workflow)` is called once in `orchestrate_start()` (orchestration_engine.py:1088–1108). If `ValidationResult.is_valid` is False, the run is rejected with `WorkflowExecutionError`.

Checks performed (in order):

1. **`_check_edge_integrity`** — every edge's source/target references a real node.
2. **`_check_acyclicity`** — Kahn's topological sort; `CYCLE_DETECTED` error if processed count ≠ node count.
3. **`_check_connectivity`** — BFS from entry nodes; `UNREACHABLE_NODES` error if any node not reached.
4. **`_check_trigger`** — at most one TRIGGER node.
5. **`_check_type_constraints`** — CONDITION has ≤2 outgoing edges.
6. **`_check_workflow_nodes`** — WORKFLOW node has `config.workflow_id`, not self-referencing.
7. **`_check_port_references`** — `source_port_id` / `target_port_id` declared on node's ports.
8. **`_check_port_cardinality`** — CONDITION declares both `true_branch` and `false_branch` out-ports.

`_check_references` (lines 117–138) is **commented out** today. It was a reference-integrity check that validated expressions point at upstream nodes; it was disabled and never restored.

The validator is also exposed via the `validate_workflow` feature use case for the editor, which today does double duty: it's both the editor linter and the runtime gate.

### 2.3 SchemaValidator — the per-node check

Source: `app/layer1_domain/services/schema_validator.py` — all 22 lines of it.

```python
class SchemaValidator:
    @staticmethod
    def validate_input(input_data, schema) -> list[str]:
        # Only checks required-field presence.
```

It is deliberately minimal: only checks that fields marked `rules.isRequired=True` exist in `input_data`. No type checks, no format checks, no nested validation. Used by `task_handler.py` at dispatch time. The companion check is `InputRequirementError` raised from `task_handler.py:34–50` when required input-mapping keys aren't resolvable.

### 2.4 VariableResolver — the strategy chain

Source: `app/layer1_domain/services/variable_resolver.py`.

Parses n8n-style expressions like `{{$node1.data[0].name}}` into a `ParsedExpression(source, path_parts)` tuple. `source` identifies the top-level key in the resolution context (usually a node id or name); `path_parts` walks nested data.

Resolution flow in `_resolve_single` (lines 310–348):

1. Look up `source` in `context` (which is `run.context.variables`). If missing → `VariableResolutionError`.
2. Walk the strategy chain in order. For each strategy, call `strategy.try_resolve(source_data, path_parts)`. Return on first non-`UNRESOLVED` result.
3. If all strategies return `UNRESOLVED`, try the **legacy-path fallback** (`_rewrite_legacy_path`, lines 351–374) — rewrites old `output.X` wrapping and `metadata.action` paths for workflows created before file-backed storage. If fallback produces a new path, re-run the strategy chain.
4. Raise the first remembered error if nothing resolves.

**Strategies wired in `bootstrap.py:150-156`** (order matters):

| # | Strategy | File | Does I/O? | What it handles |
|---|---|---|---|---|
| 1 | `FileBackedResolverStrategy` | `file_backed_resolver.py` | **Yes** | `source_data` is a file_ref; returns scalar from `extracted` cache, or OData single-row fetch, or full materialize |
| 2 | `MetadataResolverStrategy` | `variable_resolver.py:112-128` | No | Reads pre-extracted scalar from `source_data.metadata.extracted[json_path]` |
| 3 | `TraversalResolverStrategy` | `variable_resolver.py:131-163` | No | Walks nested dict/list by path; also supports flattened-key lookup for HTTP headers |

The `FileBackedResolverStrategy` has three hot paths internally:

1. **Extracted-cache hit** (`file_backed_resolver.py:72-74`) — scalar is in `source_data.extracted[cache_key]`. No I/O. Sub-millisecond.
2. **Array-field artifact resolution** (`_try_resolve_artifact`, lines 106–166) — the first path part matches an `array_field` artifact; for integer indexes, tries OData single-row fetch; else downloads the artifact CSV. **HTTP call on fallback.**
3. **OData single-row fetch** (`_try_odata_row_fetch`, lines 172–203) — for any path containing an integer index, tries `$skip`/`$top` against the main file. **HTTP call.**
4. **Full materialize** (`_cached_materialize`, lines 225–233) — downloads the whole CSV via `DataStoreService.materialize`. **Always HTTP; cached in `self._materialize_cache` keyed by `file_id`.**

Plus a dedicated `_META_KEYS` set (lines 43–46) for dunder-prefixed paths like `__file_id`, `__row_count`, `__columns` — these read file-reference metadata without fetching data.

### 2.5 ConditionEvaluator

Source: same file, `variable_resolver.py:386-437`.

```python
class ConditionEvaluator:
    def __init__(self, resolver: VariableResolver): ...
    def evaluate(self, condition_expr: str, context: dict) -> bool:
        # Splits on ' OR ' / ' AND ' recursively
        # Then calls _evaluate_single
```

`_evaluate_single` calls `self._resolver.resolve(expr, context)` to materialize the expression string, then parses a `left OP right` form with the `_CONDITION_OP_PATTERN` regex. Operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `IN`, `CONTAINS`. Values are coerced by `_coerce` (quoted strings, bool literals, null, int, float, fallback to raw string).

Called from `ConditionNodeHandler.handle()` at `condition_handler.py:47-48`.

### 2.6 Cache lifecycle

Source: `orchestration_engine.py:114-131` (`drain_events`).

```python
async def drain_events(self) -> None:
    # Clear resolver materialization cache to free memory and prevent
    # stale data from leaking across orchestration passes.
    if self._variable_resolver and hasattr(self._variable_resolver, "clear_caches"):
        self._variable_resolver.clear_caches()
    ...
```

`clear_caches()` walks every strategy and calls `clear_materialize_cache()` if present. Only `FileBackedResolverStrategy` defines it. **Effect:** within a single orchestration pass, the `_materialize_cache` dedupes file downloads; between passes, the cache is wiped and two expressions touching the same file-backed variable in the next pass will re-download.

This is the behavior Phase 1 of the execution plan targets — extending the cache lifetime to the whole run.

### 2.7 A typical handler invocation — how the three interact

Walking one CONDITION node handling a `node.input` event inside the current system:

1. Engine calls `dispatch_or_handle_task(condition_node, ...)`.
2. `ConditionNodeHandler.handle()` receives `run: WorkflowRun` and reads `run.context.variables`.
3. Handler calls `ConditionEvaluator.evaluate(expr, run.context.variables)`.
4. Evaluator splits `AND`/`OR`, calls `_evaluate_single` per clause.
5. `_evaluate_single` calls `VariableResolver.resolve(clause, run.context.variables)`.
6. Resolver parses `{{$fetch_user.status}}`, finds `fetch_user` in `variables` (which is a file_ref dict).
7. Strategy chain: `FileBackedResolverStrategy.try_resolve(fetch_user_ref, ("status",))` — checks `extracted["status"]` — hit, returns `200`.
8. Evaluator substitutes `200`, matches `200 == 200`, returns `True`.
9. Handler returns `NodeHandlerResult` with `tasks_to_dispatch` for the true branch.
10. Engine processes the result: calls `successor_processor.process()`, which applies `state_updates` to `run.context.variables`, emits `EdgeTraversed` events, recurses into successor nodes.

On a cache miss at step 7 — if the scalar isn't pre-extracted — step 7 becomes an HTTP call to the file-service via `DataStoreService.materialize`, and (per the gap analysis) that call is **synchronous** today, blocking the entire event loop.

---

## 3. How It Changes in the Event-Driven Model

### 3.1 RunContext becomes a Projection

The biggest conceptual change. **There is no shared mutable `run.context` object anymore.** State lives in two places:

- **Event log** (source of truth) — ordered per `run_id`, contains every `node.output`, `node.failed`, `node.skipped`, etc. Written by the Executor, read by the Projector.
- **State store** (materialized view) — the Projector's output. A `RunState` record per run, computed by applying the event log via a pure function `apply(state, event) -> state`.

Handlers never touch this state directly. They receive a `node.input` event that carries:

- The incoming payload (from the upstream `node.output` that fired the wire).
- A **resolution context snapshot** built from the Projector's state store at event-processing time.
- The event's metadata (run_id, scope_stack for loop iterations, trace_id, attempt counter).

After the handler runs, it emits `node.output` events with the result payload. The Projector consumes those events and updates the materialized view. Other handlers running elsewhere will see the update on their next event.

Each field of today's `RunContext` maps to a Projector-owned view:

| Today's field | Tomorrow's source | Notes |
|---|---|---|
| `variables` | Projector's `variables` map, keyed by node_id and node_name | Still stores file_refs (see §3.5); read-only from the handler's perspective |
| `loop_state` | `scope_stack` on events | Gone from RunContext; lives per-event |
| `completed_nodes` | Projector derives from `node.output` events | Read-only for features |
| `skipped_nodes` | Projector derives from `node.skipped` events | Read-only for features |
| `current_node` | Gone | Multiple handlers run concurrently; no single "current" node |
| `child_runs` | Projector derives from `child_run.started` / `child_run.completed` events | Preserved |
| `dag_info` | Moves to the Dispatcher's wire-table cache | Not attached to the run anymore |

### 3.2 DAGValidator becomes an editor linter

The runtime stops calling `DAGValidator.validate` before start. The only remaining caller is the `validate_workflow` feature use case, which the editor hits on save/validate.

Specific checks:

- **Obsolete at runtime:** `_check_acyclicity` (cycles are allowed, runtime depth counter handles infinite loops), `_check_connectivity` (unreachable nodes are non-fatal; they just never receive events), `_check_edge_integrity` (dangling edges become `wire.dead_letter` events).
- **Still useful at runtime:** nothing. Every check becomes advisory.
- **Still useful in the editor (kept as-is):** all nine checks continue to run in the `validate_workflow` path. The editor UX is unchanged — users still see red dots on broken wires at edit time. The only change is that the runtime no longer blocks.

`_check_references` (commented out today) stays commented out. Expression references are resolved lazily at runtime by the resolver, and failures are routed through the per-node `on_validation_error` policy — no static analysis needed.

### 3.3 SchemaValidator becomes a standard Executor stage

Today `SchemaValidator` is called only from `task_handler.py` for TASK nodes. In the event-driven model, input validation becomes a standard stage in the Executor that runs for **every** `node.input` event, regardless of node type. The validation is driven by the node's declared `input_schema` (JSON Schema / Pydantic / whatever the registry maps to the handler).

Output validation becomes symmetric: after the handler returns, the Executor validates the output against `output_schema` before emitting `node.output`.

Both are controlled by per-node policy:

```yaml
policy:
  on_validation_error:        { mode: fail }  # or continue / default / route_to_error
  on_exec_error:              { mode: retry, retry: { max: 3, ... } }
  on_output_validation_error: { mode: fail }
```

This is where the current `InputRequirementError` and `VariableResolutionError` plug in: both get caught at the Executor boundary and routed through `on_validation_error`.

### 3.4 VariableResolver stays, but its input and lifecycle change

The biggest win: the resolver itself barely changes. The `{{$source.path[0].key}}` syntax stays. `ParsedExpression`, `MetadataResolverStrategy`, `TraversalResolverStrategy`, `FileBackedResolverStrategy`, `ConditionEvaluator` — all stay.

What changes:

1. **Input source.** Today: `run.context.variables` — a live mutating dict. Tomorrow: an **immutable snapshot** built at the moment the Executor starts processing an event. The snapshot is derived from the Projector's `variables` view plus the current event payload. Once built, it's passed to `resolver.resolve(expression, snapshot)` exactly as today.

2. **Everything is async.** ✅ **Done (Phase 0, 2026-04-11).** `VariableResolver.resolve`, `_resolve_single`, `_resolve_value`, `_apply_transform`, `resolve_mapping`, `ConditionEvaluator.evaluate`, `_evaluate_single`, `MetadataResolverStrategy.try_resolve`, `TraversalResolverStrategy.try_resolve`, `FileBackedResolverStrategy.try_resolve`, `_cached_materialize`, `_try_resolve_artifact`, `_try_odata_row_fetch`, `DataStoreService.materialize`, `DataStoreService.store_data`, `HttpFileServiceClient.upload_file` / `download_file` / `query_file_data` / `patch_file_data` / `delete_file` / `get_presigned_url` — all `async def`. The sync-in-async stall bug is eliminated; a slow file-service call in run A no longer blocks run B's event loop.

3. **Cache lifecycle.** ✅ **Done (Phase 1, 2026-04-11).** `FileBackedResolverStrategy` now holds two per-run bucket dicts: `_run_materialize_caches[run_id][file_id]` for full-file downloads and `_run_odata_caches[run_id][(file_id, query_params_tuple)]` for single-row OData fetches. The bucket is selected via a `_current_run_id_var` contextvar set by the orchestration engine at the top of `orchestrate_start` / `orchestrate_continue` / `orchestrate_fail`. The `drain_events` cache-clear call in `orchestration_engine.py:118` is removed — per-pass wiping would defeat the optimization. Cache entries are released when the run reaches a terminal state: `check_run_completion` drops the bucket in both the FAILED and COMPLETED branches, and `CancelRunUseCase` drops it for the cancelled parent plus cascaded children. An LRU bound on the bucket is deferred until Phase 2 metrics justify it; until then a simple dict is used and the drop-on-terminal semantics keep memory bounded by the number of in-flight runs.

4. **Strategy instances may become per-event or per-replica.** Today one `VariableResolver` instance per process, shared across all runs. In the event-driven model, multiple Executor replicas each hold their own resolver; cache state must be keyed so replicas don't conflict. Moving the cache to the state store (Redis, for example) avoids replica-local staleness; moving it to a per-run object with CAS also works. Post-Phase-1 the per-process cache is correct because the per-run `asyncio.Lock` guarantees single ownership within one process; Phase 4 will reconsider this when multi-replica execution becomes real.

5. **Error routing.** Today `VariableResolutionError` is raised and bubbled up from the handler. Tomorrow it's caught at the Executor boundary and routed through `on_validation_error`. The handler doesn't even see the error unless policy is `route_to_error`. (Phase 5.)

6. **Metadata cache stays identical.** `MetadataResolverStrategy` reads `source_data.metadata.extracted` — a pre-extracted scalar cache stored on the file_ref dict itself. That cache travels on the event payload in the new model, so the fast path remains just as fast.

### 3.5 File-backed storage stays — but moves location

`DataStoreService.store_data()` still runs. It still converts dict/list values into CSV file-refs uploaded to the file-service. What changes is **where** the conversion happens:

- **Today:** `successor_processor.py:208-213` — after the handler returns, before writing to `run.context.variables`.
- **Tomorrow:** Two options, to be chosen in Phase 4:
  1. **Executor-side** — the Executor converts large payloads to file-refs before emitting `node.output`. Event log stays small. Matches target design §10.1 large-payload externalization.
  2. **Projector-side** — events carry raw payloads; the Projector converts to file-refs when writing the materialized view. Event log can grow large.

Option 1 is preferred because it keeps the event log compact and replayable. The conversion is done once per emission, not once per Projector replay.

The `extracted` scalar cache stays useful regardless — it's the fast-path for resolver hits that don't need to touch the file-service.

### 3.6 Loop state becomes event metadata

Today `run.context.loop_state` is a live dict mutated by `LoopNodeHandler` and read by `_run_loop_iterations` in `orchestration_engine.py:507-602`. Each loop has a dict entry with `current_index`, `next_dispatch_index`, `completed_iterations`, accumulated outputs, and break flags.

Tomorrow this lives on the event itself:

```json
{
  "event_id": "...",
  "run_id": "...",
  "type": "node.input",
  "node_id": "process_order",
  "payload": { "order_id": 42 },
  "scope_stack": [
    { "loop_id": "for_each_order", "iter": 3, "total": 10 }
  ],
  "loop_depth": 1
}
```

The LoopHandler becomes stateless. On each iteration, it:

1. Receives a `loop.iteration.start` event (or similar) with the scope_stack.
2. Dispatches the loop body events, with the scope_stack propagated to every event inside the body.
3. Emits a `loop.iteration.complete` event when the body's terminal events are observed.
4. Dispatches the next iteration until the collection is exhausted or a break event arrives.

This is exactly why Phase 3 of the execution plan is a **dedicated LOOP design spike** before any code changes. Questions the spike must answer include: how does `break` propagate, how do nested loops stack, how does PARALLEL-inside-LOOP quorum work, how does replay reproduce iterations deterministically. See the execution plan §6 for the full list.

### 3.7 `dag_info` moves to the Dispatcher's wire-table cache

`DAGPreparer.prepare(workflow)` today builds adjacency, predecessors, edge_map, entry_nodes, leaf_nodes and attaches them to `run.context.dag_info` at run start.

In the event-driven model, the Dispatcher loads the wire table per workflow version from a cache (LRU with TTL, invalidated on `catalog.version_published`). It's not attached to the run. It's a property of the workflow definition, not of the run. This is a small but real cleanup.

The predecessors and entry_nodes are still needed: `dag_traversal.collect_downstream_nodes_to_skip()` (the merge-safe skip propagation) uses them. The Dispatcher wire table exposes them as properties of the cached table, and `dag_traversal` reads from there.

---

## 4. The Big Impacts

### 4.1 Mental model flip

**Today:** handler is `(run, node, task) -> result; mutate run.context; return`. Shared state, direct call.

**Tomorrow:** handler is `(event, snapshot) -> [new events]`. No shared state; the Executor wraps the call and translates result events into bus publishes.

Every handler author, every feature author, and every test that builds up a fake `run.context` must be re-educated. This is the single biggest reason the execution plan does Phase 4 (Dispatcher + Projector split) before Phase 5 (event-driven BUILTIN): the split forces the mental-model flip on the *infrastructure* first, while handlers still look the same, so the two changes don't collide.

### 4.2 Feature-layer compatibility — 15+ use cases read `run.context` directly

Grep found these features that read `run.context` or fields on it:

- `get_run_context` — exposes the whole context as a REST response.
- `get_run_status` — reads `status`, `completed_at`, `error`.
- `get_node_output` — reads `context.variables[node_id]`.
- `get_node_input_data` — builds input for a node by resolving expressions against `context.variables`.
- `get_node_data_info` — reads file-ref metadata from `context.variables`.
- `get_node_execution` — reads `context.completed_nodes`, `context.skipped_nodes`.
- `get_variable_suggestions` — walks `context.variables` to suggest completions to the editor.
- `get_expression_reference` — similar; reads the variable shape.
- `get_node_artifacts` — reads `context.variables[node_id].artifacts`.
- `get_workflow_node_tree` — reads `context.completed_nodes` and `context.skipped_nodes`.
- `edit_node_input` — mutates a portion of `context.variables` to re-run a node.
- `rerun_task` — reads state then re-dispatches.
- `test_node` — builds a synthetic `RunContext` for single-node testing.
- `test_full_workflow` — same for full-workflow tests.
- `dry_run_workflow` — builds a synthetic context.
- (And the orchestration-related ones: `start_run`, `complete_task`, `cancel_run`, `pause_run`, `resume_run`, `fail_task`, `resume_input_node`, `resume_pending_task` — these don't just read the context, they expect to mutate it.)

**Impact:** each of these must be re-pointed at the Projector's materialized view. The cleanest path is to introduce a **`RunContextView` shim** early — a read-only object the Projector exposes that has the same field shape as today's `RunContext`. Features read through the shim without knowing whether the source is a live dict or a projection. During the migration, the shim wraps the existing live `RunContext`; after Phase 4 it reads from the state store. Features are never rewritten, just re-injected with the new shim.

Mutating features (`edit_node_input`, `rerun_task`) are harder. They express an intent to **change** state, which in the event-driven model means **emitting a new event** (`node.input.override`, `run.fork`, or similar) rather than mutating a dict. These four or five features need bespoke redesign in Phase 5, not just a shim. Budget time for it.

### 4.3 File-backed storage is preserved but relocates

`DataStoreService` is a core optimization and is not going away. The `FileBackedResolverStrategy` is not going away. The `extracted` scalar cache on file-refs is not going away. What changes is **when** the conversion from dict/list → file-ref happens: from "after a handler returns, in the successor processor" to "before emitting `node.output`, in the Executor." Same code, different call site.

The key invariant to preserve: **scalar resolution from a file-backed variable must remain zero-I/O** when the scalar is in the `extracted` cache. The fast path today is `FileBackedResolverStrategy.try_resolve` line 72-74; that path must survive unchanged.

### 4.4 Resolver cache lifecycle inverts

- **Today:** `_materialize_cache` lives on the `FileBackedResolverStrategy` instance, is wiped every orchestration pass via `clear_caches()`, and loses state between passes within the same run.
- **Tomorrow:** cache is run-scoped (Phase 1), bounded by LRU, and only wiped when the run completes. Same run → same cache → no repeated downloads.

This is a pure win: Phase 1 already gets most of it, and the event-driven model doesn't regress it.

### 4.5 DAG validation semantics change meaning

- **Today:** `DAGValidator.validate` both gates the run and lints the editor — same function, two callers.
- **Tomorrow:** runtime never calls it. Editor still does. Cycles become allowed, but bounded by a runtime depth counter on events. Unreachable nodes become warnings, not errors. Dangling edges become runtime `wire.dead_letter` events.

**UX risk:** users who rely on "I can't start a run with a bad wire" will see their runs start and partially execute. Broken branches will fail; working branches will succeed. This is explicitly the new behavior the event-driven model wants — partial success instead of all-or-nothing — but it's a behavior change for existing users. Preserve the editor linter aggressively; ship it as a default-on "pre-run validation" in the UI that users can opt into.

### 4.6 Concurrency semantics change

- **Today:** one `asyncio.Lock` per `run_id` in the control-plane process. All operations on a run are serialized. Works because there's one process.
- **Tomorrow:** bus-level partitioning by `run_id`. Different runs go to different partitions and run on different Executor replicas. Within a run, a single partition preserves order. The asyncio lock is no longer needed.

**Impact:** the Projector and Executor can scale horizontally without coordination. But: any code path that implicitly depends on "only one thing is happening for this run at a time" will break. Examples: the current `_run_loop_iterations` assumes it's the only mutator of `loop_state`. That assumption is fine inside one process; in a partitioned event-driven system it's fine *only because of* the partition key. Be explicit about the invariant in the code.

### 4.7 Testing surface changes

Tests today instantiate `OrchestrationEngine` with memory repos, build a synthetic `WorkflowRun`, and call `orchestrate_start` directly. In the event-driven model, tests build synthetic events and call the Executor or Dispatcher directly, or use an in-process bus driver for end-to-end tests.

The existing `tests/unit/test_variable_resolver.py`, `test_condition_handler.py`, `test_dag_validator.py`, `test_dag_traversal.py`, `test_file_backed_resolver.py` are mostly reusable — they test pure functions that don't depend on the engine structure. `tests/integration/test_node_execution_e2e.py` is the one that needs the biggest rewrite.

### 4.8 Backward-compat shim strategy

Recommended: introduce a `RunContextView` abstraction in **Phase 4** (when the Projector is built), not in Phase 5.

```python
class IRunContextView(Protocol):
    @property
    def variables(self) -> Mapping[str, Any]: ...
    @property
    def completed_nodes(self) -> frozenset[str]: ...
    @property
    def skipped_nodes(self) -> frozenset[str]: ...
    @property
    def child_runs(self) -> Mapping[str, str]: ...
    # loop_state and dag_info deliberately omitted
```

- During Phase 4, the shim wraps the existing live `RunContext`. Features migrate one at a time to read through the shim instead of touching `run.context` directly.
- In Phase 5, the shim backend flips to read from the Projector's state store. Features don't notice.
- Features that **mutate** state don't use the shim; they emit events instead. Rewrite them in Phase 5.

This is the single highest-leverage migration technique for this subsystem. Without it, Phase 5 becomes a 15+ feature rewrite on top of an already-massive engine refactor.

---

## 5. Mapping to Execution-Plan Phases

| Phase | Status | What happens to context/validator/resolver |
|---|---|---|
| **0 — Async httpx fix** | ✅ **Done 2026-04-11** | Resolver, `DataStoreService`, `HttpFileServiceClient`, all resolver strategies now `async def`. `ConditionEvaluator.evaluate` async. Every handler call site awaits. No semantic change to RunContext. ~35 files touched across all four layers; follow-on fixes in `file_controller.py` (adapter) and `mock_data_factory_client.py` (infrastructure) caught by the test run. 1196/1196 tests green. |
| **1 — Run-scoped cache** | ✅ **Done 2026-04-11** | `FileBackedResolverStrategy` now holds per-run `_run_materialize_caches` and `_run_odata_caches`, keyed via a module-level `_current_run_id_var` contextvar set by the orchestration engine at entry points (`orchestrate_start` / `orchestrate_continue` / `orchestrate_fail`). Cache is dropped in `check_run_completion` FAILED/COMPLETED branches and in `CancelRunUseCase` (parent + cascaded children). `drain_events` no longer calls `clear_caches()`. 5 targeted unit tests added. LRU bound deferred. |
| **2 — Handler classification + metrics** | Not started | Adds `variable_resolve_duration_seconds{strategy}` histogram. No semantic change. |
| **3 — LOOP design spike** | Not started | Writes the `loop_state` → `scope_stack` migration plan. **Research only**, no code. |
| **4 — Dispatcher + Projector split** | Not started | **`RunContextView` shim introduced**. Projector materializes the same fields today's RunContext has. Feature use cases migrate to read through the shim, one by one. DAGValidator still called by the runtime (flag this for Phase 5). Engine still runs inline handlers; context is still live but wrapped in the shim. |
| **5 — Event-driven BUILTIN** | Not started | `DAGValidator` demoted to editor-only. SchemaValidator promoted to Executor standard stage with per-node policy. Context becomes a pure projection; handlers receive event payloads + snapshot, not a live `WorkflowRun`. `loop_state` removed from RunContext; replaced by `scope_stack` on events. Mutating features (`edit_node_input`, `rerun_task`, etc.) rewritten to emit events. |

---

## 6. Files That Will Change

### Resolver chain (Phase 0 + 1)

- `app/layer1_domain/services/variable_resolver.py` — make async, adjust cache ownership.
- `app/layer1_domain/services/file_backed_resolver.py` — make async, relocate cache.
- `app/layer1_domain/services/data_store_service.py` — make async.
- `app/layer2_application/interfaces/resolver_strategy_interface.py` — interface async.

### Validators (Phase 5)

- `app/layer1_domain/services/dag_validator.py` — caller list drops to just `validate_workflow` feature.
- `app/layer1_domain/services/orchestration_engine.py` lines 1088–1108 — remove the pre-run call.
- `app/layer1_domain/services/schema_validator.py` — promoted to a standard Executor stage, gains per-node policy handling.
- `app/layer2_application/features/validate_workflow/...` — unchanged; continues to call `DAGValidator`.

### Context (Phase 4 + 5)

- `app/layer1_domain/entities/workflow_run.py` — `RunContext` gains a projector-backed variant; existing fields become a view.
- `app/layer1_domain/services/successor_processor.py` — lines 207–219 (`state_updates` application) and 345–348 (alias-by-name) stop mutating context directly and instead emit events.
- `app/layer1_domain/services/orchestration_engine.py` — lines 212, 1257, 1260, 1484–1486 all do direct `run.context.variables[x] = value`; these become event emissions.
- All 15+ feature use cases listed in §4.2 — re-pointed at `RunContextView` shim.

### Loop (Phase 3 + 5)

- `app/layer1_domain/services/node_handlers/loop_handler.py` — full rewrite.
- `app/layer1_domain/services/orchestration_engine.py` lines 507–602 (`_run_loop_iterations`) — deleted; replaced by event-driven iteration.
- `app/layer1_domain/entities/workflow_run.py` — `RunContext.loop_state` removed.

### Event schema (Phase 4)

- `app/layer1_domain/events.py` — add `scope_stack`, `loop_depth`, resolution-context fields to events where relevant.

---

## 7. Open Questions

Unresolved items that deserve follow-up:

- **Resolver context snapshot building.** Does the Executor build the snapshot eagerly (on event receipt) or lazily (on first resolver call)? Eager is simpler; lazy is cheaper for handlers that don't use the resolver. The choice affects how snapshot-vs-projector consistency is handled under concurrent events.
- **File-backed storage side of event emission.** If `DataStoreService.store_data` runs inside the Executor before emitting `node.output`, the Executor holds a dependency on `file-service`. That's fine, but it means the Executor is no longer purely stateless — it needs file-service credentials and retries. Alternatively, push conversion to the Projector. Phase 4 design pass should decide.
- **Mutating feature rewrites.** `edit_node_input` and `rerun_task` are the only features that fundamentally depend on mutating state in place. Their rewrite to "emit an override event" needs its own small design doc.
- **Test fixtures.** Test code builds synthetic `WorkflowRun` objects. The event-driven model needs a matching `TestRunContextView` that tests can construct inline. Needs convention.
- **Cache key for run-scoped cache under horizontal scaling.** Phase 1 puts the cache on a run-scoped object. In multi-replica deployments, which replica owns it? Probably the partition owner for that `run_id`, which means the cache lives in the Executor's in-memory state. That's fine until replicas scale or restart — then cold runs re-download. Acceptable trade-off; document it.

---

## 8. Summary

The migration touches three tightly coupled subsystems:

- **RunContext** — the mental model flips from live mutable state to a projection derived from the event log. 15+ features read it today; a `RunContextView` shim in Phase 4 preserves their API while the backend migrates. Mutating features (4–5 of them) get bespoke rewrites in Phase 5.
- **DAGValidator** — demoted from runtime gate to editor linter. All nine checks stay in `validate_workflow`; runtime accepts cycles, unreachable nodes, and dangling edges and handles them at runtime via depth counters, warnings, and dead-letter events.
- **VariableResolver** — the single biggest survivor. Expression syntax, strategy chain, condition evaluator, metadata cache, file-backed resolver, OData fast paths — all stay. Changes are: async all the way down (Phase 0), run-scoped cache (Phase 1), immutable snapshot input (Phase 4), error routing through per-node policy (Phase 5).

The hardest single piece is LOOP, because `run.context.loop_state` becomes `scope_stack` on events and the `_run_loop_iterations` while-loop disappears. Phase 3 is a dedicated design spike specifically for this.

The highest-leverage migration tool is the **`RunContextView` shim** introduced in Phase 4. Without it, every feature that reads `run.context` has to be rewritten in Phase 5, and the scope blows up. With it, Phase 5 focuses on the engine and the 4–5 mutating features, not the whole feature layer.
