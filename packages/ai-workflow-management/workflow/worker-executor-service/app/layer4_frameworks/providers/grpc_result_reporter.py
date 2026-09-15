"""gRPC result reporter — calls back to CP via gRPC.

Drop-in replacement for ``HttpResultReporter``.
Implements ``IResultReporter``.
"""
from __future__ import annotations

import logging
from typing import Any

import grpc
from google.protobuf import struct_pb2

from workflow_proto.v1 import task_result_pb2 as pb
from workflow_proto.v1 import task_result_pb2_grpc as pb_grpc

from app.layer2_application.interfaces.result_reporter_interface import IResultReporter

logger = logging.getLogger(__name__)


def _dict_to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


class GrpcResultReporter(IResultReporter):
    """Reports task results to the Control Plane via gRPC."""

    def __init__(self, grpc_target: str) -> None:
        self._target = grpc_target

    def _stub(self) -> pb_grpc.TaskResultServiceStub:
        channel = grpc.insecure_channel(self._target)
        return pb_grpc.TaskResultServiceStub(channel)

    @staticmethod
    def _set_fence(request: Any, run_generation: str, idempotency_key: str) -> None:
        # Additive proto fields — set only if the regenerated stub has them (guards
        # a stale stub), and only if supplied (push callers pass nothing → unchanged).
        fields = request.DESCRIPTOR.fields_by_name
        if run_generation and "run_generation" in fields:
            request.run_generation = run_generation
        if idempotency_key and "idempotency_key" in fields:
            request.idempotency_key = idempotency_key

    def report_completed(
        self,
        run_id: str,
        task_id: str,
        output_reference: str = "",
        output_metadata: dict[str, Any] | None = None,
        run_generation: str = "",
        idempotency_key: str = "",
    ) -> dict:
        try:
            request = pb.TaskCompletedRequest(
                run_id=run_id,
                task_id=task_id,
                output_reference=output_reference,
                output_metadata=_dict_to_struct(output_metadata or {}),
            )
            self._set_fence(request, run_generation, idempotency_key)
            response = self._stub().ReportCompleted(request, timeout=30.0)
            logger.info("gRPC ReportCompleted run=%s task=%s", run_id, task_id)
            return {"completed": response.completed, "status": response.status}
        except Exception as exc:
            logger.error("gRPC ReportCompleted failed: %s", exc)
            raise

    def report_failed(
        self,
        run_id: str,
        task_id: str,
        error: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        run_generation: str = "",
        idempotency_key: str = "",
    ) -> dict:
        try:
            err = error or {}
            request = pb.TaskFailedRequest(
                run_id=run_id,
                task_id=task_id,
                error_code=err.get("code", ""),
                error_message=err.get("message", ""),
                retryable=err.get("retryable", False),
                outputs=_dict_to_struct(outputs or {}),
            )
            self._set_fence(request, run_generation, idempotency_key)
            response = self._stub().ReportFailed(request, timeout=30.0)
            logger.info("gRPC ReportFailed run=%s task=%s", run_id, task_id)
            return {"accepted": response.accepted, "status": response.status}
        except Exception as exc:
            logger.error("gRPC ReportFailed failed: %s", exc)
            raise
