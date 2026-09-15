# Phase M.3 — Kafka Partition-by-run_id + Response Topic

> Ship cross-instance correctness for queue-routed HTTP requests
> (PATCH /runs/{id}/nodes/{node_id}/data) when the Kafka
> orchestration.actions partition that owns a run is consumed by an
> instance other than the one handling the HTTP caller.

## 1. Why

Phase 3c retired `RunLockRegistry` by routing PATCH through the
single-consumer orchestration queue. Per-instance this gives
correctness via serialisation at the consumer; **cross-instance** it
relies on every PATCH landing on the same instance that Kafka routed
the partition to.

Phase M.2 added optimistic CAS at the repo — every write is now
deterministic even under races — but it does not address the
request/response gap: the asyncio.Future in `ResponseRegistry` lives
on the instance that **enqueued** the action, not the instance that
**processes** it. If the two differ, the HTTP caller times out after
30s even though the PATCH succeeded.

M.3 closes that gap.

## 2. What ships

Five sub-tasks, all landed together (single PR — the response-topic
wiring is only useful end-to-end):

| Sub | Content | LoC | Behavior change |
|-----|---------|-----|-----------------|
| **M.3.1** | Register `EditNodeDataAction` in kafka_queue's `_ACTION_CLASSES`; rehydrate `command` dict → `EditNodeDataCommand` dataclass on consume | ~15 | Fixes pre-existing silent-drop bug in Kafka mode |
| **M.3.2** | `INSTANCE_ID` setting (env override), UUID default, exposed on container as `_instance_id` | ~10 | No — plumbing only |
| **M.3.3** | `origin_instance_id` on `EditNodeDataAction`; `ResponseTopicListener` subscribes to `orchestration.responses.{instance_id}`; worker routes response across instances when origin differs | ~200 | Yes — PATCH now round-trips across instances in Kafka mode |
| **M.3.4** | `KafkaEventPublisher` / `EnrichingEventPublisher` thread partition key (`root_run_id > run_id > workflow_id`) | ~30 | Per-run ordering on domain-event topics |
| **M.3.5** | Two-instance integration test using in-memory broker stub | ~180 | Verification only |

## 3. Design

### 3.1 Response topic per instance

Each control-plane instance subscribes to a **per-instance** topic:

```
orchestration.responses.{instance_id}
```

- Uses `IBrokerClient.subscribe_to_topic`, same Kafka consumer group
  as all other subscriptions on the instance. Because the topic name
  includes the instance_id, each instance's topic is distinct — Kafka
  delivers responses only to the intended instance without extra
  group-id plumbing.
- Topic is auto-created on `start()` via the broker's `ensure_topic`
  (Kafka admin); Event Mesh creates the queue on subscribe.
- On instance restart with a fresh UUID, the old topic lingers until
  Kafka's retention expires — acceptable (no data leak; topic names
  are opaque to users).

**Rejected alternative:** single `orchestration.responses` topic with
per-instance consumer groups + in-callback filtering. Would require
extending `KafkaBrokerClient` to support per-subscription group ids,
which is invasive and has sharp edges (group rebalancing). Per-topic
naming is simpler and leverages what the broker client already does.

### 3.2 Action envelope extension

`EditNodeDataAction` gains `origin_instance_id: str = ""`. The HTTP
controller populates it from `container["_instance_id"]` before
enqueueing. Empty string means "resolve locally" — preserves the
single-instance fast path and keeps tests that construct actions
directly working.

### 3.3 Worker routing

`OrchestrationWorker._dispatch_edit_node_data` branches on:

```python
def _is_local_origin(self, action):
    if not action.origin_instance_id:  return True  # test / single-instance
    if not self._instance_id or self._broker is None:  return True  # misconfig
    return action.origin_instance_id == self._instance_id
```

- **Local origin** → resolve/fail the local `ResponseRegistry`
  (existing fast path; zero broker hops).
- **Foreign origin** → publish `{correlation_id, result, error}` to
  the origin's response topic. Key is the correlation_id (not the
  run_id) because responses are point-to-point, not ordered per run.

Exceptions are serialised as `{class: str, message: str}`; the
listener raises `CrossInstanceExecutionError` on receipt.

### 3.4 Listener + local registry

`ResponseTopicListener` subscribes to its instance's response topic
and, on each message, calls `registry.resolve(corr_id, result)` or
`registry.fail(corr_id, exc)`. Registry is unchanged from Phase 3c —
listener is a thin bridge.

`EditNodeDataResult` is a nested dataclass (contains `OpResult`,
`RerunInfo`); `asdict → json → dict` round-trip requires explicit
rehydration. Handled by `_rehydrate_edit_node_data_result` — not a
generic solution, but the only cross-instance type we need today.

### 3.5 Domain-event partition keys

`KafkaEventPublisher.publish` and `EnrichingEventPublisher.publish`
(centralised topic path) both extract a partition key from the event
payload with the precedence `root_run_id > run_id > workflow_id >
None`. Mirrors `RealtimeBroadcastingPublisher`'s rule at
`broadcasting_publisher.py:191` — same rule on both chains means
per-run ordering composes end-to-end.

Non-keyed fallback: catalog changes, provider-level events with no
run affinity. Kafka round-robins these — fine.

## 4. Fast / slow paths

### 4.1 Single-instance (memory mode)

- `infra.broker is None` → `MemoryOrchestrationQueue` + no listener
  wired.
- Worker constructor gets `broker_client=None`; all
  `_is_local_origin` checks return True; registry is resolved
  directly.
- Zero overhead over Phase 3c.

### 4.2 Single-instance (Kafka mode, one pod)

- `BrokerOrchestrationQueue` publishes to `orchestration.actions`;
  the same pod consumes via its subscription.
- `origin_instance_id == self._instance_id` → local fast path; the
  listener subscribes to its own topic but nothing is ever published
  there.
- One extra Kafka topic subscription; no response publish.

### 4.3 Multi-instance (Kafka mode, N pods)

- Kafka routes `orchestration.actions` by `run_id` partition ≥
  different run_ids land on different pods.
- PATCH for run R: Instance A enqueues (origin=A, key=R). Kafka
  routes to partition P, owned by Instance B. Instance B processes,
  publishes `{correlation_id, result}` to
  `orchestration.responses.A` (key=correlation_id). Instance A's
  listener receives, resolves `corr_id` → future.
- One extra broker hop per PATCH, only in cross-instance case. No
  hop when A happens to own P.

## 5. Hazards + non-goals

### 5.1 Hazards

- **Instance restart with fresh UUID:** in-flight requests keyed by
  old UUID are orphaned. HTTP caller times out at 30s; idempotent
  retry recovers. Mitigation: pin `INSTANCE_ID` via env var in
  Deployments where this matters.
- **Response publish failure on Instance B:** the origin's future
  hangs until the 30s `asyncio.wait_for` timeout. Worker logs the
  publish failure. CAS already prevents data corruption; this is a
  latency issue, not correctness.
- **Response topic retention:** old responses linger on Kafka until
  retention expires. No correctness risk (correlation_ids are
  single-use), but wastes disk on high-churn clusters. Set low
  retention (minutes) on `orchestration.responses.*`.

### 5.2 Non-goals

- **Orchestration-engine 18 inner saves retry wrap** — deferred
  from M.2.2. M.3 doesn't change the calculus; still deferred.
- **Action-type extension beyond PATCH.** Only EditNodeDataAction
  needs cross-instance response routing today (it's the only
  request/response queue action). Other actions are fire-and-forget
  or emit events. If we add more request/response actions later,
  extract a generic `CrossInstanceRequestAction` base.
- **Kafka partition count tuning.** The partition count on
  `orchestration.actions` controls fan-out parallelism; not tuned
  in this phase. Production deployments should set it ≥ pod count.

## 6. Test matrix

| Test | Purpose |
|------|---------|
| `test_broker_orchestration_queue.py` (7) | Action-type registry pins every action in `_ACTION_CLASSES`; EditNodeDataAction command rehydration round-trips |
| `test_response_topic_listener.py` (8) | Listener subscribes to correct topic; resolves/fails local registry; rehydrates nested dataclass |
| `test_orchestration_worker_cross_instance.py` (6) | Worker's `_is_local_origin` branching; publishes response payload only on foreign origin |
| `test_kafka_event_publisher_keys.py` (10) | Partition-key extraction precedence + integration with KafkaEventPublisher |
| `test_cross_instance_edit_node_data.py` (3) | End-to-end two-instance flow through an in-memory broker stub |

**Regression delta:** 1694 passed / 59 baseline / 107 skipped.
+34 from post-M.2.3 (the five new test files). Zero new regressions
in M.3-touched code paths. The 59 failures were all pre-existing
(successor_processor `DAGInfo` typing issue; patch_node_data's
unrelated `sweep_timed_out_child_runs` syntax error).

## 7. What remains for full scale-out

- **M.1** — sticky L7 routing by `run_id` (config-only, no code).
  With sticky routing, cross-instance cases in M.3 are rare (only
  failover). Without it, ≈(N-1)/N of PATCHes traverse the response
  topic in an N-pod deployment.
- **Orchestration-engine 18 inner saves retry wrap** — still
  deferred; wait for multi-instance adoption to make conflicts
  observable.
- **Soak:** run with `INFRA_MODE=sap` in a ≥2-pod deployment and
  verify response-topic traffic + CAS 409 rate.
