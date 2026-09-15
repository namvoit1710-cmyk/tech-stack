# SA-2570 — Workflow owner & creator

A workflow now records **who created it** (immutable) and **who owns it**
(reassignable), and there is a UI to change the owner. Builds on SA-2528
(Keycloak SSO), which gives a server-verified caller identity.

## Data model

- `AIW_WORKFLOWS.CREATED_BY` / `UPDATED_BY` — existed (migration 033) but were
  never persisted by the repo; SA-2571 wires them through save/read.
- `AIW_WORKFLOWS.OWNER` (**new**, migration 064 + hdbtable) — the reassignable
  owner. Stored as the owner's id (Keycloak `sub`).

No local users table: **users come from Keycloak** (central identity).

## Identity

`resolve_actor(request)` (auth_dependencies) is the single source of the verified
caller for attribution:

- `AUTH_ENABLED=false` → the legacy `X-User` header (default `system`).
- `AUTH_ENABLED=true` → the BFF session cookie, else a forwarded
  `Authorization: Bearer` JWT.

It never trusts the request body, so a Bearer/M2M caller cannot spoof the owner.

## Behaviour

- **Create** (`POST /workflows`) stamps `CREATED_BY` = the verified caller and
  `OWNER` = same (owner defaults to the creator). `UPDATED_BY` is stamped on
  every save.
- **Users (owner picker)** come from **Keycloak** — `GET /users` calls the
  Keycloak Admin API (`GET /admin/realms/{realm}/users`) via the confidential
  client's service account (`KeycloakAdminClient`, 60s cache). This is the
  central-identity direction: Keycloak owns *who* the users are (and, later,
  roles + permissions). Requires the service account to hold the
  `realm-management` role `view-users` (or `query-users`); without it the call
  403s and `GET /users` degrades to `[]`. Service-account users are filtered out.
  The FE calls `/users` **only when the Choose-owner modal opens** — not on list
  load.
- **Reassign** (`PUT /workflows/{id}/owner`, body `{ "owner": "<user-id>" }`):
  **any authenticated caller** may set the owner — access control is intentionally
  not enforced here. Permissions will be defined centrally on Keycloak (roles /
  policies) later. The caller is still recorded as `updated_by`.
- **Read**: `created_by`, `updated_by`, `owner` are returned on the workflow
  detail, list, create and update responses; the **list** also returns
  `owner_name` (owner id resolved to a display name server-side, via the cached
  Keycloak client) so the FE list shows names without a `/users` call.

## Frontend

No new page — the owner picker hangs off the existing per-row **⋯** menu on the
workflow list (`Choose owner` → `workflow-choose-owner-modal`), following the
rename-modal pattern. The list resolves owner ids to names via `GET /users`.

## Testing

- `tests/unit/test_sa2570_workflow_owner.py` — create stamps creator/owner from
  the verified caller (UC-1), auth-off fallback (UC-7), reassign by owner (UC-4),
  403 for non-owner + admin override (UC-5), legacy-claim, Keycloak-backed user
  list + degrade-to-empty, admin URL derivation, `is_admin` detection.
- Deploy note: tenant-1 runs with `HANA_RUN_MIGRATIONS=false`, so the new `OWNER`
  column ships via the HDI `.hdbtable` artefact (`OWNER` on
  `AIW_WORKFLOWS.hdbtable`); local dev with `HANA_RUN_MIGRATIONS=true` applies
  migration 064.
- Keycloak one-time setup: grant the `gateway-bff-fm` service account the
  `realm-management` client role `view-users` (Clients → gateway-bff-fm →
  Service account roles → Assign role → filter by clients → `view-users`).
