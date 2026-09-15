"""Realtime infrastructure.

In normal deployments, Socket.IO and Push Gateway consume from the
``realtime.events`` broker topic so per-run ordering is enforced by the
broker's per-key partitioning. In mock/no-broker mode, the publisher falls
back to direct-notify queues inside each realtime consumer.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def create_socketio_client_manager(redis_url: str) -> Any:
    """Create a Socket.IO client manager (Redis pub/sub) for horizontal scaling.

    Returns an ``AsyncRedisManager`` on success, or ``None`` to signal that the
    caller should fall back to the default in-memory client manager.

    A synchronous PING is issued before wiring the async manager so that
    misconfigured URLs / unreachable Redis instances surface at startup
    instead of silently failing on the first publish.
    """
    if not redis_url or redis_url == "redis://localhost:6379":
        logger.info("Socket.IO client manager: in-memory (single-instance)")
        return None

    display = redis_url.split("@")[-1] if "@" in redis_url else redis_url

    try:
        import redis as _redis
        client = _redis.Redis.from_url(redis_url, socket_connect_timeout=5)
        client.ping()
        client.close()
    except Exception as exc:
        logger.warning(
            "Socket.IO client manager: Redis connection failed — "
            "falling back to in-memory (url=%s, error=%s)", display, exc,
        )
        return None

    try:
        import socketio
        manager = socketio.AsyncRedisManager(redis_url)
    except Exception as exc:
        logger.warning(
            "Socket.IO client manager: AsyncRedisManager init failed — "
            "falling back to in-memory: %s", exc,
        )
        return None

    logger.info("Socket.IO client manager: Redis connected (%s)", display)
    return manager
