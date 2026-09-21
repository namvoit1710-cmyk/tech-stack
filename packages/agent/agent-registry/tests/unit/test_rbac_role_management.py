"""T3 slice 2 — role & permission management (repos + use cases) on seeded SQLite.

Seeds the schema via the T1 migration, then exercises HANARoleRepository /
HANAPermissionRepository and the role/permission use cases incl. all is_super/is_system
guard rules and the delete-in-use (409) path. Boot-smoke asserts the routes register.
"""
import importlib.util
import os
from contextlib import contextmanager

os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ.setdefault("DATABASE_SCHEMA", "PUBLIC")

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.layer1_domain.exceptions import (
    AlreadyExistsException,
    ConflictException,
    InvalidDataException,
    InvalidOperationException,
    NotFoundException,
)
from app.layer2_application.use_cases.create_role import CreateRoleUseCase
from app.layer2_application.use_cases.delete_role import DeleteRoleUseCase
from app.layer2_application.use_cases.list_permissions import ListPermissionsUseCase
from app.layer2_application.use_cases.list_roles import ListRolesUseCase
from app.layer2_application.use_cases.set_role_permissions import SetRolePermissionsUseCase
from app.layer2_application.use_cases.update_role import UpdateRoleUseCase
from app.layer4_infrastructure.persistence.repositories.hana_permission_repository import HANAPermissionRepository
from app.layer4_infrastructure.persistence.repositories.hana_rbac_user_repository import HANARbacUserRepository
from app.layer4_infrastructure.persistence.repositories.hana_role_repository import HANARoleRepository


def _load_migration():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, "app", "layer4_infrastructure", "persistence", "migrations",
                        "versions", "20260716_01.py")
    spec = importlib.util.spec_from_file_location("t1_mig_for_t3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration()


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


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk(dbapi, _):  # noqa: ANN001
        dbapi.execute("PRAGMA foreign_keys=ON")

    conn = eng.connect()
    from app.layer4_infrastructure.persistence.models import AgentModel
    AgentModel.__table__.create(conn)
    conn.commit()
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mig.upgrade()
    conn.commit()
    yield _FakeDbFactory(eng)
    conn.close()


@pytest.fixture
def roles(db):
    return HANARoleRepository(db)


@pytest.fixture
def perms(db):
    return HANAPermissionRepository(db)


# ---- permission repo -------------------------------------------------------
def test_list_permissions_is_catalog_of_30(perms):
    items = perms.list_permissions()
    assert len(items) == 30
    assert [i.code for i in items] == sorted(i.code for i in items)


def test_find_ids_for_codes_maps_known_omits_unknown(perms):
    got = perms.find_ids_for_codes(["agent.read", "role.create", "nope.bad"])
    assert set(got) == {"agent.read", "role.create"}


# ---- role repo -------------------------------------------------------------
def test_list_roles_flags_and_counts(roles):
    by_code = {r.code: r for r in roles.list_roles()}
    assert set(by_code) == {"super_admin", "admin", "user"}
    assert by_code["super_admin"].is_super and by_code["super_admin"].is_system
    assert by_code["super_admin"].permission_codes == ()  # is_super stores none (bypass)
    assert len(by_code["admin"].permission_codes) == 21
    assert set(by_code["user"].permission_codes) == {"agent.read", "pool.read"}
    assert by_code["admin"].is_system and not by_code["admin"].is_super


def test_create_update_delete_custom_role(roles):
    created = roles.create_role("auditor", "Auditor", "read-only auditor")
    assert not created.is_system and not created.is_super
    assert roles.get_role_by_code("auditor").id == created.id
    roles.update_role(created.id, "Auditor 2", "desc2")
    assert roles.get_role(created.id).name == "Auditor 2"
    roles.delete_role(created.id)
    assert roles.get_role(created.id) is None


def test_set_role_permissions_replaces_and_cascade_on_delete(roles, perms, db):
    r = roles.create_role("auditor", "Auditor", None)
    ids = perms.find_ids_for_codes(["agent.read", "pool.read"])
    roles.set_role_permissions(r.id, list(ids.values()))
    assert set(roles.get_role(r.id).permission_codes) == {"agent.read", "pool.read"}
    # replace with a single code
    roles.set_role_permissions(r.id, [perms.find_ids_for_codes(["agent.read"])["agent.read"]])
    assert set(roles.get_role(r.id).permission_codes) == {"agent.read"}
    # delete cascades role_permissions
    roles.delete_role(r.id)
    with db.get_session() as s:
        left = s.execute(sa.text("SELECT COUNT(*) FROM role_permissions rp "
                                 "WHERE rp.role_id = :rid"), {"rid": r.id}).scalar()
    assert left == 0


def test_is_role_in_use(roles, db):
    admin = roles.get_role_by_code("admin")
    custom = roles.create_role("auditor", "Auditor", None)
    assert roles.is_role_in_use(custom.id) is False
    users = HANARbacUserRepository(db)
    uid, _ = users.upsert_user("ext-holder", email="h@b.com")
    users.set_role(uid, custom.id)
    assert roles.is_role_in_use(custom.id) is True
    # seeded super_admin user holds the super_admin role
    assert roles.is_role_in_use(roles.get_role_by_code("super_admin").id) is True
    assert admin is not None


# ---- use cases: create -----------------------------------------------------
def test_create_role_rejects_duplicate_and_empty(roles):
    uc = CreateRoleUseCase(roles)
    uc.execute("auditor", "Auditor")
    with pytest.raises(AlreadyExistsException):
        uc.execute("auditor", "Dup")
    with pytest.raises(InvalidDataException):
        uc.execute("  ", "NoCode")


# ---- use cases: update -----------------------------------------------------
def test_update_role_blocks_rename_of_system_role(roles):
    uc = UpdateRoleUseCase(roles)
    admin = roles.get_role_by_code("admin")
    # description-only change (name unchanged) is allowed on a system role
    out = uc.execute(admin.id, name=admin.name, description="new desc")
    assert out.description == "new desc"
    # renaming a system role is rejected
    with pytest.raises(InvalidOperationException):
        uc.execute(admin.id, name="administrator", description=None)
    # custom role renames freely
    custom = roles.create_role("auditor", "Auditor", None)
    assert uc.execute(custom.id, name="Auditor X").name == "Auditor X"
    with pytest.raises(NotFoundException):
        uc.execute("ghost", name="X")


# ---- use cases: set permissions -------------------------------------------
def test_set_role_permissions_rules(roles, perms):
    uc = SetRolePermissionsUseCase(roles, perms)
    su = roles.get_role_by_code("super_admin")
    with pytest.raises(InvalidOperationException):  # locked
        uc.execute(su.id, ["agent.read"])
    user = roles.get_role_by_code("user")
    out = uc.execute(user.id, ["agent.read", "agent.view_all", "agent.read"])  # dedup
    assert set(out.permission_codes) == {"agent.read", "agent.view_all"}
    with pytest.raises(InvalidDataException):  # unknown code
        uc.execute(user.id, ["nope.bad"])
    with pytest.raises(NotFoundException):
        uc.execute("ghost", ["agent.read"])


# ---- use cases: delete -----------------------------------------------------
def test_delete_role_rules(roles, db):
    uc = DeleteRoleUseCase(roles)
    admin = roles.get_role_by_code("admin")
    with pytest.raises(InvalidOperationException):  # built-in
        uc.execute(admin.id)
    custom = roles.create_role("auditor", "Auditor", None)
    users = HANARbacUserRepository(db)
    uid, _ = users.upsert_user("ext-h", email="h@b.com")
    users.set_role(uid, custom.id)
    with pytest.raises(ConflictException):  # in use → 409
        uc.execute(custom.id)
    users.set_role(uid, roles.get_role_by_code("user").id)  # release
    uc.execute(custom.id)
    assert roles.get_role(custom.id) is None
    with pytest.raises(NotFoundException):
        uc.execute("ghost")


# ---- boot smoke ------------------------------------------------------------
def test_app_registers_role_permission_routes():
    from bootstrap import create_app
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/api/v1/agents-registry/roles", "/api/v1/agents-registry/permissions",
            "/api/v1/agents-registry/roles/{role_id}",
            "/api/v1/agents-registry/roles/{role_id}/permissions"} <= paths
