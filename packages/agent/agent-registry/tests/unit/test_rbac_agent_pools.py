"""T4 slice 1 — agent pools repo + CRUD/visibility use cases on seeded SQLite.

Seeds via the T1 migration, adds a couple of `agents`, then exercises
HANAAgentPoolRepository (CRUD, soft-delete + junction cleanup, idempotent links,
find_agent_ids_visible_to) and the pool use cases incl. bypass/member visibility.
"""
import asyncio
import importlib.util
import os
from contextlib import contextmanager

os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ["DATABASE_SCHEMA"] = "PUBLIC"

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import InvalidDataException, NotFoundException
from app.layer2_application.use_cases.add_agent_to_pool import AddAgentToPoolUseCase
from app.layer2_application.use_cases.add_member_to_pool import AddMemberToPoolUseCase
from app.layer2_application.use_cases.remove_member_from_pool import RemoveMemberFromPoolUseCase
from app.layer2_application.use_cases.create_agent_pool import CreateAgentPoolUseCase
from app.layer2_application.use_cases.delete_agent_pool import DeleteAgentPoolUseCase
from app.layer2_application.use_cases.get_agent_pool import GetAgentPoolUseCase
from app.layer2_application.use_cases.list_agent_pools import ListAgentPoolsUseCase
from app.layer2_application.use_cases.remove_agent_from_pool import RemoveAgentFromPoolUseCase
from app.layer2_application.use_cases.update_agent_pool import UpdateAgentPoolUseCase
from app.layer4_infrastructure.persistence.repositories.hana_agent_pool_repository import (
    HANAAgentPoolRepository,
)
from app.layer4_infrastructure.persistence.repositories.hana_rbac_user_repository import (
    HANARbacUserRepository,
)


def _load_migration(fname, modname):
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, "app", "layer4_infrastructure", "persistence", "migrations",
                        "versions", fname)
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mig = _load_migration("20260716_01.py", "t1_mig_pools")
mig02 = _load_migration("20260716_02.py", "t5_mig_pools")


class _FakeDbFactory:
    def __init__(self, engine):
        self._sm = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    @contextmanager
    def get_session(self):
        s = self._sm()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


class _FakeAgentRepo:
    def __init__(self, ids):
        self.ids = set(ids)

    def find_by_id(self, agent_id):
        return object() if agent_id in self.ids else None


class _Info:
    def __init__(self, email, name):
        self.email, self.name = email, name


class _FakePM:
    def __init__(self, email="pm@laidon.com", name="PM", fail=False):
        self.email, self.name, self.fail, self.calls = email, name, fail, 0

    async def fetch_user_by_external_id(self, token, external_id):
        self.calls += 1
        if self.fail:
            from app.layer1_domain.exceptions import InvalidDataException as _IDE
            raise _IDE("PM unreachable")
        return _Info(self.email, self.name)


def _insert_agent(conn, aid):
    conn.execute(sa.text(
        "INSERT INTO agents (id,name,kind,status,is_published,is_alive,version,provider,"
        "model,temperature,max_tokens,config_type,timeout_ms,max_concurrency,retry_count,"
        "streaming_supported,created_at,updated_at) VALUES (:id,:id,'business','active',"
        "1,0,'1.0.0','openai','gpt-4',0.7,1024,'default',30000,1,3,0,'2026','2026')"
    ), {"id": aid})


@pytest.fixture
def ctx():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk(dbapi, _):  # noqa: ANN001
        dbapi.execute("PRAGMA foreign_keys=ON")

    conn = eng.connect()
    from app.layer4_infrastructure.persistence.models import AgentModel
    AgentModel.__table__.create(conn)
    _insert_agent(conn, "ag1")
    _insert_agent(conn, "ag2")
    conn.commit()
    c = MigrationContext.configure(conn)
    with Operations.context(c):
        mig.upgrade()
        mig02.upgrade()
    conn.commit()
    db = _FakeDbFactory(eng)
    yield db, HANAAgentPoolRepository(db), HANARbacUserRepository(db)
    conn.close()


def _bypass():
    return Principal.for_user(user_id="admin", external_id="a", role_code="admin",
                              is_super=False, permissions=frozenset({"agent.view_all", "pool.read"}))


def _member(user_id):
    return Principal.for_user(user_id=user_id, external_id="m", role_code="user",
                              is_super=False, permissions=frozenset({"pool.read"}))


# ---- repo ------------------------------------------------------------------
def test_create_get_update_soft_delete(ctx):
    _, pools, users = ctx
    uid, _ = users.upsert_user("ext-creator", email="c@b.com")  # created_by FK -> users.id
    p = pools.create_pool("Prod", "prod pool", uid)
    assert p.status == "active" and p.name == "Prod" and p.created_by == uid
    assert pools.get_pool(p.id).description == "prod pool"
    pools.update_pool(p.id, "Prod2", "d2")
    assert pools.get_pool(p.id).name == "Prod2"
    pools.soft_delete_pool(p.id)
    assert pools.get_pool(p.id) is None  # excluded from reads
    assert pools.list_pools() == []


def test_soft_delete_clears_junctions_but_keeps_agents(ctx):
    db, pools, users = ctx
    p = pools.create_pool("P", None, None)
    uid, _ = users.upsert_user("ext-1", email="u@b.com")
    pools.add_agent(p.id, "ag1")
    pools.add_member(p.id, uid)
    pools.soft_delete_pool(p.id)
    with db.get_session() as s:
        assert s.execute(sa.text("SELECT COUNT(*) FROM agent_pool_agents WHERE pool_id=:p"), {"p": p.id}).scalar() == 0
        assert s.execute(sa.text("SELECT COUNT(*) FROM agent_pool_members WHERE pool_id=:p"), {"p": p.id}).scalar() == 0
        assert s.execute(sa.text("SELECT COUNT(*) FROM agents WHERE id='ag1'")).scalar() == 1  # agent untouched


def test_add_remove_links_are_idempotent(ctx):
    _, pools, _ = ctx
    p = pools.create_pool("P", None, None)
    pools.add_agent(p.id, "ag1")
    pools.add_agent(p.id, "ag1")  # idempotent
    assert pools.list_agent_ids(p.id) == ["ag1"]
    pools.remove_agent(p.id, "ag1")
    pools.remove_agent(p.id, "ag1")  # idempotent, no error
    assert pools.list_agent_ids(p.id) == []


def test_add_agent_concurrent_race_is_idempotent(ctx, monkeypatch):
    # M-1: the check-then-insert is not atomic. Simulate the TOCTOU window where the
    # pre-check misses the existing row → the INSERT races and hits the composite PK.
    # It must be swallowed as an idempotent no-op, not surface as a 500.
    from sqlalchemy.orm import Session
    _, pools, _ = ctx
    p = pools.create_pool("P", None, None)
    pools.add_agent(p.id, "ag1")  # winner
    monkeypatch.setattr(Session, "get", lambda *a, **k: None)  # force the pre-check to miss
    pools.add_agent(p.id, "ag1")  # loser: would raise IntegrityError → 500 without the catch
    monkeypatch.undo()
    assert pools.list_agent_ids(p.id) == ["ag1"]  # still exactly one link


def test_add_member_concurrent_race_is_idempotent(ctx, monkeypatch):
    from sqlalchemy.orm import Session
    _, pools, users = ctx
    p = pools.create_pool("P", None, None)
    uid, _ = users.upsert_user("ext-race", email="race@b.com")
    pools.add_member(p.id, uid)  # winner
    monkeypatch.setattr(Session, "get", lambda *a, **k: None)  # force the pre-check to miss
    pools.add_member(p.id, uid)  # loser: IntegrityError swallowed as a no-op
    monkeypatch.undo()
    assert pools.list_member_ids(p.id) == [uid]


def test_find_agent_ids_visible_to(ctx):
    _, pools, users = ctx
    uid, _ = users.upsert_user("ext-vis", email="v@b.com")
    p1 = pools.create_pool("P1", None, None)
    p2 = pools.create_pool("P2", None, None)  # user is NOT a member of p2
    pools.add_agent(p1.id, "ag1")
    pools.add_agent(p2.id, "ag2")
    pools.add_member(p1.id, uid)
    assert pools.find_agent_ids_visible_to(uid) == {"ag1"}  # only p1's agent
    # soft-deleting p1 removes visibility
    pools.soft_delete_pool(p1.id)
    assert pools.find_agent_ids_visible_to(uid) == set()
    assert pools.find_agent_ids_visible_to(None) == set()


def test_list_pools_for_user(ctx):
    _, pools, users = ctx
    uid, _ = users.upsert_user("ext-lp", email="lp@b.com")
    p1 = pools.create_pool("P1", None, None)
    pools.create_pool("P2", None, None)
    pools.add_member(p1.id, uid)
    mine = {p.id for p in pools.list_pools_for_user(uid)}
    assert mine == {p1.id}


# ---- use cases -------------------------------------------------------------
def test_create_pool_uc_rejects_empty_name(ctx):
    _, pools, _ = ctx
    uc = CreateAgentPoolUseCase(pools)
    assert uc.execute("Prod", "d", None).name == "Prod"  # created_by NULL (system caller)
    with pytest.raises(InvalidDataException):
        uc.execute("  ", None, None)


def test_get_pool_uc_visibility(ctx):
    _, pools, users = ctx
    uid, _ = users.upsert_user("ext-g", email="g@b.com")
    p = pools.create_pool("P", None, None)
    pools.add_agent(p.id, "ag1")
    uc = GetAgentPoolUseCase(pools)
    # bypass sees it + its links
    view = uc.execute(p.id, _bypass())
    assert view.pool.id == p.id and view.agent_ids == ("ag1",)
    # non-member without bypass -> 404 (hidden)
    with pytest.raises(NotFoundException):
        uc.execute(p.id, _member(uid))
    # member sees it
    pools.add_member(p.id, uid)
    assert uc.execute(p.id, _member(uid)).pool.id == p.id
    # unknown pool -> 404
    with pytest.raises(NotFoundException):
        uc.execute("ghost", _bypass())


def test_list_pools_uc_bypass_vs_member(ctx):
    _, pools, users = ctx
    uid, _ = users.upsert_user("ext-l", email="l@b.com")
    p1 = pools.create_pool("P1", None, None)
    pools.create_pool("P2", None, None)
    pools.add_member(p1.id, uid)
    uc = ListAgentPoolsUseCase(pools)
    assert len(uc.execute(_bypass())) == 2          # sees all
    assert {p.id for p in uc.execute(_member(uid))} == {p1.id}  # only own


def test_update_pool_uc_success_and_empty_name(ctx):
    _, pools, _ = ctx
    p = pools.create_pool("P", "d", None)
    uc = UpdateAgentPoolUseCase(pools)
    updated = uc.execute(p.id, "P2", "d2")
    assert updated.name == "P2" and updated.description == "d2"
    with pytest.raises(InvalidDataException):
        uc.execute(p.id, "  ", None)  # empty name


def test_update_delete_uc_not_found(ctx):
    _, pools, _ = ctx
    with pytest.raises(NotFoundException):
        UpdateAgentPoolUseCase(pools).execute("ghost", "X", None)
    with pytest.raises(NotFoundException):
        DeleteAgentPoolUseCase(pools).execute("ghost")


def test_repo_guard_branches(ctx):
    _, pools, _ = ctx
    assert pools.list_pools_for_user("") == []          # empty user_id
    assert pools.update_pool("ghost", "X", None) is None  # unknown pool
    pools.soft_delete_pool("ghost")                      # no-op, no error
    assert pools.is_member("ghost", "") is False         # empty user_id


def test_add_remove_agent_uc(ctx):
    _, pools, _ = ctx
    p = pools.create_pool("P", None, None)
    add = AddAgentToPoolUseCase(pools, _FakeAgentRepo({"ag1"}))
    add.execute(p.id, "ag1")
    assert pools.list_agent_ids(p.id) == ["ag1"]
    # unknown agent -> 404
    with pytest.raises(NotFoundException):
        add.execute(p.id, "nope")
    # unknown pool -> 404
    with pytest.raises(NotFoundException):
        add.execute("ghost", "ag1")
    RemoveAgentFromPoolUseCase(pools).execute(p.id, "ag1")
    assert pools.list_agent_ids(p.id) == []


# ---- members (add via PM / remove) -----------------------------------------
def _run(coro):
    return asyncio.run(coro)


def test_add_member_upserts_unseen_via_pm(ctx):
    _, pools, users = ctx
    p = pools.create_pool("P", None, None)
    pm = _FakePM(email="fresh@laidon.com", name="Fresh")
    uc = AddMemberToPoolUseCase(pools, users, pm)
    assert users.find_by_external_id("ext-new") is None
    member = _run(uc.execute(p.id, "ext-new", "tok"))
    assert pm.calls == 1 and member.external_id == "ext-new" and member.email == "fresh@laidon.com"
    assert pools.is_member(p.id, member.user_id)
    # idempotent
    _run(uc.execute(p.id, "ext-new", "tok"))
    assert pools.list_member_ids(p.id) == [member.user_id]


def test_add_member_without_token_skips_pm(ctx):
    _, pools, users = ctx
    p = pools.create_pool("P", None, None)
    pm = _FakePM()
    member = _run(AddMemberToPoolUseCase(pools, users, pm).execute(p.id, "ext-notoken", None))
    assert pm.calls == 0 and pools.is_member(p.id, member.user_id)


def test_add_member_degrades_when_pm_throws(ctx):
    # token present → PM IS called and raises → degrade to external_id-only upsert.
    _, pools, users = ctx
    p = pools.create_pool("P", None, None)
    pm = _FakePM(fail=True)
    member = _run(AddMemberToPoolUseCase(pools, users, pm).execute(p.id, "ext-pmfail", "tok"))
    assert pm.calls == 1                       # PM was actually invoked
    assert pools.is_member(p.id, member.user_id)  # still added
    assert member.email in (None, "")          # no email resolved (placeholder)


def test_add_member_pool_not_found(ctx):
    _, pools, users = ctx
    uc = AddMemberToPoolUseCase(pools, users, _FakePM())
    with pytest.raises(NotFoundException):
        _run(uc.execute("ghost", "ext-x", "tok"))


def test_remove_member(ctx):
    _, pools, users = ctx
    p = pools.create_pool("P", None, None)
    uid, _ = users.upsert_user("ext-rm", email="r@b.com")
    pools.add_member(p.id, uid)
    rm = RemoveMemberFromPoolUseCase(pools)
    rm.execute(p.id, uid)
    assert not pools.is_member(p.id, uid)
    rm.execute(p.id, uid)  # idempotent
    with pytest.raises(NotFoundException):
        rm.execute("ghost", uid)


# ---- boot smoke ------------------------------------------------------------
def test_app_registers_agent_pool_routes():
    from bootstrap import create_app
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {
        "/api/v1/agents-registry/agent-pools",
        "/api/v1/agents-registry/agent-pools/{pool_id}",
        "/api/v1/agents-registry/agent-pools/{pool_id}/agents",
        "/api/v1/agents-registry/agent-pools/{pool_id}/agents/{agent_id}",
        "/api/v1/agents-registry/agent-pools/{pool_id}/members",
        "/api/v1/agents-registry/agent-pools/{pool_id}/members/{user_id}",
    } <= paths
