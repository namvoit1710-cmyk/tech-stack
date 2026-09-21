# Agent Registry — RBAC v1 (operator & deploy notes)

Dynamic role/permission RBAC. Roles and permissions are **rows** (not enums); guards check
**permission codes**, so role capabilities are changed by editing `role_permissions`, never by
shipping code. This document covers the parts an operator must know: the seed identity, the
two runtime flags, and how the schema is provisioned.

## Schema provisioning — `alembic`, not `create_all` (D26)

The schema is owned by **alembic migrations**. Bring a database up to date with:

```bash
alembic upgrade head          # 20260716_01 (base RBAC + seed) → 20260716_02 (contract)
```

The application does **not** create tables on startup by default. `lifespan` gates
`db_factory.create_tables()` behind the `DB_AUTO_CREATE` flag (default **off**):

| `DB_AUTO_CREATE` | Startup behaviour | Use when |
|---|---|---|
| `false` (default) | Skips `create_all()`; relies on `alembic upgrade`. | **Every real environment** (local HANA, QA, prod). |
| `true` | Runs `create_all()`. | A throwaway/ephemeral DB with no migrations only. |

**Why off by default:** on an alembic-migrated **HANA** schema, SQLAlchemy's schema-scoped
existence check misfires and re-issues `CREATE TABLE agents`, which HANA rejects as a duplicate
(`cannot use duplicate table name: AGENTS`) — startup then fails. Leaving `create_all()` off makes
the service boot cleanly on the correct (migrated) path. See finding D26.

## Seed identity — the bootstrap super_admin

Migration `20260716_01` seeds, idempotently:

- **30 permission codes** (app-defined; the UI never creates codes).
- **3 built-in roles**: `super_admin` (`is_super`, `is_system`), `admin` (`is_system`),
  `user` (`is_system`).
- **role_permissions**: `admin` → 21 codes, `user` → 2 codes (`agent.read`, `pool.read`).
  `super_admin` stores none — `is_super` implies **all** permissions at check time.
- **One super_admin user**: `external_id = 9e1a4f12-7a29-4f56-aa1a-f0651a777ab6`, `role_id =`
  super_admin.

**Behaviour before this user has ever logged in:** the row exists from the migration regardless of
login. On their first authenticated call, `ResolvePrincipal` matches the JWT `external_id` to this
seeded row and resolves a super_admin principal — no manual bootstrapping, no seed env var. Their
`email`/`name` fill in lazily on first access (mirror upsert). A fresh environment therefore has a
working super_admin the moment migrations are applied.

> To hand super_admin to a **different** identity, don't edit the seed — log in as the seeded
> super_admin and `PUT /users/{external_id}/role` to promote the target (anti-lockout rule R3
> prevents removing the last super_admin).

## Runtime flag — `TRUST_UNAUTHENTICATED_INTERNAL` (Phase 1 → Phase 2)

Controls how a **token-less** request is resolved:

- **Phase 1 (default `true`):** a request with no bearer token resolves to the built-in `system`
  principal, which bypasses permission checks. This is safe **only** because all external traffic is
  authenticated at the gateway; internal service-to-service calls (e.g. the orchestrator) arrive
  token-less and must keep working.
- **Phase 2 (`false`):** give internal services a **dedicated service-account token** (resolved like
  any user token) and flip this flag **off** — a token-less request then fails with `401` instead of
  silently becoming `system`.

**Flip checklist:** (1) issue a service-account identity + token, (2) configure internal callers to
send it, (3) set `TRUST_UNAUTHENTICATED_INTERNAL=false`, (4) confirm token-less calls now `401` and
the service account resolves with the intended permissions.

## Convention divergence — real FKs

Unlike some sibling services that keep association tables FK-less, RBAC v1 uses **real foreign keys**
with explicit ON DELETE rules (CASCADE on `role_permissions` / pool link tables; SET NULL on
`agent_pools.created_by`). Soft-deleted agents/pools (`status='deleted'`) do **not** fire cascade, so
the repositories clear the junction rows explicitly on soft delete.

## Capability matrix — coverage checklist

The default-role → capability matrix (below) is **executed**, not just documented, by
`tests/unit/test_rbac_capability_matrix.py`:

- **Route → guard code** — every guarded endpoint declares exactly the permission the matrix names
  (introspected via `require_permission(...).required_permissions`). Missing/extra/typo'd guard → fail.
- **Unguarded-by-design** — `GET /me` (authenticated only) and `PUT /users/{external_id}/role`
  (dynamic assign-role transition R1/R2/R3, checked in the use case) are asserted to carry **no**
  static guard.
- **No route slips through** — any agent/registry route not classified as guarded or
  deliberately-open fails the suite (guard-drift tripwire).
- **Seed → reachability** — crossing each route's codes with the seeded `admin`/`user` permission
  sets reproduces the matrix columns (`user` reaches reads only; `admin` reaches all but the
  RBAC-config tier; `super_admin`/`system` bypass).

| Endpoint group | required permission | super_admin | admin | user |
|---|---|:--:|:--:|:--:|
| Pool create/update/delete | `pool.create` / `pool.update` / `pool.delete` | ✅ | ✅ | ❌ |
| Pool attach/detach agent | `pool.agent.add` / `pool.agent.remove` | ✅ | ✅ | ❌ |
| Pool add/remove member | `pool.member.add` / `pool.member.remove` | ✅ | ✅ | ❌ |
| `GET /agent-pools`, `/{id}` | `pool.read` | all | all | own pools |
| Agent CRUD | `agent.create` / `agent.update` / `agent.delete` | ✅ | ✅ | ❌ |
| Agent lifecycle | `agent.publish` / `agent.unpublish` / `agent.activate` / `agent.deactivate` | ✅ | ✅ | ❌ |
| Agent reads `/all /{id} /by_ids /available /filter` | `agent.read` (+`agent.view_all` bypasses pool filter) | all | all | own-pool |
| `GET /users` | `user.read` | ✅ | ✅ | ❌ |
| `PUT /users/{id}/role` (admin tier) | `user.role.assign_admin_role` / `…unassign_admin_role` | ✅ | ✅ | ❌ |
| `PUT /users/{id}/role` (super_admin tier) | `user.role.assign_super_admin_role` / `…unassign_super_admin_role` | ✅ | ❌ | ❌ |
| `PUT /users/{id}/role` (custom tier) | `user.role.assign_custom_role` / `…unassign_custom_role` | ✅ | ❌ | ❌ |
| `GET /roles` | `role.read` | ✅ | ✅ | ❌ |
| Role create/update/set-perms/delete | `role.create` / `role.update` / `role.update_mapping_permission` / `role.remove` | ✅ | ❌ | ❌ |
| `GET /permissions` | `permission.view` | ✅ | ❌ | ❌ |
| `GET /me` | (authenticated) | ✅ | ✅ | ✅ |

`super_admin` (`is_super`) and the `system` principal bypass all permission checks. R3
(last-super_admin lockout) is a runtime rule → `409`.
