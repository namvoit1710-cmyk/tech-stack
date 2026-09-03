from __future__ import annotations


class StubLogger:
    def info(self, message: str, *args, **kwargs) -> None:
        pass

    def error(self, message: str, *args, **kwargs) -> None:
        pass

    def warning(self, message: str, *args, **kwargs) -> None:
        pass

    def debug(self, message: str, *args, **kwargs) -> None:
        pass


class StubMonitor:
    def __init__(self) -> None:
        self.tracked: dict[str, float] = {}

    def track(self, metric: str, value: float) -> None:
        self.tracked[metric] = self.tracked.get(metric, 0) + value
