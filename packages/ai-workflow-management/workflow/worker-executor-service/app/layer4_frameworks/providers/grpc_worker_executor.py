"""gRPC worker executor — dispatches tasks to workers via gRPC.

Drop-in replacement for ``HttpWorkerExecutor``.
Implements ``IWorkerExecutor``.
"""
from __future__ import annotations

import logging
from typing import Any

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict

from workflow_proto.v1 import worker_execution_pb2 as pb
from workflow_proto.v1 import worker_execution_pb2_grpc as pb_grpc

from app.layer2_application.interfaces.worker_executor_interface import IWorkerExecutor

logger = logging.getLogger(__name__)


def _dict_to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


class GrpcWorkerExecutor(IWorkerExecutor):
    """Executes tasks on a remote worker via gRPC."""

    def __init__(self, worker_grpc_target: str) -> None:
        self._target = worker_grpc_target

    def _stub(self) -> pb_grpc.WorkerExecutionServiceStub:
        channel = grpc.insecure_channel(self._target)
        return pb_grpc.WorkerExecutionServiceStub(channel)

    def execute(self, task_data: dict[str, Any]) -> dict[str, Any]:
        """Unary RPC — blocks until the worker finishes."""
        request = pb.ExecuteRequest(
            task_id=task_data.get("task_id", ""),
            action=task_data.get("action", "execute"),
            inputs=_dict_to_struct(task_data.get("inputs", {})),
            parameters=_dict_to_struct(task_data.get("parameters", {})),
            correlation_id=task_data.get("correlation_id", ""),
        )
        response = self._stub().Execute(request, timeout=120.0)
        outputs = MessageToDict(response.outputs) if response.HasField("outputs") else {}
        return {
            "task_id": response.task_id,
            "status": response.status,
            "outputs": outputs,
            "error": response.error,
            "duration_ms": response.duration_ms,
            "output_reference": response.output_reference,
        }

    def dispatch(self, task_data: dict[str, Any]) -> dict[str, Any]:
        """Server-streaming RPC — replaces the async callback pattern.

        Opens a stream to the worker, collects progress events, and returns
        the final result. No callback URL needed.
        """
        request = pb.ExecuteRequest(
            task_id=task_data.get("task_id", ""),
            action=task_data.get("action", "execute"),
            inputs=_dict_to_struct(task_data.get("inputs", {})),
            parameters=_dict_to_struct(task_data.get("parameters", {})),
            correlation_id=task_data.get("correlation_id", ""),
        )

        final_result: dict[str, Any] = {
            "task_id": task_data.get("task_id", ""),
            "status": "DISPATCHED",
        }

        try:
            for event in self._stub().ExecuteWithProgress(request, timeout=120.0):
                if event.event_type == "result" and event.result:
                    outputs = MessageToDict(event.result.outputs) if event.result.HasField("outputs") else {}
                    final_result = {
                        "task_id": event.result.task_id,
                        "status": event.result.status,
                        "outputs": outputs,
                        "error": event.result.error,
                        "duration_ms": event.result.duration_ms,
                        "output_reference": event.result.output_reference,
                    }
                else:
                    logger.debug(
                        "Progress task=%s pct=%.1f msg=%s",
                        event.task_id, event.progress_pct, event.message,
                    )
        except grpc.RpcError as exc:
            logger.error("gRPC ExecuteWithProgress failed: %s", exc)
            final_result["status"] = "ERROR"
            final_result["error"] = str(exc)

        return final_result
