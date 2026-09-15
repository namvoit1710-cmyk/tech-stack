import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.interfaces.pending_task_store_interface import PendingTaskEntry
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore
from app.layer2_application.features.sweep_timed_out_tasks.use_cases.sweep_timed_out_tasks_usecase import SweepTimedOutTasksUseCase
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
    def __init__(self):
        self.failed_calls = []

    def report_completed(self, **kwargs):
        pass

    def report_failed(self, **kwargs):
        self.failed_calls.append(kwargs)


class DummyResultPublisher:
    def __init__(self):
        self.published = []

    def publish_result(self, result):
        self.published.append(result)


def _make_entry(task_id, timeout_offset_seconds=-10):
    now = datetime.now(timezone.utc)
    return PendingTaskEntry(
        task_id=task_id,
        run_id="r1",
        node_id="n1",
        worker_type="w1",
        dispatched_at=now.isoformat(),
        timeout_at=(now + timedelta(seconds=timeout_offset_seconds)).isoformat(),
    )


def _make_uc(store=None, **overrides):
    if store is None:
        store = MemoryPendingTaskStore()
    defaults = dict(
        logger=DummyLogger(),
        pending_task_store=store,
    )
    defaults.update(overrides)
    return SweepTimedOutTasksUseCase(**defaults)


# --- No timed-out tasks ---

def test_sweep_no_timed_out():
    store = MemoryPendingTaskStore()
    now = datetime.now(timezone.utc)
    alive = PendingTaskEntry(
        task_id="alive",
        run_id="r1",
        node_id="n1",
        worker_type="w1",
        dispatched_at=now.isoformat(),
        timeout_at=(now + timedelta(seconds=600)).isoformat(),
    )
    store.store(alive)

    uc = _make_uc(store=store)
    result = uc.execute()
    assert result["swept"] == 0
    assert store.count() == 1


# --- One timed-out ---

def test_sweep_one_timed_out():
    store = MemoryPendingTaskStore()
    store.store(_make_entry("expired"))
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
    result = uc.execute()

    assert result["swept"] == 1
    assert store.get("expired") is None
    assert cc.active_count == 0
    assert len(reporter.failed_calls) == 1
    assert reporter.failed_calls[0]["error"]["code"] == "WORKER_TIMEOUT"
    assert len(publisher.published) == 1
    assert publisher.published[0]["message_type"] == "task.failed"


# --- Multiple timed-out ---

def test_sweep_multiple_timed_out():
    store = MemoryPendingTaskStore()
    store.store(_make_entry("e1"))
    store.store(_make_entry("e2"))
    store.store(_make_entry("e3"))
    reporter = DummyResultReporter()
    cc = ConcurrencyControl()
    cc.acquire()
    cc.acquire()
    cc.acquire()

    uc = _make_uc(
        store=store,
        result_reporter=reporter,
        concurrency_control=cc,
    )
    result = uc.execute()

    assert result["swept"] == 3
    assert store.count() == 0
    assert cc.active_count == 0
    assert len(reporter.failed_calls) == 3


# --- TOCTOU: callback completes the task between get_timed_out() and remove() ---


class _CallbackRacedStore:
    """``get_timed_out()`` reports the entry, but ``remove()`` returns None —
    simulating the callback completing + removing it on another thread in
    between."""

    def __init__(self, entries):
        self._entries = list(entries)

    def get_timed_out(self):
        return list(self._entries)

    def remove(self, task_id):
        return None  # already removed by the callback

    def count(self):
        return 0


def test_sweep_skips_release_when_callback_already_removed():
    """The sweep must NOT release the slot or report when ``remove()`` returns
    None — the callback already released + reported. A second release here would
    double-decrement the concurrency counter; a second report double-fails the
    task."""
    cc = ConcurrencyControl()
    cc.acquire()  # the (conceptual) callback owns this slot
    reporter = DummyResultReporter()
    publisher = DummyResultPublisher()
    store = _CallbackRacedStore([_make_entry("raced")])

    uc = _make_uc(
        store=store,
        result_reporter=reporter,
        result_publisher=publisher,
        concurrency_control=cc,
    )
    result = uc.execute()

    assert result["swept"] == 0          # nothing actually swept by us
    assert cc.active_count == 1          # NOT double-released
    assert reporter.failed_calls == []   # NOT double-reported
    assert publisher.published == []
