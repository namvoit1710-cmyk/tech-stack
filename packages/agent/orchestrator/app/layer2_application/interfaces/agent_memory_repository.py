"""Port for the orchestrator-owned long-term memory store (SA-1436)."""
from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.agent_memory import (
    AgentMemory,
    MemoryMatch,
    MemoryQuery,
)


class IAgentMemoryRepository(Protocol):
    def save(self, memory: AgentMemory) -> AgentMemory: ...

    def search(self, query: MemoryQuery) -> list[MemoryMatch]: ...

    def find_similar(
        self,
        *,
        tenant_id: str,
        user_id: str,
        content: str,
        memory_type: str,
        limit: int = 5,
    ) -> list[AgentMemory]: ...

    def mark_accessed(self, memory_ids: list[str], *, tenant_id: str = "") -> None: ...

    def forget(self, memory_id: str, *, reason: str = "", tenant_id: str = "") -> None: ...

    def save_event(
        self,
        *,
        memory_id: str,
        tenant_id: str,
        user_id: str,
        event_type: str,
        reason: str = "",
        payload: dict | None = None,
    ) -> None: ...
