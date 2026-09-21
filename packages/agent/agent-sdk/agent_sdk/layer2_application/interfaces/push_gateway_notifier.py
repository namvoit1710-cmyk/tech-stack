from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PushGatewayNotifierProtocol(Protocol):
    async def send_notification(
        self, key: str, data: Any, *, is_final: bool = False
    ) -> None: ...

    async def close(self) -> None: ...
