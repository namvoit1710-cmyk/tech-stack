"""B7 (SA-2032) — WCP contract: run_generation, purge_by_run, QUEUED/LEASED lifecycle
events, stable idempotency fence on results, untrusted output-pointer validation."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.services import pull_lifecycle
from app.layer2_application.interfaces.ready_task_store_interface import TaskRow
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase, ExecuteTaskInput,
)
from app.layer2_application.features.lease_tasks.use_cases.lease_tasks_usecase import (
    LeaseTasksUseCase, LeaseTasksInput,
)
from app.layer2_application.features.handle_worker_callback.use_cases.handle_worker_callback_usecase import (
    HandleWorkerCallbackUseCase, HandleWorkerCallbackInput,
)
from app.layer2_application.features.purge_run_tasks.use_cases.purge_run_tasks_usecase import (
    PurgeRunTasksUseCase, PurgeRunTasksInput,
)
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer2_application.services.concurrency_control import ConcurrencyControl


class _Log:
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def debug(self, *a, **k): pass

class _Pub:
    def __init__(self): self.msgs = []
    def publish_result(self, m): self.msgs.append(m)

def _types(pub, t): return [m for m in pub.msgs if m["message_type"] == t]

def _row(tid, run_id="r1", tenant="t-A", wt="pw1", **kw):
    return TaskRow(task_id=tid, run_id=run_id, node_id="n1", worker_type=wt, tenant=tenant, **kw)

def _registry(wt="pw1", mode="pull"):
    s = MemoryWorkerRegistryStore()
    s.register(WorkerRegistration(worker_type=wt, name=wt, delivery_mode=mode))
    return s


# ======================================================================
# pull_lifecycle builders + output-pointer validator
# ======================================================================

def test_lifecycle_event_shapes():
    row = _row("t", idempotency_key="idem", run_generation="g1")
    enq = pull_lifecycle.enqueued_event(row)
    assert enq["message_type"] == "task.enqueued" and enq["payload"]["status"] == "QUEUED"
    assert enq["payload"]["idempotency_key"] == "idem" and enq["payload"]["run_generation"] == "g1"
    assert "lease_generation" in enq["payload"]   # monotonic ordering token (PR review)
    lea = pull_lifecycle.leased_event(row)
    assert lea["message_type"] == "task.leased" and lea["payload"]["status"] == "LEASED"
    exp = pull_lifecycle.lease_expired_event(row)
    assert exp["message_type"] == "task.lease_expired"
    wu = pull_lifecycle.worker_unavailable_event(row)
    assert wu["payload"]["error"]["code"] == "WORKER_UNAVAILABLE"
    assert wu["payload"]["error"]["retryable"] is False

def test_output_pointer_validator():
    ok = pull_lifecycle.output_pointer_ok
    assert ok("t-A", "") is True
    assert ok("t-A", "s3://bucket/key") is True            # scheme, no tenant encoded
    assert ok("t-A", "https://x/y") is True
    assert ok("t-A", "minio:9000/tenants/t-A/f") is True    # host:port (no false-positive)
    assert ok("t-A", "files/tenants/t-A/f1") is True        # own tenant
    assert ok("t-A", "files/tenants/t-EVIL/f1") is False    # cross-tenant path
    assert ok("t-A", "files/TENANTS/t-EVIL/f1") is False    # case-insensitive marker
    assert ok("t-A", "tenants/t-A/tenants/t-EVIL/f") is False  # ALL segments checked
    assert ok("t-A", "tenants/t-A/../t-EVIL/f") is False    # path traversal
    assert ok("", "anything") is True                       # no tenant → file-service gates

def test_outputs_recursive_scan():
    ok = pull_lifecycle.outputs_ok
    assert ok("t-A", {"a": {"ref": "tenants/t-A/f"}}) is True
    assert ok("t-A", {"a": {"deep": ["x", "tenants/t-EVIL/secret"]}}) is False   # nested
    assert ok("t-A", {"n": 1, "b": True, "s": "ok"}) is True                     # non-str leaves


# ======================================================================
# run_generation plumbing + task.enqueued (QUEUED)
# ======================================================================

def _exec_uc(ready, pub):
    return ExecuteTaskUseCase(
        logger=_Log(), task_queue=object(), result_publisher=pub, decision_switch=object(),
        ready_task_store=ready, registry_store=_registry(),
        pending_task_store=MemoryPendingTaskStore(),
        concurrency_control=ConcurrencyControl(max_concurrent=10),
    )

def test_pull_enqueue_carries_generation_and_publishes_queued():
    ready, pub = MemoryReadyTaskStore(), _Pub()
    uc = _exec_uc(ready, pub)
    uc.execute(ExecuteTaskInput(task_id="pt1", run_id="r1", worker_type="pw1",
                                tenant="t-A", run_generation="g7", idempotency_key="idem1"))
    assert ready.get("pt1").run_generation == "g7"
    enq = _types(pub, "task.enqueued")
    assert len(enq) == 1
    assert enq[0]["payload"]["status"] == "QUEUED"
    assert enq[0]["payload"]["run_generation"] == "g7"
    assert enq[0]["payload"]["idempotency_key"] == "idem1"


# ======================================================================
# task.leased (LEASED) on lease
# ======================================================================

@pytest.mark.asyncio
async def test_lease_publishes_leased_event():
    from types import SimpleNamespace
    ready, pub = MemoryReadyTaskStore(), _Pub()
    ready.enqueue(_row("t1", idempotency_key="idem", run_generation="g1"))
    cfg = SimpleNamespace(LEASE_TTL_SECONDS=60, LEASE_TTL_OVERRIDES="",
                          LEASE_MAX_WAIT_SECONDS=20, LEASE_MAX_BATCH=16,
                          MAX_TOTAL_LEASED=0, MAX_LEASE_CONNECTIONS=0)
    uc = LeaseTasksUseCase(logger=_Log(), ready_task_store=ready, app_config=cfg, result_publisher=pub)
    got = await uc.execute(LeaseTasksInput(worker_type="pw1", tenant="t-A", max_n=5))
    assert len(got) == 1
    lea = _types(pub, "task.leased")
    assert len(lea) == 1
    assert lea[0]["payload"]["status"] == "LEASED"
    assert lea[0]["payload"]["run_generation"] == "g1"


# ======================================================================
# purge_by_run
# ======================================================================

def test_purge_by_run_removes_and_frees_bytes():
    store = MemoryReadyTaskStore(max_bytes_global=10_000)
    store.enqueue(_row("a", run_id="r1"))
    store.enqueue(_row("b", run_id="r1"))
    store.enqueue(_row("c", run_id="r2"))
    purged = store.purge_by_run("r1")
    assert {p.task_id for p in purged} == {"a", "b"}
    assert store.get("a") is None and store.get("b") is None
    assert store.get("c") is not None
    # bytes for a,b freed; only c's remain
    assert store._bytes_total == store._row_bytes["c"]

def test_purge_use_case_and_stale_callback_rejected():
    ready = MemoryReadyTaskStore()
    ready.enqueue(_row("t1", run_id="r1"))
    tok = ready.claim("pw1", "t-A", 1, 30)[0].lease_token
    PurgeRunTasksUseCase(logger=_Log(), ready_task_store=ready).execute(PurgeRunTasksInput(run_id="r1"))
    # the leased task is gone → a late callback is rejected (STALE_RUN_TASK_LEASED)
    cb = HandleWorkerCallbackUseCase(
        logger=_Log(), pending_task_store=MemoryPendingTaskStore(), ready_task_store=ready)
    r = cb.execute(HandleWorkerCallbackInput(task_id="t1", status="success",
                                             lease_token=tok, tenant="t-A", worker_type="pw1"))
    assert r["accepted"] is False


# ======================================================================
# callback carries the stable fence + rejects cross-tenant output
# ======================================================================

def _cb_uc(ready, pub):
    return HandleWorkerCallbackUseCase(
        logger=_Log(), pending_task_store=MemoryPendingTaskStore(),
        result_publisher=pub, ready_task_store=ready)

def test_callback_carries_idempotency_and_generation():
    ready, pub = MemoryReadyTaskStore(), _Pub()
    ready.enqueue(_row("t1", idempotency_key="idem9", run_generation="g3"))
    tok = ready.claim("pw1", "t-A", 1, 30)[0].lease_token
    _cb_uc(ready, pub).execute(HandleWorkerCallbackInput(
        task_id="t1", status="success", outputs={"uri": "s3://ok"},
        lease_token=tok, tenant="t-A", worker_type="pw1"))
    done = _types(pub, "task.completed")[0]["payload"]
    assert done["idempotency_key"] == "idem9"
    assert done["run_generation"] == "g3"

def test_callback_rejects_cross_tenant_output_pointer():
    ready, pub = MemoryReadyTaskStore(), _Pub()
    ready.enqueue(_row("t1", tenant="t-A"))
    tok = ready.claim("pw1", "t-A", 1, 30)[0].lease_token
    r = _cb_uc(ready, pub).execute(HandleWorkerCallbackInput(
        task_id="t1", status="success", output_reference="files/tenants/t-EVIL/f1",
        lease_token=tok, tenant="t-A", worker_type="pw1"))
    assert r["accepted"] is True                     # lease was valid → task completed
    failed = _types(pub, "task.failed")
    assert len(failed) == 1                           # but downgraded to a failure
    assert failed[0]["payload"]["error"]["code"] == "UNTRUSTED_OUTPUT_POINTER"
    assert _types(pub, "task.completed") == []        # cross-tenant ref never reported as success

def test_callback_rejects_nested_cross_tenant_output_and_scrubs():
    """A poisoned file_id hidden in a nested outputs value (not output_reference)
    must be caught, downgraded, AND scrubbed from the forwarded outputs."""
    ready, pub = MemoryReadyTaskStore(), _Pub()
    ready.enqueue(_row("t1", tenant="t-A"))
    tok = ready.claim("pw1", "t-A", 1, 30)[0].lease_token
    r = _cb_uc(ready, pub).execute(HandleWorkerCallbackInput(
        task_id="t1", status="success",
        outputs={"result": {"file": "tenants/t-EVIL/secret"}},   # nested cross-tenant
        lease_token=tok, tenant="t-A", worker_type="pw1"))
    assert r["accepted"] is True
    failed = _types(pub, "task.failed")[0]["payload"]
    assert failed["error"]["code"] == "UNTRUSTED_OUTPUT_POINTER"
    assert failed["outputs"] == {}                    # poisoned outputs scrubbed, not forwarded


class _Rep:
    def __init__(self): self.completed = []; self.failed = []
    def report_completed(self, **kw): self.completed.append(kw)
    def report_failed(self, **kw): self.failed.append(kw)

def test_reporter_path_carries_fence():
    """HIGH fix: the fence must reach WCP on the AUTHORITATIVE sync reporter path,
    not only the event-mesh publish."""
    ready, rep = MemoryReadyTaskStore(), _Rep()
    ready.enqueue(_row("t1", idempotency_key="idemZ", run_generation="g5"))
    tok = ready.claim("pw1", "t-A", 1, 30)[0].lease_token
    HandleWorkerCallbackUseCase(
        logger=_Log(), pending_task_store=MemoryPendingTaskStore(),
        result_reporter=rep, ready_task_store=ready,
    ).execute(HandleWorkerCallbackInput(
        task_id="t1", status="success", lease_token=tok, tenant="t-A", worker_type="pw1"))
    assert rep.completed[0]["run_generation"] == "g5"
    assert rep.completed[0]["idempotency_key"] == "idemZ"

def test_publish_failure_does_not_crash_callback():
    class _BadPub:
        def publish_result(self, m): raise RuntimeError("broker down")
    ready = MemoryReadyTaskStore()
    ready.enqueue(_row("t1"))
    tok = ready.claim("pw1", "t-A", 1, 30)[0].lease_token
    r = HandleWorkerCallbackUseCase(
        logger=_Log(), pending_task_store=MemoryPendingTaskStore(),
        result_publisher=_BadPub(), ready_task_store=ready,
    ).execute(HandleWorkerCallbackInput(
        task_id="t1", status="success", lease_token=tok, tenant="t-A", worker_type="pw1"))
    assert r["accepted"] is True                      # publish failure swallowed


# ======================================================================
# purge route
# ======================================================================

def test_purge_route():
    from fastapi.testclient import TestClient
    from main import create_app
    from bootstrap import build_app_container

    ready = MemoryReadyTaskStore()
    ready.enqueue(_row("a", run_id="r1"))
    ready.enqueue(_row("b", run_id="r1"))
    container = build_app_container()
    container["purge_run_tasks"] = PurgeRunTasksUseCase(logger=_Log(), ready_task_store=ready)
    app = create_app()
    app.state.container = container
    client = TestClient(app)
    resp = client.post("/api/v1/runs/r1/tasks/purge")
    assert resp.status_code == 200
    assert resp.json()["data"]["purged"] == 2
