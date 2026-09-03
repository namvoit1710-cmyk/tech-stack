from typing import Protocol


class IInputGuard(Protocol):
    def validate(self, message: str, **context) -> dict: ...
