"""Tests for WorkerRegistryServicer input validation (gRPC layer).

Requires the generated protobuf stubs at `workflow_proto`. When running
locally without the Docker build, skip if unavailable.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Skip cleanly when grpc or the generated proto stubs aren't wired (CI bakes them in).
grpc = pytest.importorskip("grpc", reason="grpc not installed")
workflow_proto = pytest.importorskip(
    "workflow_proto.v1.worker_registry_pb2",
    reason="workflow_proto stubs not installed — see proto/generate.sh",
)

from app.layer3_adapters.controllers.grpc.worker_registry_servicer import (
    WorkerRegistryServicer,
    _to_worker_detail,
)
from workflow_proto.v1 import worker_registry_pb2 as pb


class _AbortError(Exception):
    """Captures the code/message passed to context.abort()."""
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class _FakeGrpcContext:
    """Minimal async gRPC context that raises on abort() for assertion."""
    async def abort(self, code, message):
        raise _AbortError(code, message)


@pytest.mark.asyncio
async def test_register_rejects_empty_worker_type():
    servicer = WorkerRegistryServicer(container={})
    request = pb.RegisterWorkerRequest(worker_type="", version="1.0.0")
    ctx = _FakeGrpcContext()
    with pytest.raises(_AbortError) as exc_info:
        await servicer.Register(request, ctx)
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "worker_type" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_register_rejects_whitespace_worker_type():
    servicer = WorkerRegistryServicer(container={})
    request = pb.RegisterWorkerRequest(worker_type="   ", version="1.0.0")
    ctx = _FakeGrpcContext()
    with pytest.raises(_AbortError) as exc_info:
        await servicer.Register(request, ctx)
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


# --- kind field mapping (SA-1734 sub-task A, Task 5) -------------------------
# Mirrors the additive spec_version proto field: register → store → discovery.

class _DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _register_and_list_container():
    """Wire a real register + list_workers pair over one memory store."""
    from app.layer4_frameworks.repositories.memory_worker_registry_store import (
        MemoryWorkerRegistryStore,
    )
    from app.layer2_application.features.sdk_register_worker.use_cases.sdk_register_worker_usecase import (
        SdkRegisterWorkerUseCase,
    )
    from app.layer2_application.features.list_workers.use_cases.list_workers_usecase import (
        ListWorkersUseCase,
    )

    store = MemoryWorkerRegistryStore()
    log = _DummyLogger()
    return store, {
        "sdk_register_worker": SdkRegisterWorkerUseCase(logger=log, registry_store=store),
        "list_workers": ListWorkersUseCase(logger=log, registry_store=store),
    }


def test_to_worker_detail_maps_kind():
    wd = _to_worker_detail({"worker_id": "w1", "worker_type": "t", "kind": "read"})
    assert wd.kind == "read"


def test_to_worker_detail_defaults_kind_to_action():
    # Missing "kind" key → default "action" (never blank on the wire).
    wd = _to_worker_detail({"worker_id": "w1", "worker_type": "t"})
    assert wd.kind == "action"


@pytest.mark.asyncio
async def test_register_stores_kind_and_list_returns_it():
    store, container = _register_and_list_container()
    servicer = WorkerRegistryServicer(container=container)
    ctx = _FakeGrpcContext()

    await servicer.Register(
        pb.RegisterWorkerRequest(worker_type="reader", version="1.0.0", kind="read"),
        ctx,
    )

    # Persisted on the entity...
    assert store.list_all()[0].kind == "read"
    # ...and surfaced on the discovery WorkerDetail.
    resp = await servicer.ListWorkers(pb.ListWorkersRequest(), ctx)
    assert resp.workers[0].kind == "read"


@pytest.mark.asyncio
async def test_register_defaults_kind_to_action_when_absent():
    store, container = _register_and_list_container()
    servicer = WorkerRegistryServicer(container=container)
    ctx = _FakeGrpcContext()

    # proto3 absent string → "" → normalized to "action".
    await servicer.Register(
        pb.RegisterWorkerRequest(worker_type="plain", version="1.0.0"),
        ctx,
    )
    assert store.list_all()[0].kind == "action"
    resp = await servicer.ListWorkers(pb.ListWorkersRequest(), ctx)
    assert resp.workers[0].kind == "action"


@pytest.mark.asyncio
async def test_register_rejects_unknown_kind():
    """SA-1734 blocker fix: the gRPC door had no vocabulary at all — a worker
    registering `kind="banana"` flowed straight through to the store and out
    to discovery. The executor is the network boundary; it must reject an
    unknown, non-empty kind itself (INVALID_ARGUMENT) rather than trust the
    sdk / control plane to have already validated it."""
    store, container = _register_and_list_container()
    servicer = WorkerRegistryServicer(container=container)
    ctx = _FakeGrpcContext()

    with pytest.raises(_AbortError) as exc_info:
        await servicer.Register(
            pb.RegisterWorkerRequest(worker_type="rogue", version="1.0.0", kind="banana"),
            ctx,
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT
    assert store.list_all() == []
