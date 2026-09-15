"""HTTP fallback callback publisher for workflow completion notifications.

Used in mock/dev mode when no Kafka broker is available.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from app.layer2_application.interfaces.callback_publisher_interface import ICallbackPublisher

logger = logging.getLogger(__name__)

_DEFAULT_MAX_WORKERS = 5


class HttpCallbackPublisher(ICallbackPublisher):
    def __init__(
        self,
        agent_service_url: str,
        timeout: float = 30.0,
        max_workers: int = _DEFAULT_MAX_WORKERS,
    ) -> None:
        self._agent_url = agent_service_url.rstrip("/")
        self._timeout = timeout
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="http-callback",
        )
        self._client = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max_workers,
                max_keepalive_connections=max_workers,
            ),
        )

    def publish(self, topic: str, message: dict, key: str = "") -> None:
        self._executor.submit(self._post_callback, message)

    def _post_callback(self, message: dict) -> None:
        url = f"{self._agent_url}/callbacks/workflow-completion"
        correlation_id = message.get("correlation_id", "?")
        try:
            response = self._client.post(url, json=message)
            response.raise_for_status()
            logger.info(
                "HttpCallbackPublisher | posted callback | url=%s | "
                "correlation_id=%s | status=%d",
                url, correlation_id, response.status_code,
            )
        except Exception as exc:
            logger.error(
                "HttpCallbackPublisher | failed to post callback | "
                "correlation_id=%s | error=%s",
                correlation_id, exc,
            )

    def close(self) -> None:
        """Shutdown executor and close HTTP client."""
        self._executor.shutdown(wait=False)
        self._client.close()
