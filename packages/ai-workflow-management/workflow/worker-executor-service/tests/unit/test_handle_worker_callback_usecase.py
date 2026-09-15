import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.interfaces.pending_task_store_interface import PendingTaskEntry
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore
from app.layer2_application.features.handle_worker_callback.use_cases.handle_worker_callback_usecase import (
    HandleWorkerCallbackUseCase,
    HandleWorkerCallbackInput,
)
from app.layer2_application.services.concurrency_control import ConcurrencyControl


class DummyLogger:
    def __init__(self):
        self.events = []

    def info(self, message, *a, **kw):
        self.events.append(("info", message, kw))

    def error(self, message, *a, **kw):
        self.events.append(("error", message, kw))

    def warning(self, message, *a, **kw):
        self.events.append(("warning", message, kw))

    def debug(self, message, *a, **kw):
        self.events.append(("debug", message, kw))


class DummyResultReporter:
    def __init__(self, *, fail_on_completed=False, fail_on_failed=False):
        self.completed_calls = []
        self.failed_calls = []
        self._fail_on_completed = fail_on_completed
        self._fail_on_failed = fail_on_failed

    def report_completed(self, **kwargs):
        self.completed_calls.append(kwargs)
        if self._fail_on_completed:
            raise RuntimeError("report_completed failed")

    def report_failed(self, **kwargs):
        self.failed_calls.append(kwargs)
        if self._fail_on_failed:
            raise RuntimeError("report_failed failed")


class DummyResultPublisher:
    def __init__(self):
        self.published = []

    def publish_result(self, result):
        self.published.append(result)


def _make_entry(task_id="t1"):
    now = datetime.now(timezone.utc)
    return PendingTaskEntry(
        task_id=task_id,
        run_id="r1",
        node_id="n1",
        worker_type="w1",
        dispatched_at=now.isoformat(),
        timeout_at=(now + timedelta(seconds=60)).isoformat(),
    )


def _make_uc(store=None, **overrides):
    if store is None:
        store = MemoryPendingTaskStore()
    defaults = dict(
        logger=DummyLogger(),
        pending_task_store=store,
    )
    defaults.update(overrides)
    return HandleWorkerCallbackUseCase(**defaults)


# --- Happy path: success callback ---

def test_callback_success_known_task():
    store = MemoryPendingTaskStore()
    store.store(_make_entry("t1"))
    reporter = DummyResultReporter()
    publisher = DummyResultPublisher()
    cc = ConcurrencyControl()
    cc.acquire()  # simulate acquired slot

    uc = _make_uc(
        store=store,
        result_reporter=reporter,
        result_publisher=publisher,
        concurrency_control=cc,
    )
    result = uc.execute(HandleWorkerCallbackInput(
        task_id="t1",
        status="success",
        outputs={"uri": "s3://out", "output_metadata": {"rows": 5}},
        duration_ms=200.0,
    ))

    assert result["accepted"] is True
    assert store.get("t1") is None
    assert cc.active_count == 0
    assert len(reporter.completed_calls) == 1
    assert reporter.completed_calls[0]["output_reference"] == "s3://out"
    assert len(publisher.published) == 1
    assert publisher.published[0]["message_type"] == "task.completed"


# --- Unknown task ---

def test_callback_unknown_task():
    uc = _make_uc()
    result = uc.execute(HandleWorkerCallbackInput(task_id="unknown", status="success"))
    assert result["accepted"] is False
    assert "Unknown" in result["message"]


# --- Error status ---

def test_callback_error_status():
    store = MemoryPendingTaskStore()
    store.store(_make_entry("t1"))
    reporter = DummyResultReporter()
    publisher = DummyResultPublisher()

    uc = _make_uc(
        store=store,
        result_reporter=reporter,
        result_publisher=publisher,
    )
    result = uc.execute(HandleWorkerCallbackInput(
        task_id="t1",
        status="error",
        error="Something broke",
    ))

    assert result["accepted"] is True
    assert len(reporter.failed_calls) == 1
    assert reporter.failed_calls[0]["error"]["code"] == "WORKER_ERROR"
    assert len(publisher.published) == 1
    assert publisher.published[0]["message_type"] == "task.failed"


# --- Duplicate callback ---

def test_duplicate_callback_rejected():
    store = MemoryPendingTaskStore()
    store.store(_make_entry("t1"))

    uc = _make_uc(store=store)
    first = uc.execute(HandleWorkerCallbackInput(task_id="t1", status="success"))
    second = uc.execute(HandleWorkerCallbackInput(task_id="t1", status="success"))

    assert first["accepted"] is True
    assert second["accepted"] is False


# --- Reporter failure does not break ---

def test_reporter_failure_does_not_break():
    store = MemoryPendingTaskStore()
    store.store(_make_entry("t1"))
    reporter = DummyResultReporter(fail_on_completed=True)
    logger = DummyLogger()

    uc = _make_uc(store=store, logger=logger, result_reporter=reporter)
    result = uc.execute(HandleWorkerCallbackInput(task_id="t1", status="success"))

    assert result["accepted"] is True
    error_events = [e for e in logger.events if e[0] == "error"]
    assert any("Failed to report completion to WCP" in e[1] for e in error_events)
