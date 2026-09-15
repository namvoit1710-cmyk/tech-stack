"""gRPC server factory for the Worker Executor service."""
from __future__ import annotations

import logging
from typing import Any

from grpc import aio as grpc_aio

from workflow_proto.v1 import task_dispatch_pb2_grpc, worker_registry_pb2_grpc

from app.layer3_adapters.controllers.grpc.task_dispatch_servicer import TaskDispatchServicer
from app.layer3_adapters.controllers.grpc.worker_registry_servicer import WorkerRegistryServicer

logger = logging.getLogger(__name__)


async def create_grpc_server(
    container: dict[str, Any],
    port: int = 50052,
    max_workers: int = 10,
) -> grpc_aio.Server:
    """Build, configure and return a gRPC async server (not yet started)."""
    server = grpc_aio.server(
        options=[
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
        ],
    )

    task_dispatch_pb2_grpc.add_TaskDispatchServiceServicer_to_server(
        TaskDispatchServicer(container), server,
    )
    worker_registry_pb2_grpc.add_WorkerRegistryServiceServicer_to_server(
        WorkerRegistryServicer(container), server,
    )

    bind_addr = f"[::]:{port}"
    server.add_insecure_port(bind_addr)
    logger.info("Executor gRPC server configured on %s", bind_addr)
    return server
