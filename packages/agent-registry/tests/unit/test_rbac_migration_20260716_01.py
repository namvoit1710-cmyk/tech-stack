"""T1 RBAC additive migration (20260716_01) — SQLite unit tests.

Drives the real migration module against in-memory SQLite via alembic Operations,
schema=None (DATABASE_SCHEMA=PUBLIC), FK enforcement ON. Covers: table/FK/index
creation, seed (30 permissions + 3 roles + role_permissions + super_admin user),
idempotency (upgrade x2), FK CASCADE, full downgrade, and both the CREATE-users and
ALTER-users (pre-existing) branches. No HANA credentials required.
"""
import importlib.util
import os

# Settings() has required (no-default) fields; set dummies before importing the migration.
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
from sqlalchemy.pool import StaticPool

from app.layer4_infrastructure.persistence.models import AgentModel

SEED_SUPER_ADMIN_ID = "9e1a4f12-7a29-4f56-aa1a-f0651a777ab6"


def _load_migration():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(
        here, "app", "layer4_infrastructure", "persistence", "migrations",
        "versions", "20260716_01.py",
    )
    spec = importlib.util.spec_from_file_location("t1_migration", path)
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


def _run(conn, fn):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(mig, fn)()
    conn.commit()


def _make_agents(conn):
    AgentModel.__table__.create(conn)
    conn.execute(sa.text(
        "INSERT INTO agents (id,name,kind,status,is_published,is_alive,version,provider,"
        "model,temperature,max_tokens,config_type,timeout_ms,max_concurrency,retry_count,"
        "streaming_supported,created_at,updated_at) VALUES ('ag1','A1','business','active',"
        "1,0,'1.0.0','openai','gpt-4',0.7,1024,'default',30000,1,3,0,'2026','2026')"
    ))
    conn.commit()


def _make_old_users(conn):
    conn.execute(sa.text(
        "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255) NOT NULL, "
        "name VARCHAR(255), tenant_id VARCHAR(36), created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    ))
    conn.execute(sa.text("CREATE UNIQUE INDEX ix_users_email ON users (email)"))
    conn.execute(sa.text(
        "INSERT INTO users (id,email,name,tenant_id,created_at,updated_at) VALUES "
        "('legacy1','legacy@x.com','Legacy',NULL,'2026','2026')"
    ))
    conn.commit()


def _fk_map(insp, table):
    out = {}
    for fk in insp.get_foreign_keys(table):
        out[tuple(fk["constrained_columns"])] = (
            fk["referred_table"], (fk.get("options") or {}).get("ondelete"),
        )
    return out


@pytest.fixture
def created(_conn_factory=None):
    """CREATE-users branch: fresh schema with only `agents` pre-created."""
    eng = _engine()
    conn = eng.connect()
    _make_agents(conn)
    _run(conn, "upgrade")
    yield conn
    conn.close()


def test_all_tables_created(created):
    tables = set(sa.inspect(created).get_table_names())
    assert {
        "roles", "permissions", "role_permissions", "users",
        "agent_pools", "agent_pool_agents", "agent_pool_members",
    } <= tables


def test_foreign_keys_and_cascade(created):
    insp = sa.inspect(created)
    assert _fk_map(insp, "role_permissions")[("role_id",)] == ("roles", "CASCADE")
    assert _fk_map(insp, "role_permissions")[("permission_id",)] == ("permissions", "CASCADE")
    assert _fk_map(insp, "users")[("role_id",)][0] == "roles"
    assert _fk_map(insp, "agent_pools")[("created_by",)] == ("users", "SET NULL")
    assert _fk_map(insp, "agent_pool_agents")[("pool_id",)] == ("agent_pools", "CASCADE")
    assert _fk_map(insp, "agent_pool_agents")[("agent_id",)] == ("agents", "CASCADE")
    assert _fk_map(insp, "agent_pool_members")[("pool_id",)] == ("agent_pools", "CASCADE")
    assert _fk_map(insp, "agent_pool_members")[("user_id",)] == ("users", "CASCADE")


def test_indexes(created):
    insp = sa.inspect(created)
    uidx = {i["name"]: i for i in insp.get_indexes("users")}
    assert uidx["ix_users_external_id"]["unique"]
    assert not uidx["ix_users_email"]["unique"]
    assert any(i["name"] == "ix_roles_code" and i["unique"] for i in insp.get_indexes("roles"))
    assert any(i["name"] == "ix_permissions_code" and i["unique"] for i in insp.get_indexes("permissions"))


def test_seed_catalog_and_roles(created):
    assert created.execute(sa.text("SELECT COUNT(*) FROM permissions")).scalar() == 30
    assert created.execute(sa.text("SELECT COUNT(*) FROM roles")).scalar() == 3
    assert created.execute(sa.text("SELECT COUNT(*) FROM roles WHERE is_super=1")).scalar() == 1
    admin = created.execute(sa.text(
        "SELECT COUNT(*) FROM role_permissions rp JOIN roles r ON rp.role_id=r.id "
        "WHERE r.code='admin'"
    )).scalar()
    user = created.execute(sa.text(
        "SELECT COUNT(*) FROM role_permissions rp JOIN roles r ON rp.role_id=r.id "
        "WHERE r.code='user'"
    )).scalar()
    superp = created.execute(sa.text(
        "SELECT COUNT(*) FROM role_permissions rp JOIN roles r ON rp.role_id=r.id "
        "WHERE r.code='super_admin'"
    )).scalar()
    assert (admin, user, superp) == (21, 2, 0)


def _perm_codes_for_role(conn, role_code):
    rows = conn.execute(
        sa.text(
            "SELECT p.code FROM role_permissions rp "
            "JOIN roles r ON rp.role_id = r.id "
            "JOIN permissions p ON rp.permission_id = p.id "
            "WHERE r.code = :code"
        ),
        {"code": role_code},
    ).scalars().all()
    return set(rows)


def test_permission_catalog_is_exactly_the_30_codes(created):
    codes = set(created.execute(sa.text("SELECT code FROM permissions")).scalars().all())
    expected = {
        "agent.read", "agent.view_all", "agent.create", "agent.update", "agent.delete",
        "agent.publish", "agent.unpublish", "agent.activate", "agent.deactivate",
        "pool.read", "pool.create", "pool.update", "pool.delete",
        "pool.agent.add", "pool.agent.remove", "pool.member.add", "pool.member.remove",
        "user.read", "user.role.assign_admin_role", "user.role.unassign_admin_role",
        "user.role.assign_super_admin_role", "user.role.unassign_super_admin_role",
        "user.role.assign_custom_role", "user.role.unassign_custom_role",
        "role.read", "role.create", "role.update", "role.update_mapping_permission",
        "role.remove", "permission.view",
    }
    assert codes == expected


def test_admin_role_has_exact_permission_codes(created):
    admin = _perm_codes_for_role(created, "admin")
    expected_admin = {
        "agent.read", "agent.view_all", "agent.create", "agent.update", "agent.delete",
        "agent.publish", "agent.unpublish", "agent.activate", "agent.deactivate",
        "pool.read", "pool.create", "pool.update", "pool.delete",
        "pool.agent.add", "pool.agent.remove", "pool.member.add", "pool.member.remove",
        "user.read", "user.role.assign_admin_role", "user.role.unassign_admin_role",
        "role.read",
    }
    assert admin == expected_admin
    # admin must NOT hold super_admin-tier / custom-tier / RBAC-config perms
    forbidden = {
        "user.role.assign_super_admin_role", "user.role.unassign_super_admin_role",
        "user.role.assign_custom_role", "user.role.unassign_custom_role",
        "role.create", "role.update", "role.update_mapping_permission", "role.remove",
        "permission.view",
    }
    assert admin.isdisjoint(forbidden)


def test_user_role_has_exact_permission_codes(created):
    assert _perm_codes_for_role(created, "user") == {"agent.read", "pool.read"}


def test_seed_super_admin_user(created):
    row = created.execute(sa.text(
        "SELECT r.code, r.is_super FROM users u JOIN roles r ON u.role_id=r.id "
        f"WHERE u.id='{SEED_SUPER_ADMIN_ID}'"
    )).first()
    assert row is not None and row[0] == "super_admin" and row[1] in (1, True)


def test_upgrade_is_idempotent(created):
    _run(created, "upgrade")  # second time
    assert created.execute(sa.text("SELECT COUNT(*) FROM permissions")).scalar() == 30
    assert created.execute(sa.text("SELECT COUNT(*) FROM roles")).scalar() == 3
    assert created.execute(sa.text(
        f"SELECT COUNT(*) FROM users WHERE id='{SEED_SUPER_ADMIN_ID}'"
    )).scalar() == 1


def test_cascade_fires_on_hard_delete(created):
    created.execute(sa.text(
        "INSERT INTO agent_pools (id,name,status,created_at,updated_at) "
        "VALUES ('p1','P1','active','2026','2026')"
    ))
    created.execute(sa.text(
        "INSERT INTO agent_pool_agents (pool_id,agent_id,created_at) VALUES ('p1','ag1','2026')"
    ))
    created.commit()
    created.execute(sa.text("DELETE FROM agent_pools WHERE id='p1'"))
    created.commit()
    assert created.execute(sa.text(
        "SELECT COUNT(*) FROM agent_pool_agents WHERE pool_id='p1'"
    )).scalar() == 0
    assert created.execute(sa.text("SELECT COUNT(*) FROM agents WHERE id='ag1'")).scalar() == 1


def test_downgrade_reverses_create_branch(created):
    _run(created, "downgrade")
    insp = sa.inspect(created)
    tables = set(insp.get_table_names())
    assert not ({"roles", "permissions", "role_permissions", "agent_pools",
                 "agent_pool_agents", "agent_pool_members"} & tables)
    assert "users" in tables  # users predates T1; kept
    ucols = {c["name"] for c in insp.get_columns("users")}
    assert "external_id" not in ucols and "role_id" not in ucols
    assert any(i["name"] == "ix_users_email" and i["unique"] for i in insp.get_indexes("users"))
    assert created.execute(sa.text(
        f"SELECT COUNT(*) FROM users WHERE id='{SEED_SUPER_ADMIN_ID}'"
    )).scalar() == 0


def test_alter_branch_upgrade_and_downgrade():
    eng = _engine()
    conn = eng.connect()
    _make_agents(conn)
    _make_old_users(conn)
    _run(conn, "upgrade")

    insp = sa.inspect(conn)
    ucols = {c["name"] for c in insp.get_columns("users")}
    assert "external_id" in ucols and "role_id" in ucols
    assert _fk_map(insp, "users")[("role_id",)][0] == "roles"
    assert any(i["name"] == "ix_users_external_id" and i["unique"] for i in insp.get_indexes("users"))
    assert any(i["name"] == "ix_users_email" and not i["unique"] for i in insp.get_indexes("users"))
    # legacy user backfilled to the default 'user' role
    assert conn.execute(sa.text(
        "SELECT r.code FROM users u JOIN roles r ON u.role_id=r.id WHERE u.id='legacy1'"
    )).scalar() == "user"

    _run(conn, "downgrade")
    insp2 = sa.inspect(conn)
    ucols2 = {c["name"] for c in insp2.get_columns("users")}
    assert "external_id" not in ucols2 and "role_id" not in ucols2
    assert conn.execute(sa.text("SELECT COUNT(*) FROM users WHERE id='legacy1'")).scalar() == 1
    assert any(i["name"] == "ix_users_email" and i["unique"] for i in insp2.get_indexes("users"))
    conn.close()
