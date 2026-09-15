# Runbook — Per-instance response-topic quota & degradation (SA-1466)

## What this is

Each control-plane **pod** provisions its own response topic + queue on the
broker so a peer pod can route an `EditNodeDataAction` result back to the pod
that owns the caller's `Future`:

- topic: `orchestration.responses.{shard}` (`shard` = first 8 hex of the instance UUID)
- queue: `sub-orchestration-responses-{shard}` (Event Mesh local name, ≤ 60 chars)

Provisioning happens at startup in `ResponseTopicListener.start()`
(`app/layer4_frameworks/orchestration_queue/response_topic_listener.py`).

## The risk

SAP Event Mesh enforces a **per-service-binding topic/queue quota** (plan
dependent). Response topics are **per pod**, so `N` pods consume `N`
topics+queues against that shared quota. If a deployment scales past the quota
(or hits a naming-policy `422`), `start()` **degrades gracefully**: it logs an
error, sets `degraded=True`, and the pod keeps booting. The single-pod
fast-path still works, but in a **multi-pod** deployment cross-instance
responses on the degraded pod are silently lost (the caller's `Future` hangs to
timeout). This passes single-pod staging and fails only under multi-pod load.

## Observability (SA-1466)

`ResponseTopicListener` now emits, via `IMonitor`:

| Metric | Type | Meaning |
|---|---|---|
| `orchestration.response_listener.degraded` | gauge (0/1) | 1 = this pod could not subscribe → cross-instance routing disabled here |
| `orchestration.response_listener.subscribe` | counter | tags `result=ok\|degraded`, `instance`, `reason` |

Readiness endpoint: **`GET /api/v1/health/readiness`** returns the listener
`health()` snapshot (`topic`, `instance_id`, `started`, `degraded`).

### Alert rule (add to your monitoring)

```
ALERT ResponseTopicListenerDegraded
  IF sum(orchestration_response_listener_degraded) > 0
  FOR 2m
  # fires when any pod cannot own its response topic — investigate quota
```

## Mitigations (in order)

1. **Route response topics to the secondary broker.** Configure a secondary
   Event Mesh binding — `BrokerRegistry` already routes
   `BrokerPurpose.RESPONSE_TOPICS → secondary` when one is wired
   (`EVENT_MESH_SECONDARY_BINDING_NAME` / `EVENT_MESH_SECONDARY_*`). This moves
   the per-pod queues off the primary quota.
2. **Enable the readiness gate for multi-pod deployments.** Set
   `RESPONSE_LISTENER_READINESS_GATE_ENABLED=true`. A degraded pod then reports
   `503` on `/api/v1/health/readiness`, so the platform pulls it from rotation
   instead of silently mis-serving. **Leave it off for single-pod** (the
   fast-path is fine and a degraded listener is harmless there).
3. **Keep instance count ≤ broker topic/queue quota** until (1) is in place.
   Ceiling = `quota / (topics_per_pod)`; response listener uses 1 topic + 1
   queue per pod.

## Verifying a fix

- Deploy, then `GET /api/v1/health/readiness` on each pod → `degraded: false`.
- With the gate on, force a subscribe failure (e.g. exhaust quota in a scratch
  space) and confirm the pod returns `503` and is pulled from rotation.
- Confirm the `orchestration.response_listener.degraded` gauge is 0 across pods.
