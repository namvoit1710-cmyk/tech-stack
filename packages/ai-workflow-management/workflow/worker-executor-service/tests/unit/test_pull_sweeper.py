"""B8 (SA-2033) — pull sweeper forks: re-enqueue + backoff, poison/DLQ, WORKER_UNAVAILABLE,
restart-safe leased-set marker."""

import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.interfaces.ready_task_store_interface import TaskRow, TaskState
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore
from app.layer2_application.features.sweep_pull_tasks.use_cases.sweep_pull_tasks_usecase import (
    SweepPullTasksUseCase,
)
from app.layer2_application.services import pull_lifecycle


class _Log:
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def debug(self, *a, **k): pass

class _Pub:
    def __init__(self): self.msgs = []
    def publish_result(self, m): self.msgs.append(m)

def _types(pub, t): return [m for m in pub.msgs if m["message_type"] == t]

def _codes(pub): return [m["payload"].get("error", {}).get("code") for m in pub.msgs]

def _clocked():
    store = MemoryReadyTaskStore()
    clock = {"t": datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)}
    store._now = lambda: clock["t"]
    return store, clock

def _row(tid, **kw):
    return TaskRow(task_id=tid, run_id=kw.pop("run_id", "r1"), node_id="n1",
                   worker_type=kw.pop("worker_type", "pw1"), tenant=kw.pop("tenant", "t-A"), **kw)


# ======================================================================
# store: sweep_expired backoff + poison
# ======================================================================

def test_sweep_expired_reenqueues_with_backoff():
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=10)
    clock["t"] += timedelta(seconds=11)                 # lease expired
    res = store.sweep_expired_leases(max_attempts=5, backoff_base_seconds=2)
    assert [r.task_id for r in res.re_enqueued] == ["t1"]
    assert res.poisoned == []
    row = store.get("t1")
    assert row.state == TaskState.READY and row.attempt_count == 1
    # backoff: visible_at = now + 2 * 2^(1-1) = now + 2s → NOT immediately visible
    assert row.visible_at == (clock["t"] + timedelta(seconds=2)).isoformat()
    assert store.claim("pw1", "t-A", 1, lease_ttl_seconds=10) == []   # not yet visible
    clock["t"] += timedelta(seconds=3)
    assert len(store.claim("pw1", "t-A", 1, lease_ttl_seconds=10)) == 1  # now visible

def test_sweep_expired_poisons_after_max_attempts():
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    poisoned_seen = None
    for i in range(10):
        store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)
        clock["t"] += timedelta(seconds=2)
        # make it visible again immediately for the test by using backoff 0
        res = store.sweep_expired_leases(max_attempts=3, backoff_base_seconds=0)
        if res.poisoned:
            poisoned_seen = res.poisoned
            break
    assert poisoned_seen is not None
    assert poisoned_seen[0].task_id == "t1"
    assert store.get("t1") is None                       # poison removed the row
    assert store.get("t1") is None

def test_sweep_expired_off_by_default():
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)
    clock["t"] += timedelta(seconds=2)
    res = store.sweep_expired_leases()                   # no max/backoff → re-enqueue, no poison
    assert len(res.re_enqueued) == 1 and res.poisoned == []


# ======================================================================
# restart-safe leased-set marker
# ======================================================================

def test_leased_set_marker_tracks_lease():
    store, _ = _clocked()
    store.enqueue(_row("t1", run_generation="g1"))
    assert store.leased_set_snapshot() == {}
    tok = store.claim("pw1", "t-A", 1, 30)[0].lease_token
    assert store.leased_set_snapshot() == {"t1": 1}      # marked on claim (gen 1)
    store.complete("t1", tok, tenant="t-A", worker_type="pw1")
    assert store.leased_set_snapshot() == {}             # unmarked on complete

def test_leased_marker_cleared_on_reenqueue():
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)
    assert store.leased_set_snapshot() == {"t1": 1}
    clock["t"] += timedelta(seconds=2)
    store.sweep_expired_leases(max_attempts=5, backoff_base_seconds=0)
    assert store.leased_set_snapshot() == {}             # re-enqueued → no longer leased

def test_bytes_and_marker_freed_on_poison():
    store, clock = _clocked()
    store.enqueue(_row("t1", payload={"d": "x" * 100}))
    assert store._bytes_total > 0
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)
    assert store.leased_set_snapshot() == {"t1": 1}
    clock["t"] += timedelta(seconds=2)
    res = store.sweep_expired_leases(max_attempts=1, backoff_base_seconds=0)   # poison on 1st expiry
    assert [r.task_id for r in res.poisoned] == ["t1"]
    assert store.get("t1") is None
    assert store._bytes_total == 0                        # bytes freed on poison
    assert store.leased_set_snapshot() == {}             # marker cleared on poison

def test_max_attempts_one_poisons_immediately():
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)
    clock["t"] += timedelta(seconds=2)
    res = store.sweep_expired_leases(max_attempts=1, backoff_base_seconds=0)
    assert [r.task_id for r in res.poisoned] == ["t1"]   # 0 retries → poison on first expiry
    assert res.re_enqueued == []

def test_purge_clears_marker():
    store, _ = _clocked()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, 30)
    assert store.leased_set_snapshot() == {"t1": 1}
    store.purge_by_run("r1")
    assert store.leased_set_snapshot() == {}


# ======================================================================
# HIGH regression: re-enqueued task must NOT be false-killed as WORKER_UNAVAILABLE
# ======================================================================

def test_reenqueued_task_not_falsely_worker_unavailable():
    """A task leased+held past max-ready, then its lease expires → RE-ENQUEUED, NOT
    reaped as WORKER_UNAVAILABLE (its old enqueued_at must not trip stale-ready)."""
    store, clock = _clocked()
    pub = _Pub()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=10)
    clock["t"] += timedelta(seconds=2000)                # way past max-ready (600)
    uc = SweepPullTasksUseCase(logger=_Log(), ready_task_store=store, result_publisher=pub,
                               app_config=_cfg(MAX_LEASE_ATTEMPTS=5, LEASE_MAX_READY_SECONDS=600))
    r = uc.execute()
    assert r["re_enqueued"] == 1 and r["unavailable"] == 0
    assert len(_types(pub, "task.lease_expired")) == 1
    assert _types(pub, "task.failed") == []              # NO WORKER_UNAVAILABLE
    assert store.get("t1").state == TaskState.READY      # still alive, retrying

def test_lifecycle_events_carry_monotonic_lease_generation():
    """PR-review HIGH: every pull event carries lease_generation, strictly increasing
    across enqueued → leased → lease_expired → re-leased, so WCP can resequence under
    Event-Mesh reordering."""
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    # enqueued: gen 0 (never leased)
    assert pull_lifecycle.enqueued_event(store.get("t1"))["payload"]["lease_generation"] == 0
    leased = store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)[0]
    assert pull_lifecycle.leased_event(leased)["payload"]["lease_generation"] == 1
    clock["t"] += timedelta(seconds=2)
    reenq = store.sweep_expired_leases(max_attempts=5, backoff_base_seconds=0).re_enqueued[0]
    # lease_expired must order AFTER the leased(1) it expires
    assert pull_lifecycle.lease_expired_event(reenq)["payload"]["lease_generation"] == 2
    released = store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)[0]
    assert pull_lifecycle.leased_event(released)["payload"]["lease_generation"] == 3   # strictly increasing

def test_backed_off_row_not_stale_reaped():
    """PR-review MEDIUM: a row in inter-attempt backoff (future visible_at) must NOT be
    reaped as WORKER_UNAVAILABLE even if its backoff exceeds max-ready."""
    store, clock = _clocked()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)
    clock["t"] += timedelta(seconds=2)
    # re-enqueue with a backoff (1000s) LONGER than max-ready (600s)
    store.sweep_expired_leases(max_attempts=5, backoff_base_seconds=1000)
    row = store.get("t1")
    assert row.state == TaskState.READY and row.visible_at > store._now().isoformat()  # backed off
    clock["t"] += timedelta(seconds=700)          # past max-ready but still within backoff
    assert store.sweep_stale_ready(max_ready_seconds=600) == []   # NOT reaped mid-backoff
    assert store.get("t1") is not None

def test_reenqueued_then_abandoned_is_eventually_unavailable():
    """But a re-enqueued task no worker re-leases within max-ready (from the
    re-enqueue) IS terminated — the clock just restarts, it isn't disabled."""
    store, clock = _clocked()
    pub = _Pub()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=10)
    clock["t"] += timedelta(seconds=20)
    uc = SweepPullTasksUseCase(logger=_Log(), ready_task_store=store, result_publisher=pub,
                               app_config=_cfg(MAX_LEASE_ATTEMPTS=5, LEASE_MAX_READY_SECONDS=600))
    uc.execute()                                          # re-enqueued (enqueued_at reset)
    assert store.get("t1").state == TaskState.READY
    clock["t"] += timedelta(seconds=700)                 # abandoned > max-ready from re-enqueue
    r = uc.execute()
    assert r["unavailable"] == 1
    assert pull_lifecycle.WORKER_UNAVAILABLE in _codes(pub)


# ======================================================================
# SweepPullTasksUseCase — emits the lifecycle contract
# ======================================================================

def _cfg(**kw):
    from types import SimpleNamespace
    d = dict(MAX_LEASE_ATTEMPTS=3, LEASE_BACKOFF_BASE_SECONDS=0, LEASE_MAX_READY_SECONDS=600)
    d.update(kw)
    return SimpleNamespace(**d)

def test_sweep_usecase_emits_lease_expired():
    store, clock = _clocked()
    pub = _Pub()
    store.enqueue(_row("t1"))
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1)
    clock["t"] += timedelta(seconds=2)
    uc = SweepPullTasksUseCase(logger=_Log(), ready_task_store=store, result_publisher=pub, app_config=_cfg())
    r = uc.execute()
    assert r["re_enqueued"] == 1
    assert len(_types(pub, "task.lease_expired")) == 1

def test_sweep_usecase_emits_poison_and_dlq():
    store, clock = _clocked()
    pub = _Pub()
    store.enqueue(_row("t1"))
    uc = SweepPullTasksUseCase(logger=_Log(), ready_task_store=store, result_publisher=pub, app_config=_cfg(MAX_LEASE_ATTEMPTS=2))
    # attempt 1: claim+expire → re-enqueue (attempt 1)
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1); clock["t"] += timedelta(seconds=2); uc.execute()
    # attempt 2: claim+expire → next_attempt=2 == max → poison
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1); clock["t"] += timedelta(seconds=2)
    r = uc.execute()
    assert r["poisoned"] == 1
    assert pull_lifecycle.TASK_FAILED_POISON in _codes(pub)   # terminal
    assert len(_types(pub, "task.dlq")) == 1                  # DLQ/alert

def test_sweep_usecase_emits_worker_unavailable():
    store, clock = _clocked()
    pub = _Pub()
    store.enqueue(_row("t1"))                             # never leased
    clock["t"] += timedelta(seconds=3600)                # ages past max-ready
    uc = SweepPullTasksUseCase(logger=_Log(), ready_task_store=store, result_publisher=pub,
                               app_config=_cfg(LEASE_MAX_READY_SECONDS=600))
    r = uc.execute()
    assert r["unavailable"] == 1
    assert pull_lifecycle.WORKER_UNAVAILABLE in _codes(pub)
    assert store.get("t1") is None                       # terminal, removed

def test_sweep_usecase_disabled_when_no_store():
    uc = SweepPullTasksUseCase(logger=_Log(), ready_task_store=None, result_publisher=_Pub(), app_config=_cfg())
    assert uc.execute() == {"re_enqueued": 0, "poisoned": 0, "unavailable": 0}


class _Rep:
    def __init__(self): self.failed = []
    def report_failed(self, **kw): self.failed.append(kw)

def test_terminals_also_reach_reporter_path():
    """Poison + WORKER_UNAVAILABLE must reach WCP on the authoritative sync reporter
    (HTTP/gRPC mode), not only the event-mesh publish — with the fence."""
    store, clock = _clocked()
    rep = _Rep()
    # separate partitions so the claims are unambiguous: poison1 in pw1, stale1 in pw2
    store.enqueue(_row("poison1", worker_type="pw1", run_generation="g1", idempotency_key="idemP"))
    store.enqueue(_row("stale1", worker_type="pw2", run_generation="g2"))
    uc = SweepPullTasksUseCase(logger=_Log(), ready_task_store=store, result_publisher=_Pub(),
                               result_reporter=rep, app_config=_cfg(MAX_LEASE_ATTEMPTS=2, LEASE_MAX_READY_SECONDS=600))
    # drive poison1 (pw1) through 2 lease-expiry cycles → poison
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1); clock["t"] += timedelta(seconds=2); uc.execute()
    store.claim("pw1", "t-A", 1, lease_ttl_seconds=1); clock["t"] += timedelta(seconds=2)
    clock["t"] += timedelta(seconds=3600)   # stale1 (never leased) ages past max-ready
    uc.execute()
    codes = {f["error"]["code"] for f in rep.failed}
    assert "TASK_FAILED_POISON" in codes
    assert "WORKER_UNAVAILABLE" in codes
    poison = [f for f in rep.failed if f["error"]["code"] == "TASK_FAILED_POISON"][0]
    assert poison["run_generation"] == "g1" and poison["idempotency_key"] == "idemP"  # fence carried
