# Phase M.1 — Sticky L7 Routing by `run_id`

> Route every HTTP request scoped to the same `run_id` to the same
> control-plane pod. Infra config only — **no code changes** to the
> service.

## 1. Why (and why this is an optimisation, not correctness)

Phase M.3 shipped a Kafka response topic so a PATCH (`/runs/{id}/
nodes/{id}/data`) enqueued by instance A can be processed by instance
B and still deliver its response back to A's HTTP caller. With M.3 in
place, cross-instance requests are **correct**, just slower — each
cross-instance PATCH pays one extra broker round-trip (A→broker→B→
broker→A).

M.1 is the optimisation that minimises that cost: a load balancer that
consistently hashes on `run_id` pins all requests for a run to a
single pod, turning nearly every PATCH into the local fast path
(`_is_local_origin == True` in `OrchestrationWorker`).

**Rollout sequence** (pick based on deployment reality):

| State | Sticky routing | M.3 response topic | Result |
|-------|----------------|--------------------|--------|
| Pre-M.3 | Required | — | Correctness by routing |
| M.3-only | Off | On | Correct; slow cross-instance hits |
| M.3 + M.1 | On | On | Correct; fast; cross-instance only on failover |

M.1 is therefore **not blocking** multi-instance adoption. It's a
latency tax removal.

## 2. The routing contract

### 2.1 URL pattern

Every run-scoped endpoint on the control plane is mounted at:

```
/api/v1/runs/{run_id}/...
```

The `run_id` is always the **third** path segment after `/api/v1/runs/`.

Exhaustive list (`app/layer3_adapters/controllers/restful/v1/run_controller.py`):

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/v1/runs` | **No run_id** — list endpoint |
| POST | `/api/v1/runs` | **No run_id** — create endpoint |
| GET | `/api/v1/runs/{run_id}` | |
| POST | `/api/v1/runs/{run_id}/cancel` | |
| POST | `/api/v1/runs/{run_id}/pause` | |
| POST | `/api/v1/runs/{run_id}/resume` | |
| GET | `/api/v1/runs/{run_id}/nodes/{node_id}/execution` | |
| GET | `/api/v1/runs/{run_id}/nodes/{node_id}/schema` | |
| GET | `/api/v1/runs/{run_id}/nodes/{node_id}/data` | |
| **PATCH** | `/api/v1/runs/{run_id}/nodes/{node_id}/data` | **primary M.3 case** |
| POST | `/api/v1/runs/{run_id}/nodes/{node_id}/submit` | |
| POST | `/api/v1/runs/{run_id}/nodes/{node_id}/trigger` | |
| GET | `/api/v1/runs/{run_id}/tasks/{task_id}` | |
| POST | `/api/v1/runs/{run_id}/tasks/{task_id}/complete` | |
| POST | `/api/v1/runs/{run_id}/tasks/{task_id}/fail` | |
| POST | `/api/v1/runs/{run_id}/tasks/{task_id}/rerun` | |
| GET | `/api/v1/runs/{run_id}/context` | |
| GET | `/api/v1/runs/{run_id}/artifacts` | |

**Non-run paths** (`/api/v1/workflows/*`, `/api/v1/health`, etc.) do
NOT need run affinity. They can route round-robin.

### 2.2 Hashing rule

Extract `run_id` with a regex on the path:

```
^/api/v1/runs/([^/?]+)
```

Capture group 1 is the hash key. If the path doesn't match (list,
create, non-run routes), fall back to round-robin.

**Do NOT hash on a header** (`X-Run-Id`, `X-Correlation-Id`) — the
client-side contract is the URL, and any intermediary that adds /
strips headers breaks affinity silently.

### 2.3 Consistency guarantees required

- **Same run_id → same pod** as long as pod set is stable.
- On pod add/remove: **consistent hashing** (ring/jump hash) so only
  runs whose pods actually changed are re-routed. Standard LB modulo
  hashing (`hash(key) % N`) reshuffles everyone when N changes and
  must not be used.

## 3. Deployment-specific configs

### 3.1 SAP BTP Cloud Foundry (current prod)

**Constraint:** CF `gorouter` does NOT support consistent hashing on
path. It supports:
- **App-instance-level stickiness** via `JSESSIONID` or `VCAP_ID`
  cookie — useful for session affinity but not per-run routing,
  because a single client session touches many runs.
- Round-robin across instances — the default.

**Options, ordered by effort:**

1. **Ship M.3-only, skip M.1.** Acceptable if the average PATCH rate
   per instance is low and the one-broker-hop tax is tolerable.
   Simplest.

2. **SAP API Management in front.** API Management supports custom
   routing policies; a JavaScript policy can extract `run_id` from
   the path and apply a consistent-hash decision. Requires an
   additional service on the path and license.

3. **Migrate to SAP Kyma.** Kyma is SAP BTP's Kubernetes runtime;
   Istio is included. See §3.3 for the DestinationRule config. This
   is the path taken by most SAP teams that need advanced routing.

4. **Self-managed Nginx sidecar.** A CF app in front of the
   control-plane app, running nginx with the config in §3.2. Adds a
   hop but works on plain CF.

**Recommendation:** run on M.3-only in CF until a Kyma migration is
on the roadmap. Revisit M.1 then.

### 3.2 Nginx (generic reverse proxy / sidecar / K8s `nginx-ingress`)

The `ngx_http_upstream_module`'s `hash ... consistent` directive does
exactly what we need. Use `$uri` matched against the regex, or
precompute the key in a `map`:

```nginx
http {
    map $uri $run_affinity_key {
        # Capture run_id from /api/v1/runs/{run_id}/...
        ~^/api/v1/runs/(?<rid>[^/?]+)   $rid;
        # Non-run paths: empty string → nginx falls back to
        # round-robin since consistent hashing over "" is uniform.
        default                         "";
    }

    upstream workflow_control_plane {
        hash $run_affinity_key consistent;

        # Pod endpoints — in K8s nginx-ingress, use the service's
        # headless endpoints so individual pods are addressable.
        server cp-0.workflow-cp.svc.cluster.local:8001;
        server cp-1.workflow-cp.svc.cluster.local:8001;
        server cp-2.workflow-cp.svc.cluster.local:8001;

        keepalive 32;
    }

    server {
        listen 80;

        location / {
            proxy_pass         http://workflow_control_plane;
            proxy_http_version 1.1;
            proxy_set_header   Connection "";
            proxy_set_header   Host $host;
            proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;

            # Socket.IO needs upgrade headers; keep affinity the same
            # upstream block — run_id is in the query string for
            # Socket.IO, so extend the map above if you need sticky
            # realtime too. Usually realtime is served via Redis, so
            # any pod can answer.
            proxy_set_header   Upgrade $http_upgrade;
            proxy_set_header   Connection $connection_upgrade;
        }
    }
}
```

**For K8s `nginx-ingress-controller`**, the same map + upstream goes
into a `ConfigMap` annotation on the Ingress object:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: workflow-control-plane
  annotations:
    nginx.ingress.kubernetes.io/upstream-hash-by: |
      $uri_run_id
    nginx.ingress.kubernetes.io/configuration-snippet: |
      set $uri_run_id "";
      if ($uri ~ ^/api/v1/runs/([^/?]+)) {
          set $uri_run_id $1;
      }
spec:
  rules:
    - http:
        paths:
          - path: /api/v1
            pathType: Prefix
            backend:
              service:
                name: workflow-control-plane
                port:
                  number: 8001
```

### 3.3 Istio / Kyma (`DestinationRule`)

Istio's `consistentHash` load balancer is the natural fit. Because
Istio can't natively hash on a regex-extracted path segment, use an
EnvoyFilter to inject the run_id as a request header first, then
hash on the header.

```yaml
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: workflow-control-plane
spec:
  host: workflow-control-plane.workflow.svc.cluster.local
  trafficPolicy:
    loadBalancer:
      consistentHash:
        httpHeaderName: x-run-id
    connectionPool:
      http:
        http2MaxRequests: 1000
        maxRequestsPerConnection: 10
---
apiVersion: networking.istio.io/v1alpha3
kind: EnvoyFilter
metadata:
  name: extract-run-id-from-path
spec:
  workloadSelector:
    labels:
      app: workflow-control-plane
  configPatches:
    - applyTo: HTTP_FILTER
      match:
        context: SIDECAR_INBOUND
        listener:
          filterChain:
            filter:
              name: envoy.filters.network.http_connection_manager
      patch:
        operation: INSERT_BEFORE
        value:
          name: envoy.filters.http.lua
          typed_config:
            "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua
            inline_code: |
              function envoy_on_request(handle)
                local path = handle:headers():get(":path") or ""
                local rid = path:match("^/api/v1/runs/([^/?]+)")
                if rid then
                  handle:headers():add("x-run-id", rid)
                end
              end
```

The Lua filter extracts `run_id` and adds it as `x-run-id`; the
DestinationRule hashes on that header. Non-run requests have no
header → Envoy uses round-robin for them.

### 3.4 Envoy (standalone)

Equivalent to §3.3 without Istio:

```yaml
static_resources:
  listeners:
    - address: { socket_address: { address: 0.0.0.0, port_value: 8080 } }
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: ingress_http
                route_config:
                  virtual_hosts:
                    - name: workflow-cp
                      domains: ["*"]
                      routes:
                        - match: { prefix: "/" }
                          route:
                            cluster: workflow_control_plane
                            hash_policy:
                              - header:
                                  header_name: "x-run-id"
                http_filters:
                  - name: envoy.filters.http.lua
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua
                      inline_code: |
                        function envoy_on_request(handle)
                          local path = handle:headers():get(":path") or ""
                          local rid = path:match("^/api/v1/runs/([^/?]+)")
                          if rid then handle:headers():add("x-run-id", rid) end
                        end
                  - name: envoy.filters.http.router

  clusters:
    - name: workflow_control_plane
      type: STRICT_DNS
      lb_policy: RING_HASH               # consistent hashing
      ring_hash_lb_config:
        minimum_ring_size: 1024
      load_assignment:
        cluster_name: workflow_control_plane
        endpoints:
          - lb_endpoints:
              - endpoint: { address: { socket_address: { address: cp-0, port_value: 8001 } } }
              - endpoint: { address: { socket_address: { address: cp-1, port_value: 8001 } } }
              - endpoint: { address: { socket_address: { address: cp-2, port_value: 8001 } } }
```

### 3.5 HAProxy

HAProxy's `balance uri` with `hash-type consistent` can hash the
**full URI**, but we need just the `run_id` segment. Extract via an
ACL + `set-header`, then `balance hdr`:

```
frontend fe_api
    bind *:8080
    mode http

    # Capture /api/v1/runs/<rid> into a variable, then into a header
    # for the backend to see (hash-key).
    http-request set-var(txn.rid) path,regsub(^/api/v1/runs/([^/?]+).*$,\1)
    http-request set-header X-Run-Id %[var(txn.rid)] if { var(txn.rid) -m found }

    default_backend be_control_plane

backend be_control_plane
    mode http
    balance hdr(X-Run-Id)
    hash-type consistent

    server cp-0 10.0.0.10:8001 check
    server cp-1 10.0.0.11:8001 check
    server cp-2 10.0.0.12:8001 check
```

**Caveat:** HAProxy's `balance hdr` with an empty header value routes
all such requests to one backend. For non-run routes you'd want
round-robin; work around with a separate backend bound to those
paths.

## 4. Verification

### 4.1 Probe script

```bash
# Confirm same run_id repeatedly hits the same pod.
# Each pod exposes instance_id at GET /api/v1/health (or equivalent);
# add the endpoint if it doesn't exist. For M.3 verification, pods
# already log their instance_id at startup and in the response-topic
# subscription line — use those logs, or add /health to return it.

for i in $(seq 1 20); do
  curl -s https://cp.example/api/v1/runs/run-abc-123 | jq -r '.instance_id'
done | sort -u | wc -l
# Expect: 1 — single unique pod for this run.

for rid in run-{a,b,c,d,e,f,g,h,i,j}; do
  curl -s "https://cp.example/api/v1/runs/$rid" | jq -r '.instance_id'
done | sort -u | wc -l
# Expect: min(num_pods, 10) — different runs spread across pods.
```

### 4.2 Metric signal after rollout

Before M.1:
- Response-topic publish rate on `orchestration.responses.*` ≈
  PATCH rate × (N-1)/N (all non-local hits traverse it).

After M.1:
- Response-topic publish rate drops to **near-zero** in steady state.
  Non-zero floor represents routing-table churn (pod rolls,
  rebalances). Any sustained floor > 1-2% suggests the hash isn't
  actually consistent (check ring size, modulo-hashing regression).

Emit a metric on the worker's `_publish_response` call site if you
want a direct counter.

## 5. Interaction with other features

### 5.1 Socket.IO / realtime

Socket.IO already horizontally scales via the Redis adapter
(`REDIS_URL`, see `docker-compose.yml:39`). Realtime events published
by any pod reach all subscribers through Redis pub/sub; **sticky
routing is not needed for realtime**. Leave `/api/v1/events/*` on
round-robin.

### 5.2 Webhooks / gRPC

Webhook and gRPC endpoints that don't take `run_id` in the path
should route round-robin (the fallback in all configs above).

### 5.3 Graceful rollout

Sticky routing changes which pod handles in-flight runs. During a
deploy with `maxSurge > 0` and the default consistent-hash ring:

- Old pod drains normal HTTP over its grace period.
- In-flight `asyncio.Future`s for queue-routed PATCHes hang until
  either:
  - The new-pod worker picks up the partition and publishes back to
    the old pod (works — old pod's listener is still running during
    drain),
  - OR the 30-second `asyncio.wait_for` fires (client gets 504).

**Recommendation:** set pod `terminationGracePeriodSeconds ≥ 60` so
in-flight PATCHes resolve before the listener shuts down. M.3's
response topic makes this safe regardless of routing — no correctness
loss, just latency.

### 5.4 `INSTANCE_ID` stability

M.3 uses a per-process UUID by default. With M.1 this is fine; the
pod handling a run stays the same, and its in-flight correlation_ids
all resolve. On pod restart new UUID means the old listener topic is
abandoned — responses for any PATCH in flight at restart time are
lost, client retries. If that's undesirable, pin `INSTANCE_ID` to
the pod name (e.g., StatefulSet ordinal) via env var:

```yaml
env:
  - name: INSTANCE_ID
    valueFrom:
      fieldRef:
        fieldPath: metadata.name
```

## 6. Rollout checklist

- [ ] Pick deployment target from §3 (CF / Nginx / Istio-Kyma / Envoy / HAProxy).
- [ ] Add routing config, deploy to a test environment.
- [ ] Run §4.1 probe; confirm single-unique-pod-per-run behaviour.
- [ ] Verify response-topic publish rate drops to near-zero (§4.2).
- [ ] Increase pod count; re-run probe; confirm consistent hashing
      (most run_ids keep their pod).
- [ ] Set `INSTANCE_ID` to a stable pod identity (§5.4) if you need
      restart-resilient in-flight request handling.
- [ ] Document in runbook: `orchestration.responses.{instance_id}`
      topic retention should be short (minutes) to bound disk growth.

## 7. Non-goals

- **Run-id rebalancing during scale events.** Consistent hashing
  accepts that (key_count / N) runs move to new pods on a pod-count
  change. This is fine; M.3 handles the in-flight requests
  transparently.
- **Per-workflow or per-tenant affinity.** Not needed; the locking
  contract is per-run.
- **Code changes in the service.** None. The service remains
  location-transparent; the LB is the only piece that changes.
