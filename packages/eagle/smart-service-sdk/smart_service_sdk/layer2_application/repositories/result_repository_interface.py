from typing import Any, Protocol


class IResultRepository(Protocol):
    async def save_result(self, result: Any) -> Any: ...
