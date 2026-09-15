# Phase 1 "API Surface" — Detailed Execution Plan

**Companion docs:**
- `docs/design/node-palette-catalog-redesign.md` — target architecture
- `docs/design/node-palette-catalog-implementation-plan.md` — rolling Phase 0→4 plan
- `docs/design/node-palette-phase0-hard-blockers.md` — Phase 0 execution spec (landed)

This document expands Phase 1 of the rolling plan into a per-task execution spec. Phase 1 introduces the new v2 catalog endpoints, ETag caching, event-driven cache invalidation, and feature-flagged rollout.

---

## 1. Context

Phase 0 landed the foundations. Every node now has `spec_version`, every worker has a stable `worker_type` (no UUID fallback), and the pydantic `FormField` / `NodeDefinitionSummary` / `NodeDefinitionDetail` contracts exist in `workflow-control-plane-service/app/layer3_adapters/contracts/`. **Nothing consumes them yet** — the contracts module is built but not wired.

Phase 1's job is to **wire them into a clean v2 API surface** that the frontend can migrate to. Specifically:

1. **Split the monolithic `/node-palette`** into a compact list (summaries only) + a detail endpoint per node + a batch-detail endpoint. This is the key UX improvement — palette loads stay fast even when a tenant has 200+ business nodes with rich schemas.
2. **Introduce ETag caching** so the browser can skip the payload when nothing changed.
3. **Event-driven cache invalidation** so a worker registering/deregistering flushes CP caches within ~2 seconds.
4. **Feature-flag the whole thing** (`enable_catalog_v2`) so the old endpoint keeps working until the frontend migrates (Phase 2).

Phase 1 is where the pydantic contracts stop being "built-but-unused" and start validating the wire. Worker registration that produces malformed schemas must fail loudly here, not silently downstream.

---

## 2. Scope & Non-Goals

### In scope
- **Three new GET endpoints** under `/v2` prefix on the control plane.
- **Three new NodeProvider implementations** (`BuiltinNodeProvider`, `WorkerNodeProvider`, `BusinessNodeProvider`) satisfying the `NodeProvider` protocol from `layer3_adapters/contracts/providers.py`.
- **A CatalogService aggregator** that fans out to providers and merges results deterministically.
- **An in-memory NodeCatalogCache** keyed by `tenant_id`, with TTL + explicit invalidation.
- **ETag middleware** composable with the existing `ResponseWrapperMiddleware`.
- **`catalog.changed` event** emitted by the executor on register / deregister / health transition; consumed by the CP to invalidate caches.
- **`ENABLE_NODE_CATALOG_V2` feature flag** gating the new endpoints and the strict pydantic validation at the register boundary.
- **Pre-flight fixes** (Task 1.0): align the one broken builtin schema (INPUT) and the one enum mismatch (`UploadControl` vs `UploadControl`) before the validator goes live.

### Explicitly out of scope (Phase 2+)
- Retiring the old `/node-palette` endpoint (Phase 4).
- Removing `node_class` / `provider` legacy fields from responses (Phase 4).
- Frontend integration (Phase 2).
- Socket.IO push for palette updates (deferred follow-up).
- Business-node immutable versioning — `(id, version)` rows in the database (separate plan).
- Moving the contracts module into a shared `packages/node-contracts/` package (Phase 4).

---

## 3. Recommended Merge Order

```
1.0 (pre-flight fixups)         ─► 1.1 (providers) ─┐
                                                    ├─► 1.2 (use cases) ─► 1.3 (controller)
                                  1.4 (ETag mw)    ─┘
                                  1.5 (cache)      ─► 1.6 (events) ─────► 1.7 (flag)
```

**Rationale:**
- **1.0 first** — the enum mismatch + INPUT schema breakage will cause validator rejections on day one. Must land before anything consumes the validator.
- **1.1 is the fulcrum** — providers encapsulate what the use cases need. Shipping them first means 1.2 / 1.3 / 1.4 can proceed in parallel PRs against a stable interface.
- **1.4 (middleware) in parallel with 1.1/1.2** — different files, different reviewers, no conflicts.
- **1.6 (events) depends on 1.5 (cache)** — the consumer can't call `cache.invalidate()` until the cache exists.
- **1.7 (flag) last** — flip-on moment. Once merged, the new endpoints go live behind `ENABLE_NODE_CATALOG_V2=true`.

Critical path: **1.0 → 1.1 → 1.2 → 1.3 → 1.7 = ~5 days serial**. With 1.4, 1.5, 1.6 parallelized, total calendar is ~2 sprints at 1 engineer.

---

## 4. Task 1.0 — Pre-Flight Fixups

These are *not* part of Phase 1 itself, but Phase 1 depends on them. Land this task first, on its own PR, reviewed in isolation.

### 4.1 Fix the `INPUT` builtin schema

**File:** `workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/control_flow_schemas.py`

The INPUT schema's `input_fields` field declares `outputType="object"` but leaves nested `fields=[]`. Phase 0's `FormField.model_validator` rejects `OBJECT` without nested `fields`.

**Fix:** change the schema so the nested form declares at least one placeholder field, OR switch `outputType` to `"array"` of objects if the intent is a list of field descriptors (which it is, based on how INPUT nodes work). Likely the latter — every other place uses `array` for nested lists.

### 4.2 Align `SingleUpload` enum

**Canonical value:** `"UploadControl"` (what the frontend actually renders). Two CP files had `"UploadControl"` — wrong value that entered the codebase when Phase 0's contracts module was authored. Worker-sdk was correct.

**Files corrected:**
- `workflow-control-plane-service/app/layer3_adapters/contracts/form_schema.py` → `"UploadControl"`
- `workflow-control-plane-service/app/layer4_frameworks/auto_form_mapper/enums.py` → `"UploadControl"`
- Worker-sdk already correct; kept as `"UploadControl"`.

Note: `"UploadControl"` is a **distinct** control used in `get_variable_suggestions` to detect file-ref metadata objects. Both strings are valid but refer to different FE components. `FieldControl.SINGLE_UPLOAD` maps to `"UploadControl"`.

### 4.3 Round-trip the 13 builtins through `FormField.model_validate`

Add `test_builtin_schemas_roundtrip_through_form_field` in `workflow-control-plane-service/tests/unit/test_form_field_contracts.py`:

```python
@pytest.mark.parametrize("node_type", list(SCHEMAS.keys()))
def test_builtin_schema_input_roundtrips(node_type):
    from app.layer2_application.features.list_node_palette.use_cases.control_flow_schemas import SCHEMAS
    for field in SCHEMAS[node_type]["input_schema"]:
        FormField.model_validate(field)
```

Same for `output_schema`. This is the tripwire. If it fails, fix the builtin schema; don't loosen the DTO.

### 4.4 Tests

- Round-trip all 13 builtins (26 assertions).
- Round-trip the 4 bundled workers (`http-request-worker`, `agent-worker`, `wait-worker`, `mapping-data-worker`) once their SDKs re-emit.

### 4.5 Rollback

Self-contained. Revert the schema fix if the validator is too strict; the pydantic module stays dormant so loose builtins keep working.

### 4.6 Effort

~1 day. ~40 LoC schema diffs + ~60 LoC tests.

---

## 5. Task 1.1 — NodeProvider Implementations

Three concrete `NodeProvider`s, each wrapping an existing data source.

### 5.1 BuiltinNodeProvider

**New file:** `workflow-control-plane-service/app/layer2_application/providers/builtin_node_provider.py`

Reads from `control_flow_schemas.SCHEMAS` and `node_type_registry.get_all_descriptors()`. Returns 13 `NodeDefinitionSummary` entries on `list_summaries()`, one per `NodeType` except hybrid ones. Returns the matching `NodeDefinitionDetail` on `get_detail(id, spec_version)` with full `input_schema`/`output_schema` parsed into `FormField`.

**Key points:**
- `id` is `"builtin:<lowercase_node_type>"` (e.g., `"builtin:condition"`).
- `spec_version` is always `"1.0.0"` for Phase 1 (builtins don't get bumped independently).
- `provider="builtin"`, `runtime=NodeRuntime.ENGINE` for control-flow types, `NodeRuntime.PENDING` for INPUT/HUMAN_ACTION/DATA_EDIT/FILE_UPLOAD.
- `get_detail(id, spec_version)` rejects `spec_version != "1.0.0"` with `ValueError`.

### 5.2 WorkerNodeProvider

**New file:** `workflow-control-plane-service/app/layer2_application/providers/worker_node_provider.py`

Wraps `IWorkerRegistryClient`. On `list_summaries()`:
- Call `client.list_workers()`.
- Filter out entries with empty `worker_type` (Phase 0 guard; defense in depth).
- Filter out unhealthy workers (`health_status not in {"", "healthy", "active"}`).
- Map each to `NodeDefinitionSummary`.

On `get_detail(id, spec_version)`:
- Parse `id` → strip `"worker:"` prefix → worker_type.
- Call `client.get_worker_by_type(worker_type)` (add this method to the client — see §5.4 below).
- If `spec_version` is `None`, return the registered worker's `spec_version`. If pinned, verify match; mismatch raises.
- Map to `NodeDefinitionDetail` with `input_schema: list[FormField]` by `FormField.model_validate(...)` on each field. Malformed entries → log + raise `ValueError`.

### 5.3 BusinessNodeProvider

**New file:** `workflow-control-plane-service/app/layer2_application/providers/business_node_provider.py`

Wraps `INodeDefinitionRepository`. Resolves via `NodeDefinitionResolver` from Phase 0.

On `list_summaries(tenant_id)`:
- `repo.list_by_tenant(tenant_id)` + `repo.list_by_tenant("system")` (merge system + tenant; tenant wins on id collision).
- Map each `NodeDefinition` to `NodeDefinitionSummary`.
- Use `nd.spec_version` populated by Phase 0.

On `get_detail(id, spec_version)`:
- Strip `"business:"` prefix → definition id.
- `resolver.resolve(def_id, repo, spec_version=spec_version)` — enforces pinned-version match or returns leaf version.
- Build `NodeDefinitionDetail` from the `ResolvedNodeDefinition` + original `NodeDefinition` record.

### 5.4 Add `get_worker_by_type` to `IWorkerRegistryClient`

**File:** `workflow-control-plane-service/app/layer2_application/interfaces/worker_registry_client_interface.py` + `app/layer4_frameworks/clients/http_worker_registry_client.py`

The client today exposes `get_worker(worker_id: str)` (by UUID). Providers need lookup by `worker_type` (stable). Add:

```python
def get_worker_by_type(self, worker_type: str) -> dict | None:
    """Lookup via the executor's /workers/type/{worker_type} endpoint."""
```

Implement on the HTTP side by calling a new executor route `GET /workers/type/{worker_type}` (add if not present) or by filtering `list_workers()` client-side for Phase 1 (simpler; optimize later).

### 5.5 Tests

**New files** (all in `workflow-control-plane-service/tests/unit/`):
- `test_builtin_node_provider.py` — list returns 13 entries, each validates, detail for unknown id returns None, detail with wrong spec_version raises.
- `test_worker_node_provider.py` — filters empty worker_type + unhealthy workers; malformed schemas surface as errors.
- `test_business_node_provider.py` — merges system + tenant; resolver integration; pinned version mismatch raises.

Target: ~15 new assertions per file, ~45 total.

### 5.6 Rollback

Module is not yet reachable from any endpoint until Task 1.3 wires the controller. Safe to revert.

### 5.7 Effort

~1.5 days. ~400 LoC + 250 LoC tests.

---

## 6. Task 1.2 — Use Cases

Three use cases, auto-discovered by the existing `load_features()` scanner. Each must declare its dependencies via `__init__` kwargs so bootstrap can wire them.

### 6.1 `ListNodeCatalogUseCase`

**New path:** `app/layer2_application/features/list_node_catalog/use_cases/list_node_catalog_use_case.py`

```python
@dataclass
class ListNodeCatalogCommand:
    tenant_id: str = "system"

@dataclass
class ListNodeCatalogResult:
    definitions: list[NodeDefinitionSummary] = field(default_factory=list)
    category_tree: dict[str, list[str]] = field(default_factory=dict)  # category → ids
    generated_at: str = ""     # ISO-8601 UTC
    etag: str = ""             # SHA-256 hex, set by controller
    total: int = 0

class ListNodeCatalogUseCase:
    def __init__(
        self,
        logger: ILogger,
        builtin_node_provider: BuiltinNodeProvider,
        worker_node_provider: WorkerNodeProvider,
        business_node_provider: BusinessNodeProvider,
        node_catalog_cache: NodeCatalogCache,
    ): ...
```

Order of operations:
1. Check `cache.get(tenant_id)` → if hit, return with cached `etag`.
2. Fan out to all three providers in sequence (order = deterministic merge for ETag stability).
3. Compute ETag = `sha256(sorted(summaries, key=id) + generated_at_minute)`.
4. Populate `category_tree` by bucketing `summary.category`.
5. Write to cache.
6. Return.

### 6.2 `GetNodeCatalogDetailUseCase`

**New path:** `app/layer2_application/features/get_node_catalog_detail/use_cases/get_node_catalog_detail_use_case.py`

```python
@dataclass
class GetNodeCatalogDetailCommand:
    node_id: str              # "builtin:condition" | "worker:http" | "business:abc"
    spec_version: str | None = None
    tenant_id: str = "system"

@dataclass
class GetNodeCatalogDetailResult:
    detail: NodeDefinitionDetail | None
    etag: str = ""
```

Dispatch by `id` prefix to the right provider. Return `None` on not-found; the controller maps to 410.

### 6.3 `GetNodeCatalogDetailsBatchUseCase`

**New path:** `app/layer2_application/features/get_node_catalog_details_batch/use_cases/get_node_catalog_details_batch_use_case.py`

```python
@dataclass
class NodeRef:
    node_id: str
    spec_version: str | None = None

@dataclass
class GetNodeCatalogDetailsBatchCommand:
    refs: list[NodeRef] = field(default_factory=list)
    tenant_id: str = "system"

@dataclass
class GetNodeCatalogDetailsBatchResult:
    details: list[NodeDefinitionDetail] = field(default_factory=list)
    missing: list[NodeRef] = field(default_factory=list)
    etag: str = ""
```

Call `GetNodeCatalogDetailUseCase` per ref; accumulate found vs missing. Use `asyncio.gather` if any provider becomes async (for Phase 1, synchronous is fine since every provider is in-process).

**Cap at 50 refs per request** (return 400 on overflow). Protects the executor from being hammered when the frontend misuses the endpoint.

### 6.4 Bootstrap wiring

**File:** `workflow-control-plane-service/bootstrap.py`

Add to `available_dependencies`:
```python
"builtin_node_provider": BuiltinNodeProvider(),
"worker_node_provider": WorkerNodeProvider(client=worker_registry_client),
"business_node_provider": BusinessNodeProvider(
    repo=node_definition_repo,
    resolver=NodeDefinitionResolver(),
),
"node_catalog_cache": NodeCatalogCache(ttl_seconds=30),
```

`load_features()` auto-discovers the three new use cases on next bootstrap; no manual registry edits.

### 6.5 Tests

- `test_list_node_catalog_use_case.py` — cache miss → populate → hit; tenant merging; category tree correctness.
- `test_get_node_catalog_detail_use_case.py` — prefix dispatch; spec_version pinning; missing ref handling.
- `test_get_node_catalog_details_batch_use_case.py` — partial success (3 found, 1 missing); over-50 rejection.

Target ~30 assertions.

### 6.6 Effort

~2 days. ~300 LoC + 200 LoC tests.

---

## 7. Task 1.3 — Controller & Routes

**New file:** `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/node_catalog_controller.py`

```python
router = APIRouter(tags=["node-catalog"])

@router.get("/node-catalog")
async def list_node_catalog(
    tenant_id: str = "system",
    if_none_match: str | None = Header(None),
    usecase: ListNodeCatalogUseCase = Depends(_get_list_uc),
) -> Response: ...

@router.get("/node-catalog/{ref:path}")     # ref = "worker:http@1.2.0" or "business:abc"
async def get_node_catalog_detail(
    ref: str,
    if_none_match: str | None = Header(None),
    usecase: GetNodeCatalogDetailUseCase = Depends(_get_detail_uc),
) -> Response: ...

@router.post("/node-catalog/details")       # POST not GET — batch param list too large for query
async def get_node_catalog_details_batch(
    payload: BatchRefsDto,
    usecase: GetNodeCatalogDetailsBatchUseCase = Depends(_get_batch_uc),
) -> Response: ...
```

**Notes:**
- `{ref:path}` path converter so `@` and `:` characters don't get mangled.
- **Batch is POST, not GET.** Rolling plan suggested `GET /node-catalog/details?ids=a@1,b@2,…` — this breaks at ~50 refs due to URL length limits and caches the wrong thing. POST is correct.
- **410 Gone** semantics: if detail returns `None`, respond 410 with the last-known `NodeDefinitionSummary` in the body under `{"last_known": {...}}`. Phase 1 can't provide a last-known (no snapshots yet); return 404 with `{"error": "not found"}` and upgrade to 410 in a follow-up. Document this.
- **Ref parser**: add `parse_node_ref(ref: str) -> tuple[str, str | None]` at `app/layer1_domain/value_objects/node_ref.py`. Parse `<id>@<version>` → `(id, version)`; `<id>` → `(id, None)`.

### 7.1 Route registration

**File:** `app/layer3_adapters/controllers/restful/v1/__init__.py`

Mount at `/v2` prefix behind the feature flag:

```python
if settings.ENABLE_NODE_CATALOG_V2:
    app.include_router(node_catalog_router, prefix="/v2", tags=["node-catalog"])
```

When the flag is off, the new endpoints 404. When on, both old `/node-palette` and new `/v2/node-catalog` serve requests (dual-read).

### 7.2 Response envelope interaction

`ResponseWrapperMiddleware` wraps every response in `{status: "ok", data: ...}`. ETag middleware (Task 1.4) must compute the hash *after* wrapping, not before, so the client's `If-None-Match` matches what the server computed last time.

### 7.3 DTOs

**New file:** `app/layer3_adapters/controllers/restful/v1/dtos/node_catalog_dtos.py`

```python
class BatchRefsDto(BaseModel):
    refs: list[str] = Field(..., max_length=50)  # ["worker:http@1.2.0", ...]
    tenant_id: str = "system"
```

### 7.4 Tests

- `tests/integration/test_node_catalog_endpoints.py` — full HTTP cycle for all three endpoints against in-memory CP.
- `test_list_returns_304_on_etag_match` — two requests; second sends `If-None-Match` = first's ETag → 304.
- `test_detail_returns_404_for_unknown_ref`.
- `test_batch_rejects_over_50_refs` → 400.
- `test_v2_endpoints_404_when_flag_off` — set `ENABLE_NODE_CATALOG_V2=false`; expect 404.

### 7.5 Effort

~1.5 days. ~250 LoC + 200 LoC tests.

---

## 8. Task 1.4 — ETag Middleware

### 8.1 Design

**New file:** `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/middleware/etag.py`

```python
class ETagMiddleware(BaseHTTPMiddleware):
    """Computes ETag for responses matching path_prefixes; honors If-None-Match.

    Runs AFTER ResponseWrapperMiddleware (outer → inner order):
      ResponseWrapperMiddleware → ETagMiddleware → app
    So ETag is computed over the already-wrapped {status, data: ...} body.
    """

    def __init__(self, app, path_prefixes: list[str] = None):
        super().__init__(app)
        self.path_prefixes = path_prefixes or ["/v2/node-catalog"]

    async def dispatch(self, request, call_next):
        if not any(request.url.path.startswith(p) for p in self.path_prefixes):
            return await call_next(request)

        response = await call_next(request)
        if response.status_code != 200:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        etag = 'W/"' + hashlib.sha256(body).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, max-age=30"})

        return Response(
            content=body,
            status_code=200,
            headers={**dict(response.headers), "ETag": etag, "Cache-Control": "private, max-age=30"},
            media_type=response.media_type,
        )
```

### 8.2 Registration

**File:** `v1/__init__.py`

```python
app.add_middleware(ETagMiddleware, path_prefixes=["/v2/node-catalog"])
app.add_middleware(ResponseWrapperMiddleware, ...)  # already present
```

**Order matters.** `add_middleware` adds in reverse — the *last* added is the *outermost*. So:
1. Add `ETagMiddleware` first (inner).
2. Add `ResponseWrapperMiddleware` second (outer).

Effect: request flow is Response Wrapper → ETag → app → ETag → Response Wrapper. ETag sees the wrapped body before the client does, which is what we want.

Verify with an integration test: `test_etag_header_present_on_v2_palette` that issues two requests and checks the ETag matches.

### 8.3 Tests

- `test_etag_round_trip` — GET → capture ETag → GET with `If-None-Match` → 304.
- `test_etag_not_applied_to_legacy_endpoint` — old `/node-palette` has no ETag header.
- `test_etag_changes_on_content_change` — mutate the cache; ETag differs.

### 8.4 Rollback

Remove `app.add_middleware(ETagMiddleware, ...)` call. Responses keep working without ETag.

### 8.5 Effort

~0.5 day. ~80 LoC + 60 LoC tests.

---

## 9. Task 1.5 — NodeCatalogCache

### 9.1 Design

**New file:** `workflow-control-plane-service/app/layer4_frameworks/caching/node_catalog_cache.py`

```python
@dataclass
class CachedPayload:
    result: ListNodeCatalogResult
    etag: str
    expires_at_epoch_ms: int

class NodeCatalogCache:
    def __init__(self, ttl_seconds: int = 30):
        self._ttl_seconds = ttl_seconds
        self._store: dict[str, CachedPayload] = {}

    def get(self, tenant_id: str) -> CachedPayload | None:
        entry = self._store.get(tenant_id)
        if entry is None or entry.expires_at_epoch_ms < _now_ms():
            return None
        return entry

    def put(self, tenant_id: str, result, etag: str) -> None:
        self._store[tenant_id] = CachedPayload(
            result=result,
            etag=etag,
            expires_at_epoch_ms=_now_ms() + self._ttl_seconds * 1000,
        )

    def invalidate(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._store.clear()
        else:
            self._store.pop(tenant_id, None)
```

Process-local memory. One cache per CP instance. Fine for Phase 1 — CP is behind a load balancer but each instance gets its own invalidate event via the broker, so they stay consistent within ~2s.

### 9.2 Tests

- `test_cache_hit_within_ttl`.
- `test_cache_miss_after_ttl`.
- `test_invalidate_clears_all_tenants_when_none`.
- `test_invalidate_single_tenant_leaves_others`.

### 9.3 Effort

~0.25 day. ~50 LoC + 40 LoC tests.

---

## 10. Task 1.6 — Executor `catalog.changed` Event + CP Subscriber

### 10.1 Executor publishes

**File:** `worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py`

Today this has no event publisher injected. Add one:

```python
class SdkRegisterWorkerUseCase:
    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        event_publisher: IEventPublisher | None = None,
        **kwargs: Any,
    ) -> None:
        ...
        self.event_publisher = event_publisher

    def execute(self, input: SdkRegisterWorkerInput) -> dict:
        ...
        self.registry_store.register(worker)
        if self.event_publisher is not None:
            self.event_publisher.publish(
                topic="catalog.changed",
                event={
                    "action": "registered",
                    "worker_type": worker.worker_type,
                    "spec_version": worker.spec_version,
                    "tenant_id": "system",  # worker-scope = system tenant for Phase 1
                },
            )
        ...
```

Mirror changes in `deregister_worker_usecase.py` and `worker_heartbeat_usecase.py` (the latter only if health transitions from healthy→unhealthy or vice versa).

**Bootstrap:** `worker-executor-service/bootstrap.py` — wire a publisher in `available_dependencies`. Use the existing console publisher in mock mode; Kafka/EventMesh otherwise.

**Interface:** add `worker-executor-service/app/layer2_application/interfaces/event_publisher_interface.py` if not present. The CP already has an equivalent interface; mirror it.

### 10.2 CP subscribes

**New file:** `workflow-control-plane-service/app/layer4_frameworks/messaging/consumers/catalog_changed_consumer.py`

```python
class CatalogChangedConsumer:
    def __init__(self, broker_client, cache: NodeCatalogCache, logger: ILogger):
        self.broker_client = broker_client
        self.cache = cache
        self.logger = logger

    async def start(self) -> None:
        await self.broker_client.subscribe_to_topic("catalog.changed", self._on_event)

    def _on_event(self, event: dict) -> None:
        tenant_id = event.get("tenant_id", "system")
        self.logger.info("catalog.changed — invalidating", tenant_id=tenant_id)
        self.cache.invalidate(tenant_id)
```

### 10.3 Wiring

**File:** `workflow-control-plane-service/main.py`

In the lifespan context, start the consumer:

```python
catalog_consumer = CatalogChangedConsumer(
    broker_client=container["_broker"],
    cache=container["node_catalog_cache"],
    logger=container["logger"],
)
await catalog_consumer.start()
```

Bootstrap already has a precedent for this with `SocketIORealtimeConsumer` and `PushGatewayRealtimeConsumer`. Same pattern.

### 10.4 Tests

- `test_register_emits_catalog_changed` — register via executor use case with a fake publisher; assert one event emitted with action="registered".
- `test_deregister_emits_catalog_changed`.
- `test_cp_consumer_invalidates_on_event` — fake broker dispatches; assert `cache.invalidate(tenant_id)` called.
- Integration: `test_register_invalidates_cp_cache_within_2s` — full console-messaging round trip in mock mode.

### 10.5 Pitfalls

- **Event ordering.** Console mode is synchronous; Kafka is not. Tests in mock should not rely on ordering; use retry-with-timeout for the round-trip test.
- **Broker missing the event.** TTL is the fallback: after 30 seconds, cache expires anyway. Acceptable.
- **Duplicate events.** Invalidate is idempotent. No concern.
- **`tenant_id` on worker events.** Workers are system-scoped today. If multi-tenant workers land later, extend the event shape. For Phase 1, hardcode `"system"` and document.

### 10.6 Effort

~1.5 days. ~200 LoC (use-case hook + consumer) + 150 LoC tests.

---

## 11. Task 1.7 — Feature Flag

### 11.1 Declaration

**File:** `workflow-control-plane-service/app/layer4_frameworks/config/app_config.py`

Match the existing boolean-from-env pattern:

```python
ENABLE_NODE_CATALOG_V2: bool = os.getenv(
    "ENABLE_NODE_CATALOG_V2", "false"
).lower() in ("1", "true", "yes")

STRICT_CONTRACT_VALIDATION: bool = os.getenv(
    "STRICT_CONTRACT_VALIDATION", "false"
).lower() in ("1", "true", "yes")
```

Default `False` for both. Flip `true` in non-prod first, then staging, then prod (see §12).

### 11.2 Consumers

- **Router registration** (Task 1.3): `if settings.ENABLE_NODE_CATALOG_V2: app.include_router(node_catalog_router, ...)`.
- **Pydantic validation at register boundary** (deferred from Phase 0 Task 0.3): the executor doesn't have CP's contracts module, so validation has to happen on the CP side — specifically in `WorkerNodeProvider.get_detail()` when parsing the registered worker's `input_schema` into `FormField`. Gate on `settings.STRICT_CONTRACT_VALIDATION`: if on, a malformed worker surfaces as a 422 from the detail endpoint; if off, fall back to passing the dict through unvalidated.
- **Legacy endpoint deprecation header**: when `ENABLE_NODE_CATALOG_V2=True`, the old `/node-palette` responds with `Deprecation: true` + `Sunset: <date>` headers (see §12 Phase 3).

### 11.3 Tests

- `test_v2_endpoints_404_when_flag_off`.
- `test_v2_endpoints_serve_when_flag_on`.
- `test_strict_validation_rejects_malformed_worker_schema_when_flag_on`.
- `test_lenient_validation_passes_malformed_worker_schema_when_flag_off` — ensures the old endpoint never regresses even if a worker ships bad schemas.

### 11.4 Effort

~0.25 day. ~20 LoC + 30 LoC tests.

---

## 12. Cross-Cutting Concerns

### 12.1 Response envelope + ETag ordering

The single biggest integration risk. `ResponseWrapperMiddleware` wraps `{status: "ok", data: {...}}`. ETag must compute over the wrapped body so both sides (server writing, client reading) compute the same hash.

Verification: add a unit test that exercises the middleware chain order directly. See §8.2.

### 12.2 Executor doesn't import CP contracts

Phase 0 correctly placed contracts in `layer3_adapters/contracts/` (CP-local). Phase 1 validates on the CP side in `WorkerNodeProvider`, not in the executor. The executor just stores whatever shape it receives; malformed schemas surface downstream when the CP tries to parse. This is a deliberate decision: coupling the executor to the CP's contracts module would require either package extraction (Phase 4) or duplicated code. Phase 1 pays the cost of discovering bad schemas *per read* instead of *per write*; the cost is small since pydantic parsing is cached via the list-cache ETag path.

### 12.3 `tenant_id` scoping for worker catalog events

Workers today are system-scoped; every tenant sees the same worker list. The `catalog.changed` event payload uses `tenant_id="system"`; the CP consumer invalidates only the system entry. If business tenants' caches are also affected by system-worker changes (they are — since business nodes can wrap system workers), the consumer must invalidate **all** tenant entries on a system event.

Implementation: `cache.invalidate(None)` on system-scoped events. Wasteful if tenants rarely change, but correct.

### 12.4 Cold-start caching

First request after boot: cache miss → full fan-out → populate. Ensure providers are cheap to call (list_workers is one HTTP hop to executor; node_definition_repo is in-memory or HANA). Don't preload on startup — not worth the complexity.

### 12.5 Batch endpoint flood protection

Cap at 50 refs (§7.3). Also document client-side batching convention: frontend should chunk workflows with >50 nodes into multiple batch calls.

### 12.6 Deprecation of `/node-palette`

When `ENABLE_NODE_CATALOG_V2=True`, the old endpoint responds with `Deprecation: true` + `Sunset: 2026-06-30` (6 months out). Frontend reads this and logs a warning in dev tools. Actual deletion of the endpoint is Phase 4.

---

## 13. Test Surgery Manifest

| File | Status | Approx. assertions |
|---|---|---|
| `tests/unit/test_form_field_contracts.py` (round-trip 13 builtins) | Extend | 26 |
| `tests/unit/test_builtin_node_provider.py` | **NEW** | 15 |
| `tests/unit/test_worker_node_provider.py` | **NEW** | 15 |
| `tests/unit/test_business_node_provider.py` | **NEW** | 15 |
| `tests/unit/test_list_node_catalog_use_case.py` | **NEW** | 10 |
| `tests/unit/test_get_node_catalog_detail_use_case.py` | **NEW** | 10 |
| `tests/unit/test_get_node_catalog_details_batch_use_case.py` | **NEW** | 10 |
| `tests/unit/test_node_catalog_cache.py` | **NEW** | 6 |
| `tests/unit/test_etag_middleware.py` | **NEW** | 6 |
| `tests/unit/test_catalog_changed_consumer.py` | **NEW** | 5 |
| `tests/integration/test_node_catalog_endpoints.py` | **NEW** | 15 |
| `tests/integration/test_register_invalidates_catalog_cache.py` | **NEW** | 3 |
| `worker-executor-service/tests/unit/test_sdk_register_worker_use_case.py` (event emission) | Extend | 3 |
| **Total** | — | **~139 new assertions across 13 files** |

Run-time impact: integration tests add ~5s. Negligible overall.

---

## 14. Rollback Plan per Task

| Task | Rollback | Blast radius if unfixed |
|---|---|---|
| **1.0** Pre-flight fixups | Code revert (schema diffs + enum alignment). | Validator rejects on startup when enabled; fix before enabling. |
| **1.1** Providers | Code revert. Providers are new; no consumer exists until 1.2–1.3. | None (dead code). |
| **1.2** Use cases | Code revert. Auto-discovery drops them. | None (dead code). |
| **1.3** Controller | Code revert + remove route registration. | New endpoints 404; old path unaffected. |
| **1.4** ETag middleware | Remove `add_middleware()` call. | Responses work without ETag; clients just lose the 304 optimization. |
| **1.5** Cache | Code revert. Use cases lose the `node_catalog_cache` dep → bootstrap skips them. | Use cases don't wire; endpoints 503 or 500. Mitigated by 1.7 flag. |
| **1.6** Events | Remove consumer from lifespan + drop `event_publisher` from executor use cases. | Cache only invalidates on TTL (30s max staleness). Acceptable. |
| **1.7** Feature flag | Flip `ENABLE_NODE_CATALOG_V2=false`. | Instant revert — new endpoints 404 immediately. |

**Primary rollback mechanism:** feature flag. Flipping to `false` restores pre-Phase-1 behavior with zero code change.

---

## 15. Effort Estimate (senior engineer)

| Task | LoC (add/mod) | Complexity | Calendar |
|---|---|---|---|
| **1.0** Pre-flight fixups | ~100 LoC schemas + tests | Easy | 1 day |
| **1.1** NodeProviders | ~400 LoC + 250 LoC tests | Medium | 1.5 days |
| **1.2** Use cases | ~300 LoC + 200 LoC tests | Medium | 2 days |
| **1.3** Controller + routes | ~250 LoC + 200 LoC tests | Medium | 1.5 days |
| **1.4** ETag middleware | ~80 LoC + 60 LoC tests | Easy-Medium | 0.5 day |
| **1.5** Cache | ~50 LoC + 40 LoC tests | Easy | 0.25 day |
| **1.6** Events + consumer | ~200 LoC + 150 LoC tests | Medium-Hard | 1.5 days |
| **1.7** Feature flag | ~20 LoC + 30 LoC tests | Easy | 0.25 day |
| **Total** | **~2,330 LoC** | — | **~8.5 engineer-days (~2 sprints with buffer)** |

**Critical path:** 1.0 → 1.1 → 1.2 → 1.3 → 1.7 = **6 days serial**. With 1.4, 1.5, 1.6 paralleled in separate PRs, calendar compresses to ~7 days.

---

## 16. Acceptance Criteria (ties to redesign doc §13)

- [ ] `GET /v2/node-catalog?tenant_id=X` returns `< 50KB` for a tenant with 200 business nodes + 20 workers + 13 builtins.
- [ ] Repeated list call with same `If-None-Match` returns 304.
- [ ] `GET /v2/node-catalog/worker:http@1.2.0` returns `NodeDefinitionDetail` with `input_schema: list[FormField]` parsed successfully.
- [ ] Worker registers → within 2 seconds the next list call shows the new entry (cache invalidated by event).
- [ ] Legacy `/node-palette` still works when flag is off; returns identical node set.
- [ ] Legacy `/node-palette` returns `Deprecation: true` header when flag is on.
- [ ] Worker with malformed input_schema causes 422 on its detail endpoint when `STRICT_CONTRACT_VALIDATION=true`; no effect on other workers.
- [ ] Batch endpoint rejects >50 refs with 400.

---

## 17. Verification (end-to-end)

After all tasks merge and `ENABLE_NODE_CATALOG_V2=true` flipped:

1. **Unit suite green** — all three services, no regressions.
2. **Legacy parity** — `diff <(curl /node-palette | jq '.data.categories | add')  <(curl /v2/node-catalog | jq '.data.definitions')` shows same node IDs.
3. **ETag round trip** —
   ```bash
   ETAG=$(curl -sI /v2/node-catalog | grep -i etag | cut -d' ' -f2)
   curl -sI -H "If-None-Match: $ETAG" /v2/node-catalog | head -1
   # expect: HTTP/1.1 304 Not Modified
   ```
4. **Event invalidation** —
   ```bash
   # Register a worker via executor gRPC, then re-list within 2s.
   # Expect the new worker in the response (and a different ETag).
   ```
5. **Batch endpoint** —
   ```bash
   curl -X POST /v2/node-catalog/details -d '{"refs": ["builtin:condition", "worker:http@2.0.0"]}'
   # expect 200 with "details" array of length 2
   ```
6. **Strict validation** — stage a worker with a typo in `field_control`; expect 422 on `GET /v2/node-catalog/worker:bad`.

---

## 18. File Manifest

### Modified files (7)

```
workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/control_flow_schemas.py (Task 1.0)
workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/__init__.py (router registration, middleware)
workflow-control-plane-service/app/layer2_application/interfaces/worker_registry_client_interface.py (get_worker_by_type)
workflow-control-plane-service/app/layer4_frameworks/clients/http_worker_registry_client.py
workflow-control-plane-service/app/layer4_frameworks/config/app_config.py (feature flags)
workflow-control-plane-service/bootstrap.py (new deps)
workflow-control-plane-service/main.py (lifespan — start catalog consumer)
worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py (event)
worker-executor-service/app/layer2_application/features/deregister_worker/use_cases/deregister_worker_usecase.py (event)
worker-executor-service/bootstrap.py (publisher wiring)
worker/worker-sdk/worker_sdk/layer4_frameworks/auto_form_mapper/enums.py (SingleUpload rename)
```

### New files (~23)

```
app/layer2_application/providers/__init__.py
app/layer2_application/providers/builtin_node_provider.py
app/layer2_application/providers/worker_node_provider.py
app/layer2_application/providers/business_node_provider.py
app/layer2_application/features/list_node_catalog/__init__.py
app/layer2_application/features/list_node_catalog/use_cases/list_node_catalog_use_case.py
app/layer2_application/features/get_node_catalog_detail/__init__.py
app/layer2_application/features/get_node_catalog_detail/use_cases/get_node_catalog_detail_use_case.py
app/layer2_application/features/get_node_catalog_details_batch/__init__.py
app/layer2_application/features/get_node_catalog_details_batch/use_cases/get_node_catalog_details_batch_use_case.py
app/layer3_adapters/controllers/restful/v1/node_catalog_controller.py
app/layer3_adapters/controllers/restful/v1/dtos/node_catalog_dtos.py
app/layer3_adapters/controllers/restful/v1/middleware/etag.py
app/layer4_frameworks/caching/__init__.py
app/layer4_frameworks/caching/node_catalog_cache.py
app/layer4_frameworks/messaging/consumers/catalog_changed_consumer.py
app/layer1_domain/value_objects/node_ref.py
worker-executor-service/app/layer2_application/interfaces/event_publisher_interface.py
```

Plus ~13 test files (see §13).

---

## 19. Open Questions (for reviewer)

1. **Endpoint naming:** `/v2/node-catalog` (rolling-plan convention) vs `/v2/node-palette` (Phase 0 doc's naming). Recommendation: **`/v2/node-catalog`** because "catalog" is the right word for list+detail+batch semantics; "palette" historically meant only the UI drawer. Old `/node-palette` stays unchanged during dual-read.

2. **Batch endpoint verb:** POST (safer — large bodies) vs GET (cacheable). Recommendation: **POST**. The rolling plan proposed GET with comma-separated `ids` query param; URL length hits limits fast with 50 `id@version` refs.

3. **410 vs 404 on missing detail:** Phase 1 can't yet provide a "last-known" snapshot for 410. Recommendation: **404 with `{"error": "not found", "ref": "..."}` for Phase 1**; upgrade to 410 in Phase 4 once snapshots land.

4. **Tenant scope for `catalog.changed` events:** Workers are system-scoped today; recommendation is `invalidate(None)` (all tenants) on system events, even though it looks wasteful. Cheap enough per tenant; correctness > optimization. Approve?

5. **`ENABLE_NODE_CATALOG_V2` default:** `false` (opt-in) vs `true` (opt-out). Recommendation: **`false`** until Phase 3 rollout. Matches the "new endpoint ships dark" convention.

6. **TTL value:** 30s recommended. Product confirmation needed that 30s worst-case palette staleness is acceptable (when the event bus misses or the CP instance never sees the event). Alternative: 10s + event-driven.

---

## 20. Checklist for Reviewer

- [ ] Task 1.0 lands as a standalone PR before any Phase 1 task is reviewed.
- [ ] PR descriptions cross-link to this plan, the redesign doc, and the rolling plan.
- [ ] Feature flag is default-off and documented in `.env.example`.
- [ ] Legacy `/node-palette` parity test (§17 step 2) passes in CI on every Phase 1 PR.
- [ ] ETag middleware is gated to `/v2/*` prefixes only (no global application).
- [ ] Event publisher interface exists in both services (CP has one; executor needs to add).
- [ ] `docs/design/node-palette-catalog-implementation-plan.md` updated to mark Phase 1 tasks as "implemented" on each merge.
