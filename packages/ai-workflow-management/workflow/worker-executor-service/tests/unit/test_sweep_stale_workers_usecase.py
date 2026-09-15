"""SA-2051 (R4) — SweepStaleWorkersUseCase: runs the store sweep and emits
catalog.changed(deregistered) once per worker_type driven to 0 healthy."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.sweep_stale_workers.use_cases.sweep_stale_workers_usecase import (
    SweepStaleWorkersUseCase,
)


class _DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class _FakeBroker:
    def __init__(self):
        self.published = []

    def publish_to_topic(self, topic, message, key=None):
        self.published.append((topic, message))


class _SweepResultStub:
    def __init__(self, marked_count=0, removed_count=0, emptied=None):
        self.marked_count = marked_count
        self.removed_count = removed_count
        self.emptied = emptied or []


class _FakeStore:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def sweep_stale(self, stale_after_seconds, remove_after_seconds):
        self.calls.append((stale_after_seconds, remove_after_seconds))
        return self._result


class _FakeConfig:
    WORKER_STALE_AFTER_SECONDS = 90
    WORKER_REMOVE_AFTER_SECONDS = 300


def test_emit_on_emptied():
    store = _FakeStore(_SweepResultStub(marked_count=1, emptied=[("http-worker", "2.0.0")]))
    broker = _FakeBroker()
    uc = SweepStaleWorkersUseCase(
        logger=_DummyLogger(), registry_store=store,
        message_broker_client=broker, app_config=_FakeConfig(),
    )

    out = uc.execute()

    assert len(broker.published) == 1
    topic, message = broker.published[0]
    assert topic == "catalog.changed"
    assert message["action"] == "deregistered"
    assert message["worker_type"] == "http-worker"
    assert message["spec_version"] == "2.0.0"
    assert message["tenant_id"] == "system"
    assert out == {"marked": 1, "removed": 0, "emptied": 1}


def test_no_emit_when_nothing_emptied():
    store = _FakeStore(_SweepResultStub(marked_count=2, removed_count=1, emptied=[]))
    broker = _FakeBroker()
    uc = SweepStaleWorkersUseCase(
        logger=_DummyLogger(), registry_store=store,
        message_broker_client=broker, app_config=_FakeConfig(),
    )

    out = uc.execute()

    assert broker.published == []
    assert out == {"marked": 2, "removed": 1, "emptied": 0}


def test_no_store_is_a_noop():
    uc = SweepStaleWorkersUseCase(
        logger=_DummyLogger(), registry_store=None,
        message_broker_client=_FakeBroker(), app_config=_FakeConfig(),
    )
    assert uc.execute() == {"marked": 0, "removed": 0, "emptied": 0}


def test_no_broker_does_not_crash_on_emptied():
    store = _FakeStore(_SweepResultStub(marked_count=1, emptied=[("http-worker", "1.0.0")]))
    uc = SweepStaleWorkersUseCase(
        logger=_DummyLogger(), registry_store=store,
        message_broker_client=None, app_config=_FakeConfig(),
    )
    out = uc.execute()
    assert out == {"marked": 1, "removed": 0, "emptied": 1}


def test_reads_thresholds_from_app_config():
    store = _FakeStore(_SweepResultStub())
    uc = SweepStaleWorkersUseCase(
        logger=_DummyLogger(), registry_store=store,
        message_broker_client=_FakeBroker(), app_config=_FakeConfig(),
    )
    uc.execute()
    assert store.calls == [(90, 300)]


class TestReplicaGaugeRefreshWiring:
    def _store(self, rows):
        class _Result:
            marked_count = 0
            removed_count = 0
            emptied = []
        class _Store:
            def sweep_stale(self, s, r):
                return _Result()
            def list_all(self):
                return rows
        return _Store()

    class _Cfg:
        WORKER_STALE_AFTER_SECONDS = 120
        WORKER_REMOVE_AFTER_SECONDS = 600

    def test_monitor_refresh_called_with_snapshot(self):
        import logging
        from app.layer2_application.features.sweep_stale_workers.use_cases.sweep_stale_workers_usecase import (
            SweepStaleWorkersUseCase,
        )
        calls = []
        class _Mon:
            def refresh_replica_gauges(self, snapshot):
                calls.append(snapshot)
        uc = SweepStaleWorkersUseCase(
            logger=logging.getLogger("t"), registry_store=self._store([]),
            app_config=self._Cfg(), monitor=_Mon(),
        )
        uc.execute()
        assert len(calls) == 1
        assert "counts" in calls[0] and "age" in calls[0]

    def test_no_monitor_is_noop(self):
        import logging
        from app.layer2_application.features.sweep_stale_workers.use_cases.sweep_stale_workers_usecase import (
            SweepStaleWorkersUseCase,
        )
        uc = SweepStaleWorkersUseCase(
            logger=logging.getLogger("t"), registry_store=self._store([]),
            app_config=self._Cfg(), monitor=None,
        )
        # must not raise
        assert uc.execute()["marked"] == 0

    def test_refresh_exception_is_swallowed(self):
        import logging
        from app.layer2_application.features.sweep_stale_workers.use_cases.sweep_stale_workers_usecase import (
            SweepStaleWorkersUseCase,
        )
        class _BadMon:
            def refresh_replica_gauges(self, snapshot):
                raise RuntimeError("boom")
        uc = SweepStaleWorkersUseCase(
            logger=logging.getLogger("t"), registry_store=self._store([]),
            app_config=self._Cfg(), monitor=_BadMon(),
        )
        # the sweep must survive a metrics failure
        assert uc.execute()["marked"] == 0
