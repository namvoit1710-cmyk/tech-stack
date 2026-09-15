"""SA-2471 — section-level shared variable KEY SCHEMA.

Covers: schema domain (create/validate structured errors, empty allowed), memory
repo (CAS, natural-key), the five use cases (create/list/get/update/delete),
reconcile (new key -> empty, removed key dropped, type change resets), cascade
delete, env-vs-schema validation (unknown key 422, inherit keys), and the REST
controller wired to a minimal FastAPI app.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.layer1_domain.entities.env_section_schema import EnvSectionSchema, SchemaKey
from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.env_variable import EnvVariable, VariableType
from app.layer2_application.features import FEATURE_REGISTRY, load_features
from app.layer2_application.features.create_env_section_schema.use_cases.create_env_section_schema_use_case import (
    CreateEnvSectionSchemaCommand,
    CreateEnvSectionSchemaUseCase,
)
from app.layer2_application.features.delete_env_section_schema.use_cases.delete_env_section_schema_use_case import (
    DeleteEnvSectionSchemaCommand,
    DeleteEnvSectionSchemaUseCase,
)
from app.layer2_application.features.get_env_section_schema.use_cases.get_env_section_schema_use_case import (
    GetEnvSectionSchemaCommand,
    GetEnvSectionSchemaUseCase,
)
from app.layer2_application.features.list_env_section_schema.use_cases.list_env_section_schema_use_case import (
    ListEnvSectionSchemaCommand,
    ListEnvSectionSchemaUseCase,
)
from app.layer2_application.features.update_env_section_schema.use_cases.update_env_section_schema_use_case import (
    UpdateEnvSectionSchemaCommand,
    UpdateEnvSectionSchemaUseCase,
)
from app.layer2_application.features.create_environment.use_cases.create_environment_use_case import (
    CreateEnvironmentCommand,
    CreateEnvironmentUseCase,
)
from app.layer2_application.features.update_environment.use_cases.update_environment_use_case import (
    UpdateEnvironmentCommand,
    UpdateEnvironmentUseCase,
)
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    CASMismatch,
    ListEnvSectionSchemasFilter,
)
from app.layer2_application.repositories.environment_repo_interface import (
    ListEnvironmentsFilter,
)
from app.layer2_application.services.env_section.reconcile import (
    reconcile_environment_variables,
)
from app.layer3_adapters.controllers.restful.v1.env_section_schema_controller import (
    router as schema_router,
)
from app.layer4_frameworks.persistence.memory.memory_env_section_schema_repo import (
    MemoryEnvSectionSchemaRepo,
)
from app.layer4_frameworks.persistence.memory.memory_environment_repo import (
    MemoryEnvironmentRepo,
)


class _Log:
    def info(self, *a, **k): ...
    def warning(self, *a, **k): ...
    def error(self, *a, **k): ...
    def debug(self, *a, **k): ...


# ── domain ────────────────────────────────────────────────────────────────
def test_schema_create_and_empty_allowed():
    s = EnvSectionSchema.create(tenant_id="t1", section="SimpleMDG", created_by="r")
    assert s.keys == [] and s.version == 1
    s2 = EnvSectionSchema.create(
        tenant_id="t1", section="S", created_by="r",
        keys=[SchemaKey(key="API_URL", type="text"), SchemaKey(key="TOK", type="list")],
    )
    assert s2.key_names() == ["API_URL", "TOK"]
    assert s2.type_for("TOK") is VariableType.LIST


def test_schema_validate_structured_errors():
    s = EnvSectionSchema(
        tenant_id="t1", section="S", created_by="r", updated_by="r",
        keys=[
            SchemaKey(key="A", type="text"),
            SchemaKey(key="a", type="text"),      # case-insensitive dup
            SchemaKey(key="", type="text"),        # empty
            SchemaKey(key="bad key!", type="text"),  # invalid format
        ],
    )
    errs = s.validate()
    assert errs.duplicate_keys == ["a"]
    assert errs.empty_key_indexes == [2]
    assert "bad key!" in errs.invalid_keys
    assert not errs.is_ok()


def test_schema_create_rejects_bad_keys():
    with pytest.raises(ValidationError):
        EnvSectionSchema.create(
            tenant_id="t1", section="S", created_by="r",
            keys=[SchemaKey(key="dup"), SchemaKey(key="dup")],
        )


# ── memory repo ─────────────────────────────────────────────────────────
def test_repo_cas_and_natural_key():
    repo = MemoryEnvSectionSchemaRepo()
    s = EnvSectionSchema.create(tenant_id="t1", section="S", created_by="r")
    repo.save(s)
    assert repo.get_by_natural_key(tenant_id="t1", section="S").id == s.id
    # dup section
    s2 = EnvSectionSchema.create(tenant_id="t1", section="S", created_by="r")
    with pytest.raises(CASMismatch):
        repo.save(s2)
    # CAS mismatch
    with pytest.raises(CASMismatch):
        repo.save(s, expected_version=999)
    # soft delete hides + allows recreate
    repo.soft_delete(s.id)
    assert repo.get_by_natural_key(tenant_id="t1", section="S") is None
    repo.save(s2)  # now free
    assert repo.list_paged(ListEnvSectionSchemasFilter(tenant_id="t1")).total == 1


# ── reconcile (pure) ──────────────────────────────────────────────────────
def test_reconcile_new_removed_and_type_change():
    schema = EnvSectionSchema.create(
        tenant_id="t1", section="S", created_by="r",
        keys=[
            SchemaKey(key="KEEP", type="text"),
            SchemaKey(key="NEW", type="text"),
            SchemaKey(key="WASTEXT", type="list"),   # type changed vs env
        ],
    )
    env_vars = [
        EnvVariable(key="KEEP", type=VariableType.TEXT, value="v"),
        EnvVariable(key="DROP", type=VariableType.TEXT, value="gone"),
        EnvVariable(key="WASTEXT", type=VariableType.TEXT, value="old"),
    ]
    out = {v.key: v for v in reconcile_environment_variables(schema, env_vars)}
    assert set(out) == {"KEEP", "NEW", "WASTEXT"}          # DROP removed
    assert out["KEEP"].value == "v"                         # kept value
    assert out["NEW"].value == ""                          # new -> empty
    assert out["WASTEXT"].type is VariableType.LIST         # type from schema
    assert out["WASTEXT"].value == ""                       # reset on type change


# ── use cases ─────────────────────────────────────────────────────────────
def _schema_ucs():
    srepo = MemoryEnvSectionSchemaRepo()
    erepo = MemoryEnvironmentRepo()
    log = _Log()
    return (
        CreateEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        GetEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        ListEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        UpdateEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        DeleteEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        srepo, erepo,
    )


@pytest.mark.asyncio
async def test_features_registered():
    load_features()
    for key in (
        "create_env_section_schema", "get_env_section_schema",
        "list_env_section_schema", "update_env_section_schema",
        "delete_env_section_schema",
    ):
        assert key in FEATURE_REGISTRY, f"{key} not auto-discovered"


@pytest.mark.asyncio
async def test_create_invalid_keys_returns_422_details():
    create, *_ = _schema_ucs()
    r = await create.execute(CreateEnvSectionSchemaCommand(
        tenant_id="t1", section="S", created_by="r",
        keys=[{"key": "A"}, {"key": "a"}, {"key": ""}, {"key": "bad key!"}],
    ))
    assert r.error_code == "SECTION_SCHEMA_INVALID_KEYS"
    assert r.error_details["duplicate_keys"] == ["a"]
    assert r.error_details["empty_key_indexes"] == [2]
    assert "bad key!" in r.error_details["invalid_keys"]


@pytest.mark.asyncio
async def test_create_duplicate_section_409():
    create, *_ = _schema_ucs()
    ok = await create.execute(CreateEnvSectionSchemaCommand(
        tenant_id="t1", section="S", created_by="r", keys=[{"key": "A"}]))
    assert not ok.error
    dup = await create.execute(CreateEnvSectionSchemaCommand(
        tenant_id="t1", section="S", created_by="r", keys=[{"key": "B"}]))
    assert dup.error_code == "SECTION_SCHEMA_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_create_reconciles_existing_envs():
    create, get, lst, update, delete, srepo, erepo = _schema_ucs()
    # seed an env with keys A, X in section S
    env = Environment.create(
        tenant_id="t1", section="S", name="qas", created_by="r",
        variables=[EnvVariable.make(key="A", value="1"), EnvVariable.make(key="X", value="9")],
    )
    erepo.save(env)
    # create schema with A, B — env should reconcile: A kept, B added, X dropped
    r = await create.execute(CreateEnvSectionSchemaCommand(
        tenant_id="t1", section="S", created_by="r", keys=[{"key": "A"}, {"key": "B"}]))
    assert r.environments_reconciled == 1
    reconc = erepo.get_by_id(env.id)
    keys = {v.key: v.value for v in reconc.variables}
    assert set(keys) == {"A", "B"}
    assert keys["A"] == "1" and keys["B"] == ""
    assert reconc.version == 2  # bumped by reconcile save


@pytest.mark.asyncio
async def test_update_cas_and_reconcile():
    create, get, lst, update, delete, srepo, erepo = _schema_ucs()
    env = Environment.create(
        tenant_id="t1", section="S", name="qas", created_by="r",
        variables=[EnvVariable.make(key="A", value="1")])
    erepo.save(env)
    c = await create.execute(CreateEnvSectionSchemaCommand(
        tenant_id="t1", section="S", created_by="r", keys=[{"key": "A"}]))
    sid, ver = c.id, c.version

    # stale CAS
    stale = await update.execute(UpdateEnvSectionSchemaCommand(
        tenant_id="t1", id=sid, expected_version=999, updated_by="r",
        new_keys=[{"key": "A"}, {"key": "C"}]))
    assert stale.error_code == "OPTIMISTIC_CONCURRENCY_CONFLICT"

    # invalid keys 422
    bad = await update.execute(UpdateEnvSectionSchemaCommand(
        tenant_id="t1", id=sid, expected_version=ver, updated_by="r",
        new_keys=[{"key": "A"}, {"key": "A"}]))
    assert bad.error_code == "SECTION_SCHEMA_INVALID_KEYS"

    # good update reconciles env (A kept, C added)
    u = await update.execute(UpdateEnvSectionSchemaCommand(
        tenant_id="t1", id=sid, expected_version=ver, updated_by="r",
        new_keys=[{"key": "A"}, {"key": "C"}]))
    assert not u.error and u.environments_reconciled == 1
    keys = {v.key for v in erepo.get_by_id(env.id).variables}
    assert keys == {"A", "C"}


@pytest.mark.asyncio
async def test_delete_cascades_environments():
    create, get, lst, update, delete, srepo, erepo = _schema_ucs()
    for nm in ("qas", "prod"):
        erepo.save(Environment.create(
            tenant_id="t1", section="S", name=nm, created_by="r",
            variables=[EnvVariable.make(key="A", value="1")]))
    c = await create.execute(CreateEnvSectionSchemaCommand(
        tenant_id="t1", section="S", created_by="r", keys=[{"key": "A"}]))
    d = await delete.execute(DeleteEnvSectionSchemaCommand(
        tenant_id="t1", id=c.id, deleted_by="r"))
    assert d.deleted and d.environments_deleted == 2
    assert erepo.list_paged(ListEnvironmentsFilter(tenant_id="t1")).total == 0
    # schema gone from list
    lp = await lst.execute(ListEnvSectionSchemaCommand(tenant_id="t1"))
    assert lp.total == 0


@pytest.mark.asyncio
async def test_get_and_list_carry_environment_count():
    create, get, lst, update, delete, srepo, erepo = _schema_ucs()
    erepo.save(Environment.create(
        tenant_id="t1", section="S", name="qas", created_by="r",
        variables=[EnvVariable.make(key="A", value="1")]))
    c = await create.execute(CreateEnvSectionSchemaCommand(
        tenant_id="t1", section="S", created_by="r", keys=[{"key": "A"}]))
    g = await get.execute(GetEnvSectionSchemaCommand(tenant_id="t1", id=c.id))
    assert g.section_schema["environment_count"] == 1
    assert g.section_schema["keys"] == [{"key": "A", "type": "text"}]
    lp = await lst.execute(ListEnvSectionSchemaCommand(tenant_id="t1"))
    assert lp.items[0]["environment_count"] == 1
    # tenant isolation on get
    g2 = await get.execute(GetEnvSectionSchemaCommand(tenant_id="other", id=c.id))
    assert g2.error_code == "SECTION_SCHEMA_NOT_FOUND"


# ── env-vs-schema validation (§7) ─────────────────────────────────────────
def _env_ucs_with_schema():
    srepo = MemoryEnvSectionSchemaRepo()
    erepo = MemoryEnvironmentRepo()
    log = _Log()
    create = CreateEnvironmentUseCase(
        logger=log, environment_repo=erepo, env_section_schema_repo=srepo)
    update = UpdateEnvironmentUseCase(
        logger=log, environment_repo=erepo, env_section_schema_repo=srepo)
    return create, update, srepo, erepo


@pytest.mark.asyncio
async def test_env_create_unknown_key_422():
    create, update, srepo, erepo = _env_ucs_with_schema()
    srepo.save(EnvSectionSchema.create(
        tenant_id="t1", section="S", created_by="r", keys=[SchemaKey(key="A")]))
    r = await create.execute(CreateEnvironmentCommand(
        tenant_id="t1", section="S", name="qas", created_by="r",
        variables=[{"key": "A", "value": "1"}, {"key": "NOPE", "value": "x"}]))
    assert r.error_code == "ENVIRONMENT_VARIABLES_INVALID"
    assert r.error_details["unknown_keys"] == ["NOPE"]


@pytest.mark.asyncio
async def test_env_create_inherits_schema_keys():
    create, update, srepo, erepo = _env_ucs_with_schema()
    srepo.save(EnvSectionSchema.create(
        tenant_id="t1", section="S", created_by="r",
        keys=[SchemaKey(key="A"), SchemaKey(key="B")]))
    r = await create.execute(CreateEnvironmentCommand(
        tenant_id="t1", section="S", name="qas", created_by="r",
        variables=[{"key": "A", "value": "1"}]))
    assert not r.error
    env = erepo.get_by_id(r.id)
    keys = {v.key: v.value for v in env.variables}
    assert keys == {"A": "1", "B": ""}   # B inherited empty


@pytest.mark.asyncio
async def test_env_create_no_schema_is_legacy_passthrough():
    create, update, srepo, erepo = _env_ucs_with_schema()  # no schema saved
    r = await create.execute(CreateEnvironmentCommand(
        tenant_id="t1", section="Free", name="qas", created_by="r",
        variables=[{"key": "anything", "value": "1"}]))
    assert not r.error
    env = erepo.get_by_id(r.id)
    assert {v.key for v in env.variables} == {"anything"}


# ── controller (HTTP) ─────────────────────────────────────────────────────
def _client():
    app = FastAPI()
    srepo = MemoryEnvSectionSchemaRepo()
    erepo = MemoryEnvironmentRepo()
    log = _Log()
    app.state.container = {
        "create_env_section_schema": CreateEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        "list_env_section_schema": ListEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        "get_env_section_schema": GetEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        "update_env_section_schema": UpdateEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
        "delete_env_section_schema": DeleteEnvSectionSchemaUseCase(logger=log, env_section_schema_repo=srepo, environment_repo=erepo),
    }
    app.include_router(schema_router, prefix="/api/v1/env-sections")
    return TestClient(app)


def test_http_crud_roundtrip():
    client = _client()
    h = {"X-Tenant-Id": "t1", "X-User": "rowan"}

    resp = client.post("/api/v1/env-sections", headers=h, json={
        "section": "SimpleMDG", "keys": [{"key": "API_URL", "type": "text"}]})
    assert resp.status_code == 200, resp.text
    sid = resp.json()["id"]

    # dup section -> 409
    resp = client.post("/api/v1/env-sections", headers=h, json={
        "section": "SimpleMDG", "keys": [{"key": "X"}]})
    assert resp.status_code == 409

    # invalid keys -> 422 with details
    resp = client.post("/api/v1/env-sections", headers=h, json={
        "section": "Other", "keys": [{"key": "d"}, {"key": "d"}]})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["duplicate_keys"] == ["d"]

    resp = client.get("/api/v1/env-sections", headers=h)
    assert resp.json()["total"] == 1

    resp = client.get(f"/api/v1/env-sections/{sid}", headers=h)
    body = resp.json()
    assert body["section"] == "SimpleMDG"
    assert body["keys"] == [{"key": "API_URL", "type": "text"}]
    assert body["environment_count"] == 0

    # PATCH without If-Match -> 409
    resp = client.patch(f"/api/v1/env-sections/{sid}", headers=h, json={"keys": [{"key": "API_URL"}]})
    assert resp.status_code == 409

    # PATCH with If-Match
    resp = client.patch(
        f"/api/v1/env-sections/{sid}", headers={**h, "If-Match": "1"},
        json={"keys": [{"key": "API_URL"}, {"key": "TOKEN"}]})
    assert resp.status_code == 200 and resp.json()["version"] == 2

    resp = client.delete(f"/api/v1/env-sections/{sid}", headers=h)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True and resp.json()["environments_deleted"] == 0


def test_http_get_missing_is_404():
    client = _client()
    resp = client.get("/api/v1/env-sections/nope", headers={"X-Tenant-Id": "t1"})
    assert resp.status_code == 404
