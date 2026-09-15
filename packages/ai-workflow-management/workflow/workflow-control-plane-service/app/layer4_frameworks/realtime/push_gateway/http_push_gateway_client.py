"""HTTP Push Gateway client implementation."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SEC = 5


class HttpPushGatewayClient:
    """Sends events to Push Gateway via HTTP POST."""

    provider_name = "http"

    def __init__(self, base_url: str, timeout: int = REQUEST_TIMEOUT_SEC) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def send_event(
        self,
        key: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        is_final: bool = False,
    ) -> bool:
        try:
            client = self._ensure_client()
            serialized = json.dumps(payload, default=str)
            logger.debug("debug message: %s = %s", key, payload)
            response = await client.post(
                f"{self._base_url}/v1/pushgateway/send",
                json={"key": key, "data": serialized, "is_final": is_final},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "[http] Push Gateway error for key=%s: status=%s",
                key, exc.response.status_code,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("[http] Push Gateway send failed for key=%s: %s", key, exc)
            return False

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
