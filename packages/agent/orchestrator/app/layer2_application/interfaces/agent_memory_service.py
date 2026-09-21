"""Port for the orchestrator's long-term memory policy owner (SA-1436).

The use case depends on this interface, not on the concrete ``AgentMemoryService``,
so the composition seam is type-checked (no ``Any``) and the policy owner is
substitutable. Storage lives behind ``IAgentMemoryRepository``; policy lives here.
"""
from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.agent_memory import (
    AgentMemory,
    AgentMemoryScope,
    AgentMemoryType,
    MemoryCandidate,
    MemoryMatch,
)


class IAgentMemoryService(Protocol):
    def recall(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str = "",
        workflow_id: str = "",
        query_text: str,
        top_k: int = 5,
        memory_types: list[AgentMemoryType] | None = None,
        scopes: list[AgentMemoryScope] | None = None,
    ) -> list[MemoryMatch]: ...

    def save_candidates(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        workflow_id: str = "",
        conv_id: str,
        candidates: list[MemoryCandidate],
    ) -> list[AgentMemory]: ...

    def save_explicit(
        self,
        *,
        tenant_id: str,
        user_id: str,
        conv_id: str,
        text: str,
    ) -> list[AgentMemory]: ...

    def forget_matching(
        self,
        *,
        tenant_id: str,
        user_id: str,
        text: str,
    ) -> list[str]: ...

    def list_active(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
    ) -> list[MemoryMatch]: ...

    def forget(
        self,
        *,
        memory_id: str,
        tenant_id: str,
        user_id: str,
        reason: str,
    ) -> None: ...
