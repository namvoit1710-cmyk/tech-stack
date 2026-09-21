from typing import Protocol


class IOutputGuard(Protocol):
    def validate(self, response: dict, **context) -> dict: ...
