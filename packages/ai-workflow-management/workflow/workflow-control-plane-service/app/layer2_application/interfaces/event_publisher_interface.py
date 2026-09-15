from typing import Any, Protocol


class IEventPublisher(Protocol):
    def publish(self, topic: str, event: Any) -> None: ...
