"""gRPC worker registry client — registers/heartbeats with the Executor via gRPC.

Drop-in replacement for ``HttpWorkerRegistry``.
Implements ``IWorkerRegistry``.
"""
from __future__ import annotations

import logging

import grpc

from google.protobuf import struct_pb2

from workflow_proto.v1 import worker_registry_pb2 as pb
from workflow_proto.v1 import worker_registry_pb2_grpc as pb_grpc

from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus
from worker_sdk.layer2_application.interfaces.worker_registry_interface import IWorkerRegistry
from typing import Any

_log = logging.getLogger("WorkerSDK")


def _dict_to_struct(d: Any) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if isinstance(d, dict) and d:
        s.update(d)
    return s


def _list_or_none_to_struct(v: Any) -> struct_pb2.Struct:
    """Convert a list-typed schema (or None) to Struct for proto transport."""
    if v is None:
        return struct_pb2.Struct()
    return _dict_to_struct({"items": v} if isinstance(v, list) else v)


class GrpcWorkerRegistry(IWorkerRegistry):
    """Registers and heartbeats with the worker registry over gRPC."""

    def __init__(self, grpc_target: str) -> None:
        self._target = grpc_target
        self._channel: grpc.aio.Channel | None = None
        _log.debug("GrpcWorkerRegistry initialised, target=%s", grpc_target)

    def _get_channel(self) -> grpc.aio.Channel:
        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(self._target)
        return self._channel

    def _stub(self) -> pb_grpc.WorkerRegistryServiceStub:
        return pb_grpc.WorkerRegistryServiceStub(self._get_channel())

    async def register(self, registration: WorkerRegistration) -> str:
        caps = [
            pb.Capability(domain=c.get("domain", ""), action=c.get("action", ""))
            for c in (registration.capabilities or [])
        ]
        from workflow_proto.v1 import worker_execution_pb2 as exec_pb
        func_defs = []
        for f in (registration.functions or []):
            func_defs.append(exec_pb.WorkerFunctionDef(
                name=f.name,
                description=f.description,
                input_schema=_list_or_none_to_struct(f.input_schema),
                output_schema=_list_or_none_to_struct(f.output_schema),
            ))
        request = pb.RegisterWorkerRequest(
            worker_type=registration.worker_type,
            version=registration.version,
            endpoint=registration.endpoint,
            sdk_version=registration.sdk_version or "",
            name=registration.name,
            description=registration.description,
            node_class=registration.node_class,
            # ``.value`` extracts the plain wire string ("read"), never the
            # enum repr ("NodeKind.READ") — proto3 string fields accept a
            # ``str, Enum`` member directly, but being explicit here removes
            # any dependence on that implicit behaviour (SA-1734 blocker fix).
            kind=NodeKind(getattr(registration, "kind", None) or NodeKind.ACTION).value,
            icon=registration.icon,
            color=registration.color,
            tags=registration.tags or [],
            capabilities=caps,
            input_schema=_list_or_none_to_struct(registration.input_schema),
            output_schema=_list_or_none_to_struct(registration.output_schema),
            ports=_dict_to_struct(registration.ports),
            functions=func_defs,
        )
        response = await self._stub().Register(request, timeout=30.0)
        _log.debug("gRPC register successful, worker_id=%s", response.worker_id)
        return response.worker_id

    async def heartbeat(self, worker_id: str, status: WorkerStatus) -> dict:
        request = pb.HeartbeatRequest(worker_id=worker_id, status=status.value)
        response = await self._stub().Heartbeat(request, timeout=30.0)
        return {
            "acknowledged": response.acknowledged,
            "re_register": response.re_register,
        }

    async def deregister(self, worker_id: str) -> None:
        request = pb.DeregisterWorkerRequest(worker_id=worker_id)
        await self._stub().Deregister(request, timeout=30.0)
        _log.debug("gRPC deregister successful, worker_id=%s", worker_id)

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
            self._channel = None
