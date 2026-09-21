from __future__ import annotations

from typing import Protocol

from agent_sdk.layer1_domain.entities.tool_registration import ToolRegistration


class IToolRegistry(Protocol):
    async def sync_tools(
        self,
        registrations: list[ToolRegistration],
    ) -> dict[str, str]: ...

    async def activate_tools(self, tool_ids: list[str]) -> None: ...

    async def deactivate_tools(self, tool_ids: list[str]) -> None: ...

    async def close(self) -> None: ...
