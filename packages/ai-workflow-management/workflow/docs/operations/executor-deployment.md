# Executor — Standalone Deployment Operations Guide

> Phase 3 Sub 3.7 deliverable. Pairs with
> [`docs/design/phase-3-executor-extraction.md`](../design/phase-3-executor-extraction.md).
> Read that first for the why; this doc is the operator-facing how.

## 1. What this pod does

The Executor is a broker-subscribed consumer of `node.input`. For
every routed `NodeInput` event it:

1. Looks up the node + run + workflow from HANA repos.
2. Resolves the appropriate handler from `NodeHandlerRegistry`.
3. Invokes the handler.
4. (Sub 3.5 / shadow mode) records the result to the parity comparator.
5. (Gate 3 / executor mode) emits `NodeOutputProduced` /
   `TaskCompleted` / `TaskFailed`.

It also hosts the **outbox dispatcher** (Sub 3.2) — the loop that
performs side effects (file uploads, deletes, stage records, queue
enqueues) that DATA_EDIT and HITL resume flows enqueue as
`OutboxRequested` events.

What it does NOT host:

- FastAPI / HTTP routes (control plane keeps these).
- Socket.IO server (control plane).
- The orchestration queue worker that consumes `EditNodeDataAction`
  for PATCH `/data` (control plane — the HTTP path produces these).
- gRPC server.

## 2. Deploying

### 2.0 How the Executor runs today

**There is no separate Executor deployment, and no entry point for one.** The
work described above runs inside the control-plane process, selected by an env
var:

| `EXECUTOR_MODE` | Behaviour |
|---|---|
| `in_process` (default, and what tenant-1 runs) | the control plane invokes handlers itself |
| `executor` | the same process drains `node.input` and invokes handlers authoritatively |

Both are reached through the **ordinary** binary: `bootstrap.py` always
constructs `ExecutorConsumer` and `main.py` starts it, where `start()` is a
no-op unless the mode is `executor`. So switching modes is an env change on the
existing app — nothing new to deploy.

`app/executor_main.py`, a slimmer FastAPI-free entry point for a standalone pod,
was deleted under SA-2272 along with its Dockerfile and k8s manifest: nothing
built it, nothing ran it, and it was imported only by its own test. Its one
extra behaviour — warning and upgrading `in_process` to `executor`, because
`in_process` on a *standalone pod* is a misconfiguration — went with it, and
that guard only ever made sense on a pod that no longer exists.

If a standalone Executor is ever wanted again, the mode logic is all still
here; what is needed is an image and a manifest, not new application code.

### 2.1 Cloud Foundry — REMOVED (SA-2272)

There is no CF manifest for `wio-executor` any more.
`deployment/deployment-executor.yml` was deleted because nothing ever used it:

- **CI never built it.** `reusable-discover.yml` globs `deployment.yml` and
  `deployment.<variant>.yml` — dot-separated. This file was hyphen-separated,
  so discovery never saw it and every edit to it was a silent no-op. That is
  how SA-2087's `INSTANCE_ID` pin sat here looking shipped while never taking
  effect anywhere.
- **No CF space ever ran it.** tenant-1 hosts `ai-workflow-worker-executor` and
  `executor-runtime-service`; there has never been a `wio-executor` app.
- **Its keys spoke a dialect the pipeline does not read** — `dockerfile`,
  `image`, `routePrefix`, `shutdownTimeoutSeconds`. Discovery reads `imagePath`,
  `routeName` and friends.

If a CF-deployed executor is ever wanted, add it as
`deployment/deployment.executor.yml` (**dot**, so discovery finds it) with the
key names discovery actually reads — and teach discovery to honour a
manifest-level `dockerfile` key *first*, because it currently hardcodes
`$svc_path/Dockerfile`, so a rename on its own would build the executor from
the control plane's image.

The executor that **is** deployed today lives in its own service directory,
`worker-executor-service/`, with an ordinary `deployment/deployment.yml`. CI
builds and deploys it normally.

### 2.2 Kubernetes — REMOVED (SA-2272)

`deployment/k8s/executor-deployment.yaml` and `deployment/Dockerfile.executor`
were deleted along with the CF manifest. **This repository has no Kubernetes
pipeline** — no workflow runs `kubectl` or `kustomize`, and that was the only
`k8s/` directory under `apps/`. The manifest was applied by hand, if ever.

What it described, kept here as the record: 2 replicas with HPA to 10, on CPU
(70%) plus a `kafka_consumergroup_lag` custom metric via prometheus-adapter;
`terminationGracePeriodSeconds: 60`; `INSTANCE_ID` injected from
`metadata.name`; `EXECUTOR_MODE=shadow` by default.

That `INSTANCE_ID` binding is worth remembering rather than repeating: pinning
it to a k8s pod name is what SA-2087 (R12) traced a real defect to — every
replica of a Deployment shares the `wio-executor-<rs-hash>-` prefix, so a
truncating shard collapsed them onto one response topic. Identity is now
derived in-process by `resolve_instance_id`, so a future manifest does not need
to pin it at all.

## 3. Observability

### 3.1 Dashboards (build these in Grafana)

| Panel | Metric | Notes |
|---|---|---|
| Handler invocation rate | `rate(node_handler_invocations_total[5m])` by `node_type` | One stack per node type; the TASK row is the busiest |
| Handler duration | histogram_quantile(p50/p95/p99 on `node_handler_duration_seconds_bucket`) | Tag by `handler_shape` to compare CPU vs I/O bound |
| `node.input` lag per pod | `kafka_consumergroup_lag{consumergroup="wio-executor",topic="node.input"}` | One series per replica |
| Outbox pending count | gauge from `pending_outbox` view — exposed via `/metrics` once Sub 3.2's pending count is wired through `IMonitor` | Spike = dispatcher backlog |
| Outbox per-kind latency | `histogram_quantile(0.95, outbox_handler_duration_seconds_bucket{kind=~".+"}[5m])` | Diagnoses which side-effect type is slow |
| Kill-recovery time | derived from event-log: time between `OutboxRequested.timestamp` and the eventual `OutboxResolved(ok).timestamp` for entries that crossed pod boundaries | Best surfaced via a Loki/HANA join, not a pure metric |

### 3.2 Alerts

Threshold values are starting points — tune to your fleet's baseline
during the first soak week.

| Alert | Condition | Action |
|---|---|---|
| Outbox dispatcher backlog | `pending_outbox` > 100 for > 5 min | Page oncall — likely a downstream service (file service, queue) is degraded |
| Outbox failures | `rate(OutboxResolved_failed_total[1m]) > 1/min` | Investigate handler logs; common cause is upload payload size or auth token expiry |
| Handler tail latency | `histogram_quantile(0.99, node_handler_duration_seconds_bucket[5m]) > 30s` | Investigate the slow handler — TASK timeouts surface here first |
| Topic lag | `kafka_consumergroup_lag{consumergroup="wio-executor"}` > 1000 for 10 min | HPA should have scaled by now; if not, check broker connectivity |
| Pod restart loop | `kube_pod_container_status_restarts_total{pod=~"wio-executor-.*"}` increasing > 3 in 10 min | Almost always a misconfiguration (HANA creds, Event Mesh cert) |

### 3.3 Logs

Look for these structured markers when triaging:

- `ExecutorConsumer subscribed to node.input` — confirms broker bind
- `ExecutorParity drift: …` — shadow-mode divergence, indicates the
  Executor's computed result disagrees with the engine's. Drift > 0
  during the soak window blocks Gate 3.
- `outbox dispatcher iteration failed` — the dispatcher caught an
  exception not caught by handler-level error classification. Treat
  as a bug.
- `Executor received SIGTERM — initiating shutdown` — graceful drain
  began; expect "Executor shutdown complete" within 60s.

### 3.4 Probes

The `/metrics` endpoint backs both liveness and readiness. Failure
modes:

- Liveness fails → pod restarts. Should only happen on Python crash
  (Kafka client connection loss does NOT fail liveness — it surfaces
  as topic lag instead).
- Readiness fails → pod removed from Service endpoints (no traffic
  impact since the Executor is not an HTTP service, but the metrics
  scrape stops).

## 4. Rollout sequence

Phase 3 ships in five gates per
[design doc §13](../design/phase-3-executor-extraction.md). The
operator-visible flips:

| Gate | Flag | Default | Production target |
|---|---|---|---|
| 0 | (prerequisites) | — | Sub 3.0 + 3.1 + 3.2 merged with flags off |
| 1 | `OUTBOX_DATA_EDIT`, `OUTBOX_HITL_RESUME` | `false` | `true` after 1-week staging soak |
| 2 | `EXECUTOR_MODE` | `shadow` | `shadow` for 7 days staging + 14 days prod with drift = 0 |
| 3 | `EXECUTOR_MODE` | `shadow` | `executor` after Gate 3 surgery (engine inline-call deletion) merges |
| 4 | (deployment) | — | Standalone Executor pods running alongside control-plane pods; control-plane pod keeps the Executor consumer in `in_process` only as a hot fallback during the cutover |

## 5. Troubleshooting

### "Why did the Executor pod get OOMKilled?"

The handler registry holds 13 handlers, the catalog cache, and a
copy of the outbox dispatcher's pending state. None of these grow
unboundedly, but a sustained `node.input` lag combined with large
DATA_EDIT payloads can push memory. Check:

1. Per-pod `node.input` lag — if it's been > 10k for 5+ minutes the
   asyncio queue is buffering.
2. Outbox bytes channel size — `OutboxBytesChannel.total_bytes`
   defaults to a 64 MB cap with FIFO eviction. If a single
   DATA_EDIT mutation is bigger than 64 MB the channel evicts older
   entries before the dispatcher consumes them; the producer falls
   back to a direct upload.

### "Why is shadow-mode drift non-zero?"

The Executor's parity comparator reports drift on
`(run_id, node_id, attempt, event_types, output_port, error)`. Most
common causes:

1. **The Executor saw a redelivered `NodeInput` the engine didn't
   re-emit.** Check the dedup LRU's hit rate — if a redelivery is
   slipping past dedup the comparator records `executor_only`.
2. **A handler raised on the Executor but not in-process.**
   Different timing → different flake threshold. Compare the engine
   log line for the same `(run_id, node_id)` against the Executor's.
3. **A handler reads from the view-store, and the Executor's read
   missed a same-tick update from another path.** This is the case
   the Gate 3 surgery is meant to settle — engine inline-call
   deletion removes the dual-read race entirely.

### "Why does shutdown hang past 60s?"

The dispatcher's `run_forever` loop polls every
`OUTBOX_DISPATCHER_POLL_INTERVAL_S` seconds (default 1.0s). A
shutdown signal sets the stop event, but in-flight
`OutboxDispatcher.drain_once` work continues to completion before
the loop exits. If a single outbox kind (typically `file_upload`
with a slow file service) takes > 60s, k8s will SIGKILL.

Fix: lower `OUTBOX_HTTP_WAIT_TIMEOUT_S` (default 5.0s) or raise
`terminationGracePeriodSeconds`. The right number depends on the
slowest realistic handler invocation in your fleet.

## 6. Reference

| Env var | Default | Purpose |
|---|---|---|
| `EXECUTOR_MODE` | `shadow` (executor pod) / `in_process` (control plane) | Tri-state — see Phase 3 design doc §10 |
| `OUTBOX_DATA_EDIT` | `false` | Routes DATA_EDIT side-effects through the outbox |
| `OUTBOX_HITL_RESUME` | `false` | Routes HITL resume side-effects through the outbox |
| `OUTBOX_HTTP_WAIT_TIMEOUT_S` | `5.0` | Producer-side budget waiting for `OutboxResolved` |
| `OUTBOX_DISPATCHER_POLL_INTERVAL_S` | `1.0` | Dispatcher poll cadence when no entries are pending |
| `OUTBOX_MAX_ATTEMPTS` | `5` | Per-entry retry budget before terminal `failed` |
| `EVENT_LOG_BACKEND` | `memory` (dev) / `hana` (prod) | The shared event log persistence — must match the control plane |
| `INSTANCE_ID` | random UUID / `metadata.name` (k8s) / `${CF_INSTANCE_GUID}` (CF) | M.3 sticky routing for response topics |
