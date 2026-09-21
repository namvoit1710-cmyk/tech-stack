"""T5 slice 3 — contract migration 20260716_02 + soft_delete pool-link cleanup.

Drives 20260716_01 then 20260716_02 against in-memory SQLite: verifies agent_users is
dropped, users.email becomes nullable, both reverse on downgrade, and that
HANAAgentRepository.soft_delete now clears agent_pool_agents (not agent_users).
"""
import importlib.util
import os
from contextlib import contextmanager

os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")
os.environ["DATABASE_SCHEMA"] = "PUBLIC"

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.layer4_infrastructure.persistence.repositories.hana_agent_pool_repository import (
    HANAAgentPoolRepository,
)
from app.layer4_infrastructure.persistence.repositories.hana_agent_repository import HANAAgentRepository


def _load(fname, modname):
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, "app", "layer4_infrastructure", "persistence", "migrations",
                        "versions", fname)
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mig01 = _load("20260716_01.py", "cm_mig01")
mig02 = _load("20260716_02.py", "cm_mig02")


def _engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _fk(dbapi, _):  # noqa: ANN001
        dbapi.execute("PRAGMA foreign_keys=ON")

    return eng


def _make_agents(conn):
    from app.layer4_infrastructure.persistence.models import AgentModel
    AgentModel.__table__.create(conn)
    conn.execute(sa.text(
        "INSERT INTO agents (id,name,kind,status,is_published,is_alive,version,provider,"
        "model,temperature,max_tokens,config_type,timeout_ms,max_concurrency,retry_count,"
        "streaming_supported,created_at,updated_at) VALUES ('ag1','A1','business','active',"
        "1,0,'1.0.0','openai','gpt-4',0.7,1024,'default',30000,1,3,0,"
        "'2026-01-01 00:00:00','2026-01-01 00:00:00')"
    ))
    conn.commit()


def _run(conn, module, direction):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()
    conn.commit()


def _email_nullable(conn):
    col = next(c for c in inspect(conn).get_columns("users") if c["name"] == "email")
    return col["nullable"]


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


def test_contract_migration_up_and_down():
    eng = _engine()
    conn = eng.connect()
    _make_agents(conn)
    _run(conn, mig01, "upgrade")
    # simulate the legacy agent_users table (created by the pre-RBAC base schema)
    conn.execute(sa.text("CREATE TABLE agent_users (id VARCHAR(36) PRIMARY KEY, "
                         "agent_id VARCHAR(36), user_id VARCHAR(36))"))
    conn.commit()
    assert "agent_users" in inspect(conn).get_table_names()
    assert _email_nullable(conn) is False  # 01 leaves email NOT NULL

    _run(conn, mig02, "upgrade")
    assert "agent_users" not in inspect(conn).get_table_names()  # dropped
    assert _email_nullable(conn) is True                          # relaxed

    _run(conn, mig02, "downgrade")
    assert "agent_users" in inspect(conn).get_table_names()       # recreated
    assert _email_nullable(conn) is False                         # NOT NULL restored
    conn.close()


def test_contract_migration_upgrade_is_idempotent():
    eng = _engine()
    conn = eng.connect()
    _make_agents(conn)
    _run(conn, mig01, "upgrade")
    _run(conn, mig02, "upgrade")
    _run(conn, mig02, "upgrade")  # second time: agent_users already gone, email already nullable
    assert "agent_users" not in inspect(conn).get_table_names()
    assert _email_nullable(conn) is True
    conn.close()


def test_agent_soft_delete_clears_pool_links_not_agent_users():
    eng = _engine()
    conn = eng.connect()
    _make_agents(conn)
    _run(conn, mig01, "upgrade")
    _run(conn, mig02, "upgrade")
    db = _FakeDbFactory(eng)
    pools = HANAAgentPoolRepository(db)
    agents = HANAAgentRepository(db)

    p = pools.create_pool("P", None, None)
    pools.add_agent(p.id, "ag1")
    assert pools.list_agent_ids(p.id) == ["ag1"]

    agents.soft_delete("ag1")  # T5: soft-delete clears agent_pool_agents

    assert pools.list_agent_ids(p.id) == []  # pool link cleared
    with db.get_session() as s:
        status = s.execute(sa.text("SELECT status FROM agents WHERE id='ag1'")).scalar()
    assert status == "deleted"  # agent row survives (soft delete)
    conn.close()
