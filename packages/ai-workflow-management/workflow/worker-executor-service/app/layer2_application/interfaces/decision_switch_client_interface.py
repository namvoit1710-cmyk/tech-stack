from typing import Protocol, Any


class IDecisionSwitchClient(Protocol):
    def dispatch_task(self, task: Any) -> dict: ...
