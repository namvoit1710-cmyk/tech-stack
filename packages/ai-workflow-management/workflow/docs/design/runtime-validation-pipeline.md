# Runtime Validation Pipeline — Design

> Deep-dive on how schema validation, expression resolution, and file-backed data materialization fit together at runtime in the event-driven architecture. Answers: should run context live in the DB? should validation use the current `file_ref` pattern? should it run in a separate process? what does the Go-or-Python choice actually affect?
>
> Companion documents:
> - [`event-driven-orchestration.md`](./event-driven-orchestration.md) — target architecture
> - [`event-driven-execution-plan.md`](./event-driven-execution-plan.md) — phased migration plan
> - [`context-validator-resolver-migration.md`](./context-validator-resolver-migration.md) — how the three subsystems change across phases
> - [`polyglot-engine-decision.md`](./polyglot-engine-decision.md) — Go engine decision
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. The Question

The specific proposal under review:

> Should we store the run context in the DB, also using the `file_ref` pattern like today, but validate at run time — meaning when a node starts running, we pass the data to another process that fetches the information, validates it, and returns the result? Is this the right approach? Any alternatives? Is there a good way to do this from Go or Python?

This bundles four concerns that are cleaner to separate:

1. **Persistence model** — where does run state live (in-memory, DB, event log)?
2. **File-backed storage** — do we keep today's `file_ref` pattern for large values?
3. **Validation placement** — is validation inline, in a separate process, or in a worker?
4. **Language choice** — does Go or Python matter for validation?

Answering each in turn.

---

## 2. TL;DR

- **Persistence:** Yes — run state becomes a DB-backed **materialized view** derived from a persistent **event log**. This is exactly what the execution plan assumes.
- **File-backed storage:** Yes — keep the `file_ref` pattern. `DataStoreService.store_data()` + `extracted` scalar cache + `FileBackedResolverStrategy` all survive the migration. Only the call site moves.
- **Validation placement:** **No separate process on the hot path.** Do resolution + validation + execution as a single **async pipeline inside the Executor/Worker**. After Phase 0 of the execution plan, the blocking-I/O concern that would motivate a separate process disappears.
- **The one thing that IS shared/extracted:** the **schema registry**, which already exists today as `INodeDefinitionRepository` + `NodeDefinitionResolver`. That's a registry (read service + cache), not a validator.
- **Language choice:** both Python and Go can do JSON Schema validation in microseconds. Not a bottleneck. Use the language of whatever process hosts the pipeline.

The rest of this document explains why.

---

## 3. Decomposing "Validation" Into Three Distinct Things

Before choosing a location, separate the things that the word "validation" covers. They have very different costs and placement rules.

| Step | What it does | Cost | Blocking? |
|---|---|---|---|
| **3.1 Expression resolution** | Walks `{{$node.path}}` expressions, pulls scalar values from `run.context.variables` snapshot or file-backed references | Fast (µs) on scalar cache hit, slow (ms-sec) if a file-service materialize is needed | I/O-bound (file-service HTTP) |
| **3.2 Materialization** | Fetches file-backed data (full CSV download, OData single-row fetch) if scalar cache misses | Slow (10 ms - 60 sec) per HTTP call | I/O-bound |
| **3.3 Input schema validation** | Checks resolved payload against `NodeDefinition.input_schema` — required fields, types, formats | Very fast (1-10 µs) for typical schemas | CPU-only, sub-millisecond |
| **3.4 Handler execution** | Runs the actual node business logic | Variable (ms - minutes) | Whatever the handler does |
| **3.5 Output schema validation** | Checks handler's return value against `NodeDefinition.output_schema` | Very fast (1-10 µs) | CPU-only |
| **3.6 File-ref conversion** | Converts large output dict/list into a `file_ref` via `DataStoreService.store_data()` | Slow (10 ms - sec) per upload | I/O-bound (file-service HTTP) |

Steps 3.1, 3.2, 3.6 are **I/O-bound**. They touch the file-service.
Steps 3.3, 3.5 are **pure CPU** and take microseconds.
Step 3.4 is whatever the handler is.

The reason the user is asking about "separate process" is almost certainly because of steps 3.1/3.2/3.6 — the I/O-bound steps that can block. **Steps 3.3 and 3.5 on their own do not justify a separate process.**

This distinction matters because the right answer depends on what you're actually trying to isolate.

---

## 4. The Proposal — "Validation in Another Process"

Reconstructing what the proposal would look like architecturally:

```
Executor (Python asyncio)                Validator Service (Go or Python)
   │                                          │
   │  node.input event arrives                │
   │                                          │
   ├── gRPC/HTTP ──────────────────────────▶  │
   │   validate(node_input, run_id)           │
   │                                          ├── Fetch schema from registry
   │                                          ├── Fetch file_refs from Projector
   │                                          ├── Materialize file data
   │                                          ├── Resolve expressions
   │                                          ├── Validate input schema
   │                                          │
   │  ◀─────────────────────────── gRPC/HTTP ─┤
   │   { resolved_input, ok: true }           │
   │                                          │
   │  Call handler (Python)                   │
   │                                          │
   │  Handler returns output                  │
   │                                          │
   ├── gRPC/HTTP ──────────────────────────▶  │
   │   validate_output(payload, schema)       │
   │                                          ├── Validate output schema
   │  ◀─────────────────────────── gRPC/HTTP ─┤
   │   { ok: true }                           │
   │                                          │
   │  Emit node.output                        │
```

### 4.1 What this buys you

| Benefit | Reality check |
|---|---|
| Blocking I/O isolation | Post-Phase-0 (async httpx), the Executor already handles I/O without blocking. This benefit disappears. |
| Language flexibility (Go validator, Python handlers) | Real, but solvable with a shared library instead of a service. |
| Central enforcement point | Real, but a schema registry gives you this with less overhead. |
| Scale validators independently | Real only if validation is the bottleneck — it won't be (CPU validation is µs). |
| Security sandboxing | Real for untrusted schemas; irrelevant for trusted first-party schemas. |
| Consistent validation across polyglot workers | Real — but consistency comes from the **schema format**, not the process. |

### 4.2 What this costs you

| Cost | Magnitude |
|---|---|
| Extra network round-trip per node | 0.5-5 ms per call × 2 calls (input + output) = 1-10 ms per node. For a 50-node run, 50-500 ms added latency. |
| Serialization + deserialization | Payload round-trip costs on every validation. For large payloads (file-backed or not), this is real. |
| State coordination | The validator needs access to: schema registry, file-service, Projector state store. Three new dependencies on a hot path. |
| Deployment complexity | Another Kubernetes deployment, SLO, on-call rotation, monitoring. |
| Cross-service tracing | Every call needs trace propagation; debugging a validation failure now spans two services. |
| Single-point-of-failure risk | Validator down = every run stalls. Unless you co-deploy it as a sidecar, which eliminates the scaling benefit. |

### 4.3 Honest assessment

For the specific concerns that probably motivated the proposal — blocking I/O and multi-language support — a separate validation service is the wrong tool. Blocking I/O is solved by async (Phase 0). Multi-language support is solved by a schema registry + per-language validation library.

The only scenario where a separate process is justified is **untrusted schemas in a multi-tenant environment** (e.g., letting customers upload arbitrary JSON Schemas that might contain regex-based DoS attacks or expensive recursive references). Even then, you'd sandbox it at the schema *compilation* step, not at every validation call.

**Recommendation: do not build a separate validation service. Build an in-process pipeline instead.**

---

## 5. The Recommended Pipeline — In-Process Inside the Executor/Worker

The alternative is a single async coroutine that runs through all six steps from §3 in sequence, inside whichever process handles the node event (Executor today, Worker for dispatched TASK nodes).

```
async def handle_node_input(event: NodeInputEvent) -> list[DomainEvent]:
    # 1. Load node descriptor from cache (schema, policy, mapping, ports)
    node_def = await node_def_cache.get(event.workflow_id, event.node_id)

    # 2. Build resolution context from the Projector's snapshot
    context = await build_resolution_context(event.run_id, event.payload)

    # 3. Resolve expressions in input_mapping — may materialize file_refs
    try:
        resolved_input = await resolver.resolve_mapping(
            node_def.input_mapping,
            context,
        )
    except VariableResolutionError as e:
        return apply_policy(node_def.policy.on_validation_error, e)

    # 4. Validate resolved input against input_schema
    errors = schema_validator.validate(resolved_input, node_def.input_schema)
    if errors:
        return apply_policy(node_def.policy.on_validation_error, errors)

    # 5. Emit node.started
    yield NodeStarted(run_id=event.run_id, node_id=event.node_id, ...)

    # 6. Call the actual handler
    try:
        output = await handler(resolved_input, ctx)
    except TimeoutError:
        return apply_policy(node_def.policy.on_timeout, ...)
    except Exception as e:
        return apply_policy(node_def.policy.on_exec_error, e)

    # 7. Validate output
    errors = schema_validator.validate(output, node_def.output_schema)
    if errors:
        return apply_policy(node_def.policy.on_output_validation_error, errors)

    # 8. Convert large output values to file_refs (preserves today's pattern)
    stored_output = await data_store.store_large_values(output)

    # 9. Emit node.output
    yield NodeOutput(run_id=event.run_id, node_id=event.node_id,
                     port="main", payload=stored_output)
```

Every step is async. The Executor can handle thousands of these coroutines concurrently. No event-loop stalls (because Phase 0 makes everything async). No cross-process overhead. Full pipeline in one traceable call path.

### 5.1 Why this is better than the separate-process proposal

- **Latency:** zero network hops. The full pipeline is in-process. Only the file-service calls inside step 3 add network latency, and those are already needed regardless.
- **Simplicity:** no new service to build, deploy, or monitor.
- **Tracing:** one OpenTelemetry span wraps the whole pipeline. Debugging a validation failure is trivial — it's in the same call stack as the handler.
- **Backpressure:** if the Executor is overloaded, events queue naturally on the bus. No second queue between Executor and validator.
- **Same language as handlers:** the pipeline runs in the same process as the node handler, so any shared state (logger, tracer, metrics client) is already set up.

### 5.2 What stays shared (and should stay shared)

Even without a separate validation **process**, several pieces are genuinely shared across runs, nodes, and languages. Extract them as services or caches, not as validators:

| Shared component | What it does | How it's served |
|---|---|---|
| **Schema registry** | Stores `NodeDefinition` entities with their `input_schema`, `output_schema`, `input_mapping`, `output_mapping`, `locked_defaults`, `ports`. Already exists as `INodeDefinitionRepository`. | Read-only gRPC or HTTP service, aggressively cached per-replica. Cache TTL keyed on `updated_at`. |
| **File-service** | Stores file-backed CSV data. Already exists. | Unchanged — HTTP service. |
| **Projector state store** | Holds the materialized run state (`RunContextView`). | Redis or Postgres. Read by the Executor when building the resolution context. |
| **Validation library** | JSON Schema / field-list validator. | A library (`pydantic` in Python, `santhosh-tekuri/jsonschema` in Go), imported directly. |

The schema registry is the only thing that's plausibly a service. Everything else is either a cache or a library.

---

## 6. Persistence Model — DB + Event Log + File-Refs

Answering the first part of the question: **yes, run state lives in the DB, using the same `file_ref` pattern as today, but the shape changes slightly.**

### 6.1 What's stored where

```
┌───────────────────────────────┐   append-only
│   Event Log                   │   ← source of truth
│   (Kafka topic / SQL table)   │   events: run.start, node.input,
│   partitioned by run_id       │           node.output, node.failed, ...
└──────────────┬────────────────┘
               │
               │  Projector consumes
               ▼
┌───────────────────────────────┐
│   Materialized Run State      │   ← derived view
│   (Postgres / HANA / Redis)   │   RunContextView:
│   key: run_id                 │     status, variables, completed_nodes,
└──────────────┬────────────────┘     skipped_nodes, child_runs
               │
               │  variables[node_id] holds either a scalar or a file_ref
               ▼
┌───────────────────────────────┐
│   File Service                │   ← large payloads
│   (existing, unchanged)       │   CSV files keyed by file_id
└───────────────────────────────┘
```

Three storage tiers:

1. **Event log** — the ordered, replayable truth. Every state transition is an event here.
2. **Materialized view** — a projection of the log for fast reads. This is what the 15+ feature use cases read through the `RunContextView` shim.
3. **File service** — unchanged. Stores large dict/list payloads as CSVs, referenced by `file_id`.

The `file_ref` pattern inside `variables` is identical to today. Scalars live inline in the materialized view row; dicts/lists live in the file service and are referenced by `{"__file_ref__": true, "file_id": "..."}`. The `extracted` scalar cache on each file_ref also stays, so downstream scalar reads remain zero-I/O.

### 6.2 When does the file_ref conversion happen?

Today: `successor_processor.py:208-213` converts on the way into `run.context.variables` after a handler returns.

Tomorrow: **in the Executor, in step 8 of the pipeline**, before emitting the `node.output` event. The event carries file_refs in its payload (not raw CSV bytes). Event log stays compact and replayable. Projector applies the event by writing the file_ref into the materialized view; no re-upload.

### 6.3 Why not just put everything in the event log?

You could. Events could carry raw payloads and you could skip the file service entirely. Don't:

- Large payloads in events bloat the event log. Kafka messages over ~1 MB are painful.
- Replay becomes expensive — recomputing a view means re-streaming multi-megabyte payloads.
- The current `extracted` scalar cache requires a two-tier structure (metadata + optional fetch); events can't serve both tiers without duplication.

Keep the file-ref pattern.

---

## 7. Schema Registry — The One Thing That MAY Be a Service

This is the only part of the proposal that's worth pursuing as a standalone component — and it already exists.

### 7.1 What's already there

- `app/layer1_domain/entities/node_definition.py` — the `NodeDefinition` entity with:
  - `input_schema: list[dict]` — field-list schema (key, type, rules.isRequired, etc.)
  - `output_schema: list[dict]` — same shape for outputs
  - `input_mapping: dict` — declarative binding from upstream data to this node's input
  - `output_mapping: dict` — declarative binding for output shaping
  - `locked_defaults: dict` — defaults that cannot be overridden
  - `ports: dict` — in/out port declarations
  - `parent_definition_id` — inheritance chain for templating
  - `tenant_id` — multi-tenant scoping
- `app/layer1_domain/services/node_definition_resolver.py` — `NodeDefinitionResolver` walks the inheritance chain from leaf to root and merges into a single `ResolvedNodeDefinition`. Max depth 10, cycle detection.
- `INodeDefinitionRepository` — persistence interface, memory + HANA backends today.
- Feature use cases: `create_node_definition`, `update_node_definition`, `delete_node_definition`, `get_node_definition`, `list_node_definitions`, `test_node_definition`, `list_node_palette`.

This is a fully functional schema registry. The only thing missing for the event-driven model is:

1. **Aggressive caching** at every Executor/Worker replica. `NodeDefinition` rarely changes; cache by `(id, updated_at)` with long TTL and invalidate on update.
2. **Versioning** — a `version` field on `NodeDefinition` so running workflows pin the version they started with, immune to edits.
3. **Projection for the editor UI** — already served by existing features.

### 7.2 Should the registry be a separate network service?

**In monolith mode (today):** no. It's a repository in the same process. Fast.

**In the event-driven split (Phase 4+):** the registry can be its own read-only gRPC or HTTP service if the Dispatcher, Executor, and Projector run as separate processes. All three need to read node definitions. A dedicated service with aggressive caching at every client is a reasonable factoring.

**Scope the registry:** it holds schemas, mappings, and metadata. It does not run validation. It does not call the file service. Think of it as a DNS-equivalent for node definitions.

### 7.3 Should node definitions go through the event log?

No. Node definitions are **configuration**, not **run state**. They belong in a CRUD database, not an append-only log. Changes to definitions can emit `catalog.version_published` events so caches invalidate, but the storage is a regular repository.

---

## 8. Python Pipeline Sketch

Illustrating the recommended in-process pipeline in Python. Not complete code — just shape.

```python
# app/layer1_domain/services/executor/node_pipeline.py

from typing import AsyncIterator
import pydantic_core

from app.layer1_domain.events import NodeInput, NodeStarted, NodeOutput, NodeFailed
from app.layer1_domain.entities.node_definition import NodeDefinition

class NodePipeline:
    def __init__(
        self,
        registry: NodeDefinitionRegistryClient,
        resolver: VariableResolver,              # async, Phase 0
        data_store: DataStoreService,             # async, Phase 0
        handler_registry: HandlerRegistry,
        projector_client: ProjectorClient,
    ) -> None:
        self._registry = registry
        self._resolver = resolver
        self._data_store = data_store
        self._handlers = handler_registry
        self._projector = projector_client

    async def handle(self, event: NodeInput) -> AsyncIterator[DomainEvent]:
        # 1. Load node descriptor (cached in client)
        node_def = await self._registry.get(event.workflow_id, event.node_id)
        policy = node_def.policy

        # 2. Build resolution context snapshot from Projector
        variables_snapshot = await self._projector.read_variables(event.run_id)
        context = {**variables_snapshot, "$input": event.payload, "$trigger": ...}

        # 3. Resolve expressions (async — may materialize file_refs)
        try:
            resolved_input = await self._resolver.resolve_mapping(
                node_def.input_mapping, context, node_def.input_type_map,
            )
        except VariableResolutionError as e:
            async for ev in self._apply_policy(policy.on_validation_error, e, event):
                yield ev
            return

        # 4. Validate input (pydantic-core, microseconds)
        errors = self._validate(resolved_input, node_def.input_schema)
        if errors:
            async for ev in self._apply_policy(policy.on_validation_error, errors, event):
                yield ev
            return

        # 5. Emit started
        yield NodeStarted(run_id=event.run_id, node_id=event.node_id, attempt=event.attempt)

        # 6. Run handler
        try:
            output = await asyncio.wait_for(
                self._handlers.run(node_def.base_type, resolved_input),
                timeout=policy.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            async for ev in self._apply_policy(policy.on_timeout, "timeout", event):
                yield ev
            return
        except Exception as e:
            async for ev in self._apply_policy(policy.on_exec_error, e, event):
                yield ev
            return

        # 7. Validate output
        errors = self._validate(output, node_def.output_schema)
        if errors:
            async for ev in self._apply_policy(policy.on_output_validation_error, errors, event):
                yield ev
            return

        # 8. Convert large values to file_refs (preserves today's pattern)
        stored_output = await self._data_store.store_large_values(event.node_id, output)

        # 9. Emit output
        yield NodeOutput(
            run_id=event.run_id,
            node_id=event.node_id,
            port="main",
            payload=stored_output,
        )

    def _validate(self, payload, schema) -> list[str]:
        # pydantic-core (Rust-backed) for full JSON Schema
        # or SchemaValidator.validate_input for the current field-list format
        return SchemaValidator.validate_input(payload, schema)
```

**Validation library choices for Python:**

- **`pydantic-core` (recommended)** — Rust-backed validation via `pydantic` v2. Extremely fast (1-10 µs for typical schemas). First-class async. The best choice for new code.
- **`jsonschema`** — pure Python, reference implementation. Slower (100 µs - 1 ms per validation) but standard-compliant.
- **`fastjsonschema`** — compiles schemas to Python code on first use. Fast after warmup.
- **`SchemaValidator` (the current 22-line implementation)** — only checks required fields. Keep for backward compatibility with existing `NodeDefinition.input_schema` format, but extend when adding type/format checks.

### 8.1 Migration path for the current field-list schema format

Today's `input_schema` is a `list[dict]` with fields like `{"key": "user_id", "rules": {"isRequired": True}}`. It is not standard JSON Schema. For Phase 5, two options:

1. **Extend the current format** in place: add `type`, `format`, `minimum`, `maximum`, `pattern` fields. Write a new validator that understands the extended format. Keep backward compatibility.
2. **Add an optional JSON Schema field**: `json_schema: dict | None` on `NodeDefinition`. Migrate new definitions to use JSON Schema; old definitions continue using the field list.

Option 2 is less disruptive. The validator tries `json_schema` first, falls back to the field list.

---

## 9. Go Pipeline Sketch

If the Dispatcher is extracted to Go in Phase 4.5 (per the polyglot decision), the same pipeline shape applies. Validation there would use Go-native libraries:

```go
// Sketch — the equivalent in Go

type NodePipeline struct {
    registry     NodeDefinitionRegistryClient
    resolver     VariableResolver
    dataStore    DataStoreService
    handlers     HandlerRegistry
    projector    ProjectorClient
}

func (p *NodePipeline) Handle(ctx context.Context, ev NodeInput) ([]DomainEvent, error) {
    // 1. Load node descriptor
    nodeDef, err := p.registry.Get(ctx, ev.WorkflowID, ev.NodeID)
    if err != nil {
        return nil, err
    }

    // 2. Build resolution context snapshot
    vars, err := p.projector.ReadVariables(ctx, ev.RunID)
    if err != nil {
        return nil, err
    }
    resContext := buildContext(vars, ev.Payload)

    // 3. Resolve expressions (concurrent safe via goroutines for file_refs)
    resolvedInput, err := p.resolver.ResolveMapping(ctx, nodeDef.InputMapping, resContext)
    if err != nil {
        return p.applyPolicy(nodeDef.Policy.OnValidationError, err, ev), nil
    }

    // 4. Validate input
    if errs := p.validate(resolvedInput, nodeDef.InputSchema); len(errs) > 0 {
        return p.applyPolicy(nodeDef.Policy.OnValidationError, errs, ev), nil
    }

    // 5-9. Handler + output validation + file-ref conversion + emit
    // ...
}

func (p *NodePipeline) validate(payload interface{}, schema *jsonschema.Schema) []error {
    // github.com/santhosh-tekuri/jsonschema/v5 — fast, spec-compliant
    if err := schema.Validate(payload); err != nil {
        return []error{err}
    }
    return nil
}
```

**Validation library choices for Go:**

- **`github.com/santhosh-tekuri/jsonschema/v5` (recommended)** — spec-compliant, fast, low allocation, supports JSON Schema draft 2020-12.
- **`github.com/xeipuuv/gojsonschema`** — older, widely used, slightly slower.
- **`github.com/kaptinlin/jsonschema`** — newer, good ergonomics.

Per-event validation cost is sub-microsecond to a few microseconds either way — not a decision point.

### 9.1 Polyglot consistency — how to keep validation identical

If some workers are Python and others are Go, validation consistency matters: a payload that passes in one language must pass in the other. Two approaches:

1. **Standard JSON Schema** — both languages use spec-compliant libraries. Behavior is identical by construction. Use this if possible.
2. **Shared schema format + cross-language test suite** — the current field-list format is bespoke; to keep Python and Go consistent, write a conformance test suite that runs both validators against the same fixtures.

Option 1 is cheaper. If/when JSON Schema replaces the field-list format, polyglot validation becomes free.

---

## 10. When a Separate Validation Process IS Justified

To be fair: there are scenarios where a separate process is the right call. They do not apply to this product today, but for completeness:

| Scenario | Why |
|---|---|
| **Untrusted schemas** | Customers upload arbitrary JSON Schemas. Regex-based DoS (e.g., `^(a+)+$`) or expensive recursive refs can stall the Executor. Compile and run the validator in a sandboxed subprocess with a CPU budget. |
| **GPL'd validator library** | Legal isolation from the main codebase via an IPC boundary. Extreme edge case. |
| **Hardware acceleration** | Running validation on a FPGA or TPU accelerator — separate service that owns the hardware. Not relevant here. |
| **Central audit logging** | Every validation must be recorded in an immutable audit log managed by a separate team. Solvable with tracing + events instead of a service. |
| **Hard multi-tenant isolation** | Validating one tenant's schema must not be able to affect another tenant. Subprocess or container sandbox. |

None of these apply to this codebase. The validators are first-party (controlled by the same team that owns the engine), the schemas are curated (not user-uploaded arbitrary JSON Schemas), and CPU cost is negligible.

**Do not build a validation service.**

---

## 11. Mapping to Execution Plan Phases

Putting all of this into the existing Phase 0-5 sequence:

| Phase | Validation/persistence work |
|---|---|
| **0 — Async httpx fix** | Resolver + DataStoreService + file-service client become async. Phase 0 *eliminates* the blocking-I/O motivation for a separate validation process. |
| **1 — Run-scoped cache** | File-backed materialize cache becomes run-scoped. Fewer duplicate downloads during validation. |
| **2 — Handler classification + metrics** | Add `schema_validation_duration_seconds{node_type, schema_format}` metric. Baseline before deciding on JSON Schema migration. |
| **3 — LOOP design spike** | No direct impact on validation. |
| **4 — Dispatcher + Projector split** | **Materialized view introduced.** `RunContextView` shim. Event log persistence. File-ref pattern relocates to the Executor's pre-emit step. NodeDefinitionRegistryClient with aggressive per-replica caching. Node definitions pinned to workflow version on run start. |
| **5 — Event-driven BUILTIN** | **NodePipeline introduced.** The async pipeline sketched in §8/§9 becomes the Executor's standard path. SchemaValidator promoted from "required-field only" to "full schema validation" with per-node policy. `on_validation_error` / `on_exec_error` / `on_output_validation_error` policies wired up. Optional: add a `json_schema` field on `NodeDefinition` and migrate new node types to JSON Schema. |

### 11.1 What does NOT happen

- No validation microservice is built at any phase.
- No cross-process RPC on the validation hot path.
- The file_ref pattern is preserved end-to-end. `DataStoreService` is not replaced.
- The `NodeDefinition` entity is not rewritten — just extended with new fields (`policy`, optionally `json_schema`, optionally `version`).

---

## 12. Answers to the Original Questions

**Q: Should we store run context in the DB?**
**A: Yes, as a materialized view.** The event log is the source of truth; the Projector derives the materialized view and writes it to Postgres/HANA/Redis. Features read through a `RunContextView` shim. See §6 and the context-validator-resolver migration doc.

**Q: Should we use the `file_ref` pattern like today?**
**A: Yes, unchanged in shape.** Scalars inline, large dicts/lists as file-refs pointing to the file-service. The `extracted` scalar cache stays. Only the call site for conversion moves (from `successor_processor` to the Executor's pre-emit step).

**Q: Should validation happen in a separate process when a node runs?**
**A: No.** Do it as an in-process async pipeline inside the Executor/Worker. §4 explains why the separate-process proposal is costly and unnecessary. §5 shows the recommended pipeline.

**Q: Is there a better way?**
**A: Yes — the NodePipeline pattern in §5.** Resolution + validation + execution + output validation + file-ref conversion as a single async coroutine. Share the `NodeDefinition` registry across replicas via a cached client (already exists as `INodeDefinitionRepository` + `NodeDefinitionResolver`).

**Q: Is there a good way for Python or Go?**
**A: Yes — both.**
- **Python:** `pydantic-core` (Rust-backed, ~1-10 µs) for new code. `SchemaValidator` (current) for backward compat with the field-list format. `asyncio` throughout.
- **Go:** `github.com/santhosh-tekuri/jsonschema/v5` for spec-compliant validation. `goroutines` for concurrent pipeline steps.
- **Polyglot consistency:** standardize on JSON Schema. Field-list format stays for legacy; new node definitions use JSON Schema.

---

## 13. Open Questions

- **Schema format migration.** Today's `input_schema: list[dict]` is a field list. Migrating to JSON Schema gives polyglot consistency and better tooling but requires a migration for existing `NodeDefinition` rows. Scope of that migration — Phase 5 or post-launch?
- **Where does `NodeDefinition.policy` live?** Adding per-node failure policy (`on_validation_error`, `on_exec_error`, ...) means extending the `NodeDefinition` entity. Should policy be per-definition (the template) or per-node-instance (the workflow's placement)? Probably both: defaults at the definition, overrides at the instance.
- **Projector read latency on the validation hot path.** Step 2 of the pipeline reads `variables_snapshot` from the Projector. If the Projector is a separate service, this is a network call per node. Options: (a) cache reads in the Executor per run, (b) co-locate Projector as a sidecar, (c) batch reads. Needs measurement.
- **Output file-ref conversion blocking.** Step 8 uploads large output to the file-service. If the upload is slow, does the Executor wait? Options: (a) fire-and-forget to an outbox and emit `node.output` with the file_ref optimistically (risks inconsistency if upload fails); (b) wait synchronously (simpler, adds latency); (c) emit `node.output` with an inline reference, let the Projector relocate later. Option (b) is simplest and correct; revisit if latency pressure demands it.
- **Handler timeout policy interaction.** If the timeout fires during step 6 but before step 7, what state is the output in? The handler coroutine is cancelled; any partially-computed output is discarded; the node is marked failed per `on_timeout` policy. Needs explicit documentation in the policy spec.

---

## 14. Summary

The persistence and validation design is straightforward once the concerns are separated:

| Concern | Answer |
|---|---|
| Where does run state live? | Event log (truth) + materialized view in DB (derived) + file-refs in file-service (large values). |
| Do we keep the `file_ref` pattern? | Yes, unchanged in shape. Only the call site moves. |
| Where does validation run? | In-process inside the Executor/Worker, as an async pipeline. |
| Should there be a separate validator service? | No. Phase 0 eliminates the blocking motivation. Cross-process overhead costs more than it saves. |
| What IS shared across replicas? | The `NodeDefinition` schema registry (already exists as `INodeDefinitionRepository` + `NodeDefinitionResolver`), cached aggressively. |
| Python or Go for validation? | Either. Both languages have µs-level JSON Schema validators. Use the language of whichever process hosts the pipeline. |
| How do polyglot workers stay consistent? | Standardize on JSON Schema; don't rely on a bespoke shared service. |

The single insight that collapses the proposal: **"pass to another process for validation"** bundles three things (expression resolution, schema check, file materialization) whose costs and placements are very different. Once you decompose, only the schema check is pure CPU — and it's the cheapest step. The I/O-heavy steps are already solved by Phase 0's async conversion. There's no remaining work that a separate process could do faster than an in-process async coroutine.

Build the `NodePipeline` in Phase 5. Keep the `NodeDefinition` registry. Preserve the `file_ref` pattern. Do not build a validation service.
