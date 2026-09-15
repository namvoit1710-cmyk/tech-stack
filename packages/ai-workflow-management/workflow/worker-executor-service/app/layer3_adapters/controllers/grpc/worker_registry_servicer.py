"""gRPC servicer for WorkerRegistryService.

Served by the Worker Executor. Called by Workers (register, heartbeat,
deregister) and by the Control Plane (list, get, discover).
"""
from __future__ import annotations

import logging
from typing import Any

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf import struct_pb2

from workflow_proto.v1 import worker_registry_pb2 as pb
from workflow_proto.v1 import worker_registry_pb2_grpc as pb_grpc

from app.layer1_domain.value_objects.node_kind import NodeKind, normalize_kind
from app.layer1_domain.value_objects.delivery_mode import DeliveryMode, normalize_delivery_mode

logger = logging.getLogger(__name__)


def _dict_to_struct(d: Any) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if isinstance(d, dict) and d:
        s.update(d)
    return s


def _to_worker_detail(w: dict) -> pb.WorkerDetail:
    caps = [
        pb.Capability(domain=c.get("domain", ""), action=c.get("action", ""))
        for c in (w.get("capabilities") or [])
    ]
    kwargs = dict(
        worker_id=w.get("worker_id", w.get("id", "")),
        worker_type=w.get("worker_type", ""),
        version=w.get("version", ""),
        endpoint=w.get("endpoint", w.get("endpoint_url", "")),
        name=w.get("name", ""),
        description=w.get("description", ""),
        node_class=w.get("node_class", ""),
        icon=w.get("icon", ""),
        color=w.get("color", ""),
        tags=w.get("tags", []),
        capabilities=caps,
        health_status=w.get("health_status", ""),
        ports=_dict_to_struct(w.get("ports", {})),
    )
    # spec_version is an additive proto field (number 15). The field only
    # exists after proto regen; guard construction so we don't break on
    # stale generated stubs.
    if "spec_version" in pb.WorkerDetail.DESCRIPTOR.fields_by_name:
        kwargs["spec_version"] = w.get("spec_version", "") or "1.0.0"
    # kind is an additive proto field (number 16). Same guard as spec_version.
    if "kind" in pb.WorkerDetail.DESCRIPTOR.fields_by_name:
        # w["kind"] may be a NodeKind (from ListWorkersOutput/list_workers) or
        # simply absent (GetWorker/DiscoverWorkers outputs don't carry kind
        # today — pre-existing gap, out of scope here). Either way, extract the
        # plain value explicitly rather than relying on proto's string-field
        # assignment to normalize a str-Enum for us.
        raw_kind = w.get("kind") or NodeKind.ACTION
        kwargs["kind"] = raw_kind.value if isinstance(raw_kind, NodeKind) else raw_kind
    return pb.WorkerDetail(**kwargs)


class WorkerRegistryServicer(pb_grpc.WorkerRegistryServiceServicer):

    def __init__(self, container: dict[str, Any]) -> None:
        self._container = container

    # --- Worker → Executor ---

    async def Register(self, request: pb.RegisterWorkerRequest, context: Any) -> Any:
        if not request.worker_type or not request.worker_type.strip():
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "worker_type is required and cannot be blank",
            )

        uc = self._container.get("sdk_register_worker")
        if uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "register use case not available")

        from app.layer2_application.features.sdk_register_worker.use_cases.sdk_register_worker_usecase import SdkRegisterWorkerInput
        # proto3 absent strings deserialize as "" — explicit defaulting needed.
        spec_version = (getattr(request, "spec_version", "") or "").strip() or "1.0.0"
        raw_kind = (getattr(request, "kind", "") or "").strip()
        try:
            # Blank means "not supplied" (proto3-additive — an old worker that
            # predates SA-1735 sends no kind at all) -> defaults to "action".
            # A non-empty unknown value (e.g. "banana") is rejected outright —
            # the executor is the network boundary and must not let an
            # invented kind through to the control plane / frontend.
            kind = normalize_kind(raw_kind)
        except ValueError:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Unknown node kind: {raw_kind!r}",
            )
        # delivery_mode is an additive proto field. Read it with getattr so this
        # works whether or not the shared workflow_proto has been regenerated —
        # an absent field / old stub yields "" which normalizes to PUSH (the
        # legacy behaviour). Same boundary discipline as kind: an invented value
        # is rejected outright rather than reaching the registry.
        raw_delivery_mode = (getattr(request, "delivery_mode", "") or "").strip()
        try:
            delivery_mode = normalize_delivery_mode(raw_delivery_mode)
        except ValueError:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Unknown delivery_mode: {raw_delivery_mode!r}",
            )
        inp = SdkRegisterWorkerInput(
            worker_type=request.worker_type,
            version=request.version,
            spec_version=spec_version,
            kind=kind,
            delivery_mode=delivery_mode.value,
            endpoint=request.endpoint,
            sdk_version=request.sdk_version,
            name=request.name,
            description=request.description,
            node_class=request.node_class,
            icon=request.icon,
            color=request.color,
            tags=list(request.tags),
            capabilities=[{"domain": c.domain, "action": c.action} for c in request.capabilities],
            input_schema=MessageToDict(request.input_schema) if request.HasField("input_schema") else None,
            output_schema=MessageToDict(request.output_schema) if request.HasField("output_schema") else None,
            ports=MessageToDict(request.ports) if request.HasField("ports") else {},
        )
        result = uc.execute(inp)
        return pb.RegisterWorkerResponse(
            worker_id=result.get("worker_id", ""),
            worker_type=result.get("worker_type", ""),
            version=result.get("version", ""),
            endpoint_url=result.get("endpoint_url", ""),
            health_status=result.get("health_status", ""),
        )

    async def Deregister(self, request: pb.DeregisterWorkerRequest, context: Any) -> Any:
        uc = self._container.get("deregister_worker")
        if uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "deregister use case not available")

        from app.layer2_application.features.deregister_worker.use_cases.deregister_worker_usecase import DeregisterWorkerInput
        uc.execute(DeregisterWorkerInput(worker_id=request.worker_id))
        return pb.DeregisterWorkerResponse(success=True)

    async def Heartbeat(self, request: pb.HeartbeatRequest, context: Any) -> Any:
        uc = self._container.get("worker_heartbeat")
        if uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "heartbeat use case not available")

        from app.layer2_application.features.worker_heartbeat.use_cases.worker_heartbeat_usecase import WorkerHeartbeatInput
        result = uc.execute(WorkerHeartbeatInput(
            worker_id=request.worker_id,
            status=request.status,
        ))
        return pb.HeartbeatResponse(
            acknowledged=result.get("acknowledged", False),
            re_register=result.get("re_register", False),
        )

    async def HeartbeatStream(self, request_iterator: Any, context: Any) -> Any:
        """Bidirectional streaming heartbeat."""
        async for request in request_iterator:
            resp = await self.Heartbeat(request, context)
            yield resp

    # --- CP → Executor (discovery) ---

    async def ListWorkers(self, request: pb.ListWorkersRequest, context: Any) -> Any:
        uc = self._container.get("list_workers")
        if uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "list_workers use case not available")

        result = uc.execute()
        workers = result if isinstance(result, list) else result.get("workers", [])
        return pb.ListWorkersResponse(workers=[_to_worker_detail(w) for w in workers])

    async def GetWorker(self, request: pb.GetWorkerRequest, context: Any) -> Any:
        uc = self._container.get("get_worker")
        if uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "get_worker use case not available")

        result = uc.execute(request.worker_id)
        if result is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Worker {request.worker_id} not found")
        return _to_worker_detail(result)

    async def GetWorkerSchema(self, request: pb.GetWorkerSchemaRequest, context: Any) -> Any:
        uc = self._container.get("get_worker_schema")
        if uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "get_worker_schema not available")

        result = uc.execute(request.worker_id)
        if result is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Worker {request.worker_id} not found")
        return pb.WorkerSchemaResponse(
            input_schema=_dict_to_struct(result.get("input_schema", {})),
            output_schema=_dict_to_struct(result.get("output_schema", {})),
        )

    async def DiscoverWorkers(self, request: pb.DiscoverWorkersRequest, context: Any) -> Any:
        uc = self._container.get("discover_workers")
        if uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "discover_workers not available")

        result = uc.execute(request.capability)
        workers = result if isinstance(result, list) else result.get("workers", [])
        return pb.DiscoverWorkersResponse(workers=[_to_worker_detail(w) for w in workers])
