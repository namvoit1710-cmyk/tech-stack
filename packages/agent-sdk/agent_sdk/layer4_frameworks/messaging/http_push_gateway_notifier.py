import json
import logging
from typing import Any

import httpx

from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SEC = 5


class HttpPushGatewayNotifier(PushGatewayNotifierProtocol):
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC)
        return self._client

    async def send_notification(
        self, key: str, data: Any, *, is_final: bool = False
    ) -> None:
        try:
            client = self._ensure_client()
            serialized_data = json.dumps(data) if isinstance(data, dict) else str(data)
            payload = {"key": key, "data": serialized_data, "is_final": is_final}
            response = await client.post(
                f"{self._base_url}/v1/pushgateway/send",
                json=payload,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Push gateway notification failed for key=%s: %s", key, exc)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
            logger.info("HTTP push gateway notifier closed")
