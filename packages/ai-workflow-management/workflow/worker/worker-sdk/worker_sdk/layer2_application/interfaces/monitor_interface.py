from typing import Protocol


class IMonitor(Protocol):
    def track(self, metric: str, value: float) -> None: ...
