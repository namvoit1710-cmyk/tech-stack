import sys
import os
import threading
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.interfaces.pending_task_store_interface import PendingTaskEntry
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore


def _make_entry(task_id="t1", timeout_offset_seconds=60):
    now = datetime.now(timezone.utc)
    return PendingTaskEntry(
        task_id=task_id,
        run_id="r1",
        node_id="n1",
        worker_type="w1",
        dispatched_at=now.isoformat(),
        timeout_at=(now + timedelta(seconds=timeout_offset_seconds)).isoformat(),
    )


# --- store / get / remove ---

def test_store_and_get():
    store = MemoryPendingTaskStore()
    entry = _make_entry("t1")
    store.store(entry)
    assert store.get("t1") is entry
    assert store.get("unknown") is None


def test_remove_returns_entry():
    store = MemoryPendingTaskStore()
    entry = _make_entry("t1")
    store.store(entry)
    removed = store.remove("t1")
    assert removed is entry
    assert store.get("t1") is None


def test_remove_nonexistent_returns_none():
    store = MemoryPendingTaskStore()
    assert store.remove("nope") is None


def test_count():
    store = MemoryPendingTaskStore()
    assert store.count() == 0
    store.store(_make_entry("t1"))
    store.store(_make_entry("t2"))
    assert store.count() == 2
    store.remove("t1")
    assert store.count() == 1


# --- get_timed_out ---

def test_get_timed_out_returns_expired_entries():
    store = MemoryPendingTaskStore()
    expired = _make_entry("expired", timeout_offset_seconds=-10)
    not_expired = _make_entry("alive", timeout_offset_seconds=600)
    store.store(expired)
    store.store(not_expired)

    timed_out = store.get_timed_out()
    assert len(timed_out) == 1
    assert timed_out[0].task_id == "expired"


def test_get_timed_out_empty_store():
    store = MemoryPendingTaskStore()
    assert store.get_timed_out() == []


# --- Thread safety ---

def test_concurrent_store_and_remove():
    store = MemoryPendingTaskStore()
    errors = []

    def worker(start, end):
        try:
            for i in range(start, end):
                store.store(_make_entry(f"t{i}"))
            for i in range(start, end):
                store.remove(f"t{i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i * 100, (i + 1) * 100)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.count() == 0


# --- store_if_absent (dedup claim) ---

def test_store_if_absent_claims_then_rejects_duplicate():
    store = MemoryPendingTaskStore()
    entry = _make_entry("t1")
    assert store.store_if_absent(entry) is True       # newly claimed
    assert store.store_if_absent(_make_entry("t1")) is False  # already in flight
    assert store.get("t1") is entry                   # original kept, not overwritten


def test_store_if_absent_one_winner_under_contention():
    """When N threads claim the same task_id, exactly ONE gets True — the dedup
    guarantee that makes at-least-once redelivery safe under concurrent
    consumers."""
    store = MemoryPendingTaskStore()
    wins: list[bool] = []
    wins_lock = threading.Lock()
    barrier = threading.Barrier(16)

    def claim():
        barrier.wait()  # maximize the race on the same key
        got = store.store_if_absent(_make_entry("same-task"))
        with wins_lock:
            wins.append(got)

    threads = [threading.Thread(target=claim) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert wins.count(True) == 1       # exactly one winner
    assert wins.count(False) == 15
    assert store.count() == 1
