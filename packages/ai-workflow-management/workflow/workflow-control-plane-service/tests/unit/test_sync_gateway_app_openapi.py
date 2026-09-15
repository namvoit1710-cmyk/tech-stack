"""Unit tests for the OpenAPI sync use case (reconcile create/update/delete/reactivate)."""
from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.value_objects.gateway import AppKind
from app.layer2_application.features.sync_gateway_app_openapi.use_cases.sync_gateway_app_openapi_use_case import (
    SyncGatewayAppOpenapiCommand,
    SyncGatewayAppOpenapiUseCase,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    ListGatewayFunctionsFilter,
)
from app.layer4_frameworks.persistence.memory.memory_gateway_app_repo import (
    MemoryGatewayAppRepo,
)
from app.layer4_frameworks.persistence.memory.memory_gateway_function_repo import (
    MemoryGatewayFunctionRepo,
)


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def exception(self, *a, **k): pass


def _spec(*op_ids: str) -> dict:
    return {
        "openapi": "3.0.0",
        "paths": {
            f"/{oid}/{{id}}": {
                "get": {
                    "operationId": oid,
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": {
                        "type": "object", "properties": {"ok": {"type": "boolean"}}}}}}},
                }
            }
            for oid in op_ids
        },
    }


def _fetcher(holder: dict):
    async def fetch(url, headers, timeout_s):
        return json.dumps(holder["spec"]).encode()
    return fetch


def _setup(spec: dict):
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    app = GatewayApp.create(
        name="gov", kind=AppKind.INTERNAL, base_url="https://gov.internal",
        created_by="me",
    )
    app.set_spec_url("https://gov.internal/openapi.json", updated_by="me")
    apps.save(app)
    holder = {"spec": spec}
    uc = SyncGatewayAppOpenapiUseCase(_Log(), apps, fns, spec_fetcher=_fetcher(holder))
    return uc, apps, fns, holder


def _active_names(fns, app_id):
    page = fns.list_by_app(ListGatewayFunctionsFilter(app_id=app_id, include_inactive=False, top=1000))
    return {f.name for f in page.items}


async def test_first_sync_creates_functions():
    uc, apps, fns, _ = _setup(_spec("getA", "getB"))
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert (res.created, res.error) == (2, "")
    app = apps.get_by_name("gov")
    assert _active_names(fns, app.id) == {"getA", "getB"}
    assert app.spec_hash and app.last_synced_at  # recorded


async def test_unchanged_spec_is_skipped():
    uc, apps, fns, _ = _setup(_spec("getA"))
    await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert res.skipped is True
    assert (res.created, res.updated, res.deleted) == (0, 0, 0)


async def test_force_bypasses_hash_skip():
    uc, apps, fns, _ = _setup(_spec("getA"))
    await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov", force=True))
    assert res.skipped is False
    assert res.unchanged == 1  # re-parsed but nothing changed


async def test_removed_op_is_soft_deleted_and_new_added():
    uc, apps, fns, holder = _setup(_spec("getA", "getB"))
    await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    holder["spec"] = _spec("getA", "getC")  # drop getB, add getC
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert (res.created, res.deleted) == (1, 1)
    app = apps.get_by_name("gov")
    assert _active_names(fns, app.id) == {"getA", "getC"}


async def test_readded_op_is_reactivated_not_duplicated():
    # Keep a second op present throughout so the spec never goes fully empty (which the
    # safety guard would refuse) — this isolates the reactivation behavior.
    uc, apps, fns, holder = _setup(_spec("keep", "getA"))
    await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    app = apps.get_by_name("gov")
    holder["spec"] = _spec("keep")  # drop getA → soft-deleted
    await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert _active_names(fns, app.id) == {"keep"}
    holder["spec"] = _spec("keep", "getA")  # bring getA back
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert res.created == 1
    assert _active_names(fns, app.id) == {"keep", "getA"}
    # exactly one row total for (app_id, getA) — reactivated in place, not duplicated
    allrows = fns.list_by_app(ListGatewayFunctionsFilter(app_id=app.id, include_inactive=True, top=1000))
    assert sum(1 for f in allrows.items if f.name == "getA") == 1


async def test_force_allows_wipe_to_zero():
    """force=true is the explicit escape hatch past the empty-spec safety guard."""
    uc, apps, fns, holder = _setup(_spec("getA"))
    await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    app = apps.get_by_name("gov")
    holder["spec"] = {"openapi": "3.0.0", "paths": {}}
    guarded = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert guarded.error_code == "GATEWAY_SPEC_INVALID"      # refused without force
    forced = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov", force=True))
    assert forced.deleted == 1 and not forced.error
    assert _active_names(fns, app.id) == set()


async def test_missing_app_errors():
    uc, *_ = _setup(_spec("getA"))
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="nope"))
    assert res.error_code == "GATEWAY_APP_NOT_FOUND"


async def test_empty_spec_does_not_wipe_existing_functions():
    """A transient upstream that parses to 0 ops must NOT soft-delete the registry."""
    uc, apps, fns, holder = _setup(_spec("getA", "getB"))
    await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    app = apps.get_by_name("gov")
    assert _active_names(fns, app.id) == {"getA", "getB"}

    holder["spec"] = {"openapi": "3.0.0", "paths": {}}  # blip: empty-but-valid
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert res.error_code == "GATEWAY_SPEC_INVALID"
    assert res.deleted == 0
    assert _active_names(fns, app.id) == {"getA", "getB"}  # untouched

    # Hash not poisoned: when the upstream recovers with new content, sync proceeds.
    holder["spec"] = _spec("getA", "getB", "getC")
    res2 = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert res2.skipped is False and res2.created == 1
    assert _active_names(fns, app.id) == {"getA", "getB", "getC"}


async def test_failed_op_is_counted_and_others_still_sync():
    """An op that can't build a valid function is counted in `failed`, not silently dropped."""
    bad_spec = {"openapi": "3.0.0", "paths": {
        "/ok": {"get": {"operationId": "getOK", "responses": {"200": {"description": "ok"}}}},
        "/x/{a-b}": {"get": {"operationId": "bad", "responses": {"200": {"description": "ok"}}}},
    }}
    uc, apps, fns, _ = _setup(bad_spec)
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert res.created == 1 and res.failed == 1
    app = apps.get_by_name("gov")
    assert _active_names(fns, app.id) == {"getOK"}


async def test_fetch_failure_maps_to_error_code():
    apps, fns = MemoryGatewayAppRepo(), MemoryGatewayFunctionRepo()
    app = GatewayApp.create(name="gov", kind=AppKind.INTERNAL, base_url="https://gov.internal", created_by="me")
    app.set_spec_url("https://gov.internal/openapi.json", updated_by="me")
    apps.save(app)

    async def boom(url, headers, timeout_s):
        raise RuntimeError("connection refused")

    uc = SyncGatewayAppOpenapiUseCase(_Log(), apps, fns, spec_fetcher=boom)
    res = await uc.execute(SyncGatewayAppOpenapiCommand(app_name="gov"))
    assert res.error_code == "GATEWAY_SPEC_FETCH_FAILED"
