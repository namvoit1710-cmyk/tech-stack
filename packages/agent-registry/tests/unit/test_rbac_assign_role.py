"""T3 slice 3 — assign-role (R1–R3) + list users, on seeded SQLite.

Exercises the transition→permission matrix (admin / super_admin / custom tiers), the
R3 last-super_admin lockout, target upsert-on-assign, and the idempotent no-op. PM is
stubbed (not reachable in tests); the DB is real (SQLite seeded via the T1 migration).
"""
import asyncio
import importlib.util
import os
from contextlib import contextmanager

os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ.setdefault("DATABASE_SCHEMA", "PUBLIC")

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import (
    ConflictException,
    ForbiddenException,
    InvalidDataException,
    NotFoundException,
)
from app.layer2_application.use_cases.assign_role import AssignRoleUseCase
from app.layer2_application.use_cases.list_users import ListUsersUseCase
from app.layer4_infrastructure.persistence.repositories.hana_rbac_user_repository import HANARbacUserRepository
from app.layer4_infrastructure.persistence.repositories.hana_role_repository import HANARoleRepository

SEED_SUPER = "9e1a4f12-7a29-4f56-aa1a-f0651a777ab6"


def _load_migration(fname, modname):
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, "app", "layer4_infrastructure", "persistence", "migrations",
                        "versions", fname)
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mig = _load_migration("20260716_01.py", "t1_mig_assign")
mig02 = _load_migration("20260716_02.py", "t5_mig_assign")


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


class _Info:
    def __init__(self, email, name):
        self.email, self.name = email, name


class _FakePM:
    def __init__(self, email="target@laidon.com", name="Target", fail=False):
        self.email, self.name, self.fail, self.calls = email, name, fail, 0

    async def fetch_user_by_external_id(self, token, external_id):
        self.calls += 1
        if self.fail:
            raise InvalidDataException("PM unreachable")
        return _Info(self.email, self.name)


@pytest.fixture
def ctx():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk(dbapi, _):  # noqa: ANN001
        dbapi.execute("PRAGMA foreign_keys=ON")

    conn = eng.connect()
    from app.layer4_infrastructure.persistence.models import AgentModel
    AgentModel.__table__.create(conn)
    conn.commit()
    c = MigrationContext.configure(conn)
    with Operations.context(c):
        mig.upgrade()
        mig02.upgrade()
    conn.commit()
    db = _FakeDbFactory(eng)
    users = HANARbacUserRepository(db)
    roles = HANARoleRepository(db)
    yield db, users, roles
    conn.close()


def _run(coro):
    return asyncio.run(coro)


def _uc(users, roles, pm=None):
    return AssignRoleUseCase(users, roles, pm or _FakePM())


def _caller(perms, is_super=False):
    return Principal.for_user(
        user_id="caller", external_id="caller-ext",
        role_code=("super_admin" if is_super else "admin"),
        is_super=is_super, permissions=frozenset(perms),
    )


SUPER = _caller(set(), is_super=True)
ADMIN = _caller({"user.role.assign_admin_role", "user.role.unassign_admin_role", "user.read"})


def _target(users, roles, ext, role_code):
    uid, _ = users.upsert_user(ext, email="x@b.com")
    users.set_role(uid, roles.get_role_by_code(role_code).id)
    return uid


def _role_id(roles, code):
    return roles.get_role_by_code(code).id


# ---- transition matrix -----------------------------------------------------
def test_user_to_admin_by_admin_ok(ctx):
    _, users, roles = ctx
    _target(users, roles, "t1", "user")
    out = _run(_uc(users, roles).execute(ADMIN, "t1", _role_id(roles, "admin"), "tok"))
    assert out.role_code == "admin"


def test_user_to_admin_without_perm_forbidden(ctx):
    _, users, roles = ctx
    _target(users, roles, "t1", "user")
    weak = _caller({"user.read"})
    with pytest.raises(ForbiddenException):
        _run(_uc(users, roles).execute(weak, "t1", _role_id(roles, "admin"), "tok"))


def test_admin_to_user_by_admin_ok(ctx):
    _, users, roles = ctx
    _target(users, roles, "t1", "admin")
    out = _run(_uc(users, roles).execute(ADMIN, "t1", _role_id(roles, "user"), "tok"))
    assert out.role_code == "user"


def test_user_to_super_admin_by_admin_forbidden_R2(ctx):
    _, users, roles = ctx
    _target(users, roles, "t1", "user")
    with pytest.raises(ForbiddenException):
        _run(_uc(users, roles).execute(ADMIN, "t1", _role_id(roles, "super_admin"), "tok"))


def test_user_to_super_admin_by_super_ok(ctx):
    _, users, roles = ctx
    _target(users, roles, "t1", "user")
    out = _run(_uc(users, roles).execute(SUPER, "t1", _role_id(roles, "super_admin"), "tok"))
    assert out.role_code == "super_admin" and out.is_super


def test_user_to_custom_requires_custom_perm(ctx):
    _, users, roles = ctx
    custom = roles.create_role("auditor", "Auditor", None)
    _target(users, roles, "t1", "user")
    # admin lacks assign_custom_role -> 403
    with pytest.raises(ForbiddenException):
        _run(_uc(users, roles).execute(ADMIN, "t1", custom.id, "tok"))
    # super bypasses -> ok
    out = _run(_uc(users, roles).execute(SUPER, "t1", custom.id, "tok"))
    assert out.role_code == "auditor"


# ---- negative "unassign" (removal) half of the matrix ----------------------
def test_admin_cannot_demote_super_forbidden_before_R3(ctx):
    # R1: an admin lacks unassign_super_admin_role → 403 (and it fires BEFORE the R3 409,
    # even though the seed super is the last one).
    _, users, roles = ctx
    with pytest.raises(ForbiddenException):
        _run(_uc(users, roles).execute(ADMIN, SEED_SUPER, _role_id(roles, "user"), "tok"))


def test_admin_cannot_demote_custom_role_holder(ctx):
    # Removing a custom role requires unassign_custom_role, which admin lacks → 403.
    _, users, roles = ctx
    roles.create_role("auditor", "Auditor", None)
    _target(users, roles, "t1", "auditor")
    with pytest.raises(ForbiddenException):
        _run(_uc(users, roles).execute(ADMIN, "t1", _role_id(roles, "user"), "tok"))


def test_combined_transition_requires_both_perms(ctx):
    # admin → super needs BOTH unassign_admin_role AND assign_super_admin_role. A caller
    # holding only the former is still 403 (the required set is a union).
    _, users, roles = ctx
    _target(users, roles, "t1", "admin")
    caller = _caller({"user.role.unassign_admin_role"})  # missing assign_super_admin_role
    with pytest.raises(ForbiddenException) as ei:
        _run(_uc(users, roles).execute(caller, "t1", _role_id(roles, "super_admin"), "tok"))
    assert "assign_super_admin_role" in str(ei.value)


# ---- R3 last-super lockout -------------------------------------------------
def test_R3_cannot_remove_last_super(ctx):
    _, users, roles = ctx
    # seed super_admin is the only super; demoting it -> 409
    with pytest.raises(ConflictException):
        _run(_uc(users, roles).execute(SUPER, SEED_SUPER, _role_id(roles, "user"), "tok"))


def test_R3_allows_demote_when_two_supers(ctx):
    _, users, roles = ctx
    _target(users, roles, "t2", "user")
    _run(_uc(users, roles).execute(SUPER, "t2", _role_id(roles, "super_admin"), "tok"))  # 2 supers now
    out = _run(_uc(users, roles).execute(SUPER, SEED_SUPER, _role_id(roles, "user"), "tok"))
    assert out.role_code == "user"


# ---- upsert-on-assign + no-op + not found ----------------------------------
def test_assign_upserts_unseen_target_via_pm(ctx):
    _, users, roles = ctx
    pm = _FakePM(email="fresh@laidon.com", name="Fresh")
    assert users.find_by_external_id("brand-new") is None
    out = _run(_uc(users, roles, pm).execute(ADMIN, "brand-new", _role_id(roles, "admin"), "tok"))
    assert pm.calls == 1 and out.role_code == "admin"
    assert users.find_by_external_id("brand-new").email == "fresh@laidon.com"


def test_assign_degrades_when_pm_unavailable(ctx):
    _, users, roles = ctx
    pm = _FakePM(fail=True)
    out = _run(_uc(users, roles, pm).execute(ADMIN, "brand-new", _role_id(roles, "admin"), "tok"))
    assert out.role_code == "admin"  # still assigned; email empty until target logs in


def test_assign_without_token_skips_pm(ctx):
    # No token (e.g. system caller) → target upserted by external_id, PM never called.
    _, users, roles = ctx
    pm = _FakePM()
    out = _run(_uc(users, roles, pm).execute(SUPER, "no-token-target", _role_id(roles, "admin"), None))
    assert out.role_code == "admin"
    assert pm.calls == 0


def test_failed_authorization_does_not_write_or_probe_pm(ctx):
    # SECURITY: a caller who fails the permission check must NOT trigger a PM fetch or a
    # mirror INSERT (authorization precedes all side effects).
    _, users, roles = ctx
    pm = _FakePM()
    weak = _caller({"user.read"})  # no assign_admin_role
    with pytest.raises(ForbiddenException):
        _run(_uc(users, roles, pm).execute(weak, "unseen-victim", _role_id(roles, "admin"), "tok"))
    assert users.find_by_external_id("unseen-victim") is None  # no row created
    assert pm.calls == 0  # no PM probe on the victim's behalf


def test_no_op_same_role_is_idempotent_without_perm(ctx):
    _, users, roles = ctx
    _target(users, roles, "t1", "user")
    weak = _caller(set())  # no perms at all
    out = _run(_uc(users, roles).execute(weak, "t1", _role_id(roles, "user"), "tok"))
    assert out.role_code == "user"          # no-op still succeeds (user->user is neutral)
    assert out.email is None and out.name is None   # but PII is NOT leaked on the no-op path (L-1)


def test_assign_unknown_role_not_found(ctx):
    _, users, roles = ctx
    with pytest.raises(NotFoundException):
        _run(_uc(users, roles).execute(SUPER, "t1", "ghost-role", "tok"))


# ---- list users ------------------------------------------------------------
def test_list_users_use_case(ctx):
    _, users, roles = ctx
    _target(users, roles, "t1", "admin")
    listed = {u.external_id: u for u in ListUsersUseCase(users).execute()}
    assert listed["t1"].role_code == "admin"
    assert listed[SEED_SUPER].is_super is True


# ---- boot smoke ------------------------------------------------------------
def test_app_registers_users_routes():
    from bootstrap import create_app
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/api/v1/agents-registry/users",
            "/api/v1/agents-registry/users/{external_id}/role"} <= paths
