from typing import Protocol


class IIdGenerator(Protocol):
    def generate(self) -> str: ...
