"""T2 RBAC — user mirror + principal resolution (SQLite unit tests).

Seeds a real schema by running the T1 migration (20260716_01) against in-memory SQLite
(30 permissions + 3 roles + super_admin user), then exercises HANARbacUserRepository,
JwtIdentityTokenReader and ResolvePrincipalUseCase. No HANA credentials, no PyJWT.

Covers: JWT id extraction (user_uuid → sub → none); upsert new/existing (role-preserving);
role/permission reads; count_users_with_super_role; principal for new/existing/is_super
users; system principal via no-token+flag; 401 with flag off; PM-outage graceful degrade
(transient, non-persisted); permission set matches role.
"""
import asyncio
import base64
import importlib.util
import json
import os
from contextlib import contextmanager

# Settings() has required (no-default) fields; set dummies before importing anything
# that constructs it (the migration module does at import time).
os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ["DATABASE_SCHEMA"] = "PUBLIC"  # -> migration schema is None

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.layer1_domain.entities.principal import KIND_SYSTEM, KIND_USER
from app.layer1_domain.exceptions import InvalidDataException, UnauthenticatedException
from app.layer1_domain.rbac import DEFAULT_USER_PERMISSIONS
from app.layer2_application.use_cases.resolve_principal import ResolvePrincipalUseCase
from app.layer4_infrastructure.persistence.models.user_model import UserModel
from app.layer4_infrastructure.persistence.repositories.hana_rbac_user_repository import (
    HANARbacUserRepository,
)
from app.layer4_infrastructure.security.jwt_identity_token_reader import JwtIdentityTokenReader

SEED_SUPER_ADMIN_ID = "9e1a4f12-7a29-4f56-aa1a-f0651a777ab6"
ADMIN_PERMS_COUNT = 21
USER_PERMS = {"agent.read", "pool.read"}


# --------------------------------------------------------------------------- #
# Migration harness (seeds roles/permissions/super_admin — mirrors T1 test).   #
# --------------------------------------------------------------------------- #
def _load_migration():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(
        here, "app", "layer4_infrastructure", "persistence", "migrations",
        "versions", "20260716_01.py",
    )
    spec = importlib.util.spec_from_file_location("t1_migration_for_t2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration()


def _engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return eng


def _make_agents(conn):
    from app.layer4_infrastructure.persistence.models import AgentModel

    AgentModel.__table__.create(conn)
    conn.commit()


class _FakeDbFactory:
    """Minimal DatabaseFactory stand-in: commit-on-exit session context manager."""

    def __init__(self, engine):
        self._sm = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    @contextmanager
    def get_session(self):
        session = self._sm()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def db():
    """Seeded in-memory DB (T1 migration applied) + a fake db_factory over it."""
    eng = _engine()
    conn = eng.connect()
    _make_agents(conn)
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mig.upgrade()
    conn.commit()
    yield _FakeDbFactory(eng)
    conn.close()


@pytest.fixture
def repo(db):
    return HANARbacUserRepository(db)


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class _StubTokenReader:
    def __init__(self, external_id):
        self._external_id = external_id

    def extract_user_uuid(self, token):
        return self._external_id


class _FakeUserInfo:
    def __init__(self, email, name):
        self.email = email
        self.name = name


class _FakePM:
    """Fake PM client; counts calls and can simulate an outage.

    Mirrors the real client, which wraps every failure into InvalidDataException.
    """

    def __init__(self, email="new@laidon.com", name="New User", fail=False):
        self.email, self.name, self.fail = email, name, fail
        self.calls = 0

    async def fetch_current_user_info(self, token):
        self.calls += 1
        if self.fail:
            raise InvalidDataException("PM unreachable")
        return _FakeUserInfo(self.email, self.name)


class _FakeRepoNoUserRole:
    """Minimal IRbacUserRepository where the `user` role can't be read.

    Forces the deepest degrade branch of ResolvePrincipalUseCase (transient principal
    falling back to the DEFAULT_USER_PERMISSIONS constant when find_role_by_code is None).
    """

    def find_by_external_id(self, external_id):
        return None

    def find_role_by_code(self, code):
        return None

    def get_permissions_for_role(self, role_id):
        return set()


def _make_token(claims: dict) -> str:
    def seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.{seg({'sig': 'x'})}"


def _resolve(uc, token):
    return asyncio.run(uc.execute(token))


def _uc(repo, external_id=None, pm=None, trust=True, reader=None):
    return ResolvePrincipalUseCase(
        user_repository=repo,
        identity_token_reader=reader or _StubTokenReader(external_id),
        fetch_current_user_info_api_client=pm or _FakePM(),
        trust_unauthenticated_internal=trust,
    )


# --------------------------------------------------------------------------- #
# JwtIdentityTokenReader                                                        #
# --------------------------------------------------------------------------- #
def test_jwt_reader_prefers_user_uuid():
    reader = JwtIdentityTokenReader()
    token = _make_token({"user_uuid": "uuid-1", "sub": "sub-1"})
    assert reader.extract_user_uuid(token) == "uuid-1"


def test_jwt_reader_falls_back_to_sub():
    reader = JwtIdentityTokenReader()
    assert reader.extract_user_uuid(_make_token({"sub": "sub-1"})) == "sub-1"


def test_jwt_reader_returns_none_when_no_id_claim():
    reader = JwtIdentityTokenReader()
    assert reader.extract_user_uuid(_make_token({"email": "x@y.com"})) is None


@pytest.mark.parametrize("bad", ["", "not-a-jwt", "only.two", "a.b.c.d", "x.@@@.y"])
def test_jwt_reader_handles_malformed_tokens(bad):
    assert JwtIdentityTokenReader().extract_user_uuid(bad) is None


def _token_with_raw_payload(payload_obj) -> str:
    """Build a structurally-valid 3-part token whose payload decodes to `payload_obj`."""
    raw = base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).rstrip(b"=").decode()
    return f"header.{raw}.sig"


def test_jwt_reader_returns_none_for_non_dict_payload():
    # Valid base64url, valid JSON, but not an object → guarded by the isinstance(dict) check.
    assert JwtIdentityTokenReader().extract_user_uuid(_token_with_raw_payload([1, 2, 3])) is None
    assert JwtIdentityTokenReader().extract_user_uuid(_token_with_raw_payload("just-a-string")) is None


def test_jwt_reader_ignores_non_string_id_claim():
    assert JwtIdentityTokenReader().extract_user_uuid(_make_token({"sub": 12345})) is None


def test_jwt_reader_rejects_expired_token():
    reader = JwtIdentityTokenReader()
    token = _make_token({"user_uuid": "uuid-1", "exp": 1_000_000})  # far in the past
    assert reader.extract_user_uuid(token) is None


def test_jwt_reader_accepts_unexpired_token():
    reader = JwtIdentityTokenReader()
    token = _make_token({"user_uuid": "uuid-1", "exp": 9_999_999_999})  # far in the future
    assert reader.extract_user_uuid(token) == "uuid-1"


@pytest.mark.parametrize("bad_exp", ["not-a-number", None, [123]])
def test_jwt_reader_tolerates_unparseable_exp(bad_exp):
    # A non-numeric/unparseable exp must NOT reject the token (gateway is authoritative);
    # exercises the except (TypeError, ValueError) backstop in _is_expired.
    reader = JwtIdentityTokenReader()
    token = _make_token({"user_uuid": "uuid-1", "exp": bad_exp})
    assert reader.extract_user_uuid(token) == "uuid-1"


# --------------------------------------------------------------------------- #
# HANARbacUserRepository                                                        #
# --------------------------------------------------------------------------- #
def test_upsert_new_user_gets_default_user_role(repo):
    user_id, role_code = repo.upsert_user("ext-1", email="a@b.com", name="Alice")
    assert role_code == "user"
    rec = repo.find_by_external_id("ext-1")
    assert rec is not None
    assert rec.user_id == user_id
    assert rec.role_code == "user"
    assert rec.is_super is False
    assert rec.email == "a@b.com" and rec.name == "Alice"


def test_upsert_existing_refreshes_email_name_keeps_role(repo):
    uid, _ = repo.upsert_user("ext-2", email="old@b.com", name="Old")
    # promote to admin, then upsert again — role must be preserved
    admin = repo.find_role_by_code("admin")
    repo.set_role(uid, admin.role_id)
    uid2, role_code = repo.upsert_user("ext-2", email="new@b.com", name="New")
    assert uid2 == uid
    assert role_code == "admin"
    rec = repo.find_by_external_id("ext-2")
    assert rec.email == "new@b.com" and rec.name == "New"
    assert rec.role_code == "admin"


def test_find_by_external_id_unknown_returns_none(repo):
    assert repo.find_by_external_id("nope") is None


def test_count_users_with_super_role_is_one_after_seed(repo):
    assert repo.count_users_with_super_role() == 1


def test_get_permissions_for_role_matches_seed(repo):
    user_role = repo.find_role_by_code("user")
    admin_role = repo.find_role_by_code("admin")
    assert repo.get_permissions_for_role(user_role.role_id) == USER_PERMS
    assert len(repo.get_permissions_for_role(admin_role.role_id)) == ADMIN_PERMS_COUNT


def test_find_role_by_code(repo):
    assert repo.find_role_by_code("super_admin").is_super is True
    assert repo.find_role_by_code("user").is_super is False
    assert repo.find_role_by_code("ghost") is None


def test_list_users_joins_role(repo):
    repo.upsert_user("ext-list", email="l@b.com", name="L")
    users = {u.external_id: u for u in repo.list_users()}
    assert users["ext-list"].role_code == "user"
    assert users["ext-list"].is_super is False
    # seed super_admin present with its role + is_super flag derived from the join
    assert users[SEED_SUPER_ADMIN_ID].role_code == "super_admin"
    assert users[SEED_SUPER_ADMIN_ID].is_super is True


def test_set_role_on_missing_user_is_noop(repo):
    admin = repo.find_role_by_code("admin")
    # Must not raise and must not create a row.
    repo.set_role("ghost-user-id", admin.role_id)
    assert repo.find_by_external_id("ghost-user-id") is None


def test_upsert_concurrent_insert_race_returns_existing_row(repo, db):
    """Exercise the IntegrityError → rollback → refetch branch deterministically.

    A subclass hides the row on the FIRST lookup (so upsert takes the insert path),
    but the unique external_id index then rejects the insert; the except-branch refetch
    sees the real winner.
    """
    repo.upsert_user("ext-race", email="first@b.com", name="First")
    existing = repo.find_by_external_id("ext-race")

    class _RaceRepo(HANARbacUserRepository):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._n = 0

        def _row_by_external_id(self, session, external_id):
            self._n += 1
            if self._n == 1:
                return None  # pretend we didn't see it → take the insert path
            return super()._row_by_external_id(session, external_id)

    uid, role_code = _RaceRepo(db).upsert_user("ext-race", email="second@b.com", name="Second")
    assert uid == existing.user_id
    assert role_code == "user"
    # loser's email/name were NOT applied (insert rolled back)
    assert repo.find_by_external_id("ext-race").email == "first@b.com"


def test_get_permissions_for_role_empty_for_falsy_role_id(repo):
    # Defensive guard: a null/empty role_id yields no permissions (deny-all).
    assert repo.get_permissions_for_role(None) == set()
    assert repo.get_permissions_for_role("") == set()


def test_upsert_existing_user_with_null_role_returns_none_code(repo, db):
    # Existing row whose role_id is NULL → role_code resolves to None (defensive path).
    with db.get_session() as session:
        session.add(
            UserModel(id="u-norole", external_id="ext-norole", email="n@b.com", name="N", role_id=None)
        )
    uid, role_code = repo.upsert_user("ext-norole", email="n2@b.com")
    assert uid == "u-norole"
    assert role_code is None


def test_new_id_uses_injected_uuid_generator(db):
    class _FixedGen:
        def generate_uuid(self):
            return "fixed-uuid-123"

    repo = HANARbacUserRepository(db, uuid_generator=_FixedGen())
    uid, _ = repo.upsert_user("ext-fixed", email="f@b.com", name="F")
    assert uid == "fixed-uuid-123"


# --------------------------------------------------------------------------- #
# ResolvePrincipalUseCase                                                       #
# --------------------------------------------------------------------------- #
def test_no_token_with_flag_on_is_system(repo):
    principal = _resolve(_uc(repo, trust=True), None)
    assert principal.kind == KIND_SYSTEM
    assert principal.is_system is True
    assert principal.has("agent.create") is True  # system bypasses
    assert principal.bypass_pool_visibility is True


def test_no_token_with_flag_off_raises_401(repo):
    with pytest.raises(UnauthenticatedException):
        _resolve(_uc(repo, trust=False), None)


def test_token_without_identity_raises_401(repo):
    uc = _uc(repo, reader=_StubTokenReader(None))
    with pytest.raises(UnauthenticatedException):
        _resolve(uc, "some-token")


def test_new_user_is_mirrored_with_user_role(repo):
    pm = _FakePM(email="fresh@laidon.com", name="Fresh")
    principal = _resolve(_uc(repo, external_id="ext-new", pm=pm), "tok")
    assert principal.kind == KIND_USER
    assert principal.role_code == "user"
    assert principal.is_super is False
    assert principal.permissions == USER_PERMS
    assert principal.has("agent.read") is True
    assert principal.has("agent.create") is False
    assert principal.bypass_pool_visibility is False
    # persisted + PM was consulted exactly once (missing row)
    assert pm.calls == 1
    rec = repo.find_by_external_id("ext-new")
    assert rec is not None and rec.email == "fresh@laidon.com"


def test_known_user_does_not_call_pm(repo):
    repo.upsert_user("ext-known", email="k@b.com", name="K")
    pm = _FakePM()
    principal = _resolve(_uc(repo, external_id="ext-known", pm=pm), "tok")
    assert pm.calls == 0  # mirror-first: PM off the hot path
    assert principal.role_code == "user"
    assert principal.permissions == USER_PERMS


def test_is_super_user_bypasses(repo):
    pm = _FakePM()
    principal = _resolve(_uc(repo, external_id=SEED_SUPER_ADMIN_ID, pm=pm), "tok")
    assert pm.calls == 0
    assert principal.is_super is True
    assert principal.role_code == "super_admin"
    assert principal.has("role.create") is True  # bypass
    assert principal.bypass_pool_visibility is True
    # is_super carries an intentionally EMPTY set — authz goes through has(), not .permissions
    assert principal.permissions == frozenset()


def test_admin_user_permission_set(repo):
    uid, _ = repo.upsert_user("ext-admin", email="ad@b.com", name="Ad")
    repo.set_role(uid, repo.find_role_by_code("admin").role_id)
    principal = _resolve(_uc(repo, external_id="ext-admin"), "tok")
    assert principal.role_code == "admin"
    assert len(principal.permissions) == ADMIN_PERMS_COUNT
    assert principal.has("agent.create") is True
    assert principal.has("role.create") is False  # admin can't manage RBAC config
    assert principal.bypass_pool_visibility is True  # admin has agent.view_all


def test_pm_outage_on_new_user_degrades_without_persisting(repo):
    pm = _FakePM(fail=True)
    principal = _resolve(_uc(repo, external_id="ext-outage", pm=pm), "tok")
    assert pm.calls == 1
    assert principal.kind == KIND_USER
    assert principal.user_id is None  # transient — not persisted
    assert principal.role_code == "user"
    assert principal.permissions == USER_PERMS
    assert principal.bypass_pool_visibility is False
    # no mirror row written
    assert repo.find_by_external_id("ext-outage") is None


def test_pm_outage_on_known_user_is_unaffected(repo):
    repo.upsert_user("ext-safe", email="s@b.com", name="S")
    pm = _FakePM(fail=True)  # would raise if called
    principal = _resolve(_uc(repo, external_id="ext-safe", pm=pm), "tok")
    assert pm.calls == 0
    assert principal.user_id is not None
    assert principal.role_code == "user"


def test_transient_falls_back_to_constant_when_user_role_unreadable():
    """Deepest degrade: PM down AND the `user` role can't be read → constant perms."""
    uc = ResolvePrincipalUseCase(
        user_repository=_FakeRepoNoUserRole(),
        identity_token_reader=_StubTokenReader("ext-deep"),
        fetch_current_user_info_api_client=_FakePM(fail=True),
        trust_unauthenticated_internal=True,
    )
    principal = _resolve(uc, "tok")
    assert principal.kind == KIND_USER
    assert principal.user_id is None  # transient
    assert principal.role_code == "user"
    assert principal.permissions == DEFAULT_USER_PERMISSIONS
    assert principal.bypass_pool_visibility is False


def test_programming_error_in_pm_is_not_swallowed(repo):
    """A non-InvalidDataException from PM must propagate (not masquerade as outage)."""

    class _BuggyPM:
        calls = 0

        async def fetch_current_user_info(self, token):
            raise AttributeError("bug in DTO mapping")

    uc = _uc(repo, external_id="ext-bug", pm=_BuggyPM())
    with pytest.raises(AttributeError):
        _resolve(uc, "tok")
