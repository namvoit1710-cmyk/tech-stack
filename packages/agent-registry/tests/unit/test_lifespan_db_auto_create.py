"""T6 (SA-1991) slice 1 — D26 fix: lifespan gates create_tables() on DB_AUTO_CREATE.

A migrated HANA schema must NOT receive create_all() on startup — SQLAlchemy's schema-scoped
existence check misfires and re-issues CREATE TABLE for existing tables ("duplicate table
name: AGENTS"), failing startup. This locks the gate: create_tables() is called iff
settings.db_auto_create is True; otherwise the schema is left to alembic.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from lifespan import lifespan


def _drive(db_auto_create: bool):
    """Run the lifespan startup+shutdown with a fully mocked container; return the db_factory."""
    settings = SimpleNamespace(db_auto_create=db_auto_create, health_check_interval_seconds=15)
    db_factory = MagicMock()
    api_client = MagicMock()
    api_client.aclose = AsyncMock()

    container = MagicMock()
    container.settings.return_value = settings
    container.database_factory.return_value = db_factory
    container.fetch_current_user_info_api_client.return_value = api_client
    container.fetch_workflow_api_client.return_value = api_client

    app = MagicMock()
    app.state.container = container

    async def _run():
        async with lifespan(app):
            pass

    # Patch the scheduler factory so no real background thread starts during the test.
    with patch("lifespan.create_health_check_scheduler", return_value=MagicMock()):
        asyncio.run(_run())
    return db_factory


def test_create_tables_skipped_when_auto_create_off():
    # D26: default posture — schema managed by alembic, create_all() NOT invoked.
    db_factory = _drive(db_auto_create=False)
    db_factory.create_engine.assert_called_once()
    db_factory.create_tables.assert_not_called()
    db_factory.create_session_factory.assert_called_once()


def test_create_tables_called_when_auto_create_on():
    # Opt-in for an ephemeral/fresh DB with no migrations.
    db_factory = _drive(db_auto_create=True)
    db_factory.create_tables.assert_called_once()
