# Frontend — Gateway Admin console & Function nodes

**Status:** In progress — backend implemented; FE built as a feature inside the
`workflow-management` micro-frontend (`apps/frontend/apps/workflow-management`).
**Date:** 2026-06-27 (supersedes the 2026-06-14 "design only" draft)
**Scope:** `apps/frontend/apps/workflow-management` — a **new in-app feature**,
`src/features/gateway-admin/` (NOT a new app / federated remote / top-level folder).
**Backend companion:** `workflow-control-plane-service/docs/design/gateway-app-function-registry.md`

---

## 1. Context

The backend ships a **Gateway App → Function** registry. An internal developer registers an **App**
(internal/external target + encrypted credentials) and defines named **Functions** (method, path template,
input/output params). Each exposed function **auto-generates a `NodeDefinition`** that becomes a selectable
workflow node. There are two ways to populate an App's functions:

- **Manual add** — author functions one by one (name, method, path template, params).
- **Import the API doc (OpenAPI)** — set the App's `spec_url`, then **Sync**: the backend fetches the
  OpenAPI document and reconciles the App's functions to match it (create / update / delete).

Two FE deliverables:

- **FE-A — Gateway Admin console:** a self-service UI to CRUD Apps + Functions, **plus** the OpenAPI import/sync flow.
- **FE-B — Function nodes in the workflow editor:** consuming the auto-generated NodeDefinitions in the node
  palette (rides the generic node-definition/palette path; little gateway-specific work).

## 2. FE stack (as actually found in `workflow-management`)

> ⚠️ The 2026-06-14 draft described the **shell/common** stack (UI5 + `ldc-auto-form` + axios). The
> `workflow-management` remote, where this feature lives, uses a **different** stack. This is the real one:

- **React 19 + TypeScript**, **Rsbuild** module-federation remote (`@ldc/workflow-management`), mounted by the shell.
- **UI:** `@ldc/ui` — a shadcn/Radix component kit (`@ldc/ui/components/{button,input,select,dialog,label,switch,
  textarea,badge,tabs,card,table,spinner,...}`) + **Tailwind**. **No SAP UI5** in this app.
- **Icons:** `lucide-react`. **Toast:** `@ldc/ui/blocks/toast/toast`. **Confirm:** `useMessageBox()`.
- **Data:** **TanStack Query** via `@ldc/tanstack-query` (`useQuery`/`useMutation`/`queryKeyFactory`).
- **HTTP:** `@ldc/api-sdk` `APISdk` instance in `src/lib/api.ts` (`api`), base URL
  `PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE + "/api/v1"`. Responses are `{status, data}`-wrapped (read `res.data`).
- **State:** Zustand. **Validation:** Zod. **i18n:** `useLanguage()` → `t()`, keys in
  `src/locales/{en,jp}/workflow-management.json`.
- **Routing:** `src/remote-app-routes.tsx` (react-router). **Nav:** `src/components/layouts/side-bar/app-sidebar.tsx`.
- **Existing CRUD pattern to mirror:** the **node-definitions** feature
  (`src/features/node-definitions/`) — list page + create modal + builder, TanStack hooks under
  `hooks/apis/`, types under `types/`. Copy its shape.

## 3. FE-A — Gateway Admin console (new feature)

Location: **`src/features/gateway-admin/`** inside `workflow-management` (sibling of `node-definitions`,
`workflows`). Pages live in `src/pages/`, routes in `src/remote-app-routes.tsx`, nav entry in `app-sidebar.tsx`.

### 3.1 API service (TanStack hooks)
`src/features/gateway-admin/hooks/apis/gateway-apps.ts` and `gateway-functions.ts`, using the shared `api`
(`@/lib/api`). Real backend routes (under `/api/v1`):

| Action | Method + path |
|---|---|
| List apps | `GET /gateway/apps?name_contains=&include_inactive=&skip=&top=` |
| Get app | `GET /gateway/apps/{name}` |
| Create app | `POST /gateway/apps` |
| Update app | `PATCH /gateway/apps/{name}` — **requires `If-Match: <version>` header** |
| Delete app | `DELETE /gateway/apps/{name}` (soft delete) |
| Test app | `POST /gateway/apps/{name}/test` `{timeout_s}` → `{reachable,status_code,latency_ms,upstream_error}` |
| **Sync OpenAPI** | `POST /gateway/apps/{name}/sync?force=` → `{created,updated,deleted,unchanged,failed,skipped}` |
| List functions | `GET /gateway/apps/{app}/functions?...` |
| Get / Create / Update / Delete function | `.../functions[/{name}]` (apps are keyed by **name**, functions by **name within the app**) |

Types in `src/features/gateway-admin/types/gateway.ts` (`IGatewayApp`, `IGatewayFunction`, payloads, projections).

### 3.2 Screens (master-detail, two routes)
1. **Apps list** — `/gateway/apps`. Card/row grid: name, kind (internal/external), auth_type, active, version.
   Search box; "Create App" → `GatewayAppModal`.
2. **App detail** — `/gateway/apps/:appName`. App summary + actions: **Edit**, **Test**, **Delete**, and the
   **OpenAPI import** panel (§3.4). Below it, the **Functions** list with "Add Function" (manual, §3.5).
3. **App form** (`GatewayAppModal`, create/edit) — `name`, `kind` (Select internal|external),
   `base_url` (internal) / `allowed_hosts` (external, array rows), `auth_type` (Select), conditional
   **credential fields** (§3.6), and **`spec_url`** (§3.4). Edit sends `If-Match` from the loaded version.
4. **Function form** (`GatewayFunctionModal`, create/edit) — `name`, `description`, `method` (Select),
   `path_template`, `timeout_ms`, icon/color/tags, **plus the param builder** (§3.5) for `input_params` /
   `output_fields`.

### 3.3 Manual add
Both Apps and Functions are fully hand-authorable via the modals above — no spec required. This is the default
path and the only one that works for an App with no OpenAPI document.

### 3.4 OpenAPI / API-doc import (the new flow)
- The App form has a **`spec_url`** field (the OpenAPI document URL). Empty = no sync.
- App detail shows a **"Sync from OpenAPI"** action → `POST /gateway/apps/{name}/sync` (and a
  **"Force re-sync"** → `?force=true` to bypass the unchanged-hash short-circuit).
- On success, surface the reconcile counts (`created / updated / deleted / unchanged / failed`) via a
  `MessageStrip`/toast; if `skipped` (spec body unchanged), say so. Refetch the functions list afterward.
- After a sync, imported functions appear in the same Functions list and remain individually editable.

### 3.5 Param builder (the one genuinely new widget)
`components/functions/param-schema-builder.tsx` — a repeatable rows editor used twice:
- **input_params** rows: `{key, type, location, required, label}` where `type ∈ {string,number,boolean,object,array}`
  and `location ∈ {path,query,header,body}`.
- **output_fields** rows: `{key, type, label}`.
This authors the function's params, which the backend stores and later ships as the generated NodeDefinition's
form. Path params declared here must match `{placeholders}` in `path_template`.

### 3.6 Secret handling (critical)
Credentials are **write-only**. The GET projection returns only
`auth_summary {credential_present, key_version, last_rotated_at}` — never plaintext. Auth shapes:
`bearer → {token}`, `api_key_header → {header, value}`, `none → {}` (`oauth_cc` is **not implemented** backend-side
— hide or disable it). The form renders a **"Set / Rotate credential"** affordance (sends `auth_config` only
when the user types a new secret); never display or round-trip the stored secret.

### 3.7 Role gating (follow-up)
Backend admin-role gating is a P5 follow-up and there is **no auth on these endpoints yet** (see the backend
security review). When roles land, hide the nav entry and guard the routes for non-admins. Until then the
console is reachable by anyone who can reach the control plane — do not expose it publicly.

## 4. FE-B — Function nodes in the workflow editor
Each exposed function auto-generates a **NodeDefinition**, served by the existing node catalog and rendered by
the existing palette/builder path — so gateway-function nodes appear automatically once published; no
gateway-specific editor work is required beyond what node-definitions already does.

## 5. Files (this feature)
- **Create** under `src/features/gateway-admin/`: `types/gateway.ts`, `hooks/apis/gateway-apps.ts`,
  `hooks/apis/gateway-functions.ts`, `components/apps/{gateway-app-list,gateway-app-modal}.tsx`,
  `components/functions/{gateway-function-list,gateway-function-modal,param-schema-builder}.tsx`.
- **Create** pages: `src/pages/{gateway-apps,gateway-app-detail}.tsx`.
- **Edit**: `src/remote-app-routes.tsx` (+2 routes), `src/components/layouts/side-bar/app-sidebar.tsx` (nav entry),
  `src/locales/{en,jp}/workflow-management.json` (`gateway.*` keys).

## 6. Phasing & verification
- **FE-P1:** Apps CRUD + credential set + Test. **FE-P2:** Functions CRUD + param builder. **FE-P3:** OpenAPI
  import/sync; confirm imported functions appear and a published one shows in the node catalog.
- **Verify locally:** serve the shell + `workflow-management` remote, point
  `PUBLIC_URL_AI_WORKFLOW_CONTROL_PLANE` at the running CP, open **Gateway** in the sidebar → create an
  internal App (e.g. CP `/api/v1`, auth none) → add a Function (GET `/healthz`) → **Test** → or set a `spec_url`
  and **Sync** → confirm functions populate.

## 7. Open items
- No backend auth/tenant scoping yet (tracked in the backend security review) — console must not be public.
- `last_synced_at` is stored backend-side but **not yet in the GET projection**; add it to surface "last synced".
- Canvas/palette consumer is tracked separately; gateway nodes are exercised once it lands.
