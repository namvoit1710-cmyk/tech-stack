# Runbook — the executor stops receiving tasks

## What this covers

The executor takes work from the Event Mesh topic `workflow.tasks` (control
plane → executor) whenever `TRANSPORT_MODE=event_mesh`, which is the tenant-1
configuration. If that intake stops, runs hang with their node `NOT_STARTED`
and *nothing else looks wrong*: the executor is up, `/healthz` is green,
workers heartbeat normally, and the control plane happily keeps publishing.

That is the same failure the control plane hit three times on 2026-08-05. The
signals below exist so it is visible here before someone notices a stuck run.

## Signals

| metric | kind | meaning |
|---|---|---|
| `wes_eventmesh_amqp_delivery_total` | counter `{queue}` | tasks a receiver link actually delivered |
| `wes_eventmesh_amqp_links_attached` | gauge | receiver links currently attached |
| `wes_eventmesh_amqp_links_stalled` | gauge | of those, how many are inside the stall window |
| `wes_eventmesh_amqp_stall_total` | counter `{queue}` | the watchdog stood a link down |
| `wes_eventmesh_amqp_reconnect_total` | counter `{queue, reason}` | `token_expiry` \| `watchdog_stall` \| `disconnect` |
| `wes_eventmesh_token_refresh_total` | counter `{outcome}` | `success` \| `failure` — OAuth2 tokens minted |
| `wes_eventmesh_consume_transport_rest` | gauge | 1.0 while intake runs on the REST poll loop |
| `wes_eventmesh_consume_fallback_total` | counter `{reason}` | `proton_missing` \| `amqp_unreachable` \| `queue_has_consumers` |
| `wes_eventmesh_dmq_deadlettered_total` | counter `{queue}` | tasks newly dead-lettered — **work is being lost** |
| `wes_eventmesh_dmq_depth` | gauge | total across every DMQ, including a backlog older than this process |

**Every signal above `dmq_*` describes the transport, and all of them read
healthy while tasks are being destroyed** — the messages really were delivered,
then thrown away. `dmq_deadlettered` is the only one that says work is lost.
It runs under **both** transports, because REST is the rollback target and
that is exactly when you need it.

Its first reading of each DMQ only seeds a baseline, so a restart does not
alarm on tasks lost last week; a pre-existing backlog is logged instead. The
depth gauge is not published until the first check interval has elapsed — a
`dmq_depth` that is simply absent for the first minute after a restart is
normal, not "nothing dead-lettered".

## Page on

- `rate(wes_eventmesh_dmq_deadlettered_total) > 0` — tasks are being lost right
  now. Page regardless of how healthy the transport looks, because it will look
  healthy. Note that **a flapping link dead-letters good tasks**: every
  reconnect redelivers whatever was unsettled, so enough reconnects exhaust the
  redelivery budget on tasks that were never poisonous. Correlate with
  `wes_eventmesh_amqp_reconnect_total` before blaming the payload.
- `wes_eventmesh_amqp_links_stalled > 0` sustained for 5 minutes.
- `rate(wes_eventmesh_amqp_reconnect_total{reason="disconnect"}) > 1/min` — a
  link that keeps dying and reconnecting. **A flapping link is not a stalled
  link**, so the watchdog will not fire and `links_stalled` stays 0: the link
  really is attached and really is delivering, right up until it dies.
- `rate(wes_eventmesh_amqp_reconnect_total{reason="token_expiry"}) > 1/min` — a
  credential rotation is worth **one** reconnect per queue per token lifetime
  (~2/day at a 12h token). Anything sustained above that means reconnecting is
  not clearing the condition that triggers it. Divide by
  `rate(wes_eventmesh_token_refresh_total{outcome="success"})`: a ratio far
  above 1 says links are being rebuilt on a token that never changed.
  Measured on tenant-1 before this alert existed: **40,327 reconnects in 7 days
  on one queue** against 0 token metrics, in ~12-hourly bursts of several
  hundred per minute. Because a flapping link redelivers whatever was
  unsettled, that storm was also silently spending the redelivery budget of
  perfectly good tasks — see the dead-letter note above.
- `wes_eventmesh_consume_fallback_total{reason="queue_has_consumers"}` — G1
  refused to attach because something else is bound to the AMQP queue.

Do **not** page on a flat `delivery_total` alone. An idle system and a dead one
look identical in that counter — which is why the watchdog requires queue depth
> 0 *as well as* flat deliveries before calling a stall.

## Triage

**1. Which transport is actually running?**
`wes_eventmesh_consume_transport_rest` — 1.0 means REST. AMQP falls back to
REST transparently (by design, so intake never stops), so "configured for AMQP"
and "running AMQP" are different questions. `consume_fallback_total` says why.

**2. On REST: are you being rate-limited?**
```
ERROR ... Event Mesh consume failed: 429 (queue=.../sub-workflow.tasks)
```
The poll pool is `BROKER_CONSUMER_THREADS` (4) threads against the shared
consumption endpoint. A steady trickle of 429s is the poll pool being throttled;
the retry is transparent, but the throughput ceiling is real and it is the
reason the AMQP transport exists.

**3. On AMQP: check who else is bound.**
```
GET /hub/rest/api/v1/management/messaging/queues/<url-encoded-queue>
```
`consumerCount` is a **live** count, not a configured limit. Expect **1** — just
our link.

- **3 or 4** ⇒ REST consumer flows are still bound. SAP allocates 3 per
  REST-polled queue and releases them only after **30 minutes with no
  publishing**, which a task queue never has. The queues are `NON_EXCLUSIVE`, so
  the broker round-robins and whatever it hands a stopped flow is stranded.
  This should now be impossible — AMQP uses its own `-amqp` queue — so finding
  it means something is consuming the AMQP queue over REST.
- **0** ⇒ the link is not attached; a connectivity problem, not a stall.

**4. Is the link flapping rather than stalling?**
Look for `AMQP receiver (queue=…) ended after <n>s (attached=True): …`.
Repeated `AMQP FIRST delivery` lines for the same queue are the tell — each one
means a **fresh** bridge, so the previous one died. **No** `FIRST delivery` at
all, paired with `ended after 0.1s`, means the link is dying on its first
inbound frame and has never delivered anything.

If the reason is `unable to process all bytes: <consumed>, <offered>` — proton
consumed the first number and we handed it the second — the frame exceeded
proton's **input buffer**, not necessarily the negotiated frame size:

| limit | bounds | knob |
|---|---|---|
| `max_frame_size` | what the peer may send in one frame | `EVENT_MESH_AMQP_MAX_FRAME_BYTES` (default 1 MiB) |
| `transport.capacity()` | what we may hand proton in one `push` | none — `_feed` chunks to it automatically |

Both are handled in code, so this should not reappear. If it does, the peer is
sending frames larger than the buffer can ever assemble and `_feed` fails the
link with `AMQP input buffer stuck at 0 capacity` rather than desynchronise the
stream. Raise `EVENT_MESH_AMQP_MAX_FRAME_BYTES`.

**5. Check the DMQ.** `<queue>-dmq` filling means tasks are exhausting
`BROKER_MAX_REDELIVERY_COUNT`. Before assuming a poison payload, see below.

## Redelivery is also the backpressure rail

This is the thing that most easily gets misread here.

The executor signals "I am at capacity" by **not acking**: `main.py` raises on
`CONCURRENCY_LIMIT` / `WORKER_SATURATED` / `ADMISSION_FULL`, the ack is skipped,
and the broker re-offers the task once the lock TTL expires and a slot frees.
That is correct, healthy behaviour — and **every bounce burns a redelivery**.

So at a 60s lock TTL, `BROKER_MAX_REDELIVERY_COUNT = N` buys roughly *N minutes
of sustained saturation* before a perfectly healthy task is dead-lettered
instead. The default here is **50**, ten times the control plane's 5, precisely
because 5 would silently convert a normal busy period into lost work.

If you see the DMQ growing during a load spike, check `wes_active_tasks` and
`wes_concurrency_rejections_total` before treating the tasks as poisonous —
raise `BROKER_MAX_REDELIVERY_COUNT` or `MAX_CONCURRENT_TASKS` rather than
discarding them.

## Mitigation

Roll back to REST — one env var:

```
cf set-env ai-workflow-worker-executor EVENT_MESH_CONSUME_TRANSPORT rest
cf restart ai-workflow-worker-executor
```

No data is lost: the REST queue keeps its subscription and its own backlog, and
the poll loop picks it up.

**Do not delete and re-provision a queue.** Every message published while the
topic has no subscribed queue is discarded silently and the publisher still gets
HTTP 204.

## Tunables

| env | default | notes |
|---|---|---|
| `EVENT_MESH_CONSUME_TRANSPORT` | `amqp` | `rest` is the rollback |
| `EVENT_MESH_AMQP_PREFETCH` | 10 | messages held unacked per link — low on purpose (see backpressure above) |
| `EVENT_MESH_AMQP_STALL_CHECK_S` | 30 | how often the watchdog looks |
| `EVENT_MESH_AMQP_STALL_AFTER_S` | 90 | silence + waiting tasks before standing a link down |
| `EVENT_MESH_AMQP_TOKEN_MARGIN_S` | 300 | reconnect this long before the 12h token expires |
| `EVENT_MESH_AMQP_MAX_FRAME_BYTES` | 1 MiB | must exceed the largest task payload |
| `BROKER_MAX_REDELIVERY_COUNT` | 50 | redeliveries before dead-lettering |
| `BROKER_LOCK_DURATION_MS` | 60000 | message lock TTL |
| `BROKER_CONSUMER_THREADS` | 4 | REST poll pool size |

## The watchdog watches LINKS, not queues

`concurrency` opens that many AMQP receiver links onto the **same** queue. Two things
follow, and both were wrong until the per-link fix:

- **Every link is watched.** The bridge registry is keyed by `(queue, index)`. Keyed by
  queue, N links overwrote each other and only the last to register was ever checked — so
  N-1 could go silent unseen, which is exactly the failure this watchdog exists to catch,
  and `links_attached` reported 1 while N existed.
- **A reconnect resets that link's delivery baseline.** `deliveries` restarts at 0 on every
  new bridge. Compared against the previous bridge's lifetime total it reads as "no
  progress" forever, so on a queue with work waiting the watchdog would stand down a
  healthy, actively-delivering link — and its replacement would start from 0 again. That
  loop also flatlines `amqp_delivery_total`, i.e. the watchdog manufactures the exact
  symptoms it exists to detect. **It needed no concurrency: a 12-hour token rotation was
  enough.** The forced-reconnect streak deliberately survives the reset, so a queue that
  genuinely cannot deliver still widens its window instead of reconnect-storming.

Queue depth is read once per queue per pass, not once per link, so raising `concurrency`
does not multiply management-API calls.
