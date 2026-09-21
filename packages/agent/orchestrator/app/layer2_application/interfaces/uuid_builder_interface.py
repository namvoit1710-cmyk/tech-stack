from typing import Protocol


class IUuidBuilder(Protocol):
    def create_uuid7(self) -> str: ...
