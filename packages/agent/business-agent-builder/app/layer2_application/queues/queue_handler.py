"""Consumer-mode queue handler.

This agent is primarily driven synchronously by the Agent Hub chat box (SERVER mode).
The handler is a no-op stub kept for CONSUMER-mode compatibility with the SDK.
"""

from typing import Any


async def handle_queue_message(message: Any) -> dict:
    return {}
