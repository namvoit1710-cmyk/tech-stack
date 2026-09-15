# Node Catalog API Split — Implementation Plan

**Status:** Ready to execute
**Date:** 2026-04-19
**Design doc:** `node-palette-catalog-redesign.md`
**Scope:** Split today's single `GET /node-palette` into a list endpoint + detail endpoint, with typed pydantic contracts, ETag caching, and frontend prefetch.

---

## 0. Reading Order

1. §1 Goal & non-goals — confirm scope match.
2. §2 Current-state anchors — file paths this plan mutates.
3. §3 Phase 0 (foundations) — **land before any other phase**.
4. §4–§6 Phases 1–3.
5. §7 Test strategy.
6. §8 Rollout, feature flag, rollback.

---

## 1. Goal & Non-Goals

### Goals
- Split `GET /node-palette` → `GET /node-catalog` (list) + `GET /node-catalog/{id}@{version}` (detail) + batch variant.
- Replace loose-dict schemas on the wire with pydantic-validated `FormField`.
- Cache the list response (ETag + short TTL + event-driven invalidation).
- Frontend prefetches detail on drag-start; no perceived latency regression.

### Non-goals for this plan (deferred to later plans)
- Business-node immutable versioning (`(id, version)` rows).
- Shared `packages/node-contracts/` package + TS codegen.
- Retiring `node_class` / `provider` from the wire.
- Category tree restructuring.
- Socket.IO push for palette updates.

Each non-goal is an independent follow-up PR; none blocks this plan.

---

## 2. Current-State Anchors

Files this plan touches, with line refs:

| Area | File | What's there today |
|---|---|---|
| Palette use case | `workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/list_node_palette_use_case.py:80-94` | Single `execute()` returns everything in one dict |
| Builtin schemas | `…/list_node_palette/use_cases/control_flow_schemas.py:603-656` | Hardcoded `SCHEMAS` dict |
| Palette controller | `…/layer3_adapters/controllers/restful/v1/node_palette_controller.py:20-24` | `GET /` |
| Worker registry client | `…/layer4_frameworks/clients/http_worker_registry_client.py:35-42` | `list_workers()` → executor `/workers` |
| AutoFormField models | `…/layer4_frameworks/auto_form_mapper/models.py:1-126` | Dataclass-based form schema |
| Business node entity | `…/layer1_domain/entities/node_definition.py:12-59` | Missing `version`, `spec_version` |
| Worker SDK node type | `worker/worker-sdk/worker_sdk/layer1_domain/entities/node_type_definition.py:9-30` | `version` present, no `spec_version`/`category` |
| Worker registration (SDK→exec) | `worker/worker-sdk/worker_sdk/layer4_frameworks/providers/registry/http_worker_registry.py:21-59` | `POST /api/v1/workers/register` |
| Worker entity (executor) | `worker-executor-service/app/layer1_domain/entities/worker_registration.py:1-46` | Mirrors SDK model |
| List workers | `worker-executor-service/app/layer2_application/features/list_workers/use_cases/list_workers_usecase.py:43-67` | Returns all fields |

---

## 3. Phase 0 — Foundations

**Exit criteria:** every node in the palette has a stable `id` and a `spec_version`; pydantic `FormField` model exists and is used for validation on the register path; unversioned workflow references resolve without breaking.

### Task 0.1 — Remove UUID fallback for worker IDs

**Problem:** `list_node_palette_use_case.py:155-156` falls back to `worker.id` (UUID) when `worker_type` is missing. A worker restart changes its UUID → the detail endpoint URL breaks → caches invalidate incorrectly.

**Change:**
- `worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py` — reject registration with empty `worker_type` (return 400).
- `list_node_palette_use_case.py:155-156` — delete the fallback; if `worker_type` is empty, skip the worker and log a warning.
- `worker-sdk/.../worker_server/__init__.py` — assert `worker_type` non-empty on startup.

**Tests:**
- Unit: `test_register_worker_rejects_empty_worker_type`.
- Unit: `test_palette_skips_worker_with_empty_type`.

**Files modified:** ~3. LoC: ~30.

### Task 0.2 — Add `spec_version` field everywhere a node is described

**Change A — SDK:**
```python
# worker/worker-sdk/worker_sdk/layer1_domain/entities/node_type_definition.py
@dataclass(kw_only=True)
class NodeTypeDefinition:
    worker_type: str
    name: str = ""
    # ...
    version: str = "0.1.0"        # worker build version (existing)
    spec_version: str = "1.0.0"   # NEW — node contract version
    category: str = "business"    # NEW — dot-path
```

**Change B — Executor entity:**
```python
# worker-executor-service/app/layer1_domain/entities/worker_registration.py
spec_version: str = "1.0.0"   # NEW
category: str = "business"    # NEW
```

**Change C — Register wire payload:**
- `worker-sdk/.../providers/registry/http_worker_registry.py:21-59` — emit `spec_version`, `category`.
- `worker-executor-service/app/layer3_adapters/controllers/restful/v1/worker_controller.py` — accept both (optional → default).

**Change D — Builtins get a spec_version:**
```python
# workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/list_node_palette_use_case.py
BUILTIN_NODES: list[dict[str, Any]] = [
    _node("builtin:condition", "Condition", "CONDITION", spec_version="1.0.0", category="control_flow.branching", ...),
    # ... update each _node call
]
```

**Change E — Business nodes get a spec_version projection:**
Business `NodeDefinition` (the entity) does not yet get immutable versioning (that's a later plan). For palette purposes, project a synthetic `spec_version="1.0.0"` into the DTO at read time. When versioning lands, this becomes a real field.

**Tests:**
- Unit: `test_worker_registration_defaults_spec_version_to_1_0_0`.
- Unit: `test_builtin_node_carries_spec_version`.
- Contract: every palette entry has a non-empty `spec_version`.

**Files modified:** ~6. LoC: ~80.

### Task 0.3 — Pydantic contracts module inside control-plane

Create `workflow-control-plane-service/app/layer1_domain/contracts/` with the models from the design doc §3 and §3.1.

**New files:**

```
app/layer1_domain/contracts/
├── __init__.py
├── form_schema.py         # FormField, FieldConfig, FieldControl, ControlProps, WrapperProps,
│                          # ConditionConfig, ValidationRule + all enums + model_validator
├── node_definition_dto.py # NodeDefinitionSummary, NodeDefinitionDetail, NodePort,
│                          # NodeCapability, NodeVisibility, NodeRuntime
└── providers.py           # Protocol: NodeProvider.list_summaries()/get_detail(id, version)
```

**Validation boundaries added in this task:**
- Executor's register use case re-parses the payload into `NodeDefinitionDetail` before storing — rejects with 400 on pydantic `ValidationError`.
- CP's palette use case re-parses executor responses into `NodeDefinitionSummary` — logs and drops any malformed entry.

**Retired (deprecated but not deleted):** `app/layer4_frameworks/auto_form_mapper/models.py`. Leave in place behind a re-export shim that constructs the equivalent `FormField`. Delete in Phase 4.

**Tests:**
- Unit: `test_form_field_rejects_unknown_control_props_key`.
- Unit: `test_form_field_select_without_options_fails_validation`.
- Unit: `test_form_field_switch_requires_boolean_output_type`.
- Unit: `test_form_field_nested_array_requires_fields`.
- Contract: each existing builtin schema from `control_flow_schemas.py:603-656` round-trips through `FormField.model_validate(...)` without loss.

**Files added:** 4. LoC: ~450 (mostly the form_schema module).

### Task 0.4 — Unversioned workflow reference resolution

**Problem:** existing saved workflows reference nodes without `spec_version`. After this plan, some nodes ship pinned versions. The engine must handle both.

**Rule:** a workflow node with no `spec_version` → resolve to the highest non-deprecated version of that definition.

**Change:**
- `workflow-control-plane-service/app/layer1_domain/services/node_definition_resolver.py` — accept optional `spec_version`; if None, query the palette provider for the latest non-deprecated version.
- Engine dispatch site (wherever handlers fetch the definition for execution) — pass `spec_version=None` until workflows start pinning.

**Tests:**
- Unit: `test_resolve_without_spec_version_picks_latest_non_deprecated`.
- Integration: `test_existing_saved_workflow_runs_without_spec_version`.

**Files modified:** ~2. LoC: ~40.

**Phase 0 total:** ~13 files, ~600 LoC, ~1 sprint.

---

## 4. Phase 1 — New Endpoints

**Exit criteria:** `/node-catalog` list + detail + batch endpoints live behind feature flag `catalog_v2`, with ETag support and CP-side cache invalidated by executor events.

### Task 1.1 — Use cases

**New:**
```
app/layer2_application/features/list_node_catalog/use_cases/list_node_catalog_use_case.py
   → execute(tenant_id) -> ListNodeCatalogResult
     { definitions: list[NodeDefinitionSummary], category_tree, generated_at, etag }

app/layer2_application/features/get_node_catalog_detail/use_cases/get_node_catalog_detail_use_case.py
   → execute(id: str, version: str | None) -> NodeDefinitionDetail
     version=None → latest non-deprecated

app/layer2_application/features/get_node_catalog_details_batch/use_cases/...
   → execute(refs: list[NodeRef]) -> list[NodeDefinitionDetail]
     NodeRef = {id, version?}
```

Each use case uses a `NodeProvider` chain (builtin, worker, business) via the protocol from Task 0.3. No hardcoded dispatch inside the use case.

### Task 1.2 — Controller & routes

**New file:** `app/layer3_adapters/controllers/restful/v1/node_catalog_controller.py`

```python
@router.get("/node-catalog")
async def list_node_catalog(
    tenant_id: str = "system",
    if_none_match: str | None = Header(None),
) -> Response: ...

@router.get("/node-catalog/{ref}")   # ref = "worker:http-request@1.2.0" or "business:abc"
async def get_node_catalog_detail(
    ref: str,
    if_none_match: str | None = Header(None),
) -> Response: ...

@router.get("/node-catalog/details")
async def get_node_catalog_details_batch(
    ids: str = Query(..., description="comma-separated id@version refs"),
) -> Response: ...
```

Parse `ref` with a `parse_node_ref(ref: str) -> tuple[id, version|None]`. Return 410 Gone on unknown `ref` with last-known summary in body.

### Task 1.3 — ETag middleware

**New file:** `app/layer4_frameworks/http/etag_middleware.py`
- SHA-256 of response body → hex → `W/"…"`.
- Honor `If-None-Match`, return 304 on match.
- Apply to the three new routes only (opt-in decorator, not global).

### Task 1.4 — CP list cache

**New file:** `app/layer4_frameworks/caching/node_catalog_cache.py`

```python
class NodeCatalogCache:
    def __init__(self, ttl_seconds: int = 30): ...
    def get(self, tenant_id: str) -> CachedPayload | None: ...
    def put(self, tenant_id: str, payload, etag: str) -> None: ...
    def invalidate(self, tenant_id: str | None = None) -> None: ...
```

In-memory dict keyed by `tenant_id`. Start simple; swap for Redis later if needed.

### Task 1.5 — Executor `catalog.changed` event

**Change — executor:**
- `sdk_register_worker_usecase.py` — after successful `registry_store.register(worker)`, emit `catalog.changed { worker_type, action: "registered" }` on the messaging bus.
- Mirror on deregister and health transitions.

**Change — CP:**
- Subscribe in `app/layer4_frameworks/messaging/consumers/catalog_changed_consumer.py`.
- Handler: `NodeCatalogCache.invalidate()`.

Use the existing console-mode messaging backend for `INFRA_MODE=mock`; works out of the box in tests.

### Task 1.6 — Feature flag

**Change:** `app/layer4_frameworks/config/settings.py` — add `enable_catalog_v2: bool = False`.
- When `True`: new endpoints served; old `/node-palette` returns `299 Deprecation` warning header but still works.
- When `False`: new endpoints return `404`, old path unchanged.

**Tests for Phase 1:**
- Unit per use case (list, detail, batch).
- Integration: `test_list_returns_304_on_etag_match`.
- Integration: `test_detail_returns_410_for_unknown_ref`.
- Integration: `test_register_worker_invalidates_cp_cache_within_2s`.
- Contract: old `/node-palette` and new `/node-catalog` list return the same node set (order-independent).
- Load: batch endpoint with `ids=a@1,b@2,...,z@26` under 150ms P95 locally.

**Files added/modified:** ~12 new, ~4 modified. LoC: ~900. ~2 sprints.

---

## 5. Phase 2 — Frontend Integration

**Exit criteria:** Workflow builder uses new endpoints end-to-end behind flag; perceived latency is equal or better than the current single-fetch path.

### Task 2.1 — Frontend feature flag plumb

- Read `catalog_v2` from backend config endpoint on builder load.
- Route palette requests through a `CatalogClient` that picks old vs new path.

### Task 2.2 — Prefetch on drag-start

- Palette tile `onDragStart` → `CatalogClient.getDetail(id, spec_version)` → populate a `Map<ref, NodeDefinitionDetail>` cache with ~5-minute TTL.
- Node modal `onOpen` → read from cache; fetch only on miss.
- Drag-cancel does not evict (user often re-drags).

### Task 2.3 — Batch fetch on workflow load

- Workflow JSON loaded → collect all `(definition_id, spec_version)` pairs → single `GET /node-catalog/details?ids=…`.
- Populate prefetch cache in one shot.

### Task 2.4 — Contract test

- Add `frontend/tests/contracts/catalog-v2.test.ts`.
- Run CP in mock mode; assert the response parses into the frontend's TS types (hand-written from the pydantic models).
- CI gate on this test blocks backend releases that break the shape.

### Task 2.5 — 410 Gone handling

- On `410 Gone` from detail endpoint, render a disabled "unavailable node" placeholder on the canvas with the `last known summary` from the response body.
- Offer "remove from workflow" and "migrate to compatible version" actions.

**Tests for Phase 2:**
- E2E: builder open → palette list renders in <200ms on warm cache.
- E2E: drag node → open modal → form renders with no visible network wait.
- E2E: load workflow with 20 nodes → batch detail call observed, not 20 individual calls.

**Files modified:** frontend only. Backend untouched in this phase.

---

## 6. Phase 3 — Rollout

### Task 3.1 — Gradual flag rollout
- Day 0: `catalog_v2=true` for internal tenants only.
- Day 7: 25% of production tenants.
- Day 14: 100%.
- Day 30: disable flag (always-on).

### Task 3.2 — Observability
Add metrics:
- `node_catalog.list.requests_total{tenant_id, status}`
- `node_catalog.list.cache_hits_total`
- `node_catalog.detail.requests_total{id, version, status}`
- `node_catalog.detail.p95_latency_ms`
- `node_palette.legacy.requests_total` (for watching the old endpoint die)

Dashboards + alerts:
- Alert if `detail.p95_latency_ms > 500` for 5 min.
- Alert if `legacy.requests_total > 0` after the flag is permanent (= a client didn't migrate).

### Task 3.3 — Deprecation banner
- Add `Deprecation: true` + `Sunset: <date>` headers on `GET /node-palette`.
- Log every call with caller user-agent.

---

## 7. Phase 4 — Cleanup

Blocked on Phase 3 staying green for 2 weeks.

| Task | Action |
|---|---|
| 4.1 | Delete `GET /node-palette` controller and use case. |
| 4.2 | Delete `node_class`, `provider` fields from wire responses. |
| 4.3 | Delete `app/layer4_frameworks/auto_form_mapper/` (no re-exports). |
| 4.4 | Extract `app/layer1_domain/contracts/` into `packages/node-contracts/` + TS codegen pipeline. |
| 4.5 | Announce `spec_version` pinning in new workflows (builder starts writing it). |

---

## 8. Testing Strategy

### Unit test additions (minimum set)
- `test_form_field_models.py` — all `@model_validator` branches + each enum value rejected when used wrong.
- `test_node_provider_builtin.py` — builtin provider returns all 13 types with spec_version.
- `test_node_provider_worker.py` — worker provider filters by health, uses stable IDs.
- `test_list_node_catalog_use_case.py` — aggregation across providers.
- `test_get_node_catalog_detail_use_case.py` — version resolution (pinned vs latest).
- `test_node_catalog_cache.py` — TTL, invalidation, per-tenant keying.

### Integration test additions
- `test_register_triggers_catalog_changed.py` — register → bus emits → CP cache invalidates.
- `test_catalog_etag_304.py` — If-None-Match round trip.
- `test_legacy_vs_v2_parity.py` — for each tenant fixture, old `/node-palette` and new `/node-catalog` return equivalent node sets.

### Contract test
- `test_frontend_ts_types_match_pydantic.py` — runs the pydantic models through a JSON dump, asserts the frontend's TS interfaces parse it.

### Load test
- `load_test_batch_details.py` — 500 concurrent clients fetching 20-id batches; assert P95 < 150ms against a warm cache.

---

## 9. Rollback Plan

Each phase has an independent rollback:

| Phase | Rollback action |
|---|---|
| 0 | Revert commits; no runtime effect until Phase 1 starts consuming. |
| 1 | Set `enable_catalog_v2=false`; new endpoints 404, old path unaffected. |
| 2 | Frontend flag off → client uses old path; no backend change needed. |
| 3 | Increase error-budget sensitivity; flip flag back to internal-only. |
| 4 | Flag can't easily reverse. Treat Phase 4 as a one-way door; only enter after 2 green weeks of Phase 3. |

---

## 10. Acceptance Criteria (ties back to design doc §13)

- [ ] New worker deploy adds a node to the palette with **zero** CP or frontend code changes.
- [ ] `GET /node-catalog` response <50KB regardless of schema complexity.
- [ ] Workflow pinned to `spec_version=1.2.0` still runs after worker upgrade to `1.3.0`.
- [ ] Worker with a bad `FormField` (typo in `field_control`, unknown key) fails startup.
- [ ] Renaming a pydantic field breaks the frontend contract test before it ships.
- [ ] 30-second TTL + event-driven invalidation keeps palette fresh under worker churn.
- [ ] Prefetch-on-drag makes config modal open instantaneous on warm cache.

---

## 11. Estimated Effort

| Phase | Calendar | Engineers | Risk |
|---|---|---|---|
| 0 Foundations | 1 sprint | 1 backend | Low — additive, no deletes |
| 1 New endpoints | 2 sprints | 1 backend | Medium — cache + events |
| 2 Frontend | 1 sprint | 1 frontend | Low — behind flag |
| 3 Rollout | 1 month calendar | 0.25 backend on-call | Low — gradual |
| 4 Cleanup | 1 sprint | 1 backend | Low — after proof |

Critical path: Phase 0 → Phase 1 is serial. Phase 2 can start in parallel with Phase 1 once `NodeDefinitionSummary/Detail` shapes are frozen (mid-Phase 1).

---

## 12. Open Items Before Kickoff

1. Agree with frontend on TS model location: handwritten in `frontend/src/types/node-catalog.ts` for Phase 2, codegen'd in Phase 4.
2. Confirm messaging backend available in `INFRA_MODE=local` supports the `catalog.changed` topic pattern (should — it's already used for other events).
3. Confirm cache TTL default (30s proposed) with product — is 30s palette staleness acceptable if the event bus misses?
4. Decide whether business-node spec_version defaults to `"1.0.0"` (projected) or `f"{updated_at_epoch}"` (live-updating) before the immutable-versioning plan lands.
