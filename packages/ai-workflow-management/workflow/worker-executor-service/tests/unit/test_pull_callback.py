"""B5 (SA-2030) — pull callback hardening: triple-binding CAS + supersession.

Proves: only the exact (lease_token, tenant, worker_type) matching the STORED row
completes a pull task; a push credential can't complete a pull task and vice versa;
supersession is uniform + opaque; the push callback path is unchanged.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.features.handle_worker_callback.use_cases.handle_worker_callback_usecase import (
    HandleWorkerCallbackUseCase, HandleWorkerCallbackInput,
)
from app.layer2_application.interfaces.ready_task_store_interface import (
    TaskRow, TaskState, COMPLETE_OK, COMPLETE_SUPERSEDED, COMPLETE_UNKNOWN,
)
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore
from app.layer2_application.interfaces.pending_task_store_interface import PendingTaskEntry


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class DummyReporter:
    def __init__(self):
        self.completed = []
        self.failed = []
    def report_completed(self, **kw): self.completed.append(kw)
    def report_failed(self, **kw): self.failed.append(kw)


class DummyPublisher:
    def __init__(self): self.published = []
    def publish_result(self, msg): self.published.append(msg)


def _leased(store, task_id="t1", tenant="t-A", worker_type="pw1"):
    store.enqueue(TaskRow(task_id=task_id, run_id="r1", node_id="n1",
                          worker_type=worker_type, tenant=tenant))
    return store.claim(worker_type, tenant, 1, lease_ttl_seconds=30)[0].lease_token


# ======================================================================
# store.complete triple-binding
# ======================================================================

class TestCompleteTripleBinding:
    def test_all_match_ok(self):
        s = MemoryReadyTaskStore(); tok = _leased(s)
        assert s.complete("t1", tok, tenant="t-A", worker_type="pw1") == COMPLETE_OK
        assert s.get("t1") is None

    def test_wrong_lease_superseded(self):
        s = MemoryReadyTaskStore(); _leased(s)
        assert s.complete("t1", "forged", tenant="t-A", worker_type="pw1") == COMPLETE_SUPERSEDED
        assert s.get("t1").state == TaskState.LEASED    # untouched

    def test_wrong_tenant_superseded(self):
        s = MemoryReadyTaskStore(); tok = _leased(s)
        assert s.complete("t1", tok, tenant="t-EVIL", worker_type="pw1") == COMPLETE_SUPERSEDED
        assert s.get("t1") is not None

    def test_wrong_worker_type_superseded(self):
        s = MemoryReadyTaskStore(); tok = _leased(s)
        assert s.complete("t1", tok, tenant="t-A", worker_type="other") == COMPLETE_SUPERSEDED

    def test_binding_optional_when_not_presented(self):
        """A caller that presents no tenant/type still needs the exact lease token."""
        s = MemoryReadyTaskStore(); tok = _leased(s)
        assert s.complete("t1", tok) == COMPLETE_OK     # lease alone, binding not claimed

    def test_unknown_task(self):
        s = MemoryReadyTaskStore()
        assert s.complete("ghost", "x", tenant="t-A", worker_type="pw1") == COMPLETE_UNKNOWN

    def test_empty_presented_token_rejected(self):
        """An empty token never matches — not even a leased row (self-sufficient,
        not reliant on state-check ordering)."""
        s = MemoryReadyTaskStore(); _leased(s)
        assert s.complete("t1", "", tenant="t-A", worker_type="pw1") == COMPLETE_SUPERSEDED

    def test_ready_row_empty_token_rejected(self):
        """A READY row (lease_token=None) cannot be completed by X-Lease-Token: ''."""
        s = MemoryReadyTaskStore()
        s.enqueue(TaskRow(task_id="t1", run_id="r", node_id="n", worker_type="pw1", tenant="t-A"))
        assert s.complete("t1", "") == COMPLETE_SUPERSEDED
        assert s.complete("t1", "anything") == COMPLETE_SUPERSEDED   # also not LEASED

    def test_concurrent_complete_only_one_wins(self):
        import threading
        s = MemoryReadyTaskStore(); tok = _leased(s)
        results = []
        rlock = threading.Lock()
        barrier = threading.Barrier(4)
        def worker():
            barrier.wait()
            r = s.complete("t1", tok, tenant="t-A", worker_type="pw1")
            with rlock:
                results.append(r)
        ts = [threading.Thread(target=worker) for _ in range(4)]
        for t in ts: t.start()
        for t in ts: t.join()
        assert results.count(COMPLETE_OK) == 1          # exactly one completion
        assert results.count(COMPLETE_UNKNOWN) == 3     # the rest find the row gone


# ======================================================================
# callback use case — pull path
# ======================================================================

def _uc(ready=None, pending=None, reporter=None, publisher=None):
    return HandleWorkerCallbackUseCase(
        logger=DummyLogger(),
        pending_task_store=pending or MemoryPendingTaskStore(),
        result_reporter=reporter,
        result_publisher=publisher,
        ready_task_store=ready,
    )


class TestPullCallback:
    def test_success_completes_and_reports(self):
        ready = MemoryReadyTaskStore(); tok = _leased(ready)
        rep, pub = DummyReporter(), DummyPublisher()
        uc = _uc(ready=ready, reporter=rep, publisher=pub)
        r = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", outputs={"uri": "s3://x"},
            lease_token=tok, tenant="t-A", worker_type="pw1",
        ))
        assert r["accepted"] is True
        assert ready.get("t1") is None                 # completed (CAS removed)
        assert len(rep.completed) == 1
        assert rep.completed[0]["run_id"] == "r1"      # STORED row's identity
        assert rep.completed[0]["output_reference"] == "s3://x"
        assert pub.published[0]["message_type"] == "task.completed"

    def test_error_status_reports_failed(self):
        ready = MemoryReadyTaskStore(); tok = _leased(ready)
        rep = DummyReporter()
        uc = _uc(ready=ready, reporter=rep)
        r = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="error", error="boom",
            lease_token=tok, tenant="t-A", worker_type="pw1",
        ))
        assert r["accepted"] is True
        assert len(rep.failed) == 1
        assert rep.failed[0]["error"]["message"] == "boom"

    def test_forged_lease_rejected_and_not_reported(self):
        ready = MemoryReadyTaskStore(); _leased(ready)
        rep = DummyReporter()
        uc = _uc(ready=ready, reporter=rep)
        r = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", lease_token="forged",
            tenant="t-A", worker_type="pw1",
        ))
        assert r["accepted"] is False
        assert r["message"] == "TASK_SUPERSEDED"
        assert ready.get("t1").state == TaskState.LEASED   # NOT completed
        assert rep.completed == []                          # nothing reported to WCP

    def test_cross_tenant_rejected(self):
        ready = MemoryReadyTaskStore(); tok = _leased(ready)
        uc = _uc(ready=ready, reporter=DummyReporter())
        r = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", lease_token=tok,
            tenant="t-EVIL", worker_type="pw1",
        ))
        assert r["accepted"] is False and r["message"] == "TASK_SUPERSEDED"

    def test_omit_tenant_against_populated_row_rejected(self):
        """A token-holder cannot bypass the tenant binding by OMITTING X-Tenant-Id:
        empty tenant is compared to the row's real tenant → mismatch → reject."""
        ready = MemoryReadyTaskStore(); tok = _leased(ready)   # row tenant='t-A'
        uc = _uc(ready=ready, reporter=DummyReporter())
        r = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", lease_token=tok,
            tenant="", worker_type="pw1",           # tenant omitted
        ))
        assert r["accepted"] is False and r["message"] == "TASK_SUPERSEDED"
        assert ready.get("t1").state == TaskState.LEASED    # not completed

    def test_omit_worker_type_rejected(self):
        ready = MemoryReadyTaskStore(); tok = _leased(ready)
        uc = _uc(ready=ready, reporter=DummyReporter())
        r = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", lease_token=tok,
            tenant="t-A", worker_type="",           # type omitted
        ))
        assert r["accepted"] is False

    def test_double_callback_second_rejected(self):
        ready = MemoryReadyTaskStore(); tok = _leased(ready)
        uc = _uc(ready=ready, reporter=DummyReporter())
        first = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", lease_token=tok, tenant="t-A", worker_type="pw1"))
        second = uc.execute(HandleWorkerCallbackInput(
            task_id="t1", status="success", lease_token=tok, tenant="t-A", worker_type="pw1"))
        assert first["accepted"] is True
        assert second["accepted"] is False              # row gone → UNKNOWN → reject


class TestPushUnchanged:
    def test_push_task_not_routed_to_pull_even_with_ready_store(self):
        """A push task lives in the pending store; a callback for it (with a stray
        lease_token) must take the PUSH path, never the pull CAS."""
        ready = MemoryReadyTaskStore()          # empty — the task isn't a pull task
        pending = MemoryPendingTaskStore()
        pending.store(PendingTaskEntry(
            task_id="pushT", run_id="r1", node_id="n1", worker_type="w",
            dispatched_at="", timeout_at="9999",
        ))
        rep = DummyReporter()
        uc = _uc(ready=ready, pending=pending, reporter=rep)
        r = uc.execute(HandleWorkerCallbackInput(
            task_id="pushT", status="success", lease_token="stray", tenant="t-A"))
        assert r["accepted"] is True                # push path handled it
        assert pending.get("pushT") is None         # push removed from pending store

    def test_callback_without_ready_store_is_pure_push(self):
        pending = MemoryPendingTaskStore()
        pending.store(PendingTaskEntry(
            task_id="p1", run_id="r1", node_id="n1", worker_type="w",
            dispatched_at="", timeout_at="9999"))
        uc = _uc(ready=None, pending=pending, reporter=DummyReporter())   # feature off
        r = uc.execute(HandleWorkerCallbackInput(task_id="p1", status="success"))
        assert r["accepted"] is True


# ======================================================================
# route: 200 accept / 409 supersede
# ======================================================================

def test_callback_route_200_and_409():
    from fastapi.testclient import TestClient
    from main import create_app
    from bootstrap import build_app_container

    ready = MemoryReadyTaskStore()
    tok = _leased(ready, task_id="rt1")
    container = build_app_container()
    container["handle_worker_callback"] = HandleWorkerCallbackUseCase(
        logger=DummyLogger(), pending_task_store=MemoryPendingTaskStore(),
        result_reporter=DummyReporter(), ready_task_store=ready,
    )
    app = create_app()
    app.state.container = container
    client = TestClient(app)

    # Forged first — the task is still leased, so this hits the pull CAS → 409
    # (a good callback first would complete+remove the row, and a later callback
    # would then be a push-path "unknown task" 200, not a pull supersede).
    bad = client.post("/api/v1/tasks/callback",
                      json={"task_id": "rt1", "status": "success", "worker_type": "pw1"},
                      headers={"X-Lease-Token": "forged", "X-Tenant-Id": "t-A"})
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "TASK_SUPERSEDED"

    good = client.post("/api/v1/tasks/callback",
                       json={"task_id": "rt1", "status": "success", "worker_type": "pw1"},
                       headers={"X-Lease-Token": tok, "X-Tenant-Id": "t-A"})
    assert good.status_code == 200
    assert good.json()["data"]["accepted"] is True
