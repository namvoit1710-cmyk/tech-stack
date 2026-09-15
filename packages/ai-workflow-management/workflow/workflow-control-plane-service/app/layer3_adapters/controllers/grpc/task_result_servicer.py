"""gRPC servicer for TaskResultService.

Served by the Control Plane. Called by the Worker Executor to report
task completion or failure (replaces HTTP POST /runs/.../complete|fail).
"""
from __future__ import annotations

import logging
from typing import Any

import grpc

from workflow_proto.v1 import task_result_pb2 as pb
from workflow_proto.v1 import task_result_pb2_grpc as pb_grpc
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)


class TaskResultServicer(pb_grpc.TaskResultServiceServicer):
    """Delegates incoming gRPC calls to the existing use cases."""

    def __init__(self, container: dict[str, Any]) -> None:
        self._container = container

    async def ReportCompleted(self, request: pb.TaskCompletedRequest, context: grpc.aio.ServicerContext) -> Any:
        complete_task_uc = self._container.get("complete_task")
        if complete_task_uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "complete_task use case not available")

        output_metadata = MessageToDict(request.output_metadata) if request.HasField("output_metadata") else {}

        from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import CompleteTaskCommand
        command = CompleteTaskCommand(
            run_id=request.run_id,
            task_id=request.task_id,
            status="COMPLETED",
            output_reference=request.output_reference,
            output_metadata=output_metadata,
        )

        result = await complete_task_uc.execute(command)
        logger.info("gRPC ReportCompleted run=%s task=%s error=%s", request.run_id, request.task_id, result.error)

        return pb.TaskCompletedResponse(
            completed=result.error == "",
            status=result.status if hasattr(result, "status") else "COMPLETED",
        )

    async def ReportFailed(self, request: pb.TaskFailedRequest, context: grpc.aio.ServicerContext) -> Any:
        fail_task_uc = self._container.get("fail_task")
        if fail_task_uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "fail_task use case not available")

        from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import FailTaskCommand
        outputs = MessageToDict(request.outputs) if request.HasField("outputs") else {}
        command = FailTaskCommand(
            run_id=request.run_id,
            task_id=request.task_id,
            error={
                "code": request.error_code,
                "message": request.error_message,
                "retryable": request.retryable,
            },
            outputs=outputs,
        )

        result = await fail_task_uc.execute(command)
        logger.info("gRPC ReportFailed run=%s task=%s", request.run_id, request.task_id)

        return pb.TaskFailedResponse(
            accepted=result.error == "" if hasattr(result, "error") else True,
            status="FAILED",
        )
