"""Tests for the Phase 4b.7 event-log backend selection in ``bootstrap.py``.

Verifies:
- ``_build_event_log_repo`` returns the correct type for each setting.
- Default (``"memory"``) produces ``MemoryEventLogRepository``.
- ``"sqlite"`` produces ``SqliteEventLogRepository`` with the configured
  ``EVENT_LOG_DB_PATH``.
- Unknown values fall back to memory with a warning (defensive — the
  engine must never fail to start because of a misconfigured backend).
- End-to-end: ``build_app_container`` with ``EVENT_LOG_BACKEND=sqlite``
  wires a SQLite-backed event log into the container's dependency pool.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.sqlite.sqlite_event_log_repo import (
    SqliteEventLogRepository,
)
from bootstrap import _build_event_log_repo


# ───────────────────── factory-level tests ─────────────────────


class TestBuildEventLogRepoFactory:
    """Exercise ``_build_event_log_repo`` directly with stub settings.

    Uses ``SimpleNamespace`` rather than constructing the full Pydantic
    Settings model so the tests run without touching env vars or VCAP.
    """

    def test_default_memory_backend(self):
        settings = SimpleNamespace(EVENT_LOG_BACKEND="memory", EVENT_LOG_DB_PATH=":memory:")
        repo = _build_event_log_repo(settings)
        assert isinstance(repo, MemoryEventLogRepository)

    def test_explicit_sqlite_backend(self):
        settings = SimpleNamespace(EVENT_LOG_BACKEND="sqlite", EVENT_LOG_DB_PATH=":memory:")
        repo = _build_event_log_repo(settings)
        try:
            assert isinstance(repo, SqliteEventLogRepository)
        finally:
            repo.close()

    def test_sqlite_backend_accepts_custom_db_path(self):
        """The db_path is passed through to the SQLite backend so
        production deployments can point at a real file."""
        import os
        import tempfile
        fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            settings = SimpleNamespace(
                EVENT_LOG_BACKEND="sqlite", EVENT_LOG_DB_PATH=db_path,
            )
            repo = _build_event_log_repo(settings)
            try:
                assert isinstance(repo, SqliteEventLogRepository)
                # Sanity: the repo is fully functional with the custom path.
                from app.layer2_application.interfaces.event_log_repository_interface import EventLogEnvelope
                repo.append(EventLogEnvelope(
                    event_id="e1", run_id="r1", topic="t",
                    event_type="TestEvent",
                    timestamp="2026-04-11T00:00:00Z", payload={},
                ))
                assert len(repo.list_by_run("r1")) == 1
            finally:
                repo.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_case_insensitive_backend_name(self):
        """Env vars may arrive in any case. The factory normalises to
        lowercase so ``SQLITE``, ``Sqlite``, ``sqlite`` all work.
        """
        settings = SimpleNamespace(EVENT_LOG_BACKEND="SQLITE", EVENT_LOG_DB_PATH=":memory:")
        repo = _build_event_log_repo(settings)
        try:
            assert isinstance(repo, SqliteEventLogRepository)
        finally:
            repo.close()

    def test_unknown_backend_falls_back_to_memory(self):
        """Misconfiguration should not fail the engine startup — the
        event log is a shadow log, not a critical path, so we degrade
        gracefully."""
        settings = SimpleNamespace(
            EVENT_LOG_BACKEND="bogus-backend", EVENT_LOG_DB_PATH=":memory:",
        )
        repo = _build_event_log_repo(settings)
        assert isinstance(repo, MemoryEventLogRepository)

    def test_none_or_empty_backend_falls_back_to_memory(self):
        """Missing / empty settings fall back to memory (the default)."""
        settings = SimpleNamespace(EVENT_LOG_BACKEND=None, EVENT_LOG_DB_PATH=":memory:")
        repo = _build_event_log_repo(settings)
        assert isinstance(repo, MemoryEventLogRepository)

        settings = SimpleNamespace(EVENT_LOG_BACKEND="", EVENT_LOG_DB_PATH=":memory:")
        repo = _build_event_log_repo(settings)
        assert isinstance(repo, MemoryEventLogRepository)

    def test_hana_backend_without_infra_falls_back_to_memory(self):
        """Phase 4b.8: ``EVENT_LOG_BACKEND=hana`` but no HANA connection
        (INFRA_MODE != sap or HANA init failed → infra.db is None)
        degrades gracefully to memory. The engine must never fail to
        start because of a misconfigured event log.
        """
        settings = SimpleNamespace(EVENT_LOG_BACKEND="hana", EVENT_LOG_DB_PATH=":memory:")
        # No infra passed
        repo = _build_event_log_repo(settings)
        assert isinstance(repo, MemoryEventLogRepository)

        # Explicit infra with db=None (mock mode)
        infra = SimpleNamespace(db=None)
        repo = _build_event_log_repo(settings, infra=infra)
        assert isinstance(repo, MemoryEventLogRepository)

    def test_hana_backend_with_infra_db_uses_hana_repo(self):
        """When ``infra.db`` is a real ``IDatabaseConnection``, the
        factory returns a ``HanaEventLogRepository`` wired around it.
        """
        from app.layer4_frameworks.persistence.hana.hana_event_log_repo import (
            HanaEventLogRepository,
        )

        # Minimal IDatabaseConnection stub — doesn't need to do
        # anything because the factory only stores the reference.
        class _StubDb:
            def execute_query(self, sql, params):
                return []
            def execute_write(self, sql, params):
                return None

        stub_db = _StubDb()
        infra = SimpleNamespace(db=stub_db)
        settings = SimpleNamespace(EVENT_LOG_BACKEND="hana", EVENT_LOG_DB_PATH=":memory:")

        repo = _build_event_log_repo(settings, infra=infra)
        assert isinstance(repo, HanaEventLogRepository)
        # The repo should hold a reference to the exact db stub we passed
        assert repo._db is stub_db


# ───────────────────── end-to-end bootstrap wiring ─────────────────────


class TestBootstrapWiring:
    """Verify ``build_app_container`` picks up the selected backend.

    Monkey-patches ``settings`` in the bootstrap module because the
    module reads it at call time, so a local monkey-patch is sufficient.
    """

    def test_bootstrap_default_uses_memory_backend(self, monkeypatch):
        """Without any setting override, the container wires a memory
        event log — preserving existing default behavior."""
        from bootstrap import build_app_container

        container = build_app_container()
        event_log_repo = container["event_log_repo"]
        assert isinstance(event_log_repo, MemoryEventLogRepository)

    def test_bootstrap_sqlite_setting_wires_sqlite_backend(self, monkeypatch):
        """Setting ``EVENT_LOG_BACKEND=sqlite`` in the global ``settings``
        causes the container's event_log_repo to be SQLite-backed."""
        # Patch the bootstrap module's imported ``settings`` object so
        # build_app_container reads the override. BaseSettings instances
        # allow attribute assignment.
        import bootstrap
        monkeypatch.setattr(bootstrap.settings, "EVENT_LOG_BACKEND", "sqlite")
        monkeypatch.setattr(bootstrap.settings, "EVENT_LOG_DB_PATH", ":memory:")

        container = bootstrap.build_app_container()
        event_log_repo = container["event_log_repo"]
        try:
            assert isinstance(event_log_repo, SqliteEventLogRepository)
        finally:
            # Close the SQLite connection the bootstrap opened so the
            # process teardown is clean.
            event_log_repo.close()


# ────────────── Phase 1a engine-split: view store selection ───────────────


class TestRunContextViewStoreSelection:
    """Phase 3b.3 collapsed the tri-state ``PROJECTOR_READ_MODE`` into
    a single wiring: the container always exposes
    ``CachedProjectorRunContextViewStore``. The former ``live`` /
    ``shadow`` backing stores and the parity comparator are deleted.
    """

    def test_bootstrap_wires_cached_projector_store(self):
        """Production path: ``run_context_view_store`` is always a
        ``CachedProjectorRunContextViewStore`` with both fallback repos
        wired (``workflow_repo`` for topology / entry_nodes,
        ``run_repo`` for get_by_root_id)."""
        from bootstrap import build_app_container
        from app.layer4_frameworks.run_context.cached_projector_view_store import (
            CachedProjectorRunContextViewStore,
        )

        container = build_app_container()
        assert "run_context_view_store" in container
        store = container["run_context_view_store"]
        assert isinstance(store, CachedProjectorRunContextViewStore), (
            f"expected CachedProjectorRunContextViewStore; got "
            f"{type(store).__name__}"
        )
        assert store._workflow_repo is not None
        assert store._run_repo is not None

    def test_projector_drift_recorder_no_longer_exposed(self):
        """Phase 3b.3 deleted the comparator + drift recorder; the
        ``projector_drift_recorder`` container key is gone."""
        from bootstrap import build_app_container
        container = build_app_container()
        assert "projector_drift_recorder" not in container
