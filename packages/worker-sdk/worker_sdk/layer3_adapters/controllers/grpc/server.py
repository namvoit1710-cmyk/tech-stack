"""gRPC server factory for Worker SDK."""
from __future__ import annotations

import logging
from typing import Any

from grpc import aio as grpc_aio

from workflow_proto.v1 import worker_execution_pb2_grpc

from worker_sdk.layer3_adapters.controllers.grpc.worker_execution_servicer import WorkerExecutionServicer

logger = logging.getLogger(__name__)


async def create_grpc_server(
    container: dict[str, Any],
    port: int = 50053,
    worker_info: dict[str, Any] | None = None,
) -> grpc_aio.Server:
    """Build, configure and return a gRPC async server for the worker."""
    server = grpc_aio.server(
        options=[
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
        ],
    )

    function_registry = container.get("_dependencies", {}).get("function_registry")
    worker_execution_pb2_grpc.add_WorkerExecutionServiceServicer_to_server(
        WorkerExecutionServicer(container, worker_info=worker_info, function_registry=function_registry), server,
    )

    bind_addr = f"[::]:{port}"
    server.add_insecure_port(bind_addr)
    logger.info("Worker gRPC server configured on %s", bind_addr)
    return server
