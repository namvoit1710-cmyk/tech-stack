# Polyglot Engine Decision — Go Engine + Python Handlers

> Deep-dive on whether `workflow-control-plane-service` should be split into a Go engine with Python node handlers, or remain single-stack Python with selective optimization. Builds on the event-driven execution plan and the gap analysis.
>
> Companion documents:
> - [`event-driven-orchestration.md`](./event-driven-orchestration.md) — target architecture
> - [`event-driven-gap-analysis.md`](./event-driven-gap-analysis.md) — current vs. target gap
> - [`event-driven-execution-plan.md`](./event-driven-execution-plan.md) — phased migration plan
>
> All file paths are relative to `apps/backend/ai-workflow-management/workflow/workflow-control-plane-service/`.

---

## 1. TL;DR

**Do not rewrite the engine in Go now.** Stay Python through Phase 4 of the execution plan. After Phase 4 ships and metrics are collected, **extract only the Dispatcher to Go** if the data justifies it. Everything else — Projector, feature layer, handlers, persistence, realtime — remains Python forever.

Rationale in one paragraph: the current bottleneck is not Python, it is a sync-HTTP-client bug (Phase 0 fixes it). The event-driven migration (Phases 1-5) is already a massive undertaking; doing it while also changing languages multiplies risk and cost without proportional reward. The Dispatcher is the only service in the target architecture whose workload (CPU-hot, pure functional, partition-parallel) actually favors Go over Python. Extract **only that one** if metrics ever demand it, and do it as a gated follow-up to Phase 4, not as a precondition.

---

## 2. What "Split into Go Engine + Python Handlers" Actually Means

The proposal is to split the target architecture along two language boundaries:

| Service | Responsibility | Language choice |
|---|---|---|
| Trigger Layer | Webhook, cron, manual, event-bridge → emit `run.start` | Go |
| **Dispatcher** | Subscribe to `node.output`, evaluate wires, fan out `node.input` | Go |
| Executor *router* | Receive `node.input`, look up handler, invoke it | Go |
| Node handlers | CONDITION, SWITCH, LOOP, PARALLEL, MERGE, TRIGGER, WORKFLOW, COMPUTE, TASK, HITL | **Python** |
| Projector | Consume event log, materialize `RunState` | Go |
| Realtime Gateway | Socket.IO fan-out | Go or Python |
| Persistence adapters | SAP HANA, SQLite, Postgres, Redis, Kafka/Event Mesh | Go |
| Feature layer / REST | 51 use cases (`start_run`, `validate_workflow`, `get_run_context`, …) | Go |

The boundary between Go and Python becomes the **event bus + a node-handler invocation protocol** (gRPC, HTTP, or a sidecar IPC). Python workers would receive `node.input` events or synchronous RPC calls, run the handler, and return `node.output` via the bus or response.

This matches how **Temporal** is built: Go core, polyglot SDKs. It would also match the existing `worker-executor-service` + `worker-sdk` pattern in this repo, which already supports polyglot workers — only the control plane would change.

---

## 3. What Would Have to Be Rewritten

Laying out the rewrite scope honestly, because this is where the decision actually gets made. Current Python components that would need a Go port:

### 3.1 Engine core (highest effort)

- `app/layer1_domain/services/orchestration_engine.py` — ~1,600 LOC, the heart of the system. Already being rewritten by the execution plan; Go adds a language port on top.
- `app/layer1_domain/services/successor_processor.py` — post-completion DAG advance logic.
- `app/layer1_domain/services/dag_traversal.py` — merge-safe skip propagation.
- `app/layer1_domain/services/dag_validator.py` — DAG validation (demoted to linter in Phase 5).
- `app/layer1_domain/services/variable_resolver.py` + resolver strategies — lazy expression resolution with `FileBackedResolverStrategy`, `MetadataResolverStrategy`, `TraversalResolverStrategy`.
- `app/layer1_domain/services/condition_evaluator.py` — boolean expression evaluator.
- `app/layer1_domain/services/task_factory.py` — task construction.
- `app/layer1_domain/services/event_collector.py` — priority-lane buffering with contextvars.

Each of these has no direct Go equivalent library; they are bespoke code that must be re-implemented and re-tested for behavior parity.

### 3.2 Messaging and realtime (high effort, high subtlety)

- `app/layer4_frameworks/messaging/` — Console, Kafka, SAP Event Mesh publishers. Kafka and Event Mesh are the non-trivial ones. Event Mesh has AMQP-over-HTTP with OAuth tokens and a circuit breaker; there is no off-the-shelf Go library.
- `app/layer4_frameworks/realtime/broadcasting_publisher.py` — decorator that fans out to Socket.IO rooms.
- `app/layer4_frameworks/realtime/socketio_consumer.py` — **3-queue backpressure system** (critical/normal/bulk with independent capacities and drop policies). This is a significant asset; rewriting it in Go requires careful semantics preservation.
- Socket.IO server binding. Python has `python-socketio`; Go has less mature Socket.IO libraries. Options: keep the realtime gateway in Python as a separate process, or migrate the frontend to raw WebSocket/SSE and use Go's first-class `gorilla/websocket`.

### 3.3 Persistence (medium effort)

- `app/layer4_frameworks/persistence/` adapters for memory / SQLite / HANA. Memory and SQLite are easy. **HANA is the hard one** — it uses `pyodbc` via `sqlalchemy-hana` today. Go has `github.com/SAP/go-hdb`, which is workable but would require re-implementing every repository (`WorkflowRepository`, `RunRepository`, `TaskRepository`, `NodeDefinitionRepository`, `WebhookRepository`, `StageHistoryRepository`) against the Go driver.
- All ORM models in `app/layer4_frameworks/persistence/orm/entities/` would need Go equivalents.
- VCAP_SERVICES credential loading for SAP deployments.

### 3.4 Feature / application layer (huge effort)

- 51 auto-discovered use cases under `app/layer2_application/features/`. Each is an independent feature with its own controller, DTOs, validation, and tests:
  `add_node_artifact, cancel_run, clear_all_data, complete_task, create_node_definition, create_webhook, create_workflow, delete_*, dry_run_workflow, edit_node_input, fail_task, get_* (13 features), list_* (6 features), pause_run, rerun_task, reset_test_run, resume_*, set_main_flow, start_run, store_output, sweep_timed_out_child_runs, test_full_workflow, test_node, test_node_definition, update_*, validate_workflow`
- `bootstrap.py` uses Python's signature introspection (`inspect.signature`) to auto-wire dependencies. Go has no equivalent; the Go port would need explicit wiring or a generated DI framework (`wire`, `fx`) — thousands of lines of boilerplate.

### 3.5 REST controllers and DTOs

- `app/layer3_adapters/controllers/restful/v1/` — ~10 controllers (run, workflow, node_definition, expression, file, artifact, output, socketio, webhook, health).
- Pydantic DTOs for request/response. Go rewrite would use `encoding/json` + struct tags + handwritten validation or a library like `go-playground/validator`. Pydantic's ergonomics are notably better.
- gRPC server (conditionally created) would port more cleanly; Go has first-class gRPC.

### 3.6 Tests

- Unit tests (`tests/unit/`): ~15 files covering DAG, resolver, handlers, CSV, broadcasting, circuit breaker, etc.
- Integration tests (`tests/integration/`): ~4 files covering API, node execution, data factory.
- All must be ported or re-written. Go test ergonomics are different enough that straight translation is often not the right approach.

### 3.7 Rough effort estimate

Not a promise, but an honest order-of-magnitude:

| Component | Effort |
|---|---|
| Engine core (orchestration, dag, resolver, handlers infrastructure) | 3-5 months |
| Messaging + realtime (Kafka + Event Mesh + Socket.IO parity) | 1.5-2 months |
| Persistence (memory + SQLite + HANA + repositories) | 1-1.5 months |
| 51 feature use cases + controllers + DTOs | 2-3 months |
| Bootstrap / DI rewrite | 0.5 month |
| Test suite migration | 1-2 months |
| Bug parity (finding subtle behavior differences) | 1-2 months |
| Integration with existing services | 0.5-1 month |

**Total: ~10-17 engineer-months**, assuming 2-3 engineers working in parallel and familiar with Go. Add ramp-up time if the team is Python-strong.

During the rewrite, the Python service must continue running in production, meaning **dual maintenance** for any feature work that lands in the same window. This effectively doubles opportunity cost for the duration.

---

## 4. What Would Stay Python

Even in the most aggressive polyglot split, these stay Python:

- **Node handlers** (the 13 handler files). Python dominates the connector ecosystem — Pydantic v2 for schemas (Rust-backed, fast), `httpx.AsyncClient` for HTTP, `sqlalchemy` for DB, `openai` / `anthropic` for LLMs, every SaaS SDK. Porting handlers to Go would cost 3-5x more engineering per connector and lose access to the LLM ecosystem.
- **Workers** (`worker-executor-service`, `worker/*`). Already Python; no reason to touch.
- **Feature layer** (51 use cases). The use-case layer is thin business logic over the engine; if the engine goes Go, the use-case layer would follow only because it calls into the engine. But if a polyglot option kept the engine Go and the use cases Python via gRPC, the use cases could stay — at the cost of a cross-language hot path on every API request.

The cleaner boundary: **only rewrite code on the event loop's hot path**, which is the Dispatcher. Everything else stays.

---

## 5. Cost-Benefit Analysis

### 5.1 What Go gives you

- **Per-core throughput.** Dispatcher events/sec per core: Python ~10-20k, Go ~100-500k. Projector and Executor less so, since they are I/O-bound.
- **Goroutines for fan-out.** Natural fit for high fan-out dispatch; no GIL concerns.
- **Lower memory footprint per replica.** ~10-50 MB per Go process vs. 100-300 MB per Python process.
- **Faster startup.** Matters for autoscaling and Kubernetes pod churn.
- **Single static binary.** Simpler deployment, no virtualenv or dependency install.
- **Operational precedent.** Temporal, Argo Workflows, NATS, etcd, Kubernetes — every modern workflow/event system is Go.

### 5.2 What Go costs you

- **10-17 engineer-months of rewrite.**
- **Bug parity** — every subtle Python behavior must be reproduced. Examples from this codebase:
  - `EventCollector` priority-lane semantics via contextvars.
  - `RealtimeBroadcastingPublisher` decorator chain behavior.
  - Socket.IO 3-queue backpressure with exact capacity limits.
  - `VariableResolver` regex parsing quirks and lazy caching.
  - Merge-safe skip propagation in `dag_traversal.collect_downstream_nodes_to_skip`.
- **Connector velocity collapse** — if node handlers ever migrate to Go, adding a new integration becomes 3-5x slower. This is strategic damage for an n8n-shaped product.
- **Feature freeze** (or dual maintenance) during the rewrite.
- **Team retraining** if Python-strong.
- **Two stacks to operate** (Go control plane + Python handlers + Python workers). Monitoring, profiling, deployment tooling all need cross-stack support.
- **Cross-language boundary overhead** — every handler invocation from a Go Executor to a Python handler pays serialization + IPC cost per event. For workflows with 50-500 node invocations, this adds real latency.

### 5.3 The opportunity cost

Every engineer-week spent on a language port is a week not spent on:

- Building the event-driven migration (which is valuable on its own).
- Adding new node integrations (which is where product value lives).
- Operational improvements (observability, alerting, SLOs).
- Customer-facing features.

In the specific case of this repo, the event-driven migration (Phases 0-5 of the execution plan) is the project that needs to happen regardless. Adding a language port on top triples it.

---

## 6. When Go Is Actually Justified

Concrete thresholds where a Go extraction becomes worth it:

| Metric | Threshold for Go extraction |
|---|---|
| Sustained Dispatcher throughput needed | > 50k events/sec/replica |
| p99 dispatch latency budget | < 10 ms |
| Replica count to serve peak load | > 20 (cost dominated by replica count, not external systems) |
| Team has Go expertise | ≥ 2 engineers comfortable writing Go systems |
| Long-term platform direction | Move toward Go (not just one-off optimization) |
| Cost-per-tenant pressure | High (SaaS multi-tenant with thin margins) |

**If all six are true:** extract the Dispatcher to Go. It pays back.

**If fewer than four are true:** stay Python. The constant factor improvement does not justify the rewrite cost. Airflow, Prefect, Dagster all run Python in production at much larger scale than most self-hosted workflow tools ever reach.

### 6.1 Honest assessment for this specific product

Based on what is visible in the codebase:

- **Scale target:** not yet clear, but this is a self-hosted product with SAP HANA integration — enterprise single-tenant, not hyperscale SaaS. Dispatcher throughput rarely exceeds a few thousand events/sec in this class of deployment.
- **Latency budget:** users wait on human-in-the-loop nodes and long-running tasks. Dispatcher overhead is noise compared to handler latency.
- **Team:** Python-strong (judged by the existing codebase quality and test coverage). No Go code in the repo.
- **Product differentiator:** connectors and integrations (node handlers + features). Engine throughput is not the product.

**Provisional answer:** 0 of 6 thresholds are clearly met today. Go extraction cannot be justified on current data.

---

## 7. The Recommended Path — Gated Go Extraction

The risk-minimal approach is **"measure, then cut."**

### 7.1 Sequencing

Extend the execution plan with a new checkpoint between Phase 4 and Phase 5:

```
Phase 0  Async HTTP client fix              (Python)
Phase 1  Run-scoped materialization cache   (Python)
Phase 2  Handler classification + metrics    (Python)
Phase 3  LOOP design spike                   (research)
Phase 4  Dispatcher + Projector split        (Python)
Phase 4.5  ★ Metrics review + Go decision    (new)
Phase 5  Event-driven BUILTIN (Option C)     (Python, or Go if 4.5 says so)
```

### 7.2 Phase 4.5 — Metrics review

After Phase 4 ships and runs for 2-4 weeks under representative load:

1. Collect metrics from Phase 2 infrastructure:
   - `node_handler_duration_seconds{handler_shape, node_type}`
   - `variable_resolve_duration_seconds{strategy}`
   - Dispatcher CPU and events-per-second
   - Projector lag
   - Executor queue depth
2. Review against the thresholds in §6. Compare Dispatcher cost to the rest of the system.
3. If 4+ thresholds are clearly exceeded, proceed to Phase 4.6.
4. If not, close the decision and stay single-stack.

### 7.3 Phase 4.6 (conditional) — Dispatcher Go extraction

Only if Phase 4.5 says yes.

**Scope:** rewrite only `app/layer1_domain/services/dispatcher/` (the new module created in Phase 4). Everything else remains Python.

**Interface contract preserved:**
- Consumes `node.output` events from Kafka.
- Produces `node.input` events to Kafka.
- Reads wire table from the Workflow Catalog (via gRPC or cached refresh).
- Emits `wire.traversed`, `wire.skipped`, `wire.dead_letter` events.
- Deterministic event ids via `uuid5(source_event_id, wire_id)`.

**What this does NOT touch:**
- Python Projector, Executor, feature layer, handlers, persistence, realtime — all unchanged.
- No handler re-implementation in Go.
- No cross-language RPC on the handler path. Handlers remain Python.

**Migration approach:**
- Build Go Dispatcher alongside Python Dispatcher.
- Route a percentage of runs through the Go Dispatcher via Kafka consumer group routing.
- Compare behavior in real traffic.
- Gradually shift traffic; cut over fully; retire the Python Dispatcher.

**Effort estimate:** 1-2 engineer-months. Much smaller than a full rewrite because the Dispatcher has narrow, clearly defined inputs and outputs and no shared state with the rest of the engine.

### 7.4 Why extract only the Dispatcher

The Dispatcher is the best candidate because:

1. **Smallest service** — maybe 500-1,000 LOC of Go after Phase 4 factoring.
2. **Pure function** — `(event, wire_table) → [events]`. No persistent state to migrate.
3. **CPU-hot** — the only service in the target architecture where Python constant factors are likely to matter.
4. **Clean boundary** — consumes and produces events via the bus. Zero shared state with Projector or Executor.
5. **Goroutines fit naturally** — high fan-out of thousands of concurrent dispatch operations.
6. **No connector integrations** — Dispatchers do not call external APIs directly. The rewrite does not pay the "connector velocity" cost.

### 7.5 Why NOT extract the Projector

The Projector is read/write-heavy on Redis/Postgres. It is I/O-bound, not CPU-bound. Python with `asyncio` + `asyncpg` + `redis.asyncio` handles this workload with ease. The GIL never engages meaningfully because the loop is waiting on the DB most of the time. Go buys nothing here.

### 7.6 Why NOT extract the Executor

The Executor invokes handlers. Handlers are Python. Moving the Executor to Go means every handler call crosses a language boundary, adding serialization + IPC latency to every single node execution. This is exactly the cross-language boundary overhead noted in §5.2. Colocating the Executor with the handlers (Python) is strictly better.

### 7.7 Why NOT extract the Feature Layer / REST API

The feature layer is thin business logic orchestrating the engine. Rewriting it in Go means the 51 use cases need Go implementations and the Pydantic DTO ergonomics are lost. Zero performance upside (these are request-response endpoints that spend their time in DB queries, not computation).

---

## 8. Alternative Options Rejected

For completeness — these were considered and rejected.

### 8.1 Full Go rewrite

Rejected. 10-17 engineer-months; destroys existing Python investment; breaks connector velocity; doubles operational surface; no proportional throughput benefit at current scale. Only justifiable if all six thresholds in §6 are already exceeded and the team is Go-strong.

### 8.2 Rewrite in Rust

Rejected. Peak performance is Rust's advantage; the Dispatcher is not peak-performance-bound. Rust's async ergonomics are harder than Go's, and the connector ecosystem is thinner. Reserve Rust only for the case where Go extraction has already happened and still is not enough (extremely unlikely for this product).

### 8.3 Go everywhere except handlers (Temporal-style)

Rejected for this product, at this time. Temporal made sense for Uber because they had extreme scale and an existing Java codebase (Cadence) they wanted to modernize. This codebase has a clean Python foundation that already solves 40% of the target design (per the gap analysis). The Temporal-style split is 10x the effort of a gated Dispatcher extraction with the same eventual throughput ceiling.

### 8.4 Embed a Go Dispatcher in the Python process

Rejected as a design trap. Embedding Go via cgo or similar introduces memory-safety friction, build complexity, and cross-runtime debugging pain. If the Dispatcher is worth extracting, it is worth extracting as a separate process.

### 8.5 Keep Python but optimize with Cython / Rust extensions

Worth watching, not part of this plan. `pydantic-core` is already a Rust extension. `orjson`, `uvloop`, `httpx-rs` (if it ever exists) all reduce Python overhead. For the Dispatcher hot path, writing the expression evaluator as a Rust extension via PyO3 is a plausible late-stage optimization — cheaper than a Go rewrite, if profiling ever points there.

---

## 9. Decision Matrix

| Criterion | Weight | Stay Python | Full Go rewrite | Gated Go extraction (recommended) |
|---|---|---|---|---|
| Time to ship | High | Immediate | 10-17 months | Immediate; optional 1-2 months later |
| Risk | High | Low | High | Low (optional follow-up) |
| Peak throughput ceiling | Medium | Bounded (~20k events/sec/replica) | Highest | High where it matters (Dispatcher) |
| Preserves existing investment | High | 100% | ~20% | ~95% |
| Team fit (Python-strong team) | Medium | Good | Poor (retraining) | Good (core Python, 1 Go service) |
| Connector velocity (handlers in Python) | High | Max | Slow | Max |
| Operational complexity | Medium | One stack | Two stacks everywhere | Two stacks for one service |
| Migration reversibility | Medium | N/A | Hard to reverse | Easy (bus is the contract) |

**Recommendation: Gated Go extraction.** Strictly better than the full rewrite on every axis except peak throughput, and peak throughput is the axis that can be unlocked conditionally via the Dispatcher cut if the data ever demands it.

---

## 10. Where This Leaves the Execution Plan

The execution plan (`event-driven-execution-plan.md`) is unchanged through Phase 4. The gated Go extraction adds a new, optional Phase 4.5 between Phase 4 and Phase 5:

- Phase 0-4 execute in Python as originally specified.
- Phase 4.5 is a **decision gate**, not a deliverable by itself. It reviews metrics collected during Phase 2 and Phase 4 against the thresholds in §6. The deliverable is a short decision memo: "extract" or "do not extract."
- Phase 4.6 (conditional) rewrites only the Dispatcher in Go. 1-2 engineer-months scoped work. All other services stay Python.
- Phase 5 (event-driven BUILTIN) proceeds in whichever language the Dispatcher is now in. Everything else is still Python.

This keeps the execution plan intact and adds the language-choice decision as a late, data-backed off-ramp rather than a precondition.

---

## 11. Honest Caveats

Three things this document is deliberately not doing:

1. **Not promising numbers.** The throughput numbers in §5.1 are order-of-magnitude estimates from general benchmarks, not measurements of this codebase. Real numbers come from Phase 2 metrics.
2. **Not ruling out a bigger rewrite forever.** If this product grows to hyperscale SaaS with hundreds of thousands of concurrent runs, the answer changes. Revisit with real scale data.
3. **Not advocating language purity.** If specific hot paths in Python (e.g., the expression evaluator) become bottlenecks before Phase 4 completes, a Rust extension via PyO3 or a Cython optimization is a cheaper fix than a language port. Optimize the measured bottleneck, not the imagined one.

---

## 12. Bottom Line

**Stay Python through Phase 4. At Phase 4.5, measure. If — and only if — the data says the Dispatcher is the bottleneck, extract it to Go in isolation. Never touch the feature layer, handlers, Projector, or persistence.** The existing Python investment is preserved, the event-driven migration happens regardless of language, and the language decision is deferred until there is real data to answer it. This is the least risky path that also leaves the Go option fully open.
