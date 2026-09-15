"""gRPC Push Gateway client implementation.

Uses the Push Gateway's native proto: ``pushgateway.PushGateway/SendData``.
The server expects ``SendDataRequest(key, data, is_final)`` where ``data``
is a JSON string — identical to the HTTP REST body field.
"""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import grpc

from workflow_proto.v1 import push_gateway_pb2 as pb
from workflow_proto.v1 import push_gateway_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SEC = 5


class GrpcPushGatewayClient:
    """Sends events to Push Gateway via gRPC ``SendData`` unary calls."""

    provider_name = "grpc"

    def __init__(
        self,
        target: str,
        timeout: int = REQUEST_TIMEOUT_SEC,
        max_workers: int = 10,
    ) -> None:
        self._target = target
        self._timeout = timeout
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="grpc-push-gw",
        )
        self._channel = grpc.insecure_channel(self._target)
        self._stub = pb_grpc.PushGatewayStub(self._channel)

    async def send_event(
        self,
        key: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        is_final: bool = False,
    ) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._send_sync, key, event_type, payload, is_final,
        )

    def _send_sync(
        self, key: str, event_type: str, payload: dict, is_final: bool,
    ) -> bool:
        try:
            serialized = json.dumps(payload, default=str)

            req = pb.SendDataRequest(
                key=key,
                data=serialized,
                is_final=is_final,
            )
            resp = self._stub.SendData(req, timeout=self._timeout)
            if not resp.success:
                logger.warning(
                    "[grpc] Push Gateway error for key=%s: %s",
                    key, resp.message,
                )
                return False
            logger.info(
                "[grpc] Push Gateway OK key=%s chunk_index=%s is_final=%s msg=%s",
                key, resp.chunk_index, resp.is_final, resp.message,
            )
            return True
        except grpc.RpcError as exc:
            logger.warning(
                "[grpc] Push Gateway RPC error for key=%s: %s",
                key, exc.details() if hasattr(exc, "details") else str(exc),
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("[grpc] Push Gateway send failed for key=%s: %s", key, exc)
            return False

    async def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
        self._executor.shutdown(wait=False)
