from typing import Protocol, Any


class IResultPublisher(Protocol):
    def publish_result(self, result: Any) -> None: ...
