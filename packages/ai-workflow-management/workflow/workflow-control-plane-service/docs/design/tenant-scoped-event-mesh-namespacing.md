# Design — tenant-scoped Event Mesh topic namespacing + quotas (SA-1467)

Status: **ACCEPTED — sealed by Edgar Nguyen 2026-07-02** (Jira SA-1467 comment):
(1) **NET-NEW** implementation under SA-1467; (2) default tier = **O2
instance-per-tenant**, drawing from the provisioned 16-instance pool (see the
SA-1516 capacity doc) via BrokerRegistry tenant routes. Implementation slices
S1→S2→S3 below are now schedulable.

## Problem (risk T2)

Topics qualify as `{namespace}/{topic}` with one service-level namespace
(`event_mesh_names.py`). Every tenant's runs share `orchestration.actions`,
`realtime.events`, `workflow.results` and ONE broker quota/rate budget. A single
tenant's burst trips the shared 429 (SA-1470) or fills the publish buffer
(SA-1469) for ALL tenants; the broker cannot quota or rate-limit per tenant.

## Proposed target

**Qualification**: `{namespace}/{tenant}/{safe_topic}` — one extra level at the
existing single chokepoint (`EventMeshNames.qualify_topic/qualify_queue`), fed by
a tenant id resolved from run metadata (stamped at `POST /runs` by
`MetadataStampingPublisher`) — no per-call plumbing.

**Isolation options** (choose per tenant tier):
| Option | Mechanism | When |
|---|---|---|
| O1: sub-namespace on shared instance | `{ns}/{tenant}/{topic}` + app-level token bucket per tenant | many small tenants |
| O2: instance-per-tenant | bind a dedicated instance from the existing 16-instance pool (SA-1516) via a `BrokerPurpose`-style tenant route | large/noisy tenants |

**Quota/rate caps**: per-tenant token bucket at the publish chokepoint
(pre-`_enqueue`) + per-tenant consume budget; caps in config/DB, alerts tagged
`tenant=` (extends the SA-1469/70 metric families).

**Migration (dual-read)**: consumers bind legacy + tenant-scoped queues during
rollout; publishers cut over per tenant behind a flag; `_run_seq` semantics are
unchanged (per-run, orthogonal to tenancy). Rollback = flip the flag back —
legacy queues are still bound.

## Interactions (already landed)

- SA-1468: namespace is lock-safe + can be required at boot — tenant layer adds a
  SECOND level, never mutates the base namespace.
- SA-1469/71: spool + durable taxonomy are tenant-agnostic (envelope carries the
  qualified name — replay is tenant-correct for free).
- SA-1515 (open ADR): per-node-type queues compose — `{ns}/{tenant}/workflow.tasks.{type}`.
- SA-1516: budget doc's sharding table reserves the object-pool for worker/tenant planes.

## Effort & sequencing

Design-first (this doc) → sign-off → S1 tenant context + qualification (~3 pts) →
S2 token buckets + alerts (~3 pts) → S3 dual-read migration tooling (~5 pts).
Gate: multi-tenancy roadmap (per ticket).

## Decision record (AC met)

1. Net-new vs fold-in → **NET-NEW under SA-1467** (sealed 2026-07-02).
2. Default tier → **O2 instance-per-tenant** (O1 sub-namespace remains available
   for small tenants as an opt-down).

Refs: SA-1467 (parent SA-1302; blocked-by SA-1468 ✅ done; relates SA-1515).
