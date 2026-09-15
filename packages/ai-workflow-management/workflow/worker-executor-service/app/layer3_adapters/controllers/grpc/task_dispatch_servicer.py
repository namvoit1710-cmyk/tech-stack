"""gRPC servicer for TaskDispatchService.

Served by the Worker Executor. Called by the Control Plane to dispatch
tasks (replaces HTTP POST /api/v1/tasks/execute).
"""
from __future__ import annotations

import logging
from typing import Any

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf import struct_pb2

from workflow_proto.v1 import task_dispatch_pb2 as pb
from workflow_proto.v1 import task_dispatch_pb2_grpc as pb_grpc
from workflow_proto.v1 import common_pb2

logger = logging.getLogger(__name__)


def _dict_to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


class TaskDispatchServicer(pb_grpc.TaskDispatchServiceServicer):
    """Delegates incoming gRPC task dispatch to ExecuteTaskUseCase."""

    def __init__(self, container: dict[str, Any]) -> None:
        self._container = container

    async def DispatchTask(self, request: pb.DispatchTaskRequest, context: grpc.aio.ServicerContext) -> Any:
        execute_task_uc = self._container.get("execute_task")
        if execute_task_uc is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "execute_task use case not available")

        input_data = MessageToDict(request.input) if request.HasField("input") else {}
        node_config = MessageToDict(request.node_config) if request.HasField("node_config") else {}

        from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import ExecuteTaskInput
        task_input = ExecuteTaskInput(
            task_id=request.task_id,
            run_id=request.run_id,
            node_id=request.node_id,
            worker_type=request.worker_type,
            payload=input_data,
            node_config=node_config,
            idempotency_key=request.idempotency_key,
        )

        result = execute_task_uc.execute(task_input)
        logger.info("gRPC DispatchTask task=%s status=%s", request.task_id, result.get("status", "?"))

        output_metadata = result.get("output_metadata", {})

        status_map = {
            "COMPLETED": common_pb2.TASK_STATUS_COMPLETED,
            "FAILED": common_pb2.TASK_STATUS_FAILED,
            "DISPATCHED": common_pb2.TASK_STATUS_DISPATCHED,
        }

        return pb.DispatchTaskResponse(
            task_id=request.task_id,
            run_id=request.run_id,
            status=status_map.get(result.get("status", ""), common_pb2.TASK_STATUS_UNSPECIFIED),
            output_reference=result.get("output_reference", ""),
            output_metadata=_dict_to_struct(output_metadata) if output_metadata else struct_pb2.Struct(),
            execution_time_ms=int(result.get("execution_time_ms", 0)),
            error_message=result.get("error_message", result.get("error", "")),
        )

    async def DispatchTaskStream(self, request: pb.DispatchTaskRequest, context: grpc.aio.ServicerContext) -> Any:
        """Server-streaming: dispatch and yield progress until completion."""
        # Start with a progress event
        yield pb.TaskProgressEvent(
            task_id=request.task_id,
            event_type="progress",
            progress_pct=0.0,
            message="Task accepted",
        )

        # Delegate to the unary path for the actual execution
        response = await self.DispatchTask(request, context)

        # Yield the final result as a completed event
        event_type = "completed" if response.status == common_pb2.TASK_STATUS_COMPLETED else "failed"
        yield pb.TaskProgressEvent(
            task_id=request.task_id,
            event_type=event_type,
            progress_pct=1.0,
            message=response.error_message or "Done",
            data=response.output_metadata,
        )
