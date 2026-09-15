"""SA-2365 — HanaEnvironmentRepo persists environments (table AIW_ENVIRONMENT).

Exercised against a faithful row-store fake of the db handle: it stores what the
repo INSERTs (by the repo's own column order) and answers only the statements the
repo emits, so a passing test reflects the real SQL shape — variables JSON
roundtrip, tenant-scoped list, VERSION CAS, and (tenant, section, name) natural
key on ACTIVE rows.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.environment import Environment
from app.layer1_domain.value_objects.env_variable import EnvVariable, VariableType
from app.layer2_application.repositories.environment_repo_interface import (
    ListEnvironmentsFilter,
)
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch
from app.layer4_frameworks.persistence.hana.hana_environment_repo import (
    HanaEnvironmentRepo,
)

_INSERT_COLS = (
    "ID", "TENANT_ID", "SECTION", "NAME", "VARIABLES", "IS_ACTIVE",
    "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT", "VERSION",
)
_SET_COLS = (
    "TENANT_ID", "SECTION", "NAME", "VARIABLES", "IS_ACTIVE",
    "CREATED_BY", "CREATED_AT", "UPDATED_BY", "UPDATED_AT",
)


class FakeDb:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    @staticmethod
    def _lc(r: dict) -> dict:
        return {k.lower(): v for k, v in r.items()}

    def _tenant_active(self, tenant_id: str) -> list[dict]:
        rows = [r for r in self.rows.values()
                if r["TENANT_ID"] == tenant_id and r["IS_ACTIVE"] == 1]
        rows.sort(key=lambda r: (r["SECTION"], r["NAME"]))
        return rows

    def execute_query(self, sql: str, params: tuple = ()):  # noqa: ANN001
        u = " ".join(sql.upper().split())
        if u.startswith("SELECT VERSION FROM"):
            r = self.rows.get(params[0])
            return [{"version": r["VERSION"]}] if r else []
        if u.startswith("SELECT ID FROM"):  # natural-key conflict probe
            tid, sec, name, eid = params
            return [{"id": r["ID"]} for r in self.rows.values()
                    if r["TENANT_ID"] == tid and r["SECTION"] == sec
                    and r["NAME"] == name and r["IS_ACTIVE"] == 1 and r["ID"] != eid]
        if u.startswith("SELECT COUNT"):
            return [{"cnt": len(self._tenant_active(params[0]))}]
        if "ORDER BY" in u:  # paged list (default filter: tenant + active)
            page = self._tenant_active(params[0])
            top, skip = params[-2], params[-1]
            return [self._lc(r) for r in page[skip:skip + top]]
        if u.startswith("SELECT * FROM") and "AND NAME" in u:  # get_by_natural_key
            tid, sec, name = params
            hit = [r for r in self.rows.values()
                   if r["TENANT_ID"] == tid and r["SECTION"] == sec
                   and r["NAME"] == name and r["IS_ACTIVE"] == 1]
            return [self._lc(hit[0])] if hit else []
        if u.startswith("SELECT * FROM") and "WHERE ID" in u:  # get_by_id
            r = self.rows.get(params[0])
            return [self._lc(r)] if r else []
        raise AssertionError(f"unexpected query: {u}")

    def execute_write(self, sql: str, params: tuple = ()) -> int:  # noqa: ANN001
        u = " ".join(sql.upper().split())
        if u.startswith("INSERT INTO"):
            self.rows[params[0]] = dict(zip(_INSERT_COLS, params))
            return 1
        if u.startswith("UPDATE") and "IS_ACTIVE = 0" in u:  # soft delete
            r = self.rows.get(params[0])
            if r:
                r["IS_ACTIVE"] = 0
                r["VERSION"] += 1
                return 1
            return 0
        raise AssertionError(f"unexpected write: {u}")

    def execute_write_returning_rowcount(self, sql: str, params: tuple = ()) -> int:  # noqa: ANN001
        u = " ".join(sql.upper().split())
        vals = params[:len(_SET_COLS)]
        new_version = params[len(_SET_COLS)]
        rid = params[len(_SET_COLS) + 1]
        r = self.rows.get(rid)
        if not r:
            return 0
        if "AND VERSION =" in u:
            expected = params[len(_SET_COLS) + 2]
            if r["VERSION"] != expected:
                return 0
        for c, v in zip(_SET_COLS, vals):
            r[c] = v
        r["VERSION"] = new_version
        return 1


def _env(tenant="t1", section="SimpleMDG", name="qas") -> Environment:
    return Environment.create(
        tenant_id=tenant, section=section, name=name, created_by="rowan",
        variables=[
            EnvVariable.make(key="BASE_URL", type="text", value="https://srv-qas"),
            EnvVariable.make(key="TOKENS", type="list", values=["a", "b"]),
            EnvVariable.make(key="API_TOKEN", type="credential", value="cred-123"),
        ],
    )


def test_insert_then_get_roundtrips_variables():
    db = FakeDb()
    repo = HanaEnvironmentRepo(db)
    env = _env()
    repo.save(env)

    got = repo.get_by_id(env.id)
    assert got is not None
    assert got.tenant_id == "t1" and got.section == "SimpleMDG" and got.name == "qas"
    by_key = {v.key: v for v in got.variables}
    assert by_key["BASE_URL"].type is VariableType.TEXT
    assert by_key["BASE_URL"].value == "https://srv-qas"
    assert by_key["TOKENS"].type is VariableType.LIST
    assert by_key["TOKENS"].values == ("a", "b")
    # credential var stores its REF id (non-secret), not a secret and not null
    assert by_key["API_TOKEN"].type is VariableType.CREDENTIAL
    assert by_key["API_TOKEN"].value == "cred-123"
    assert got.version == 1


def test_get_by_natural_key_and_tenant_scoped_list():
    db = FakeDb()
    repo = HanaEnvironmentRepo(db)
    repo.save(_env(tenant="t1", name="qas"))
    repo.save(_env(tenant="t1", name="dev"))
    repo.save(_env(tenant="t2", name="qas"))  # other tenant

    hit = repo.get_by_natural_key(tenant_id="t1", section="SimpleMDG", name="dev")
    assert hit is not None and hit.name == "dev"

    page = repo.list_paged(ListEnvironmentsFilter(tenant_id="t1"))
    assert page.total == 2
    assert sorted(e.name for e in page.items) == ["dev", "qas"]  # t2 excluded


def test_version_cas_conflict_raises():
    db = FakeDb()
    repo = HanaEnvironmentRepo(db)
    env = _env()
    repo.save(env)  # version 1
    env.set_variables([EnvVariable.make(key="X", type="text", value="1")], updated_by="r")  # version -> 2
    with pytest.raises(CASMismatch):
        repo.save(env, expected_version=99)  # stale read
    repo.save(env, expected_version=1)       # correct read succeeds
    assert repo.get_by_id(env.id).version == 2


def test_natural_key_conflict_on_second_active_env():
    db = FakeDb()
    repo = HanaEnvironmentRepo(db)
    repo.save(_env(tenant="t1", name="qas"))
    with pytest.raises(CASMismatch):
        repo.save(_env(tenant="t1", name="qas"))  # same (tenant, section, name), new id


def test_soft_delete_frees_the_natural_key():
    db = FakeDb()
    repo = HanaEnvironmentRepo(db)
    first = _env(tenant="t1", name="qas")
    repo.save(first)
    repo.soft_delete(first.id)
    assert repo.get_by_id(first.id).is_active is False
    # the triple is free again → a fresh active env can take it
    repo.save(_env(tenant="t1", name="qas"))
    page = repo.list_paged(ListEnvironmentsFilter(tenant_id="t1"))
    assert page.total == 1
