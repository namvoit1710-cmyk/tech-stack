"""Orchestrator push-gateway notifier port.

Mirrors the business-agent IPushGatewayNotifier: high-level send methods that BUILD a
UIEvent, persist it, and push it — so a direct orchestrator reply survives into
conversation history even when no business agent runs. Also stays compatible with the
raw agent_sdk PushGatewayNotifierProtocol (send_notification / close) so it is a drop-in
for any dependency that only expects the raw notifier.
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IPushGatewayNotifier(Protocol):
    # Raw agent_sdk protocol (passthrough; dict payloads from other callers).
    async def send_notification(self, key: str, data: Any, *, is_final: bool = False) -> None: ...
    async def close(self) -> None: ...

    # High-level, persist-then-push (the orchestrator's own replies).
    async def send_chat_response(self, conv_id: str, content: str) -> None: ...
    async def send_chat_thinking(self, conv_id: str, content: str) -> None: ...
    async def send_token_info(
        self, conv_id: str, message_id: str, content: str, status: str
    ) -> None: ...
