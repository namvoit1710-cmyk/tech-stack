# Gateway Admin & OpenAPI Sync — UI/UX Guide & Design

App: **workflow-management** (`@ldc/workflow-management`)
Status: **Design (greenfield)** — no gateway UI exists yet. Backend is shipped & live-verified.
Proposed feature dir: `src/features/gateways/` · Pages: `src/pages/gateways.tsx`, `src/pages/gateway-app-detail.tsx`
Backend: `workflow-control-plane-service` `/api/v1/gateway/*` (reached via `src/lib/api.ts`)

Audience: anyone designing or building the Gateway admin UI. Goal of the feature: let an
**integration admin** register an upstream app, **point it at an OpenAPI spec**, and have its
operations imported & kept in sync as gateway functions — without hand-authoring each function.

> Supersedes `apps/backend/ai-workflow-management/workflow/docs/design/frontend/gateway-admin-and-function-nodes.md`,
> which assumed a stale React 18 + SAP UI5 stack. This doc targets the live stack: **React 19 +
> Rsbuild + `@ldc/ui` + `@ldc/autoform` + `@ldc/data-table` + TanStack Query**.

---

## 1. Who uses this and what they're trying to do

| Persona | Goal | Pain if we get it wrong |
|---|---|---|
| **Integration admin** (semi-tech) | Register "Partner CRM", store its bearer token, point at its `openapi.json`, and get all its endpoints as functions. | Hand-creates every endpoint by typing method/path/params; drifts out of date the moment the API changes. |
| **Platform operator** | Keep the registry honest: re-sync after the upstream ships, see what changed, spot import failures. | Can't tell if functions are current; a bad upstream silently wipes the registry. |
| **Workflow builder** (downstream) | Drop a synced function on the canvas as a node. | *(Out of scope here — see §9. Covered by the node palette work.)* |

The feature succeeds when an admin registers an app + spec **once** and never hand-edits a
function again — re-sync does it, and the UI makes *what changed* and *what failed* obvious.

---

## 2. Information architecture

Top-level **"Gateways"** nav item (sidebar `src/components/layouts/side-bar/nav-main.tsx`),
mirroring how Node Definitions is a peer of Workflows. Two routes in `src/remote-app-routes.tsx`:

```tsx
<Route path="gateways" element={<GatewaysPage />} />            // Apps list
<Route path="gateways/:appName" element={<GatewayAppDetailPage />} />  // App detail (tabbed)
```

```
Gateways (list of apps)
└── {appName}  (detail, tabbed)
      ├── Overview     — target, auth summary, description, version
      ├── Functions    — list + create/edit/delete (editable; sync may overwrite)
      ├── Sync         — spec_url, last-synced, "Sync now", result, guard/force   ← centerpiece
      └── Danger       — deactivate / delete
```

---

## 3. Screen 1 — Apps list (`/gateways`)

A `@ldc/data-table` (model on `features/workflows/.../workflow-list.tsx`), server-paged via `nuqs`.

| Column | Source | Render |
|---|---|---|
| **Name** | `name` | link → `/gateways/{name}` |
| **Kind** | `kind` | badge `Internal` / `External` |
| **Auth** | `auth_type` | badge `none` / `bearer` / `api_key_header` / `oauth_cc` |
| **Sync** | `spec_url`, `last_synced_at` | none → "—" · else "Synced {relative}" + a health dot (green ok / amber stale / grey never) |
| **Active** | `is_active` | dot / muted row if inactive |
| **Version** | `version` | small mono — also the CAS token for edits |
| _actions_ | | **Sync now** (if `spec_url`), **Edit**, **Delete** (confirm) — fixed-right |

- **Header:** title + **"Register app"** → App form (§4) in a sheet/dialog.
- **Filters:** search (`name_contains`), `include_inactive` toggle, pagination (`skip`/`top`, top ≤ 200) — all via the list query params.
- **Empty state:** "No gateway apps yet. Register an app to expose its functions as workflow nodes." + primary Register button.
- **Loading:** `LoadingOverlay` over the table on first load; subtle top loader on background refetch (no layout shift). Mutations invalidate the apps query + toast.

```
┌ Gateways ───────────────────────────────────────────[ Register app ]┐
│  search [ partner            ]   ☐ Show inactive                      │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Name          Kind      Auth     Sync              Active  Ver  ⋯ │ │
│ │ Partner CRM   External  bearer   ● Synced 5m ago     ●     12   ⟳✎🗑│ │
│ │ Billing API   Internal  none     ○ never             ●      1   ⟳✎🗑│ │
│ │ Legacy SOAP   Internal  api_key  ◐ Synced 9d ago     ●      3   ⟳✎🗑│ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Screen 2 — App form (sheet/dialog, `@ldc/autoform`)

Config-driven `ISchema` (per the project rule "check autoform first"); conditional fields use
`condition: { fieldKey, conditionValue, action: "invisible" }` (`useCheckCondition`).

| Field | Control | Visible when | Maps to |
|---|---|---|---|
| `name` | InputControl | always | `name` |
| `kind` | SelectControl `internal\|external` | always | `kind` |
| `base_url` | InputControl | `kind = internal` | `base_url` |
| `allowed_hosts` | array / tags | `kind = external` | `allowed_hosts[]` |
| `auth_type` | SelectControl `none\|bearer\|api_key_header\|oauth_cc` | always | `auth_type` |
| `token` | PasswordControl | `auth_type = bearer` | `auth_config.token` |
| `header` + `value` | Input + Password | `auth_type = api_key_header` | `auth_config.{header,value}` |
| `token_url` / `client_id` / `client_secret` | Input + Password | `auth_type = oauth_cc` | `auth_config.{…}` |
| `description` | TextareaControl | always | `description` |
| **`spec_url`** | InputControl (url) | always | `spec_url` — the OpenAPI source that powers Sync |

**Credentials are write-only (critical).** GET returns only
`auth_summary { credential_present, key_version, last_rotated_at }` — **never** the secret. On
**edit**, render a read state "🔒 Credential set · v{key_version} · rotated {relative}" plus a
**"Set / Rotate credential"** toggle that reveals the empty secret inputs only when the user
intends to replace it. Never prefill or round-trip a secret value.

**Save semantics:**
- Create → `POST /gateway/apps`.
- Edit → `PATCH /gateway/apps/{name}` with header **`If-Match: {version}`** (CAS). On `409`
  (`OPTIMISTIC_CONCURRENCY_CONFLICT` / `PRECONDITION_REQUIRED`) → toast "This app was changed
  elsewhere — reload and retry," refetch, keep the form open.
- Validation surfaces the backend `VALIDATION_ERROR` (e.g. external app needs ≥1 allowed host;
  bearer needs a token) inline; don't swallow it.

> **`oauth_cc` caveat:** the backend accepts the config but **token exchange is not implemented**
> (`materialize_auth_header` raises). If a function on an oauth_cc app is resolved/synced with a
> protected spec, it fails. The form should show an inline "Not yet supported at runtime" note on
> the `oauth_cc` option until the backend lands it.

---

## 5. Screen 3 — App detail (`/gateways/:appName`), tabbed

Mirrors the node-definition-builder tab pattern (`src/pages/node-definition-builder.tsx`).

### 5.1 Overview
Read view of kind, target (`base_url` or `allowed_hosts`), `auth_summary`, description, version,
created/updated. **Edit** opens the App form (§4).

### 5.2 Functions
`@ldc/data-table`: **Name**, **Method** (badge), **path_template** (mono), **Active**, **Version**.
Full **create / edit / delete** (this app's decision: functions stay manually editable).

- **Sync-overwrite warning banner** when the app has a `spec_url`:
  > ⚠️ Functions on this app are synced from its OpenAPI spec. Manual edits here **will be
  > overwritten on the next sync**. Edit the upstream spec, or remove the spec URL to manage
  > functions by hand.
- **Function form** (`@ldc/autoform`): `name`, `method` (Select), `path_template`, `timeout_ms`,
  `icon`/`color`/`tags`, plus a **param-schema builder** — reuse the node-definitions
  `schema-field-list.tsx` / `schema-field-editor.tsx` row editor:
  - `input_params` rows: **key**, **type** (Select string/number/boolean/object/array),
    **location** (Select path/query/header/body), **required** (toggle). Humanize type labels
    ("Text", "Number", "Yes/No", …) as node-definitions does.
  - `output_fields` rows: key, type, label.
  - Mirror the backend invariant in the UI: every `{placeholder}` in `path_template` needs a
    matching **path** param (and vice-versa) — validate before submit so the create/PATCH doesn't
    bounce with `VALIDATION_ERROR`.
- Create/PATCH (with `If-Match`) / DELETE map to `/gateway/apps/{app}/functions[/{name}]`.

### 5.3 Sync — the centerpiece
The reason the feature exists. Lives in its own tab so the state model has room.

```
┌ Sync ────────────────────────────────────────────────────────────────┐
│ Spec URL   https://api.partner.com/openapi.json            [ Edit ]   │
│ Last sync  ● 5 minutes ago        Auto-sync: every 1h (1 instance)*   │
│                                                       [ ⟳ Sync now ]  │
│ ┌── Last result ───────────────────────────────────────────────────┐ │
│ │  +12 created   ~3 updated   −1 deleted   45 unchanged             │ │
│ │  ⚠ 2 failed  ›  show details                                      │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
  * shown read-only iff the backend exposes the poller state; else omit.
```

- **Sync now** → `POST /gateway/apps/{name}/sync`. Button shows spinner + disables while in-flight
  (use `useMutation` `isPending`, not a layout-shifting spinner swap).
- Render the `SyncGatewayAppOpenapiResult` as the **state model** below. After a successful sync,
  invalidate the functions query so the Functions tab reflects it; update "Last sync".

### 5.4 Danger
Deactivate (soft) / delete the app, behind the shared confirm `messagebox-provider`.

---

## 6. The sync state model (the differentiator)

Every `POST …/sync` response is one of seven UI states. The doc treats each distinctly — this is
the heart of the UX. All seven were **verified live** against the control-plane (:8001).

| State | Backend signal | UI treatment |
|---|---|---|
| **idle** | — | "Never synced" / last result card |
| **syncing** | request in-flight | button spinner + disabled; functions list dimmed |
| **success** | `error:""`, `skipped:false` | green result card with chips: `+created ~updated −deleted N unchanged`; toast "Synced" |
| **skipped** | `skipped:true` | neutral note: "Spec unchanged since last sync — nothing to import." (offer **Force sync**) |
| **partial** | `failed > 0` | success chips **plus** amber "⚠ N operations couldn't be imported" → expandable list (op name + reason, e.g. unsupported path param). Don't hide it behind the green. |
| **fetch error** | `error_code: GATEWAY_SPEC_FETCH_FAILED` (502) | red card: "Couldn't reach the spec at {url}" + the detail; retry button. |
| **invalid spec** | `error_code: GATEWAY_SPEC_INVALID` (422) | red card: "The spec couldn't be parsed" + detail. |
| **guard** | `error_code: GATEWAY_SPEC_INVALID`, message "refusing to delete … N active function(s)" | **destructive confirm**, see below. |

**The empty-spec guard → destructive confirm (the key safety UX).** The backend refuses to
soft-delete an app's whole function set when the spec parses to **0 operations** (a transient
upstream blip), unless `force=true`. The UI surfaces this as an explicit, scary confirm — never an
auto-destroy:

```
┌ Spec has no operations ───────────────────────────────────────────────┐
│ The OpenAPI spec at this URL now defines 0 operations, but this app    │
│ has 47 active functions.                                               │
│                                                                        │
│ This usually means the upstream is mid-deploy, behind an auth wall, or │
│ the URL is wrong. Syncing now would DELETE all 47 functions.           │
│                                                                        │
│              [ Cancel ]            [ Force sync — delete 47 ]  (danger) │
└────────────────────────────────────────────────────────────────────────┘
```
"Force sync" → `POST …/sync?force=true`. **Force** is also the explicit path to a legitimate
"shrink to zero" and to re-run a `skipped` (unchanged) sync.

---

## 7. UX principles to keep (and the rationale)

1. **Make "what changed" loud.** A sync's value is the diff — show created/updated/deleted/unchanged
   as first-class chips, never a bare "Done."
2. **Never hide failures behind success.** `failed > 0` gets its own amber row with reasons. A green
   "+12 created" that quietly dropped 2 ops is the trap to avoid (cf. the node-definition modal's
   silent `JSON.parse` catch in the sibling doc §2B).
3. **Destroy only on explicit intent.** The empty-spec guard maps to a danger-styled confirm with a
   real count ("delete 47"), not a toast. `force` is opt-in.
4. **Secrets are write-only, always.** Show `auth_summary`, never the value; "Set / Rotate" reveals
   empty inputs only on intent. No prefilled, no round-tripped secret.
5. **Respect CAS.** Every edit carries `If-Match: {version}`; a 409 is a friendly "changed
   elsewhere — reload," not a stack trace.
6. **Tell the truth about sync ownership.** When `spec_url` is set, the Functions tab says manual
   edits get overwritten — honest about who owns the data.
7. **Humanize vocabulary** (shared with node-definitions): "Text/Number/Yes-No/List" for param
   types, "Bearer token" for `bearer`, "Last synced 5m ago" for the timestamp.
8. **No hard-coded copy.** All strings via `useLanguage()` / `@ldc/i18n` under a `gateways.*`
   namespace in `src/locales/{en,vi}/workflow-management.json`.

---

## 8. Reuse map (don't reinvent)

| Need | Reuse | Path |
|---|---|---|
| List + paging | `@ldc/data-table` + `nuqs` | `features/workflows/.../workflow-list.tsx` |
| Forms + conditional fields | `@ldc/autoform` `Builder`, `condition`, `SelectControl`, `PasswordControl` | `apps/frontend/packages/autoform/...` |
| Param-schema rows | node-definitions schema editor | `features/node-definitions/.../schema-field-{list,editor}.tsx` |
| Tabbed detail | node-definition builder | `src/pages/node-definition-builder.tsx` |
| Confirm dialogs | messagebox provider | `src/components/providers/messagebox-provider.tsx` |
| API client (→ control-plane `/api/v1`) | `api` (`@ldc/api-sdk`) | `src/lib/api.ts` (no env change needed) |
| Query/mutation + keys | `@ldc/tanstack-query`, query-key factory | model on `features/node-definitions/hooks/apis/node-definitions.ts` |
| i18n | `useLanguage()` | `src/hooks/use-language.ts`, `src/locales/**` |

**New code (thin):** `src/features/gateways/{hooks/apis/gateways.ts, types/, config/ (ISchema), components/}`,
`src/pages/{gateways,gateway-app-detail}.tsx`, route + nav entries, `gateways.*` i18n keys.

`hooks/apis/gateways.ts` shape (mirrors node-definitions): `useGatewayAppList`, `useGatewayApp`,
`useCreateGatewayApp`, `useUpdateGatewayApp` (sends `If-Match`), `useDeleteGatewayApp`,
`useSyncGatewayApp` (`{name, force}`), `useTestGatewayApp`, and the function CRUD equivalents.
Define `gatewayKey` factory first so mutations invalidate apps/functions precisely.

---

## 9. Out of scope (call it out, don't silently skip)

- **Node consumption (palette/canvas).** Synced functions becoming draggable nodes in the workflow
  editor is a separate, greenfield concern (the backend P3 NodeDefinition bridge isn't built yet,
  per the gateway research). This doc is admin-side only.
- **RBAC / role-gating.** The backend write/resolve endpoints are currently trust-the-network
  (admin gate = unbuilt P5). The UI can ship without a role guard now; add a nav/page guard when
  `IUser.roles` and the backend gate land.
- **Autoform action-engine pipelines.** Not needed — direct `useMutation` calls per the
  node-definitions pattern are simpler and sufficient here.

---

## 10. Build backlog (prioritized, lazy-first)

| # | Item | Why | Effort |
|---|---|---|---|
| 1 | `features/gateways` scaffold: route, nav item, Apps list page + `useGatewayAppList` | the spine; everything hangs off it | S |
| 2 | App form (autoform ISchema, conditional fields, write-only credential affordance) | register apps + set `spec_url` | M |
| 3 | App detail shell + Overview + Sync tab with the **7-state result + guard/force** | the centerpiece; highest user value | M |
| 4 | Functions tab (table + form + param-schema rows + overwrite banner) | manual management + transparency | M |
| 5 | `gateways.*` i18n keys (en + vi, kept in sync) | no hard-coded copy | S |
| 6 | Test action (`POST /{name}/test`) surfaced in Overview | reachability check before relying on it | S |
| 7 | Sync history / "last result" persistence beyond the in-memory mutation result | operators want a trail; needs a backend list or local cache | M (backend) |

> 1–6 are FE-only against the shipped backend. 7 needs a backend affordance.

---

## 11. Accessibility / interaction notes

- Autoform `SelectControl`s (kind, auth_type, method, param type/location): keep visible focus
  rings + associated `<label>`s.
- The empty-spec **destructive confirm** must be keyboard-complete (Esc cancels; focus lands on the
  safe "Cancel", not "Force"); the danger button needs a non-color cue (icon/label "delete N"), not
  red alone.
- Sync result chips: don't convey created/failed by color only — pair with `+`/`⚠` glyphs and text.
- Write-only credential state: the "🔒 Credential set" text must be a real label, not just an icon,
  for screen readers; the "Set / Rotate" control announces that it clears/replaces the secret.
- Loading: dim + top loader (no spinner-swapping that shifts layout), matching the node-definitions
  list behavior.
