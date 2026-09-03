from typing import Awaitable, Callable, Protocol

from agent_sdk.layer2_application.interfaces.message_delivery import IMessageDelivery


class IMessageConsumer(Protocol):
    async def start(
        self, handler: Callable[[IMessageDelivery], Awaitable[None]]
    ) -> None: ...

    async def stop(self) -> None: ...
