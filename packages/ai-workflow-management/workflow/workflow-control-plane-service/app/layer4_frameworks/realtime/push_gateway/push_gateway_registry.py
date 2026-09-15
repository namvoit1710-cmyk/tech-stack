"""Push Gateway client registry — register transport implementations at import time.

Add a new transport (e.g. WebSocket) by implementing ``IPushGatewayClient``
and calling ``register_client("websocket", WebSocketPushGatewayClient)``
in ``__init__.py``.  Existing transports stay untouched.
"""
from __future__ import annotations

import logging
from typing import Protocol, Any

logger = logging.getLogger(__name__)


class IPushGatewayClient(Protocol):
    """Contract that every Push Gateway transport must satisfy."""

    provider_name: str

    async def send_event(
        self,
        key: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        is_final: bool = False,
    ) -> bool: ...

    async def close(self) -> None: ...


class PushGatewayClientRegistry:
    """Name → class mapping.  Instantiation is deferred to ``create``."""

    def __init__(self) -> None:
        self._clients: dict[str, type] = {}

    def register(self, name: str, client_class: type) -> None:
        self._clients[name] = client_class
        logger.debug("Registered Push Gateway client: %s", name)

    def create(self, name: str, **kwargs: Any) -> IPushGatewayClient:
        if name not in self._clients:
            available = ", ".join(self._clients.keys()) or "(none)"
            raise ValueError(
                f"Unknown Push Gateway client '{name}'. Available: {available}"
            )
        return self._clients[name](**kwargs)

    def list_providers(self) -> list[str]:
        return list(self._clients.keys())


# ── module-level helpers ──────────────────────────────────────────

_registry = PushGatewayClientRegistry()


def register_client(name: str, client_class: type) -> None:
    _registry.register(name, client_class)


def create_client(name: str, **kwargs: Any) -> IPushGatewayClient:
    return _registry.create(name, **kwargs)


def get_registry() -> PushGatewayClientRegistry:
    return _registry