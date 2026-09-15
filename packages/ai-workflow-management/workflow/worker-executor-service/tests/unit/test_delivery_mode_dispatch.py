"""B1 (SA-2026) — delivery_mode field + the pull dispatch branch.

Two invariants under test:
  1. PUSH IS UNTOUCHED. With the pull feature wired, a push worker (and an
     unregistered worker, and the feature-off case) takes the byte-identical push
     path — never the ready store.
  2. PULL ENQUEUES. A pull worker's task is stored for lease and returns ENQUEUED;
     the executor is NOT invoked and no dispatch-concurrency slot is consumed.

Plus the register-surface plumbing (entity, value object, SDK use case, HTTP DTO).
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer1_domain.value_objects.delivery_mode import DeliveryMode, normalize_delivery_mode
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskInput,
)
from app.layer2_application.features.sdk_register_worker.use_cases.sdk_register_worker_usecase import (
    SdkRegisterWorkerUseCase,
    SdkRegisterWorkerInput,
)
from app.layer4_frameworks.repositories.memory_worker_registry_store import MemoryWorkerRegistryStore
from app.layer4_frameworks.storage.memory_ready_task_store import MemoryReadyTaskStore
from app.layer4_frameworks.storage.memory_pending_task_store import MemoryPendingTaskStore
from app.layer2_application.services.concurrency_control import ConcurrencyControl


# --- Dummies (mirror test_execute_task_use_case.py) ---

class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


class DummyResultPublisher:
    def __init__(self): self.published = []
    def publish_result(self, result): self.published.append(result)


class DummyExecutor:
    def __init__(self, result=None):
        self._result = result or {"output": {}, "metrics": {"duration_ms": 1}}
        self.last_dispatch_payload = None
        self.last_payload = None
    def dispatch(self, payload):
        self.last_dispatch_payload = payload
        return {"task_id": payload.get("task_id", ""), "accepted": True}
    def execute(self, payload):
        self.last_payload = payload
        return self._result


class DummyWorkerDispatcher:
    def __init__(self, executor=None): self._executor = executor
    def get_executor(self, worker_id): return self._executor


def _uc(**overrides):
    defaults = dict(
        logger=DummyLogger(),
        task_queue=object(),
        result_publisher=DummyResultPublisher(),
        decision_switch=object(),
    )
    defaults.update(overrides)
    return ExecuteTaskUseCase(**defaults)


def _registry_with(worker_type: str, delivery_mode: str = "push", endpoint_url: str = ""):
    store = MemoryWorkerRegistryStore()
    store.register(WorkerRegistration(
        worker_type=worker_type,
        name=worker_type,
        endpoint_url=endpoint_url,
        delivery_mode=delivery_mode,
    ))
    return store


# ======================================================================
# Value object + entity
# ======================================================================

class TestDeliveryModeValueObject:
    def test_blank_defaults_to_push(self):
        assert normalize_delivery_mode("") is DeliveryMode.PUSH
        assert normalize_delivery_mode(None) is DeliveryMode.PUSH
        assert normalize_delivery_mode("  ") is DeliveryMode.PUSH

    def test_known_values(self):
        assert normalize_delivery_mode("push") is DeliveryMode.PUSH
        assert normalize_delivery_mode("pull") is DeliveryMode.PULL

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            normalize_delivery_mode("poll")

    def test_is_str_subclass(self):
        assert DeliveryMode.PULL == "pull"


class TestEntityDeliveryMode:
    def test_defaults_to_push(self):
        assert WorkerRegistration(worker_type="w1").delivery_mode == "push"

    def test_can_be_set_pull(self):
        assert WorkerRegistration(worker_type="w1", delivery_mode="pull").delivery_mode == "pull"

    def test_blank_normalizes_to_push(self):
        assert WorkerRegistration(worker_type="w1", delivery_mode="").delivery_mode == "push"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            WorkerRegistration(worker_type="w1", delivery_mode="poll")


# ======================================================================
# Register plumbing (SDK use case + store round-trip)
# ======================================================================

class TestSdkRegisterPlumbsDeliveryMode:
    def _uc_and_store(self):
        store = MemoryWorkerRegistryStore()
        return SdkRegisterWorkerUseCase(logger=DummyLogger(), registry_store=store), store

    def test_default_push(self):
        uc, store = self._uc_and_store()
        uc.execute(SdkRegisterWorkerInput(worker_type="w1"))
        assert store.get_by_worker_type("w1").delivery_mode == "push"

    def test_explicit_pull(self):
        uc, store = self._uc_and_store()
        uc.execute(SdkRegisterWorkerInput(worker_type="w1", delivery_mode="pull"))
        assert store.get_by_worker_type("w1").delivery_mode == "pull"

    def test_invalid_raises(self):
        uc, _ = self._uc_and_store()
        with pytest.raises(ValueError):
            uc.execute(SdkRegisterWorkerInput(worker_type="w1", delivery_mode="poll"))


class TestHttpDtoDeliveryMode:
    def _dto(self):
        from app.layer3_adapters.controllers.restful.v1.worker_controller import (
            SdkRegisterWorkerInputDto,
        )
        return SdkRegisterWorkerInputDto

    def test_valid_and_default(self):
        Dto = self._dto()
        assert Dto(worker_type="w1", delivery_mode="pull").delivery_mode == "pull"
        assert Dto(worker_type="w1").delivery_mode == "push"

    def test_blank_normalizes_to_push_not_422(self):
        """The boundary contract: an explicit "" is back-compat, not an error —
        must match gRPC + entity (red-team B1 LOW fix)."""
        Dto = self._dto()
        assert Dto(worker_type="w1", delivery_mode="").delivery_mode == "push"

    def test_invalid_is_422(self):
        from pydantic import ValidationError
        Dto = self._dto()
        with pytest.raises(ValidationError):
            Dto(worker_type="w1", delivery_mode="poll")


# ======================================================================
# Dispatch branch — the two invariants
# ======================================================================

class TestPushIsUntouched:
    def test_push_worker_takes_push_path_even_with_pull_feature_on(self):
        """Feature wired (ready_task_store present) + a push worker → the async
        push path (DISPATCHED). The ready store is never touched."""
        executor = DummyExecutor()
        ready = MemoryReadyTaskStore()
        pending = MemoryPendingTaskStore()
        cc = ConcurrencyControl(max_concurrent=10)
        uc = _uc(
            worker_dispatcher=DummyWorkerDispatcher(executor=executor),
            pending_task_store=pending,
            ready_task_store=ready,
            registry_store=_registry_with("w1", "push", "http://w1"),
            concurrency_control=cc,
        )
        r = uc.execute(ExecuteTaskInput(task_id="t1", run_id="r1", node_id="n1", worker_type="w1"))
        assert r["status"] == "DISPATCHED"
        assert ready.count() == 0                       # pull store untouched
        assert executor.last_dispatch_payload is not None  # push dispatch happened
        assert cc.active_count == 1                     # push slot held until callback

    def test_feature_off_pull_registration_still_takes_push_path(self):
        """The gate: with NO ready_task_store (feature off), even a worker that
        registered delivery_mode=pull falls through to the push path — the pull
        branch is completely dormant. Proves the deployment-level clean seam."""
        executor = DummyExecutor()
        pending = MemoryPendingTaskStore()
        cc = ConcurrencyControl(max_concurrent=10)
        uc = _uc(
            worker_dispatcher=DummyWorkerDispatcher(executor=executor),
            pending_task_store=pending,
            # ready_task_store OMITTED → None
            registry_store=_registry_with("pw1", "pull", ""),
            concurrency_control=cc,
        )
        r = uc.execute(ExecuteTaskInput(task_id="t2", run_id="r1", node_id="n1", worker_type="pw1"))
        # pull worker has no endpoint → push async path finds no executor for it,
        # but the point is: it did NOT enqueue and did NOT raise. It's the push code.
        assert r["status"] in ("DISPATCHED", "COMPLETED")

    def test_unregistered_worker_defaults_push(self):
        """ready_task_store present, but the worker isn't in the registry →
        resolve defaults to PUSH → push path, no enqueue."""
        executor = DummyExecutor()
        ready = MemoryReadyTaskStore()
        pending = MemoryPendingTaskStore()
        cc = ConcurrencyControl(max_concurrent=10)
        uc = _uc(
            worker_dispatcher=DummyWorkerDispatcher(executor=executor),
            pending_task_store=pending,
            ready_task_store=ready,
            registry_store=MemoryWorkerRegistryStore(),   # empty
            concurrency_control=cc,
        )
        r = uc.execute(ExecuteTaskInput(task_id="t3", run_id="r1", node_id="n1", worker_type="ghost"))
        assert r["status"] == "DISPATCHED"
        assert ready.count() == 0


class TestPullEnqueues:
    def test_pull_worker_enqueues_and_returns_enqueued(self):
        executor = DummyExecutor()
        ready = MemoryReadyTaskStore()
        pending = MemoryPendingTaskStore()
        cc = ConcurrencyControl(max_concurrent=10)
        uc = _uc(
            worker_dispatcher=DummyWorkerDispatcher(executor=executor),
            pending_task_store=pending,
            ready_task_store=ready,
            registry_store=_registry_with("pw1", "pull", ""),
            concurrency_control=cc,
        )
        r = uc.execute(ExecuteTaskInput(
            task_id="pt1", run_id="r1", node_id="n1", worker_type="pw1",
            payload={"k": "v"}, idempotency_key="idem-1",
        ))
        assert r["status"] == "ENQUEUED"
        assert ready.count() == 1                        # task stored for lease
        assert ready.get("pt1").payload == {"k": "v"}
        assert ready.get("pt1").idempotency_key == "idem-1"
        assert executor.last_dispatch_payload is None    # executor NOT invoked
        assert executor.last_payload is None             # sync .execute NOT called
        assert cc.active_count == 0                       # no dispatch slot consumed

    def test_pull_enqueue_plumbs_tenant(self):
        """The dispatch tenant is written onto the ready row (SA-2030) — the
        load-bearing write-side for pull tenant isolation + the callback binding."""
        ready = MemoryReadyTaskStore()
        uc = _uc(
            worker_dispatcher=DummyWorkerDispatcher(executor=DummyExecutor()),
            pending_task_store=MemoryPendingTaskStore(),
            ready_task_store=ready,
            registry_store=_registry_with("pw1", "pull", ""),
            concurrency_control=ConcurrencyControl(max_concurrent=10),
        )
        uc.execute(ExecuteTaskInput(task_id="pt1", run_id="r1", worker_type="pw1", tenant="t-A"))
        assert ready.get("pt1").tenant == "t-A"

    def test_pull_admission_full_returns_requeue(self):
        """When the ready store rejects on a byte quota, the pull dispatch returns
        REQUEUE (backpressure) — the task is NOT stored, and Event Mesh redelivers."""
        from app.layer2_application.interfaces.ready_task_store_interface import TaskRow
        fill = TaskRow(task_id="fill", run_id="r", node_id="n", worker_type="pw1",
                       tenant="t-A", payload={"d": "x" * 80})
        per = MemoryReadyTaskStore._estimate_bytes(fill)
        ready = MemoryReadyTaskStore(max_bytes_global=per)   # room for exactly the fill row
        ready.enqueue(fill)
        uc = _uc(
            worker_dispatcher=DummyWorkerDispatcher(executor=DummyExecutor()),
            pending_task_store=MemoryPendingTaskStore(),
            ready_task_store=ready,
            registry_store=_registry_with("pw1", "pull", ""),
            concurrency_control=ConcurrencyControl(max_concurrent=10),
        )
        r = uc.execute(ExecuteTaskInput(
            task_id="pt1", run_id="r1", worker_type="pw1", tenant="t-A", payload={"x": 1}))
        assert r["status"] == "REQUEUE"
        assert r["error"]["code"] == "ADMISSION_FULL"
        assert r["error"]["retryable"] is True
        assert ready.get("pt1") is None      # not stored

    def test_pull_dedup_returns_duplicate(self):
        ready = MemoryReadyTaskStore()
        uc = _uc(
            worker_dispatcher=DummyWorkerDispatcher(executor=DummyExecutor()),
            pending_task_store=MemoryPendingTaskStore(),
            ready_task_store=ready,
            registry_store=_registry_with("pw1", "pull", ""),
            concurrency_control=ConcurrencyControl(max_concurrent=10),
        )
        first = uc.execute(ExecuteTaskInput(task_id="dp", run_id="r1", worker_type="pw1"))
        second = uc.execute(ExecuteTaskInput(task_id="dp", run_id="r1", worker_type="pw1"))
        assert first["status"] == "ENQUEUED"
        assert second["status"] == "DUPLICATE"
        assert ready.count() == 1
