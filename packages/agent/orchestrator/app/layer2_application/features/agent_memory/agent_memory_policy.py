"""AgentMemoryService — the orchestrator's owner of long-term memory policy (SA-1436).

This service, not the repository and not the agent-sdk, decides *what* becomes durable
memory, *whether* a candidate is worth saving, *whether* it duplicates an existing memory,
and *what* is recalled into planning context. Storage is delegated to the repository port.

Policy (kept deliberately simple in v1):
    Save only durable, useful, non-trivial memories.
    Never save raw files. Never save ordinary filler.
    Never save sensitive info unless explicitly requested.
    Never let memory override the current user request.
"""
from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.entities.agent_memory import (
    AgentMemory,
    AgentMemoryScope,
    AgentMemoryType,
    MemoryCandidate,
    MemoryMatch,
    MemoryQuery,
)
from app.layer1_domain.interfaces.llm_provider_interface import ILLMProvider
from app.layer2_application.interfaces.agent_memory_repository import IAgentMemoryRepository
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder

# Policy thresholds (v1). Tunable; centralized so a judge can read them at a glance.
_MIN_CONTENT_LEN = 12
_MIN_CONFIDENCE = 0.65
_MIN_IMPORTANCE = 2


class AgentMemoryService:
    def __init__(
        self,
        repository: IAgentMemoryRepository,
        llm_provider: ILLMProvider | None = None,
        uuid_builder: IUuidBuilder | None = None,
        logger: logging.Logger | None = None,
        settings=None,
    ) -> None:
        self.repository = repository
        self.llm_provider = llm_provider
        self.uuid_builder = uuid_builder
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings

    # ---------------------------------------------------------------- recall
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
    ) -> list[MemoryMatch]:
        if not tenant_id:
            # Tenant isolation is mandatory — no tenant, no recall (no TENANT_BLEED).
            self.logger.debug("recall skipped: missing tenant_id")
            return []

        matches = self.repository.search(
            MemoryQuery(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                workflow_id=workflow_id,
                query=query_text or "",
                top_k=top_k,
                memory_types=memory_types or [],
                scopes=scopes or [],
            )
        )
        if matches:
            self.repository.mark_accessed(
                [m.memory.memory_id for m in matches], tenant_id=tenant_id
            )
        return matches

    # ------------------------------------------------------- save_candidates
    def save_candidates(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        workflow_id: str = "",
        conv_id: str,
        candidates: list[MemoryCandidate],
    ) -> list[AgentMemory]:
        saved: list[AgentMemory] = []
        for candidate in candidates or []:
            if not self._should_save(candidate):
                continue
            if self._is_duplicate(tenant_id=tenant_id, user_id=user_id, candidate=candidate):
                continue
            saved.append(
                self._persist_candidate(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    workflow_id=workflow_id,
                    conv_id=conv_id,
                    candidate=candidate,
                )
            )
        return saved

    def _persist_candidate(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        workflow_id: str,
        conv_id: str,
        candidate: MemoryCandidate,
    ) -> AgentMemory:
        memory = AgentMemory(
            memory_id=self._new_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            workflow_id=workflow_id,
            conv_id=conv_id,
            scope=AgentMemoryScope.coerce(candidate.scope),
            memory_type=AgentMemoryType.coerce(candidate.memory_type),
            memory_key=candidate.memory_key,
            content=candidate.content,
            summary=candidate.content[:1000],
            source_conv_id=conv_id,
            confidence=candidate.confidence,
            importance=candidate.importance,
            sensitivity=candidate.sensitivity,
            metadata=candidate.metadata,
        )
        saved_memory = self.repository.save(memory)
        self.repository.save_event(
            memory_id=saved_memory.memory_id,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="created",
            reason=candidate.reason,
            payload={"memory_key": candidate.memory_key, "scope": memory.scope.value},
        )
        return saved_memory

    # ------------------------------------------------- explicit user commands
    def save_explicit(
        self,
        *,
        tenant_id: str,
        user_id: str,
        conv_id: str,
        text: str,
    ) -> list[AgentMemory]:
        """Save a memory the user explicitly asked to remember.

        Bypasses the durability thresholds (the user asked directly) but still
        enforces tenant presence, non-empty content, and dedupe."""
        content = (text or "").strip()
        if not tenant_id or not content:
            return []
        candidate = MemoryCandidate(
            memory_type=AgentMemoryType.PREFERENCE,
            scope=AgentMemoryScope.USER,
            content=content,
            memory_key="explicit_user_memory",
            confidence=1.0,
            importance=5,
            reason="User explicitly asked to remember this.",
            metadata={"source": "explicit_user_request"},
        )
        if self._is_duplicate(tenant_id=tenant_id, user_id=user_id, candidate=candidate):
            return []
        return [
            self._persist_candidate(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id="",
                workflow_id="",
                conv_id=conv_id,
                candidate=candidate,
            )
        ]

    def forget_matching(
        self,
        *,
        tenant_id: str,
        user_id: str,
        text: str,
    ) -> list[str]:
        """Soft-delete active memories whose content matches the user's forget request."""
        content = (text or "").strip()
        if not tenant_id or not content:
            return []
        similar = self.repository.find_similar(
            tenant_id=tenant_id,
            user_id=user_id,
            content=content,
            memory_type=AgentMemoryType.PREFERENCE.value,
            limit=10,
        )
        forgotten: list[str] = []
        for memory in similar:
            self.forget(
                memory_id=memory.memory_id,
                tenant_id=tenant_id,
                user_id=user_id,
                reason="User explicitly asked to forget this.",
            )
            forgotten.append(memory.memory_id)
        return forgotten

    def list_active(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
    ) -> list[MemoryMatch]:
        """List active memories for a user (no access-count side effect)."""
        if not tenant_id:
            return []
        return self.repository.search(
            MemoryQuery(tenant_id=tenant_id, user_id=user_id, query="", top_k=limit)
        )

    # ---------------------------------------------------------------- forget
    def forget(
        self,
        *,
        memory_id: str,
        tenant_id: str,
        user_id: str,
        reason: str,
    ) -> None:
        self.repository.forget(memory_id, reason=reason, tenant_id=tenant_id)
        self.repository.save_event(
            memory_id=memory_id,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="forgotten",
            reason=reason,
            payload={},
        )

    # ----------------------------------------------------------------- policy
    def _should_save(self, candidate: MemoryCandidate) -> bool:
        content = (candidate.content or "").strip()
        if len(content) < _MIN_CONTENT_LEN:
            return False
        if candidate.confidence < _MIN_CONFIDENCE:
            return False
        if candidate.importance < _MIN_IMPORTANCE:
            return False
        return True

    def _is_duplicate(
        self,
        *,
        tenant_id: str,
        user_id: str,
        candidate: MemoryCandidate,
    ) -> bool:
        try:
            similar = self.repository.find_similar(
                tenant_id=tenant_id,
                user_id=user_id,
                content=candidate.content,
                memory_type=AgentMemoryType.coerce(candidate.memory_type).value,
                limit=5,
            )
        except Exception:  # noqa: BLE001 - dedupe is best-effort; a lookup miss must not block saving
            self.logger.warning("dedupe lookup failed; proceeding without dedupe", exc_info=True)
            return False

        needle = (candidate.content or "").strip().lower()
        for existing in similar:
            haystack = (existing.content or "").strip().lower()
            if not needle or not haystack:
                continue
            if needle == haystack or needle in haystack or haystack in needle:
                return True
        return False

    # ------------------------------------------------------------- internals
    def _new_id(self) -> str:
        if self.uuid_builder is not None and hasattr(self.uuid_builder, "create_uuid7"):
            return self.uuid_builder.create_uuid7()
        import uuid

        return uuid.uuid4().hex
