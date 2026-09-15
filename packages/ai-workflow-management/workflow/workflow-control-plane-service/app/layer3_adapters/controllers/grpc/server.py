"""gRPC server factory for the Control Plane service.

Creates an ``grpc.aio.Server`` with all CP-side servicers registered.
Run alongside the FastAPI/ASGI server on a separate port.
"""
from __future__ import annotations

import logging
from typing import Any

import grpc
from grpc import aio as grpc_aio

from workflow_proto.v1 import task_result_pb2_grpc

from app.layer3_adapters.controllers.grpc.task_result_servicer import TaskResultServicer

logger = logging.getLogger(__name__)


async def create_grpc_server(
    container: dict[str, Any],
    port: int = 50051,
    max_workers: int = 10,
) -> grpc_aio.Server:
    """Build, configure and return a gRPC async server (not yet started)."""
    server = grpc_aio.server(
        options=[
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
        ],
    )

    # --- Register servicers ---
    task_result_pb2_grpc.add_TaskResultServiceServicer_to_server(
        TaskResultServicer(container), server,
    )

    bind_addr = f"[::]:{port}"
    server.add_insecure_port(bind_addr)
    logger.info("CP gRPC server configured on %s", bind_addr)
    return server
