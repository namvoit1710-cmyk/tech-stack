"""B2 (SA-2027) — single-store task state machine + single-row CAS.

Proves RT#1: lease/renew/complete/sweep are CAS on ONE row — no double-lease, no
lost/dup completion, correct supersession, partition isolation.
"""

import sys
import os
import threading
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.interfaces.ready_task_store_interface import (
    TaskRow, TaskState, COMPLETE_OK, COMPLETE_SUPERSEDED, COMPLETE_UNKNOWN,
    ENQUEUE_OK, ENQUEUE_DUPLICATE, ENQUEUE_ADMISSION_FULL,
)
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore


class Clock:
    """Monkeypatchable clock for deterministic lease-expiry tests."""
    def __init__(self, base=None):
        self.t = base or datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    def now(self):
        return self.t
    def advance(self, seconds):
        self.t = self.t + timedelta(seconds=seconds)


def _store_with_clock():
    store = MemoryReadyTaskStore()
    clock = Clock()
    store._now = clock.now  # type: ignore[method-assign]
    return store, clock


def _row(task_id, worker_type="pw1", tenant="", **kw):
    return TaskRow(task_id=task_id, run_id=kw.get("run_id", "r1"),
                   node_id=kw.get("node_id", "n1"), worker_type=worker_type,
                   tenant=tenant, payload=kw.get("payload", {}),
                   idempotency_key=kw.get("idempotency_key", ""))


# --- enqueue / state ---

def test_enqueue_lands_ready():
    store, _ = _store_with_clock()
    assert store.enqueue(_row("t1")) == ENQUEUE_OK
    r = store.get("t1")
    assert r.state == TaskState.READY
    assert r.lease_token is None
    assert r.enqueued_at and r.visible_at == r.enqueued_at

def test_enqueue_dedup():
    store, _ = _store_with_clock()
    assert store.enqueue(_row("t1")) == ENQUEUE_OK
    assert store.enqueue(_row("t1")) == ENQUEUE_DUPLICATE
    assert store.count() == 1


# --- claim (READY -> LEASED) ---

def test_claim_moves_ready_to_leased_once():
    store, _ = _store_with_clock()
    store.enqueue(_row("t1"))
    leased = store.claim("pw1", "", max_n=5, lease_ttl_seconds=30)
    assert len(leased) == 1
    assert leased[0].task_id == "t1"
    assert leased[0].lease_token  # fresh token handed out
    assert leased[0].deadline
    assert store.get("t1").state == TaskState.LEASED
    # A second claim finds nothing leasable.
    assert store.claim("pw1", "", max_n=5, lease_ttl_seconds=30) == []

def test_claim_partition_isolation():
    store, _ = _store_with_clock()
    store.enqueue(_row("a", worker_type="pw1", tenant="t-A"))
    store.enqueue(_row("b", worker_type="pw1", tenant="t-B"))
    store.enqueue(_row("c", worker_type="pw2", tenant="t-A"))
    got = store.claim("pw1", "t-A", max_n=10, lease_ttl_seconds=30)
    assert [r.task_id for r in got] == ["a"]           # only (pw1,t-A)

def test_claim_fifo_and_max_n():
    store, clock = _store_with_clock()
    for tid in ["t1", "t2", "t3"]:
        store.enqueue(_row(tid))
        clock.advance(1)   # distinct enqueued_at → deterministic FIFO
    got = store.claim("pw1", "", max_n=2, lease_ttl_seconds=30)
    assert [r.task_id for r in got] == ["t1", "t2"]

def test_claim_respects_visible_at():
    store, clock = _store_with_clock()
    r = _row("t1")
    r.enqueued_at = clock.now().isoformat()
    r.visible_at = (clock.now() + timedelta(seconds=60)).isoformat()  # not yet visible
    store.enqueue(r)
    assert store.claim("pw1", "", max_n=5, lease_ttl_seconds=30) == []
    clock.advance(61)
    assert len(store.claim("pw1", "", max_n=5, lease_ttl_seconds=30)) == 1

def test_lease_generation_monotonic():
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    store.claim("pw1", "", 1, lease_ttl_seconds=1)
    assert store.get("t1").lease_generation == 1
    clock.advance(5)
    store.sweep_expired_leases()   # re-enqueue now BUMPS lease_generation → 2 (ordering token)
    assert store.get("t1").lease_generation == 2
    store.claim("pw1", "", 1, lease_ttl_seconds=1)
    assert store.get("t1").lease_generation == 3   # strictly increasing across the lease lifecycle


# --- renew ---

def test_renew_requires_matching_lease():
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    tok = store.claim("pw1", "", 1, lease_ttl_seconds=30)[0].lease_token
    assert store.renew("t1", tok, 60) is not None          # returns the new deadline
    assert store.renew("t1", "wrong-token", 60) is None     # SUPERSEDED
    assert store.renew("missing", tok, 60) is None

def test_renew_returns_and_extends_deadline():
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    tok = store.claim("pw1", "", 1, lease_ttl_seconds=30)[0].lease_token
    before = store.get("t1").deadline
    clock.advance(10)
    new_deadline = store.renew("t1", tok, 30)
    assert new_deadline == store.get("t1").deadline        # returned value IS the stored deadline
    assert new_deadline > before


# --- hard total-leased ceiling (B4) ---

def test_claim_respects_total_leased_ceiling():
    store, _ = _store_with_clock()
    for i in range(5):
        store.enqueue(_row(f"t{i}"))
    first = store.claim("pw1", "", max_n=10, lease_ttl_seconds=30, max_total_leased=2)
    assert len(first) == 2                    # capped at the global ceiling
    assert store.count_leased() == 2
    assert store.claim("pw1", "", max_n=10, lease_ttl_seconds=30, max_total_leased=2) == []  # at ceiling
    # completing one frees headroom for one more
    store.complete(first[0].task_id, first[0].lease_token)
    assert len(store.claim("pw1", "", max_n=10, lease_ttl_seconds=30, max_total_leased=2)) == 1

def test_ceiling_zero_is_unlimited():
    store, _ = _store_with_clock()
    for i in range(3):
        store.enqueue(_row(f"t{i}"))
    # A configured 0 must NOT brick leasing — it means unlimited.
    got = store.claim("pw1", "", max_n=10, lease_ttl_seconds=30, max_total_leased=0)
    assert len(got) == 3

def test_ceiling_excludes_expired_leases():
    """An expired-but-unswept LEASED row is reclaimable, so it must NOT count
    against the ceiling (else dead leases wedge all leasing before the B8 sweeper)."""
    store, clock = _store_with_clock()
    store.enqueue(_row("dead"))
    store.enqueue(_row("fresh"))
    store.claim("pw1", "", max_n=1, lease_ttl_seconds=10)   # 'dead' leased
    clock.advance(11)                                        # its lease expired (unswept)
    # ceiling=1: the expired lease must not block a new claim of 'fresh'
    got = store.claim("pw1", "", max_n=1, lease_ttl_seconds=10, max_total_leased=1)
    assert [r.task_id for r in got] == ["fresh"]


# --- complete (CAS, at-most-once) ---

def test_complete_ok_then_unknown():
    store, _ = _store_with_clock()
    store.enqueue(_row("t1"))
    tok = store.claim("pw1", "", 1, lease_ttl_seconds=30)[0].lease_token
    assert store.complete("t1", tok) == COMPLETE_OK
    assert store.get("t1") is None                 # row gone (terminal)
    assert store.complete("t1", tok) == COMPLETE_UNKNOWN  # second callback rejected

def test_complete_wrong_token_superseded():
    store, _ = _store_with_clock()
    store.enqueue(_row("t1"))
    store.claim("pw1", "", 1, lease_ttl_seconds=30)
    assert store.complete("t1", "forged") == COMPLETE_SUPERSEDED
    assert store.get("t1").state == TaskState.LEASED  # untouched

def test_complete_ready_task_superseded():
    """A callback for a task that was never leased (still READY) is rejected."""
    store, _ = _store_with_clock()
    store.enqueue(_row("t1"))
    assert store.complete("t1", "anything") == COMPLETE_SUPERSEDED


# --- sweep ---

def test_sweep_expired_lease_reenqueues():
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    store.claim("pw1", "", 1, lease_ttl_seconds=10)
    clock.advance(11)  # lease expired
    swept = store.sweep_expired_leases().re_enqueued
    assert [r.task_id for r in swept] == ["t1"]
    r = store.get("t1")
    assert r.state == TaskState.READY
    assert r.lease_token is None
    assert r.attempt_count == 1
    # and it is claimable again
    assert len(store.claim("pw1", "", 1, lease_ttl_seconds=10)) == 1

def test_sweep_expired_then_immediate_resweep_is_noop():
    """After re-enqueue (deadline cleared, state READY), a second sweep_expired
    must NOT re-sweep the now-READY row (double-guarded: state!=LEASED + empty
    deadline)."""
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    store.claim("pw1", "", 1, lease_ttl_seconds=10)
    clock.advance(11)
    assert len(store.sweep_expired_leases().re_enqueued) == 1
    assert store.sweep_expired_leases().re_enqueued == []   # nothing to re-sweep
    assert store.get("t1").attempt_count == 1          # not double-incremented


def test_sweep_does_not_touch_live_lease():
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    store.claim("pw1", "", 1, lease_ttl_seconds=60)
    clock.advance(10)  # still within TTL
    assert store.sweep_expired_leases().re_enqueued == []
    assert store.get("t1").state == TaskState.LEASED

def test_sweep_stale_ready_removes():
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    clock.advance(3600)
    stale = store.sweep_stale_ready(max_ready_seconds=600)
    assert [r.task_id for r in stale] == ["t1"]
    assert store.get("t1") is None   # terminal, left the store

def test_sweep_stale_ready_keeps_fresh():
    store, clock = _store_with_clock()
    store.enqueue(_row("t1"))
    clock.advance(60)
    assert store.sweep_stale_ready(max_ready_seconds=600) == []
    assert store.get("t1") is not None


# --- snapshot isolation (red-team B2 MEDIUM fix) ---

def test_snapshots_do_not_alias_stored_payload():
    """claim/get return isolated copies: mutating a snapshot's payload/node_config
    must NOT reach the stored row or another snapshot."""
    store, _ = _store_with_clock()
    store.enqueue(_row("t1", payload={"k": "v"}))
    leased = store.claim("pw1", "", 1, lease_ttl_seconds=30)[0]
    leased.payload["_leased_by"] = "wid-1"     # consumer mutates its snapshot
    leased.node_config["x"] = 1
    fresh = store.get("t1")
    assert "_leased_by" not in fresh.payload   # stored row untouched
    assert fresh.node_config == {}

def test_enqueue_does_not_alias_caller_row():
    """The store must not hold the caller's object: mutating the caller's payload
    after enqueue must not change stored state."""
    store, _ = _store_with_clock()
    row = _row("t1", payload={"k": "v"})
    store.enqueue(row)
    row.payload["mutated"] = True              # caller mutates after enqueue
    assert "mutated" not in store.get("t1").payload


# --- concurrency: no double-lease ---

def test_concurrent_claim_no_double_lease():
    store = MemoryReadyTaskStore()   # real clock; only correctness under threads matters
    N = 200
    for i in range(N):
        store.enqueue(_row(f"t{i:04d}"))

    leased_ids: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        start.wait()
        spins = 0
        while spins < 100_000:            # bounded: never livelock-hang the suite
            got = store.claim("pw1", "", max_n=1, lease_ttl_seconds=30)
            if got:
                with lock:
                    leased_ids.extend(r.task_id for r in got)
                continue
            # Empty claim: rows are never re-enqueued in this test, so once every
            # task has been leased, all future claims stay empty → stop. An empty
            # claim before that is transient contention (another thread mid-claim).
            with lock:
                if len(leased_ids) == N:
                    return
            spins += 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)

    assert len(leased_ids) == N                    # every task leased
    assert len(set(leased_ids)) == N               # each exactly once (no double-lease)
