import importlib
import inspect
import json
import logging
from typing import Any

from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)
from agent_sdk.layer4_frameworks.messaging.protos import (
    push_gateway_pb2,
    push_gateway_pb2_grpc,
)

_grpc: Any
_grpc: Any | None
try:
    _grpc = importlib.import_module("grpc")
except ImportError:
    _grpc = None

grpc: Any | None = _grpc

logger = logging.getLogger(__name__)


class GrpcPushGatewayNotifier(PushGatewayNotifierProtocol):
    def __init__(self, target: str) -> None:
        self._target = target
        self._channel = None
        self._stub = None

    def _ensure_stub(self):
        if grpc is None:
            raise RuntimeError("grpcio is required for gRPC push gateway transport")

        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(self._target)

        if self._stub is None:
            self._stub = push_gateway_pb2_grpc.PushGatewayStub(self._channel)

        return self._stub

    async def send_notification(
        self, key: str, data: Any, *, is_final: bool = False
    ) -> None:
        try:
            serialized_data = json.dumps(data) if isinstance(data, dict) else str(data)
            request = push_gateway_pb2.SendDataRequest(
                key=key,
                data=serialized_data,
                is_final=is_final,
            )
            stub = self._ensure_stub()
            await stub.SendData(request)
        except Exception as exc:
            logger.warning("Push gateway notification failed for key=%s: %s", key, exc)

    async def close(self) -> None:
        if self._channel is not None:
            try:
                close_result = self._channel.close()
                if inspect.isawaitable(close_result):
                    await close_result
            finally:
                self._channel = None
                self._stub = None
            logger.info("gRPC push gateway notifier closed")
