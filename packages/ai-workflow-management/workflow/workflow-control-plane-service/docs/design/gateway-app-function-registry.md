# Gateway App + Function Registry — self-service "expose an app & its functions"

**Status:** Proposed (design only — no implementation yet)
**Date:** 2026-06-14
**Scope:** workflow-control-plane-service (gateway registry, node-definition bridge), gateway-worker (proxy)
**Companion:** frontend implementation doc → `workflow/docs/design/frontend/gateway-admin-and-function-nodes.md`

---

## 1. Context & motivation

The platform already has a **v1 gateway**: a `gateway-worker` that proxies HTTP calls to internal/external
apps with injected credentials, backed by a stored, encrypted "service" registry:

- Entity `InternalServiceRegistration` → HANA table `AIW_GATEWAY_INTERNAL_SERVICES` (migration `038`).
- AES-256-GCM `credential_cipher` (`aes_gcm_credential_cipher.py`) with KEK rotation
  (`GATEWAY_REGISTRY_KEK_V<n>`); decrypt only at resolve/test.
- REST CRUD under `/api/v1/gateway/services`, worker-facing `POST /{name}/_resolve`, operator `POST /{name}/test`.
- `gateway-worker` functions `call_internal` (resolve a service by name → inject auth → proxy) and
  `call_external` (static `external.yaml` host allowlist + SSRF guard).

**Gaps relative to "stored, configurable, developer self-service to expose an app + functions":**

1. **No "App → Functions" model.** A service is a single `base_url` + auth; there are no named operations
   (method / path / param schema). Functions are not implemented.
2. **No self-service / discoverability.** Registration is an admin API call (trust-the-network, no
   `tenant_id`, no roles), and exposed calls are not discoverable — a workflow author hand-writes
   `service`/`endpoint`/`method` via a `_function_call` marker.
3. **Static external allowlist** lives in `worker/gateway-worker/config/external.yaml`, not stored/configurable.
4. **Disabled in non-mock runs** — when no KEK is set, `bootstrap._build_credential_cipher` returns `None`
   and all gateway-service features are skipped (`missing dependencies: {'credential_cipher'} — skipping`).

**Goal:** let an internal developer (via an admin UI + API) register an **App** and define named **Functions**
on it, and have each function become a **first-class, selectable workflow node** automatically — without the
author knowing URLs or credentials.

## 2. Decisions (confirmed)

- **Model:** App → named Functions; a function's target is an **internal** registered app *or* an
  **allowlisted external** URL — one registry unifying today's `call_internal` + `call_external`.
- **Consumption:** each exposed function **auto-generates a `NodeDefinition`**, so it appears in the workflow
  node palette as a custom node (reuses the NodeDefinition → enrich → dispatch path).
- **Governance:** **global** registry (no tenant scoping), **admin-only writes** + a self-service **UI**;
  add a minimal admin gate on write/resolve.

## 3. Data model (clean-break redesign of v1 — no compat shims, per house style)

> **The `name` contract (added 2026-08-12, learned the hard way).** On both entities `name`
> plays three roles at once: REST route key (`/gateway/apps/{name}`,
> `…/functions/{name}`), unique index key, and an `NVARCHAR(128)` column. It is therefore
> **not free text** — `app/layer1_domain/value_objects/gateway/resource_name.py` restricts it
> to `[A-Za-z0-9][A-Za-z0-9._-]*`, max 128, enforced on create *and* rename for apps and
> functions alike.
>
> Why it is a hard rule rather than a nicety: `%2F` is percent-decoded before routing and
> `{name}` matches one path segment, so a name containing `/` matches **no** route — `GET`,
> `PATCH` and `DELETE` all 404 and the row can never be read, renamed or deleted through the
> API again. tenant-1 accumulated **42 such rows** from an OWASP ZAP scan and they had to be
> deleted straight out of HANA. `openapi_import._slug` folds its output into the same set by
> construction, so the producer path cannot mint a rejectable name.
>
> Validation runs from `create` and the mutators only, **never on hydration** — a row that
> predates the rule must stay readable and deletable, or the rule rebuilds the trap it
> closes. `DELETE /gateway/apps/by-id/{id}` exists for exactly those rows.

**`GatewayApp`** — evolves `InternalServiceRegistration`; new entity `app/layer1_domain/entities/gateway_app.py`:

| field | notes |
|---|---|
| `id`, `name` (unique), `description`, `is_active`, `version` (CAS), audit | as today |
| `kind` | `internal` \| `external` |
| `base_url` | internal apps (proxy target) |
| `allowed_hosts` | external apps — moves `external.yaml` host allowlist into the DB |
| `auth_type` | reuse `AuthType` (bearer / api_key_header / oauth_cc / none) |
| `auth_config_encrypted` + `key_version` | reuse `EncryptedCredential` + `credential_cipher` unchanged |

**`GatewayFunction`** — new entity `app/layer1_domain/entities/gateway_function.py`, child of an app:

| field | notes |
|---|---|
| `id`, `app_id`, `name` (unique within app), `description`, `is_active`, `version`, audit | |
| `method` | GET / POST / … |
| `path_template` | e.g. `/orders/{order_id}` (internal) or templated URL (external) |
| `input_schema` | AutoForm-style param list; each param tagged `in: path\|query\|header\|body` |
| `output_schema` | declared response fields (drives the node's output mapping) |
| `timeout_ms`, `icon`, `color`, `tags` | palette presentation + runtime knobs |
| `node_definition_id` | back-link to the auto-generated NodeDefinition (for sync/cleanup) |

**Storage:** new migration creating `AIW_GATEWAY_APPS` (supersedes `038`) + `AIW_GATEWAY_FUNCTIONS`
(FK `APP_ID`, unique `(APP_ID, NAME)`); memory + HANA repos mirroring the existing
`gateway_service_registry_repo` pattern (CAS on `VERSION`, soft-delete). Reuse the cipher untouched.

## 4. The bridge: function → auto NodeDefinition → palette (the key reuse)

On **function create/update/publish**, upsert a `NodeDefinition` (deterministic id `gwfn-<function_id>`):

- `base_type = TASK`, `base_worker_id = "gateway-worker"`, `name/description/icon/color/tags` from the function.
- `input_schema` = the function's param schema → renders the node's form (existing AutoForm path).
- `locked_defaults = {"_gw_app": <app_id>, "_gw_function": <function_id>}` — the immutable routing identity the
  worker uses (the workflow author cannot repoint it).
- `input_mapping` = maps `{{$custom.<param>}}` form fields into the gateway call args object.
- `output_mapping` = maps the gateway response into the function's declared `output_schema` fields.

This reuses, unchanged:

- `app/layer1_domain/entities/node_definition.py`
- save-time enrichment `create_workflow/.../create_workflow_use_case.py::_enrich_nodes_with_definitions`
  (and the update equivalent) that stamps `definition_snapshot`
- `list_node_palette` (the function now shows up in the palette / node catalog)
- runtime `app/layer2_application/services/node_handlers/task_handler.py::_apply_node_definition`
  (applies `locked_defaults` + `input_mapping` at dispatch)

**"Expose a function" = create a `GatewayFunction` → its NodeDefinition appears in the palette.**
On function delete/disable → delete/deactivate the NodeDefinition.

> Verified this session that the NodeDefinition → enrichment → dispatch path works end-to-end against the
> running stack (a TASK node backed by a `NodeDefinition` with `base_worker_id` of a real worker dispatched and
> completed live). Gateway-function nodes ride the exact same path.

## 5. Runtime resolution flow (internal + external unified)

A workflow node from a gateway function carries `worker_type=gateway-worker` + `locked_defaults`
(`_gw_app`, `_gw_function`) + the user-supplied args. At dispatch the gateway-worker calls a new
**function-aware resolve** on the CP and proxies:

1. Worker → `POST /api/v1/gateway/apps/{app}/functions/{fn}/_resolve` → CP returns the **call-spec**:
   `{kind, base_url|allowed_hosts, method, path_template, params_spec, auth_header_name, auth_header_value}`
   (internal: decrypted auth header injected, as `resolve_gateway_service` does today; external: no creds).
2. Worker fills `path_template` with path args, places remaining args into query/headers/body per
   `params_spec`, and builds the final URL.
3. **External:** validate the final URL against the app's `allowed_hosts` + the existing `ExternalUrlPolicy`
   SSRF guard (`worker/gateway-worker/app/layer1_domain/value_objects/external_url_policy.py`) — now seeded from
   the DB app, not `external.yaml`.
4. Proxy via the existing `UpstreamHttpClient`; return `GatewayResult` (status / headers / body).

**Implementation:** add one worker function `invoke_app_function(params={_gw_app, _gw_function, args})` in
`worker/gateway-worker/app/functions.py` (sibling to `call_internal` / `call_external`, reusing
`registry_client`, `upstream_http`, `external_url_policy`); extend the CP resolve feature to be function-aware.

## 6. API surface (admin)

- `POST/GET/PATCH/DELETE /api/v1/gateway/apps` (+ `/{app}`) — evolve `gateway_service_registry_controller.py`.
- `POST/GET/PATCH/DELETE /api/v1/gateway/apps/{app}/functions` (+ `/{fn}`) — new controller.
- `POST /api/v1/gateway/apps/{app}/functions/{fn}/_resolve` — worker-facing call-spec.
- `POST /api/v1/gateway/apps/{app}/functions/{fn}/test` — operator probe (reuse `test_gateway_service` shape).
- `POST .../{fn}/publish` (or sync-on-write) — (re)generate the NodeDefinition.

All write/resolve endpoints get a **minimal admin gate** (role check on the caller identity header),
tightening today's trust-the-network model.

## 7. Security / ops

- Credentials: **reuse** `aes_gcm_credential_cipher` + `EncryptedCredential` (encrypt at write, decrypt only in
  `_resolve` / `test`). No new crypto. GET projections return only `auth_summary {credential_present,
  key_version}` — never plaintext.
- **Enablement gap:** gateway features are skipped when no KEK is set in non-mock mode
  (`bootstrap._build_credential_cipher` returns `None`). Require `GATEWAY_REGISTRY_KEK_V1` for the feature to
  load (already present in this box's `.env`).
- SSRF / allowlist enforced per-app for external calls.

## 8. Clean-break migration

Replace `InternalServiceRegistration` / `AIW_GATEWAY_INTERNAL_SERVICES` / `/gateway/services` with the
apps+functions model (the registry has no real production rows — it has been unused/skipped). The low-level
`call_internal` / `call_external` worker functions remain as primitives; the new `invoke_app_function` is the
registry-driven path.

## 9. Critical files (reuse / modify / create)

- **Reuse as-is:** `app/layer4_frameworks/security/aes_gcm_credential_cipher.py`,
  `app/layer1_domain/value_objects/auth_type.py`, `app/layer1_domain/entities/node_definition.py`,
  `create_workflow/.../create_workflow_use_case.py` (`_enrich_nodes_with_definitions`),
  `worker/gateway-worker/app/layer1_domain/value_objects/external_url_policy.py`,
  `worker/gateway-worker/app/layer4_frameworks/{registry_client,upstream_http}.py`.
- **Modify:** `gateway_service_registry_controller.py` (→ apps), the `resolve_gateway_service` use case
  (→ function-aware), `bootstrap.py` (wire app/function repos + NodeDefinition sync),
  `worker/gateway-worker/app/functions.py` (+ `invoke_app_function`), `list_node_palette` (confirm gateway
  nodes surface).
- **Create:** `entities/gateway_app.py`, `entities/gateway_function.py`, function CRUD use cases under
  `features/`, function controller, repos (memory + HANA), new migration, a NodeDefinition-sync service.

## 10. Phased plan

1. **P1 — Model + storage** ✅ **shipped:** GatewayApp / GatewayFunction entities, repos (memory + HANA),
   migration, CRUD use cases + apps/functions REST. (No runtime change yet.)
2. **P2 — Function-aware resolve + worker proxy** ✅ **shipped:** function-aware `_resolve` (by function id);
   `invoke_app_function`; internal path end-to-end. `/test` probe.
3. **P3 — Auto NodeDefinition sync** ✅ **shipped:** generate/update/delete NodeDefinition on function
   lifecycle; reconcile GC (endpoint + periodic sweeper); palette surfacing + run wiring — see §13.
4. **P4 — External target + per-app allowlist** ✅ **shipped:** author-time target validator
   (internal⇒relative, external⇒absolute http(s) URL whose host ∈ app allowlist) + worker timeout/scheme
   fixes; external functions run end-to-end. See §14.
5. **P5 — Admin gate + UI contract:** minimal RBAC on writes/resolve; document the API for the FE console.

## 11. Verification (end-to-end, live)

Reuse the live-stack runbook (CP + worker-executor + start the **gateway-worker** with `REGISTRY_URL=:8004`,
and ensure `GATEWAY_REGISTRY_KEK_V1` is set so the gateway features load — confirm via the boot log that
`create_gateway_service` / `resolve_*` are no longer "missing credential_cipher — skipping"):

1. `POST /api/v1/gateway/apps` (internal app → e.g. the CP's own `/api/v1/health`, auth none) →
   `POST .../functions` (GET `/api/v1/health`).
2. Confirm the function appears in `GET /api/v1/node-palette` (and `/api/v2/node-catalog`) as a node.
3. Create a workflow using that node via the API; confirm enrichment stamps `definition_snapshot`.
4. Start a run; confirm the gateway-worker resolves the function and proxies (worker log shows the upstream
   call; node output carries the response).
5. Repeat for an **external** function against an allowlisted host; confirm a non-allowlisted host is rejected.
6. Unit / integration tests per phase (entity validation, CAS, cipher round-trip, resolve, NodeDefinition sync,
   SSRF reject); run the full integration suite as a regression gate.

**P3 live result (2026-06-14, full stack vs real HANA):** CP(:8001, INFRA_MODE=local + `GATEWAY_REGISTRY_KEK_V1`) +
executor(:8004) + gateway-worker(:35001). All 13 gateway features loaded (incl. `reconcile_gateway_node_definitions`,
none skipped). Created internal app `p3e2e` (base_url=CP, auth none) + function `health` (GET `/api/v1/health`);
it surfaced in `GET /node-palette` as a `BUSINESS` TASK tagged `gateway-function`; the stored NodeDefinition carried
the `_function_call` marker + `_gw_function` locked default. Built a workflow using the node (enrichment set
`worker_type=gateway-worker` + stamped the snapshot), started a run → **COMPLETED**; the `gw` node output carried the
proxied response (`{$gw.status}="ok"`, `{$gw.status_code}="200"`, `{$gw.body.status}="ok"`) and the CP log showed the
worker's inbound `GET /api/v1/health 200`. `POST …/node-definitions/reconcile` returned `{updated:1}`. Deleting the app
cascaded: function gone, palette business nodes → 0, generated def `NOT_FOUND`. CP suite 3315 passed / 161 skipped,
gateway-worker 63 passed.

## 12. Open items

- Agent tool-calling exposure (LLM tools) deferred — the NodeDefinition path covers workflows; agent tools can
  be a later add-on reusing the same function registry.

## 13. P3 implementation (shipped) — auto NodeDefinition + garbage collector

### 13.1 What was built (vs the sketch in §4)

- **Domain owns the translation.** `value_objects/gateway/node_definition_spec.py` holds
  `GatewayNodeDefinitionSpec` + `build_spec(...)`; `GatewayFunction.build_node_definition_spec()` derives the
  node from the function's signature. The application-layer sync only persists the result — it has **no
  mapping logic**.
- **Deterministic id** `gwfn-<function_id>` (`gateway_node_definition_id()`), so upsert/delete are idempotent
  by id and the GC can enumerate generated defs by prefix. Exposed as
  `GatewayFunction.generated_node_definition_id` (derived; distinct from the stored `node_definition_id`
  back-link field, which stays reserved).
- **`locked_defaults = {"_gw_function": <id>}`** only (no `_gw_app`). The function id is the single routing
  identity — P2 `_resolve` is **by function id** and looks the parent app up itself, so the node never needs
  the app id.
- **`input_mapping`** carries the `_function_call` marker under the `result` key:
  `{"result": {"_function_call": {"worker_function": "invoke_app_function",
  "params": {"function_id": "<lit>", "args": {<p>: "{{$custom.<p>}}"}}}}}`.
  At dispatch `TaskNodeHandler._apply_node_definition` resolves the nested `{{$custom.*}}` against the node's
  form input, then `_resolve_function_calls` fires the marker → `invoke_app_function` (P2) → splices the
  gateway response into `result`. The TASK then dispatches to the gateway-worker `/execute` (passthrough echo)
  and completes; `CompleteTaskUseCase` applies `output_mapping` against `{"_output": task_output}` to expose
  `status_code` / `body` + each declared output field. **All of this reuses existing code unchanged.**
- **`input_schema` rows** are valid `FormField`s: type → control = string/number→`InputControl`,
  boolean→`SwitchControl`, object/array→`CodeControl` (raw JSON, so no nested `fields` needed). Output rows are
  read-only. Generated defs are `tenant_id="system"` (global) and surface as `provider:"business"` in the
  palette + `/api/v2/node-catalog`.

### 13.2 Lifecycle & garbage collection

`GatewayNodeDefinitionSync` (`upsert` / `remove` / `list_generated_ids`, + `*_best_effort` wrappers):

| Trigger | Action |
|---|---|
| Function created | `sync.upsert(fn)` — create `gwfn-<id>` (best-effort, post-persist) |
| Function updated | `sync.upsert(fn)` — **replay**: re-derive + overwrite (idempotent) |
| Function deleted (soft) | `sync.remove(fn.id)` — delete `gwfn-<id>` |
| App deleted (soft) | cascade: each function soft-deleted **and** its node removed |
| Reconcile (endpoint + sweeper) | expected = active funcs on active apps; **delete** `existing − expected` (orphans), **upsert** every expected (heal drift / restore missing) |

**Create vs replay vs remove.** Create only when no def exists for an active function (upsert is
create-or-overwrite by id — never duplicates). Replay on every update and on reconcile. Remove on function
soft-delete, on app soft-delete (cascade), or when reconcile finds a `gwfn-*` whose function/app is
missing or inactive.

**Safety / idempotency.** GC keys off the **function lifecycle** — it never deletes a `gwfn-*` whose function
**and** app are both active. CRUD sync is **best-effort, post-persist**: a NodeDefinition failure logs +
warns but never fails the function operation (registry is the source of truth); reconcile is the safety net.
Deleting a generated def is **non-fatal to existing workflows** — `definition_snapshot` is frozen at workflow
save and drives the run; deletion only removes the palette entry for new authoring (a re-save against a
deleted def degrades to an empty snapshot, non-raising).

### 13.3 Reconcile endpoint + sweeper

- `POST /api/v1/gateway/node-definitions/reconcile` (`gateway_admin_controller.py` →
  `reconcile_gateway_node_definitions` use case) → `{created, updated, deleted, failed}`.
- Periodic sweeper in `main.py` lifespan, env-gated `GATEWAY_RECONCILE_ENABLED` +
  `GATEWAY_RECONCILE_INTERVAL_SECONDS` (default off; enable on exactly one instance). No boot reconcile.

### 13.4 Tests

`tests/unit/test_gateway_node_definition_p3.py` (spec shape, `FormField` validity, sync idempotency,
best-effort swallow, reconcile create/replay/orphan/inactive-func/inactive-app, and the generated marker
invoking `invoke_app_function` through the real dispatch path) + lifecycle HTTP tests in
`tests/integration/test_gateway_apps_functions_endpoints.py` (palette surfacing, update reflects schema,
delete removes node, app-delete cascade, reconcile endpoint). Full CP suite green as the regression gate.

## 14. P4 implementation (shipped) — external functions + per-app allowlist

### 14.1 What was already there vs new
P2 had built the bulk of the external runtime path (per-app `allowed_hosts` on the resolve call-spec; the
worker's `ExternalUrlPolicy` SSRF gate — exact + one-level-wildcard host match, scheme allowlist, DNS-resolved
private/loopback/link-local/reserved/multicast block — applied in `invoke_app_function`'s external branch;
absolute-URL request building). P3's auto-NodeDefinition path is kind-agnostic, so external functions already
become palette nodes. **P4 added the author-time guard + two worker fixes and proved the path live.**

### 14.2 Addressing model & domain rule (confirmed)
- **External function `path_template` = the full absolute `http(s)` URL** (e.g.
  `https://api.partner.com/v1/orders/{order_id}`); `base_url` stays unused for external. Internal functions
  keep relative paths. (Chosen over a uniform-base_url refactor.)
- The kind-aware rule lives in a **domain validator** (the function entity is a separate aggregate and can't
  hold `app.kind`), mirroring `validate_auth_config`:
  - `value_objects/gateway/host_allowlist.py` → `HostAllowlist.matches(host)` (exact / `*.one-level` wildcard;
    case-insensitive) — same rule the worker enforces, now owned by the CP domain too.
  - `value_objects/gateway/function_target.py` → `validate_function_target(app_kind, allowed_hosts,
    path_template)`: INTERNAL ⇒ `path_template.is_relative()`; EXTERNAL ⇒ absolute URL with scheme ∈
    {http,https} + non-empty host that `allowed_hosts.matches(host)`. (Author-time checks URL well-formedness +
    host membership; the runtime scheme policy stays https-only by default and can tighten independently.)
- **Enforcement = author-time + runtime.** `create_gateway_function` + `update_gateway_function` call the
  validator (they already load the parent app) → `ValidationError → VALIDATION_ERROR`; the worker's
  `ExternalUrlPolicy` remains the hard runtime gate.
- **Documented edge (not cascaded):** narrowing an app's `allowed_hosts` can orphan an existing function's
  host. P4 does NOT re-validate every function on app update — the runtime SSRF check is the backstop
  (`HOST_NOT_ALLOWED` at dispatch; live-verified below).

### 14.3 Worker fixes (gateway-worker)
- **Honor the function's configured timeout:** `_make_invoke_app_function_handler` now uses
  `req.timeout_ms if provided else (resolved.timeout_ms or None)` (was always the caller value, ignoring the
  function's stored timeout). Applies to internal + external.
- **Configurable schemes:** `INVOKE_ALLOWED_SCHEMES` (default `https`) → `build_gateway_worker_functions` →
  the handler. (`external.yaml` stays — it feeds only the legacy `call_external` primitive, which is kept.)

### 14.4 Tests
`tests/unit/test_gateway_function_target_p4.py` (HostAllowlist.matches matrix; validate_function_target
internal/external matrix; create/update use cases reject bad host / internal-absolute) + external cases in
`tests/integration/test_gateway_apps_functions_endpoints.py` (external lifecycle + resolve returns
kind/allowed_hosts; bad-host/relative/internal-absolute rejected at create) + worker
`tests/unit/test_invoke_app_function.py` (timeout precedence both ways; https-only scheme reject).
**CP 3336 passed / 161 skipped; gateway-worker 66 passed.**

### 14.5 Live result (2026-06-14, full stack vs real HANA, egress to api.github.com)
External app `gh` (`allowed_hosts=[api.github.com]`, auth none) + function `zen`
(`GET https://api.github.com/zen`): resolve returned `kind=external` + `allowed_hosts`; surfaced as a palette
node; a workflow using it **ran to COMPLETED** with `{$gw.result.status_code}=200`,
`{$gw.result.body}="Avoid administrative distraction."`, `result.headers.server=github.com` (real proxied
HTTPS call through the SSRF allowlist). All three runtime SSRF gates confirmed via direct invoke:
`SCHEME_NOT_ALLOWED` (http target), `PRIVATE_IP_BLOCKED` (`https://localhost`→127.0.0.1),
`HOST_NOT_ALLOWED` (orphan edge — narrowed the app allowlist after function create). Cleaned up + stack stopped.
