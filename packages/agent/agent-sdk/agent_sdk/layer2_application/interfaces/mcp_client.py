from __future__ import annotations

from typing import Any, Protocol


class IMCPClientService(Protocol):
    async def get_tools(self) -> list[Any]: ...

    async def get_filtered_tools(self, tool_names: list[str]) -> list[Any]: ...
