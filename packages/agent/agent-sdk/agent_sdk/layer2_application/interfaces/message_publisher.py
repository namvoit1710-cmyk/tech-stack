from typing import Optional, Protocol


class IMessagePublisher(Protocol):
    async def publish(
        self, topic: str, message: dict, key: Optional[str] = None
    ) -> None: ...

    async def close(self) -> None: ...
