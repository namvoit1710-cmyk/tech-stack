# Phase 0 "Hard Blockers" — Detailed Execution Plan

**Companion docs:**
- `docs/design/node-palette-catalog-redesign.md` — target architecture
- `docs/design/node-palette-catalog-implementation-plan.md` — rolling Phase 0→4 plan

This document expands Phase 0 of the rolling plan into a per-task execution spec. Each of the four tasks below must land before Phase 1 (new `/v2/node-palette` + `/v2/node-definitions` API surface) is safe to ship.

---

## 1. Context

Phase 0 exists because four flaws in the current node-palette pipeline **must** be repaired before Phase 1 introduces versioned endpoints and strict DTOs. Each flaw has cascading consequences:

1. **Empty `worker_type` accepted** → palette falls back to the worker UUID → every worker restart silently churns palette IDs → frontend cache keys and CP in-memory caches get junk entries.
2. **Single conflated `version` field** → no way to distinguish worker build identity from contract identity → breaking schema changes affect every workflow in flight.
3. **No shared DTO surface** → REST responses are `dataclasses.asdict(result)` passthrough → field typos and missing keys ship silently → `extra="forbid"` validation has no home.
4. **Unversioned references resolve implicitly** → Phase 1 adding a version column would silently break legacy workflows unless the resolution semantics are pinned down first.

All four must land before any code in Phase 1 is merged. They are "additive / defensive" (no behavior switch), so they can ship without a feature flag and be bisected independently if something breaks.

---

## 2. Scope & Non-Goals

### In scope
- Reject empty `worker_type` at three boundaries (SDK bootstrap, gRPC servicer, use case).
- Add `spec_version: str = "1.0.0"` to worker SDK entity, executor registration entity, protobuf, HTTP DTO, palette projection, business `NodeDefinition`.
- Create `workflow-control-plane-service/app/layer3_adapters/contracts/` with pydantic v2 DTOs (`FormField`, `NodeDefinitionSummary`, `NodeDefinitionDetail`, `NodeProvider`) — **added but not enforced** in Phase 0.
- Add `spec_version: str | None = None` to `WorkflowNode`; extend `NodeDefinitionResolver` signature to accept optional `spec_version`; add helper `resolve_worker_spec_version()`; document the "unversioned → latest non-deprecated" rule.

### Explicitly out of scope (Phase 1+)
- New endpoints (`/v2/node-palette`, `/v2/node-definitions/{id}`).
- ETag caching or `catalog.changed` events.
- Enforcing pydantic validation on live worker registration (`STRICT_CONTRACT_VALIDATION=True`).
- Deprecation flow for definitions.
- Frontend integration.

---

## 3. Recommended Merge Order

```
0.4 (resolution shape)     ──┐
                             ├─►  can land in any order; each merges independently
0.1 (reject empty worker_type) ─┤
                             ├─►  keep 0.2 ahead of 0.3 in time
0.2 (spec_version plumbing)  ──┤
                             └─►  0.3 (pydantic contracts) — DTOs reference spec_version
```

**Rationale (deviates from the rolling plan's implicit 0.1→0.2→0.3→0.4 order):**

- **0.4 first** is preferable because it's fully orthogonal — no contact with registration or serialization. Shipping it first de-risks the resolver change from the new-field change: if a regression surfaces later, bisect is clean.
- **0.1 and 0.2 are parallel PRs** — different files; only both touch `sdk_register_worker_usecase.py` and `worker_registry_servicer.py`; trivial merge.
- **0.3 depends on 0.2 being merged**, not just designed — `NodeDefinitionDetail.spec_version` is a required field; shipping the DTO before `spec_version` exists on the source entities means every caller constructs invalid models on day one.

Serial critical path = **0.2 → 0.3 = ~3.5 days**. With 0.1 and 0.4 paralleled, the full phase fits one sprint (~5 engineer-days).

---

## 4. Task 0.1 — Reject Empty `worker_type` End-to-End

### Problem

Three boundaries accept empty `worker_type` today:

| Layer | File | Current behavior |
|---|---|---|
| SDK bootstrap | `worker/worker-sdk/worker_sdk/layer3_adapters/controllers/worker_server/__init__.py:48-91` | Registers anyway; logs only |
| Executor gRPC | `worker-executor-service/app/layer3_adapters/controllers/grpc/worker_registry_servicer.py:57-86` | Direct pass-through into `SdkRegisterWorkerInput` |
| Executor use case | `worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py:51-100` | No guard; empty string becomes capability domain |

This lets malformed workers reach the CP palette, which then falls back to the worker UUID (`list_node_palette_use_case.py:155-156`). UUID = not stable across restarts → palette-entry IDs churn → frontend & CP caches corrupt.

### Files to modify

| File | Change |
|---|---|
| `worker/worker-sdk/worker_sdk/layer1_domain/entities/node_type_definition.py` | Add `__post_init__` raising `ValueError` if `worker_type` is blank. Fails at construction. |
| `worker/worker-sdk/worker_sdk/layer3_adapters/controllers/worker_server/__init__.py` (multi-type branch ~L55, single-type branch ~L70) | Before `await registry.register(...)`, assert `registration.worker_type.strip()`; raise `RuntimeError("WORKER_TYPE is required — refusing to register")`. |
| `worker-executor-service/app/layer3_adapters/controllers/grpc/worker_registry_servicer.py:57` | At start of `Register()`: `if not request.worker_type: await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_type is required")`. |
| `worker-executor-service/app/layer3_adapters/controllers/restful/v1/worker_controller.py` | Change `worker_type: str` on `SdkRegisterWorkerInputDto` to `worker_type: str = Field(..., min_length=1)` — FastAPI rejects at 422 before use case runs. |
| `worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py:51-60` | Guard: `if not input.worker_type or not input.worker_type.strip(): raise ValueError("worker_type is required")`. REST controller translates to 400; gRPC already guarded above. |
| `workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/list_node_palette_use_case.py:155-156` | Replace UUID fallback with: `if not worker_type: self.logger.warning("skipping worker with empty worker_type", worker_id=w.get("id")); continue` |

### Code snippets

**SDK entity guard** (`node_type_definition.py`):
```python
def __post_init__(self) -> None:
    if not self.worker_type or not self.worker_type.strip():
        raise ValueError(
            "NodeTypeDefinition.worker_type is required and cannot be blank"
        )
```

**Use case guard** (`sdk_register_worker_usecase.py`):
```python
async def execute(self, input: SdkRegisterWorkerInput) -> SdkRegisterWorkerOutput:
    if not input.worker_type or not input.worker_type.strip():
        raise ValueError("worker_type is required for SDK registration")
    self.logger.info("register_worker_request", worker_type=input.worker_type, ...)
    ...
```

**Palette fallback removal** (`list_node_palette_use_case.py`):
```python
# BEFORE
worker_type = w.get("worker_type", "")
worker_id = worker_type or w.get("id", "")

# AFTER
worker_type = w.get("worker_type", "")
if not worker_type:
    self.logger.warning(
        "palette skipping worker with empty worker_type",
        worker_registration_id=w.get("id"),
    )
    continue
worker_id = worker_type
```

### Tests

- **New file** `worker-executor-service/tests/unit/test_sdk_register_worker_use_case.py`:
  - `test_rejects_empty_worker_type`
  - `test_rejects_whitespace_only_worker_type`
  - `test_accepts_valid_payload`
- **New file** `worker-executor-service/tests/unit/test_worker_registry_servicer_contracts.py`:
  - `test_grpc_register_rejects_empty_worker_type` (mock servicer container, assert `INVALID_ARGUMENT`).
- **New file** (or extend existing) `worker/worker-sdk/tests/unit/test_node_type_definition.py`:
  - `test_blank_worker_type_raises`
- **Extend** `workflow-control-plane-service/tests/unit/test_node_palette_v2.py`:
  - `test_palette_skips_worker_with_empty_type` — provide a fake `worker_registry_client` returning `[{"id": "uuid-1", "worker_type": "", ...}]`; assert the palette has zero worker nodes.

Target: ~8 new assertions.

### Rollout options

**Option A (recommended): ship as single PR.** Rationale: UUID fallback was *already* broken for these workers; rejecting them restores correct behavior, doesn't regress it.

**Option B (defensive two-step):** PR1 logs a `warning` but still accepts; wait one release (~1 week); PR2 flips to reject. Use if there are known production workers with misconfigured env.

**Ops escape hatch:** for Option A, accept an env var `SDK_ALLOW_EMPTY_WORKER_TYPE=true` that skips the SDK-side assert only. Do NOT expose it executor-side — that gate must stay closed.

### Risks

- **In-flight test fixtures with empty `worker_type`.** Pre-grep confirmed no `WorkerRegistration(worker_type="")` fixtures exist; `worker_type=""` hits elsewhere are on `TaskInstance` entities, unrelated. Run `pytest -k "register or palette"` before merging.
- **Workers with misconfigured env in production** would lose registration. See Option B mitigation.

---

## 5. Task 0.2 — `spec_version` Plumbing

### Problem

Today `NodeTypeDefinition.version` serves two meanings ("my worker build" vs "my schema contract"). Mixing them means a worker can't ship a bugfix patch without appearing as a new contract to every workflow referencing it. Introduce `spec_version: str` as a distinct, semver-valued field. Default `"1.0.0"` for backward compat.

### Files to modify (13 files, ~120 LoC)

| Layer | File | Change |
|---|---|---|
| **SDK** | `worker/worker-sdk/worker_sdk/layer1_domain/entities/node_type_definition.py` | Add `spec_version: str = "1.0.0"` after `version`. Docstring must separate **build identity** (`version`) from **contract identity** (`spec_version`). |
| **SDK** | `worker/worker-sdk/worker_sdk/layer1_domain/entities/worker_registration.py` | Add `spec_version: str = "1.0.0"`. |
| **SDK** | `worker/worker-sdk/worker_sdk/layer3_adapters/controllers/worker_server/__init__.py:17-36, 62-91` | Thread `spec_version=nt.spec_version` in both branches. Default `"1.0.0"` where not present. |
| **SDK** | `worker/worker-sdk/worker_sdk/layer4_frameworks/providers/registry/http_worker_registry.py:23-47` | Emit `"spec_version": registration.spec_version` in the JSON payload. |
| **Executor domain** | `worker-executor-service/app/layer1_domain/entities/worker_registration.py` | Add `spec_version: str = "1.0.0"`. |
| **Executor app** | `worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py` | Add `spec_version: str = "1.0.0"` to `SdkRegisterWorkerInput`; forward into `WorkerRegistration` constructor. |
| **Executor app** | `worker-executor-service/app/layer2_application/features/list_workers/use_cases/list_workers_usecase.py` | Add `spec_version: str = "1.0.0"` to `ListWorkersOutput`; populate from `w.spec_version`. |
| **Executor adapter (REST)** | `worker-executor-service/app/layer3_adapters/controllers/restful/v1/worker_controller.py` | Add `spec_version: str = "1.0.0"` to `SdkRegisterWorkerInputDto`. |
| **Executor adapter (gRPC)** | `worker-executor-service/app/layer3_adapters/controllers/grpc/worker_registry_servicer.py:57-86` | `spec_version = request.spec_version or "1.0.0"` (explicit default — proto3 absent strings are `""`, not Python `"1.0.0"`). Add `spec_version=w.get("spec_version", "1.0.0")` in `_to_worker_detail` (~L28). |
| **Proto** | `proto/workflow/v1/worker_registry.proto` | Add `string spec_version = 16;` to `RegisterWorkerRequest`; `string spec_version = 15;` to `WorkerDetail`. **Additive = wire-compatible.** Run `proto/generate.sh` to regenerate stubs. |
| **CP adapter** | `workflow-control-plane-service/app/layer4_frameworks/clients/http_worker_registry_client.py` | No code change (dict passthrough); verify `"spec_version"` passes through untouched. |
| **CP use case** | `workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/list_node_palette_use_case.py` | (a) Update `_node()` helper (L14-40) to thread `spec_version="1.0.0"` into returned dict for BUILTIN_NODES. (b) `_fetch_worker_nodes` (L141-195): add `"spec_version": w.get("spec_version", "1.0.0")` to returned dict. (c) `_fetch_business_nodes` (L113-139): add `"spec_version": nd.spec_version` (new field). |
| **CP domain** | `workflow-control-plane-service/app/layer1_domain/entities/node_definition.py` | Add `spec_version: str = "1.0.0"`. Do **not** add to `validate()` — no rule yet in Phase 0. |

### Docstring additions (both `version` and `spec_version`)

```python
@dataclass
class NodeTypeDefinition:
    worker_type: str
    name: str = ""
    description: str = ""
    version: str = "0.1.0"          # BUILD identity — bump on any code change
    spec_version: str = "1.0.0"     # CONTRACT identity — bump only when input_schema
                                    # / output_schema / ports change in a way that
                                    # could affect workflows referencing this node
    ...
```

Copy the same two-line distinction into `WorkerRegistration` (SDK + executor).

### Tests

- **Extend** `workflow-control-plane-service/tests/unit/test_node_palette_v2.py`:
  - `test_every_palette_node_has_spec_version` — assert every node in `all_nodes` has `"spec_version"` and matches `r"^\d+\.\d+\.\d+$"`.
- **Extend** `workflow-control-plane-service/tests/unit/test_node_definition_entity.py`:
  - `test_spec_version_default` — `NodeDefinition().spec_version == "1.0.0"`.
- **New test** in `test_sdk_register_worker_use_case.py` (created in 0.1):
  - `test_spec_version_defaults_when_missing`
  - `test_spec_version_passthrough`

Target: ~6 new assertions.

### Risks / pitfalls

- **Proto backward-compat.** Additive field is wire-safe, but proto3 absent strings deserialize as `""`. Every read site must do `or "1.0.0"`; do **not** rely on the Python dataclass default alone.
- **gRPC `_to_worker_detail` at L28** silently drops new fields — add explicitly.
- **`dataclasses.asdict(result)` boundary leaks new field** on `NodeDefinition` endpoints. This is actually desirable (frontend gets `spec_version` for free) — but verify no snapshot tests break.

### Rollback

Fully additive. Revert the Python changes anytime. **Do not reuse proto field numbers 15/16** even on revert — reserve them per proto3 discipline.

---

## 6. Task 0.3 — Shared DTO Contracts Module

### Problem

REST responses are `dataclasses.asdict(result)` passthrough. No validation of required fields, no typo rejection. Phase 1's `/v2/node-palette` endpoints need a strict contract — but shipping those endpoints without the DTO foundation is self-defeating.

### Placement decision: `workflow-control-plane-service/app/layer3_adapters/contracts/`

**Rejected alternatives and why:**

1. **`layer1_domain/contracts/` (as the rolling plan §Task 0.3 suggested).** Violates the project's layering convention ("layer1/layer2 use dataclasses; pydantic at layer3/layer4 only"). The rolling plan was written assuming the rule would be relaxed; we'd rather honor the invariant until Phase 4 extracts the module as `packages/node-contracts/`.
2. **Promote `auto_form_mapper/` to pydantic in place.** The module is already imported as dataclasses-with-`to_dict()` by worker build-time tooling; flipping semantics breaks unrelated callers.
3. **Wrap `AutoFormField` dataclass inside pydantic via `arbitrary_types_allowed=True`.** Defeats the entire point — pydantic validators don't recurse into arbitrary types, so `extra="forbid"` and `@model_validator` checks would silently noop. This kills the value proposition.

### Files to create

| File | Purpose |
|---|---|
| `workflow-control-plane-service/app/layer3_adapters/contracts/__init__.py` | Re-export all symbols. |
| `workflow-control-plane-service/app/layer3_adapters/contracts/form_schema.py` | Pydantic v2 `FormField`, `FieldConfig`, `ControlProps`, `WrapperProps`, `ValidationRule`, `ConditionConfig`, `SelectOption` + enums (`OutputType`, `FieldControl`, `FieldWrapper`, `ConditionAction`, `ConditionOperator`, `ValidationMethod`) + `@model_validator` enforcing per-control-type constraints. All models `model_config = ConfigDict(extra="forbid")`. Copy verbatim from redesign doc §3.1. |
| `workflow-control-plane-service/app/layer3_adapters/contracts/node_definition_dto.py` | `NodeProvider` (Literal), `NodePort`, `NodeCapability`, `NodeVisibility`, `NodeRuntime`, `NodeDefinitionSummary`, `NodeDefinitionDetail(NodeDefinitionSummary)`. Mirror redesign §3. |
| `workflow-control-plane-service/app/layer3_adapters/contracts/providers.py` | `NodeProvider` Protocol: `list_summaries() -> list[NodeDefinitionSummary]`, `get_detail(id: str, version: str \| None) -> NodeDefinitionDetail`. |

### Feature-flagged validation (optional and default-off)

- `worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py`:
  After constructing `WorkerRegistration`, behind the env flag `STRICT_CONTRACT_VALIDATION` (default `False`), call `NodeDefinitionDetail.model_validate(...)`. This is the **only runtime consumer** in Phase 0 — it stays off by default so in-flight workers don't break.
- Phase 1 flips it to `True` alongside the `enable_catalog_v2` flag.

### Wiring rule for Phase 0

> **The contracts module is built but not enforced.** No endpoint consumes it yet. Phase 1 wires `/v2/node-palette` and `/v2/node-definitions/{id}` to return these DTOs.

This keeps Phase 0 safe: the module can be reverted freely, and if it has bugs they don't affect production.

### Tests

- **New file** `workflow-control-plane-service/tests/unit/test_form_field_contracts.py`:
  - `test_unknown_control_props_key_rejected` — extra key in `controlProps` → validation error
  - `test_select_without_options_rejected`
  - `test_switch_with_non_boolean_output_type_rejected`
  - `test_array_output_type_requires_nested_fields`
  - `test_roundtrip_all_builtin_schemas` — parameterized over every schema in `control_flow_schemas.py`; guarantees builtins aren't drifting from the new shape.
- **New file** `workflow-control-plane-service/tests/unit/test_node_definition_dto.py`:
  - `test_summary_forbids_extra_fields`
  - `test_detail_includes_summary_fields`
  - `test_detail_constructs_from_builtin_dict`
  - `test_detail_constructs_from_worker_dict`
  - `test_detail_constructs_from_business_node_dict`

Target: ~12 new assertions. Run time: negligible.

### Risks

- **Builtin schema drift.** The round-trip test on every `control_flow_schemas.py` entry is the tripwire. If that fails, **fix the schema, not the DTO** — the DTO is the target shape; loose builtins are the debt.
- **Frozen `extra="forbid"` rejects fields we forgot.** Mitigation: write tests **against the redesign doc §3 field list**, not against what current code emits. Any missing field is a bug we now catch before Phase 1.

### Rollback

Module is unused unless `STRICT_CONTRACT_VALIDATION=True`. Revert = dead-code removal. Harmless.

---

## 7. Task 0.4 — Unversioned Reference Resolution

### Problem

`WorkflowNode.worker_type` and `WorkflowNode.node_definition_id` have no version pinning. Phase 1 will add `spec_version`/`version` columns. Without the resolution rule formalized *now*, existing workflows silently break on Phase 1 deploy (their references become dangling).

The fix is conceptually small but semantically critical: **unversioned reference = latest non-deprecated version**.

### Files to modify

| File | Change |
|---|---|
| `workflow-control-plane-service/app/layer1_domain/entities/workflow.py` (`WorkflowNode` ~L122-146) | Add `spec_version: str \| None = None`. Default `None` = unversioned reference. Existing serialized rows deserialize to `None` — no migration. |
| `workflow-control-plane-service/app/layer1_domain/services/node_definition_resolver.py` | (a) Add optional kwarg `spec_version: str \| None = None` to `resolve()`. When `None`, current behavior (walk chain, take leaf). When a string, verify leaf's `spec_version == pinned`, else raise. (b) Add `resolved_spec_version: str = "1.0.0"` to `ResolvedNodeDefinition`; populate from leaf. (c) New helper `resolve_worker_spec_version(worker_type: str, pinned: str \| None, workers: list[dict]) -> str`: if pinned, return; else return `max()` of `spec_version` across matching workers using `packaging.version.parse` for semver comparison. |
| Engine dispatch site consuming `WorkflowNode.worker_type` | `orchestration_engine.py` — pass `node.spec_version` (may be `None`) through to the dispatcher. **Phase 0 dispatcher ignores the value.** The actual "resolve latest" happens in Phase 1 — Phase 0 only formalizes the shape. |

### Code snippets

**WorkflowNode extension:**
```python
@dataclass(kw_only=True)
class WorkflowNode(BaseDomainEntity):
    ...
    worker_type: str | None = None
    node_definition_id: str | None = None
    spec_version: str | None = None  # None = unversioned ref = "latest non-deprecated"
    ...
```

**Resolver helper:**
```python
from packaging.version import parse as parse_version

def resolve_worker_spec_version(
    self,
    worker_type: str,
    pinned: str | None,
    workers: list[dict],
) -> str:
    """Given a worker_type reference, return the spec_version to use.

    Phase 0: formalize the rule. Phase 1: wire engine to call this.

    Rule: pinned version wins. Unpinned references resolve to the
    highest spec_version among registered, non-deprecated workers
    matching `worker_type`.
    """
    if pinned:
        return pinned
    candidates = [
        w.get("spec_version", "1.0.0")
        for w in workers
        if w.get("worker_type") == worker_type
    ]
    if not candidates:
        return "1.0.0"
    return max(candidates, key=parse_version)
```

### Tests

- **Extend** `workflow-control-plane-service/tests/unit/test_node_definition_resolver.py`:
  - `TestSpecVersionResolution::test_resolve_returns_leaf_spec_version_when_none_pinned`
  - `TestSpecVersionResolution::test_resolve_accepts_pinned_spec_version`
  - `TestSpecVersionResolution::test_resolve_worker_spec_version_picks_max_when_unpinned`
  - `TestSpecVersionResolution::test_resolve_worker_spec_version_returns_pinned_value`
  - `TestSpecVersionResolution::test_resolve_worker_spec_version_default_when_no_match`

Target: ~5 new assertions.

### Risks

- **`packaging` dependency.** Already transitively available via pydantic; add to `requirements.txt` explicitly to be safe.
- **`ResolvedNodeDefinition` serialization.** If any consumer does `asdict(result)` and posts it over the wire, the new field rides along. Desirable but worth greping.

### Rollback

Field is defaulted (`None`, `"1.0.0"`). Helper method is new — revert removes nothing in use. Fully safe.

---

## 8. Cross-Cutting Concerns

### 8.1 Proto additive-field discipline

Field numbers 15 and 16 on `WorkerDetail` / `RegisterWorkerRequest` are reserved for `spec_version`. **Never reuse these numbers** even on revert. Add a proto comment:

```proto
// Reserved for future use; do not reuse numbers 15-16 (historically spec_version).
```

### 8.2 `STRICT_CONTRACT_VALIDATION` flag

- Declared in env config (e.g., `worker-executor-service/app/layer4_frameworks/config/app_config.py`) as `strict_contract_validation: bool = False`.
- Read only by `sdk_register_worker_usecase.py` in Phase 0.
- Flipping to `True` is a Phase 1 decision once all registered workers have been audited.

### 8.3 No feature flag for the rest

0.1, 0.2, 0.4 are all additive/defensive — no flag needed. Only 0.3's strict-validation escape hatch needs a flag, and it ships `False`.

---

## 9. Test Surgery Manifest

| File | Status | Approx. assertions |
|---|---|---|
| `worker-executor-service/tests/unit/test_sdk_register_worker_use_case.py` | **NEW** | 6 |
| `worker-executor-service/tests/unit/test_worker_registry_servicer_contracts.py` | **NEW** | 2 |
| `worker/worker-sdk/tests/unit/test_node_type_definition.py` | **NEW or extend** | 3 |
| `workflow-control-plane-service/tests/unit/test_form_field_contracts.py` | **NEW** | 6 |
| `workflow-control-plane-service/tests/unit/test_node_definition_dto.py` | **NEW** | 5 |
| `workflow-control-plane-service/tests/unit/test_node_palette_v2.py` | Extend | 3 |
| `workflow-control-plane-service/tests/unit/test_node_definition_entity.py` | Extend | 2 |
| `workflow-control-plane-service/tests/unit/test_node_definition_resolver.py` | Extend (`TestSpecVersionResolution` class) | 5 |
| **Total** | — | **~32 new assertions across 8 files** |

Run time impact: negligible (pure Python; no I/O, no network).

**Verification command sequence:**
```bash
# From repo root, after each PR:
cd worker-executor-service && python3 -m pytest tests/ -v
cd ../workflow-control-plane-service && python3 -m pytest tests/ -v
cd ../worker/worker-sdk && python3 -m pytest tests/ -v
```

---

## 10. Rollback Plan per Task

| Task | Rollback mechanism | Blast radius if left unfixed |
|---|---|---|
| **0.1** | Code revert. Escape hatch: `SDK_ALLOW_EMPTY_WORKER_TYPE=true` (SDK side only) for emergency. Do NOT expose executor-side. | Workers with misconfigured env can't register; palette loses those nodes; **no data corruption**. |
| **0.2** | Code revert for Python changes (fully additive). Proto: leave field numbers 15/16 **reserved**; drop Python wiring. | Missing `spec_version` everywhere defaults to `"1.0.0"`. Effectively no-op. |
| **0.3** | Code revert — module is unused in Phase 0 with `STRICT_CONTRACT_VALIDATION=False`. Dead-code removal, harmless. | None. |
| **0.4** | Code revert. New field `spec_version: str \| None = None` is default-`None`; existing serialized workflows missing it deserialize fine after revert. | Low. Legacy workflows keep working; the "latest non-deprecated" rule just isn't enforced yet. |

---

## 11. Effort Estimate (senior engineer)

| Task | LoC (add/mod) | Complexity | Calendar |
|---|---|---|---|
| **0.1** Reject empty `worker_type` | ~40 LoC across 6 files | Easy | 0.5 day |
| **0.2** `spec_version` plumbing | ~120 LoC across 13 files + proto regen | Medium | 1.5 days |
| **0.3** Pydantic contracts module | ~450 LoC (copy from redesign §3.1) + ~30 LoC wiring + ~80 LoC tests | Medium | 2 days |
| **0.4** Unversioned resolution | ~50 LoC across 3 files + ~40 LoC tests | Easy-Medium | 1 day |
| **Total** | **~700 LoC** | — | **~5 engineer-days (1 sprint with buffer)** |

**Critical path** = 0.2 → 0.3 = 3.5 days serial. With 0.1 and 0.4 paralleled, achievable in one sprint.

---

## 12. Verification (end-to-end)

After all four tasks merge:

1. **Unit suite green** — all three services.
2. **Proto regen compiles** — `proto/generate.sh` runs cleanly; generated stubs importable from both executor and any worker.
3. **Round-trip registration test:**
   - Start executor (`INFRA_MODE=mock`).
   - Register a worker with explicit `spec_version="2.0.0"` via gRPC.
   - Call `/api/v1/workers` and assert the response includes `spec_version: "2.0.0"`.
   - Call CP `/node-palette` and assert the worker entry has `spec_version: "2.0.0"`.
4. **Empty-worker-type rejection test:**
   - Attempt gRPC registration with `worker_type=""` → expect `INVALID_ARGUMENT`.
   - Attempt REST registration with `worker_type=""` → expect `422`.
   - Attempt SDK startup with blank `WORKER_TYPE` → expect `RuntimeError` at boot.
5. **Contracts module importable and validating:**
   ```python
   from app.layer3_adapters.contracts import NodeDefinitionDetail, FormField
   FormField.model_validate({"key": "x", "outputType": "string", "fieldConfig": {"fieldControl": "InputControl"}})  # ok
   FormField.model_validate({"key": "x", "outputType": "string", "extra": 1})  # raises ValidationError
   ```
6. **Resolver helper sanity:**
   ```python
   resolver.resolve_worker_spec_version("http-worker", None, [
       {"worker_type": "http-worker", "spec_version": "1.0.0"},
       {"worker_type": "http-worker", "spec_version": "2.1.3"},
   ])  # returns "2.1.3"
   ```

---

## 13. File Manifest

### Modified files (14)

```
worker/worker-sdk/worker_sdk/layer1_domain/entities/node_type_definition.py
worker/worker-sdk/worker_sdk/layer1_domain/entities/worker_registration.py
worker/worker-sdk/worker_sdk/layer3_adapters/controllers/worker_server/__init__.py
worker/worker-sdk/worker_sdk/layer4_frameworks/providers/registry/http_worker_registry.py
worker-executor-service/app/layer1_domain/entities/worker_registration.py
worker-executor-service/app/layer2_application/features/sdk_register_worker/use_cases/sdk_register_worker_usecase.py
worker-executor-service/app/layer2_application/features/list_workers/use_cases/list_workers_usecase.py
worker-executor-service/app/layer3_adapters/controllers/restful/v1/worker_controller.py
worker-executor-service/app/layer3_adapters/controllers/grpc/worker_registry_servicer.py
proto/workflow/v1/worker_registry.proto
workflow-control-plane-service/app/layer1_domain/entities/workflow.py
workflow-control-plane-service/app/layer1_domain/entities/node_definition.py
workflow-control-plane-service/app/layer1_domain/services/node_definition_resolver.py
workflow-control-plane-service/app/layer2_application/features/list_node_palette/use_cases/list_node_palette_use_case.py
```

### New files (8)

```
workflow-control-plane-service/app/layer3_adapters/contracts/__init__.py
workflow-control-plane-service/app/layer3_adapters/contracts/form_schema.py
workflow-control-plane-service/app/layer3_adapters/contracts/node_definition_dto.py
workflow-control-plane-service/app/layer3_adapters/contracts/providers.py
worker-executor-service/tests/unit/test_sdk_register_worker_use_case.py
worker-executor-service/tests/unit/test_worker_registry_servicer_contracts.py
workflow-control-plane-service/tests/unit/test_form_field_contracts.py
workflow-control-plane-service/tests/unit/test_node_definition_dto.py
```

### Extended test files (3)

```
workflow-control-plane-service/tests/unit/test_node_palette_v2.py
workflow-control-plane-service/tests/unit/test_node_definition_entity.py
workflow-control-plane-service/tests/unit/test_node_definition_resolver.py
```

---

## 14. Open Questions (for reviewer)

1. **Rollout strategy for Task 0.1** — single PR (Option A) vs two-step warn-then-reject (Option B)? Recommendation: **Option A** + `SDK_ALLOW_EMPTY_WORKER_TYPE` escape hatch. Justification: UUID fallback is already broken; rejecting restores correct behavior.
2. **`STRICT_CONTRACT_VALIDATION` default** — Recommendation: `False` in Phase 0, flip to `True` in Phase 1 alongside `enable_catalog_v2`.
3. **Contracts module placement** — Recommendation: `layer3_adapters/contracts/`. Honors the "pydantic at adapters" convention until Phase 4 extracts the shared package.
4. **Proto field-number reservation** — 15 (`WorkerDetail.spec_version`), 16 (`RegisterWorkerRequest.spec_version`). Any concern with these numbers?

---

## 15. Checklist for Reviewer

- [ ] Four tasks, four PRs (or bundle 0.1 + 0.2 if reviewer is comfortable).
- [ ] PR descriptions cross-link to the redesign doc (`docs/design/node-palette-catalog-redesign.md`) and this plan.
- [ ] Proto regen committed alongside `.proto` change (single atomic PR).
- [ ] All new test files have at least one `test_rejects_*` and one `test_accepts_*` pair where applicable.
- [ ] No endpoint, no feature flag, and no CP-side validation surface change in this phase. Phase 1 introduces those.
- [ ] `docs/design/node-palette-catalog-implementation-plan.md` updated to mark Phase 0 tasks as "implemented" upon each merge.
