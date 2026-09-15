# Control-Plane ↔ Executor Messaging — "metadata-only on the queue" audit

**Date:** 2026-06-09
**Question:** Do the control-plane (WCP) and worker-executor communicate over Event Mesh / Kafka, and does any *real data output* cross the message queue — or only metadata/references?

**Verdict:** The **broadcast/persisted event topic** (path 3) — the queue that matters most — is **already metadata-only in steady state**: CP uploads each node's output to the file-service and emits a `file_ref` pointer. It leaks raw data only on the file-service-down *fallback*. The **unavoidable raw-data hop is the executor→CP result transport** (`workflow.results` queue in `local`/`sap` modes; HTTP reporter otherwise): the executor has **no file-service client**, so it ships full inline output to CP, which is the one that persists+thins it. The `uri`/`output_reference` seam to fix this exists but is unused.

---

## 1. How they communicate

Two broker backends behind interfaces (`IBrokerClient` / `IMessageBrokerClient`), selected by config:

| Backend | CP client | Executor client |
|---|---|---|
| **Kafka** (`confluent_kafka`, at-least-once) | `kafka_broker_client.py` | `kafka_broker_client.py` |
| **SAP Event Mesh** (REST + OAuth2, queues+topics) | `event_mesh_broker_client.py` | `event_mesh_broker_client.py` |

Task **dispatch** transport is selected by `TRANSPORT_MODE` (default **`http`**): `http` | `grpc` | `event_mesh`.
Result/observability **always** use the broker when one is configured.

### The three broker paths

| # | Topic | Direction | When active | Payload | Bulk-data risk |
|---|---|---|---|---|---|
| 1 | `workflow.tasks` | CP → Executor | `TRANSPORT_MODE=event_mesh` (or `MESSAGING_MODE=local` Kafka) | full resolved **`input`** | **input** data on queue; no size mitigation |
| 2 | `workflow.results` | Executor → CP | `MESSAGING_MODE`/`INFRA_MODE` = `local`/`sap` (a real broker exists) | **`output_metadata` = full inline worker `outputs`** | **output** data on queue; no size mitigation; **no executor-side thinning possible** (executor has no file-service client) |
| 3 | `WORKFLOW_EVENT_TOPIC` (`orchestrator.workflow_event`) + inner authoritative event topic | CP → projector/observability | whenever CP has a broker | `NodeOutputProduced.state_updates`, `NodeDispatched.input` | **steady-state already thinned to `file_ref` pointers**; leaks raw data only on the file-service-down fallback (see §2.3); 1 MB drop-largest cap on the enriched copy |

### Broker discriminator (corrected)
Result transport depends on mode, **not** just `broker is not None`:
- **`mock` (default)** → `broker = None` → `result_publisher = ConsoleResultPublisher` → **no real queue**. Results reach CP via the HTTP `result_reporter` (`POST /complete`) — real data over HTTP, not a queue.
- **`local` (Kafka) / `sap` (Event Mesh)** → real `workflow.results` queue. In **`sap`/`event_mesh` transport, `result_reporter = None`** (`bootstrap.py:193`), so the **queue is the only result path** — full output data must ride it, with no HTTP fallback to lean on.

> In default HTTP transport, dispatch input (path 1) does **not** touch a queue (HTTP POST to `/api/v1/tasks/execute`).

### The executor has no file-service client
The worker-executor's only outbound dependencies are `result_reporter` (→ WCP) and `worker_dispatcher` (→ workers). There is **no** `FILE_SERVICE_URL` / file client wired (`bootstrap.py:211-228`). So bulk-output offload-to-file-service **cannot happen in the executor today** — only CP has `HttpFileServiceClient`. This is what makes path 2 hard to thin: the executor must ship raw output to *someone* who can persist it.

---

## 2. Where real data output crosses the queue

### Path 2 — `workflow.results` (the direct leak)
Executor forwards the worker's **entire inline `outputs` dict** as `output_metadata`:

- Sync: `execute_task_usecase.py:224-229` — `else: output_metadata = worker_outputs` (whole dict) → published `result.__dict__` at `:281-286`.
- Async callback: `handle_worker_callback_usecase.py:113-160` — `output_metadata = input.outputs` → published at `:147-160`; failure case publishes `outputs: input.outputs`.

And **every worker returns inline data**, none return a `uri`:

| Worker | Inline payload returned |
|---|---|
| http-request | `response_body`, `response_headers`, `resolved_body` |
| database-connection | `data` = full SELECT rows as dicts |
| mapping-data | `mapped_data` = full transformed dataset |
| agent | `response_content` (LLM text) |
| ms-teams | webhook `response` |
| gateway | echoes resolved `inputs` |
| wait | timing scalars (genuinely metadata) |

`worker-sdk` `ExecuteTaskResult` has an `output_reference` field — commented **"currently unused"**. The `if "uri" in worker_outputs` branch in the executor is therefore effectively dead; the `else` (full-dict) branch is always taken.

### 2.3 Path 3 — `WORKFLOW_EVENT_TOPIC` / event topic (already thin, with one fallback leak)
**Steady state is metadata-only.** Before emitting `NodeOutputProduced`, CP converts the *whole* worker output to a `file_ref` pointer: `complete_task_use_case.py:180` (`store_data` if not already a ref) and `apply_state_updates_as_files`→`_to_file_ref`→`store_data` (`orchestration_engine.py:2317-2434`). The output dict is uploaded as a CSV to the file-service and replaced with a `{__file_ref: true, file_id, …}` pointer; the `extracted` scalar cache is dropped (OData-only refactor). Downstream `$node.output.<key>` resolves on demand via `FileBackedResolverStrategy` (materialize / OData row-fetch). So `state_updates` rides the topic as a **pointer**, not inline rows.

**The real leak is the file-service-down fallback.** When upload fails, `_store_dict` and `_to_file_ref` `return data` (the raw dict) instead of a ref — the documented "File-service upload swallow" path. So the event topic **silently emits the full inline output exactly when uploads fail**. This is a fallback bug, not steady-state.

**Caveat — bootstrap seed:** the bootstrap-seed emission (`orchestration_engine.py:990-1005`, `node_id="__bootstrap__"`) passes `state_updates` **unwrapped**, including the run's `input` write-set. Whether `input` is already a ref by then depends on the start path; if not, run input can ride this emission inline.

The only other safety net is `EnrichingEventPublisher._enforce_size_limit` (1 MB drop-largest-field + `input_schema` default stripping) on the *enriched* copy — reject-avoidance, not a metadata guarantee.

---

## 3. Recommendation (corrected after verification)

The two queue paths have very different cost/feasibility, so they are separate decisions.

### 3.1 Bounded, safe win — ship now (path 3, the broadcast/persisted topic)
This is the queue that matters most: multi-consumer, persisted in the event log, replayed by the projector. It is **already** metadata-only in steady state; the only leak is the file-service-down fallback returning raw data.

- **Harden the fallback so the event topic never silently carries raw inline output.** When `store_data` / `_to_file_ref` cannot get a `file_id`, emit a reference-with-error placeholder (`file_ref` with empty `file_id` + an explicit error marker) instead of `return data`, and log loudly — so upload failures surface as failures rather than as raw payloads on the wire. (Tradeoff to confirm: on upload failure the node loses resolvable output; today it silently keeps it inline. This trades resilience for the wire-cleanliness invariant — acceptable if the file-service is a hard dependency.)
- **Add a fence test** asserting that what is published to the event topic for a completed node is a `file_ref` (or a bounded placeholder), never a dict carrying bulk fields like `response_body` / row arrays. Regression fence so future changes can't reintroduce inline payloads.

No new dependencies; does not touch the executor; cannot break steady-state downstream resolution.

### 3.2 Larger, gated decision — path 2 (`workflow.results`, executor → CP)
Making this queue metadata-only requires **giving the executor a file-service client** (new `FILE_SERVICE_URL` dependency in a service that has none today). The executor would upload bulk output, set the already-present-but-unused `output_reference`/`uri` field, and CP would build a `file_ref` from it (CP already checks `is_file_ref` at `complete_task:181`). This activates the dead `if "uri" in worker_outputs` seam rather than inventing a mechanism.

**Gating risk — verify first:** CP's store + resolver path is CSV/OData-oriented (`dict_to_csv`, `__row_id`, OData row-fetch). `upload_file` itself accepts arbitrary `content_type`/bytes, but resolving an arbitrary non-tabular blob (an HTTP `response_body`, LLM text, arbitrary JSON) back via `$node.output.<key>` may not be supported by the materialize path. If the file-service/resolver can only round-trip tabular data, offloading arbitrary worker output needs a blob store, not this file-service — a materially bigger job. **In `sap`/`event_mesh` transport there is no HTTP fallback, so the queue cannot simply drop the data; the executor offload is the only route.**

### Out of scope for "message queue"
The HTTP/gRPC `result_reporter` (`POST /complete`) carries full `output_metadata` too, but point-to-point over HTTP — real data on the wire, not on a queue. Worth noting; not a queue leak.

---

## Key files

- Executor result build/publish: `worker-executor-service/.../execute_task/use_cases/execute_task_usecase.py:214-286`; `.../handle_worker_callback/use_cases/handle_worker_callback_usecase.py:113-201`
- Result wiring per mode: `worker-executor-service/bootstrap.py:143-196`
- CP centralized publisher + size cap: `workflow-control-plane-service/.../messaging/enriching_event_publisher.py`
- CP NodeOutputProduced (wire-thin): `workflow-control-plane-service/.../services/orchestration_engine.py:2412-2434`
- Dispatch transports: `.../messaging/http_task_dispatcher.py`, `event_mesh_task_dispatcher.py`, `grpc_task_dispatcher.py`
- Event/DTO schemas: `workflow-control-plane-service/.../layer1_domain/events.py`
