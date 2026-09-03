"""gRPC servicer for WorkerExecutionService.

Served by each Worker (via the SDK). Called by the Worker Executor
to run tasks (replaces HTTP POST /api/v1/execute).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from workflow_proto.v1 import worker_execution_pb2 as pb
from workflow_proto.v1 import worker_execution_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)


def _dict_to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


class WorkerExecutionServicer(pb_grpc.WorkerExecutionServiceServicer):
    """Delegates gRPC Execute calls to the worker's ExecuteTaskUseCase."""

    def __init__(self, container: dict[str, Any], worker_info: dict[str, Any] | None = None, function_registry: Any=None) -> None:
        self._container = container
        self._worker_info = worker_info or {}
        self._function_registry = function_registry

    async def Execute(self, request: pb.ExecuteRequest, context: grpc.aio.ServicerContext) -> Any:
        # Both keys — see the note in worker_pull.run_pull_worker. A container
        # from ``build_app_container`` carries ``execute_task_usecase``; only
        # hand-built ones (tests, embedders) use the short name.
        execute_task_uc = (
            self._container.get("execute_task")
            or self._container.get("execute_task_usecase")
        )
        if execute_task_uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "execute_task use case not available")

        inputs = MessageToDict(request.inputs) if request.HasField("inputs") else {}
        parameters = MessageToDict(request.parameters) if request.HasField("parameters") else {}

        start = time.perf_counter()
        try:
            # Worker SDK use cases expect a command/input object
            from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import ExecuteTaskCommand
            command = ExecuteTaskCommand(
                task_id=request.task_id,
                action=request.action,
                inputs=inputs,
                parameters=parameters,
                correlation_id=request.correlation_id,
            )
            # ``execute`` is a SYNC use case. Awaiting its return value raised
            # TypeError on every call (caught below and reported as a generic
            # ERROR response), and calling it inline would block the grpc.aio
            # loop for the whole handler. Dispatch to a thread — same contract
            # as the HTTP (routes.py) and PULL transports.
            result = await asyncio.to_thread(execute_task_uc.execute, command)
            duration = (time.perf_counter() - start) * 1000

            outputs = getattr(result, "outputs", {}) or {}
            return pb.ExecuteResponse(
                task_id=request.task_id,
                status=getattr(result, "status", "SUCCESS"),
                outputs=_dict_to_struct(outputs),
                error=getattr(result, "error", "") or "",
                duration_ms=duration,
                output_reference=getattr(result, "output_reference", ""),
            )
        except Exception as exc:
            duration = (time.perf_counter() - start) * 1000
            logger.error("Worker execution failed: %s", exc)
            return pb.ExecuteResponse(
                task_id=request.task_id,
                status="ERROR",
                error=str(exc),
                duration_ms=duration,
            )

    async def ExecuteWithProgress(self, request: pb.ExecuteRequest, context: grpc.aio.ServicerContext) -> Any:
        """Server-streaming: yield progress + final result."""
        yield pb.ExecutionEvent(
            task_id=request.task_id,
            event_type="progress",
            progress_pct=0.0,
            message="Accepted",
        )

        response = await self.Execute(request, context)

        yield pb.ExecutionEvent(
            task_id=request.task_id,
            event_type="result",
            progress_pct=1.0,
            message="Done",
            result=response,
        )

    async def GetInfo(self, request: pb.GetInfoRequest, context: grpc.aio.ServicerContext) -> Any:
        func_defs = []
        if self._function_registry:
            for defn in self._function_registry.list_definitions():
                func_defs.append(pb.WorkerFunctionDef(
                    name=defn.name,
                    description=defn.description,
                    input_schema=_dict_to_struct({"items": defn.input_schema} if isinstance(defn.input_schema, list) else defn.input_schema),
                    output_schema=_dict_to_struct({"items": defn.output_schema} if isinstance(defn.output_schema, list) else defn.output_schema),
                ))
        return pb.WorkerInfoResponse(
            worker_type=self._worker_info.get("worker_type", ""),
            version=self._worker_info.get("version", ""),
            sdk_version=self._worker_info.get("sdk_version", ""),
            name=self._worker_info.get("name", ""),
            description=self._worker_info.get("description", ""),
            node_class=self._worker_info.get("node_class", ""),
            icon=self._worker_info.get("icon", ""),
            color=self._worker_info.get("color", ""),
            tags=self._worker_info.get("tags", []),
            input_schema=_dict_to_struct(self._worker_info.get("input_schema", {})),
            output_schema=_dict_to_struct(self._worker_info.get("output_schema", {})),
            functions=func_defs,
        )

    async def InvokeFunction(self, request: pb.InvokeFunctionRequest, context: grpc.aio.ServicerContext) -> Any:
        if not self._function_registry:
            return pb.InvokeFunctionResponse(
                status="error",
                error="No function registry configured",
            )
        params = MessageToDict(request.params) if request.HasField("params") else {}
        try:
            result = await self._function_registry.invoke(request.function_name, params)
            return pb.InvokeFunctionResponse(
                status="success",
                result=_dict_to_struct(result if isinstance(result, dict) else {"value": result}),
            )
        except KeyError as exc:
            return pb.InvokeFunctionResponse(status="error", error=str(exc))
        except Exception as exc:
            logger.error("InvokeFunction '%s' failed: %s", request.function_name, exc)
            return pb.InvokeFunctionResponse(status="error", error=str(exc))
