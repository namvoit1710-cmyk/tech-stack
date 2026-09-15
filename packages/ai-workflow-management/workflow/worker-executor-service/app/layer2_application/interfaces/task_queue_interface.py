from typing import Protocol, Any
from collections.abc import Callable


class ITaskQueue(Protocol):
    def consume(self, callback: Callable) -> None: ...
    def acknowledge(self, message: Any) -> None: ...
