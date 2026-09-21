from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.step import ResolvedCapability


class ICapabilityResolver(Protocol):
    async def resolve(self, execute_by: str) -> ResolvedCapability | None: ...
