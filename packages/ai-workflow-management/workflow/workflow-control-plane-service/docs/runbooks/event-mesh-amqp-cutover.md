# Runbook — cutting the consume transport over to AMQP

## What this buys

`EVENT_MESH_POLL_INTERVAL_S` is 0.5 s, and a REST pickup costs **2 × poll = ~1.0 s per
broker hop**. A run with *n* nodes costs roughly **2n + 1** hops, so a 5-node run spends
about **11 s** waiting for polls against ~4.6 s of actual work — roughly 70 % of wall
clock. AMQP push removes that floor.

It removes the floor, **not the tail**: the measured executor-result → continue window was
1.75–13.88 s, of which ~1.0 s per hop is floor and the rest is variance AMQP does not
address.

## Why the first three attempts failed

Enabling `amqp` on tenant-1 on 2026-08-05 hung every run, three times.

SAP allocates **3 broker-side consumer flows per REST-polled queue**, and stopping the
poller does **not** release them — they expire only after 30 minutes with *no publishing*,
which a hot queue never has. Queues are `NON_EXCLUSIVE`, so the broker round-robins, and
an AMQP link attaching to the same queue became a 4th consumer. Whatever round-robin
handed to a stopped flow was stranded — measured on a scratch queue: 20 published, 17
delivered, **3 stuck `unacked` forever**. A run only needs its `StartRunAction` stranded
to hang, which is why every node sat `NOT_STARTED`.

Every earlier probe used a *fresh* queue (`consumerCount = 0`), so the AMQP link was the
only consumer — which is exactly why they all scored 160/160 while production delivered
nothing.

**This is now structurally prevented**: the AMQP transport uses its own `<queue>-amqp`
queue (P1), and the consumer refuses to attach to a queue that already has consumers (G1).

## The cutover — make before break

There is nothing to delete and no window where messages go missing.

**1. Deploy.** The new queues are provisioned automatically on the first start in `amqp`
mode: `subscribe` creates `<queue>-amqp` + its subscription, and it begins spooling
immediately while the current REST queues keep being consumed.

**2. Flip one environment.** Dev first.
```
cf set-env <app> EVENT_MESH_CONSUME_TRANSPORT amqp
cf restart <app>
```
The consumer switch is atomic at restart. During the restart nothing is consumed but
**everything spools** — backlog, not loss.

**3. Verify before trusting it.**
- [ ] every queue logs `AMQP FIRST delivery (queue=…)`
- [ ] `consumerCount == 1` on each `-amqp` queue — only us
- [ ] `eventmesh.consume.transport.rest` is 0.0, `eventmesh.amqp.links.attached` equals
      the queue count
- [ ] `eventmesh.amqp.links.stalled` stays 0 through a full run
- [ ] a 10-node run completes; queue depth returns to 0
- [ ] kill the WebSocket mid-run → the watchdog reconnects and the run still completes
- [ ] a poison message lands in `<queue>-dmq` and does not loop
- [ ] no `eventmesh.consume.fallback{reason="queue_has_consumers"}`

**4. Soak 48 h on dev**, then repeat on tenant-1.

**5. Retire the REST queues** only once tenant-1 is green for an agreed window: drain each
old queue via REST once, then drop its subscription and delete it. Keep the REST path as
documented break-glass — on its own queue.

## Rolling back

One env var, and it has always worked:
```
cf set-env <app> EVENT_MESH_CONSUME_TRANSPORT rest
cf restart <app>
```
All three tenant-1 rollbacks drained the backlog to COMPLETED with no data loss.

Note the abandoned `-amqp` queues stay subscribed and keep accumulating. That is
deliberate — unsubscribing them could break another instance that is still on AMQP — but
it means **their depth must be watched and drained** before the next attach.

## Never do this

**Do not delete and re-provision a queue to "clean it up".** Every message published while
the topic has no subscribed queue is discarded, and the publisher still gets **HTTP 204**
(measured: 5 published into the gap, 0 arrived, 5 × 204). There is no error, no log, and
no way to tell afterwards. The management PUT is an upsert, so queue config can be changed
in place — 201 creates, 200 updates.

## Related

- `event-mesh-amqp-liveness.md` — the silent-link alert and triage
- `event-mesh-consume-transport.md` — the REST fallback gauge
- `event-mesh-dlq.md` — publisher-side DLQ (distinct from the per-queue DMQ)
