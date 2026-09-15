"""SA-2370 — Environment CRUD use cases + REST controller.

Tests the application layer (use cases) directly and the controller via a
minimal FastAPI app (no middleware, so responses are the bare result dict).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.layer2_application.features import FEATURE_REGISTRY, load_features
from app.layer2_application.features.create_environment.use_cases.create_environment_use_case import (
    CreateEnvironmentCommand,
    CreateEnvironmentUseCase,
)
from app.layer2_application.features.delete_environment.use_cases.delete_environment_use_case import (
    DeleteEnvironmentCommand,
    DeleteEnvironmentUseCase,
)
from app.layer2_application.features.get_environment.use_cases.get_environment_use_case import (
    GetEnvironmentCommand,
    GetEnvironmentUseCase,
)
from app.layer2_application.features.list_environments.use_cases.list_environments_use_case import (
    ListEnvironmentsCommand,
    ListEnvironmentsUseCase,
)
from app.layer2_application.features.update_environment.use_cases.update_environment_use_case import (
    UpdateEnvironmentCommand,
    UpdateEnvironmentUseCase,
)
from app.layer3_adapters.controllers.restful.v1.environment_controller import router
from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
    MemoryEnvironmentRepo,
)


class _Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...
    def debug(self, *a, **k): ...


def _container():
    repo = MemoryEnvironmentRepo()
    log = _Log()
    return {
        "create_environment": CreateEnvironmentUseCase(logger=log, environment_repo=repo),
        "list_environments": ListEnvironmentsUseCase(logger=log, environment_repo=repo),
        "get_environment": GetEnvironmentUseCase(logger=log, environment_repo=repo),
        "update_environment": UpdateEnvironmentUseCase(logger=log, environment_repo=repo),
        "delete_environment": DeleteEnvironmentUseCase(logger=log, environment_repo=repo),
    }, repo


def _client():
    app = FastAPI()
    container, repo = _container()
    app.state.container = container
    app.include_router(router, prefix="/api/v1/environments")
    return TestClient(app), repo


# ── feature auto-discovery ────────────────────────────────────────────────
def test_features_registered():
    load_features()
    for key in (
        "create_environment", "list_environments", "get_environment",
        "update_environment", "delete_environment",
    ):
        assert key in FEATURE_REGISTRY, f"{key} not auto-discovered"


# ── use cases (application layer) ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_use_case_crud_flow():
    repo = MemoryEnvironmentRepo()
    log = _Log()
    create = CreateEnvironmentUseCase(logger=log, environment_repo=repo)
    get = GetEnvironmentUseCase(logger=log, environment_repo=repo)
    update = UpdateEnvironmentUseCase(logger=log, environment_repo=repo)
    delete = DeleteEnvironmentUseCase(logger=log, environment_repo=repo)
    lst = ListEnvironmentsUseCase(logger=log, environment_repo=repo)

    r = await create.execute(CreateEnvironmentCommand(
        tenant_id="t1", section="SimpleMDG", name="qas", created_by="rowan",
        variables=[
            {"key": "base_url", "type": "text", "value": "https://srv-qas"},
            {"key": "auth", "type": "credential", "value": "cred_qas"},
        ],
    ))
    assert not r.error and r.version == 1
    env_id = r.id

    # duplicate natural key -> conflict
    dup = await create.execute(CreateEnvironmentCommand(
        tenant_id="t1", section="SimpleMDG", name="qas", created_by="rowan"))
    assert dup.error_code == "ENVIRONMENT_ALREADY_EXISTS"

    g = await get.execute(GetEnvironmentCommand(tenant_id="t1", id=env_id))
    assert g.environment["name"] == "qas"
    assert g.environment["variables"][1]["is_secret"] is True

    # tenant isolation on get
    g2 = await get.execute(GetEnvironmentCommand(tenant_id="other", id=env_id))
    assert g2.error_code == "ENVIRONMENT_NOT_FOUND"

    # update requires If-Match; CAS enforced
    u = await update.execute(UpdateEnvironmentCommand(
        tenant_id="t1", id=env_id, expected_version=1, updated_by="rowan",
        new_name="qas2"))
    assert not u.error and u.version == 2

    stale = await update.execute(UpdateEnvironmentCommand(
        tenant_id="t1", id=env_id, expected_version=1, updated_by="rowan",
        new_name="qas3"))
    assert stale.error_code == "OPTIMISTIC_CONCURRENCY_CONFLICT"

    lp = await lst.execute(ListEnvironmentsCommand(tenant_id="t1"))
    assert lp.total == 1

    d = await delete.execute(DeleteEnvironmentCommand(tenant_id="t1", id=env_id, deleted_by="rowan"))
    assert d.deleted is True
    lp2 = await lst.execute(ListEnvironmentsCommand(tenant_id="t1"))
    assert lp2.total == 0


@pytest.mark.asyncio
async def test_use_case_rejects_bad_variable():
    repo = MemoryEnvironmentRepo()
    create = CreateEnvironmentUseCase(logger=_Log(), environment_repo=repo)
    r = await create.execute(CreateEnvironmentCommand(
        tenant_id="t1", section="S", name="qas", created_by="r",
        variables=[{"key": "bad key!", "type": "text", "value": "x"}]))
    assert r.error_code == "VALIDATION_ERROR"


# ── controller (HTTP) ─────────────────────────────────────────────────────
def test_http_crud_roundtrip():
    client, _ = _client()
    h = {"X-Tenant-Id": "t1", "X-User": "rowan"}

    # create
    resp = client.post("/api/v1/environments", headers=h, json={
        "section": "SimpleMDG", "name": "qas",
        "variables": [{"key": "base_url", "type": "text", "value": "https://srv-qas"}],
    })
    assert resp.status_code == 200, resp.text
    env_id = resp.json()["id"]

    # list
    resp = client.get("/api/v1/environments", headers=h)
    assert resp.json()["total"] == 1

    # get
    resp = client.get(f"/api/v1/environments/{env_id}", headers=h)
    assert resp.json()["environment"]["name"] == "qas"

    # update without If-Match -> 409
    resp = client.patch(f"/api/v1/environments/{env_id}", headers=h, json={"name": "qas2"})
    assert resp.status_code == 409

    # update with If-Match
    resp = client.patch(
        f"/api/v1/environments/{env_id}",
        headers={**h, "If-Match": "1"}, json={"name": "qas2"})
    assert resp.status_code == 200 and resp.json()["version"] == 2

    # delete
    resp = client.delete(f"/api/v1/environments/{env_id}", headers=h)
    assert resp.status_code == 200 and resp.json()["deleted"] is True


def test_http_get_missing_is_404():
    client, _ = _client()
    resp = client.get("/api/v1/environments/nope", headers={"X-Tenant-Id": "t1"})
    assert resp.status_code == 404
