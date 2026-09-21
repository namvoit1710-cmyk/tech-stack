"""Domain model for orchestrator-owned long-term agent memory (SA-1436).

Memory IS durable, reusable knowledge learned from prior interactions that improves
future planning/routing/execution/personalization. Memory IS NOT raw conversation
history, raw file content, chat timeline, execution checkpoints, task status, or RAG
document content — those already exist elsewhere and must not be duplicated here.

The orchestrator owns policy, storage, recall, save, forget, and audit. The business
agent only receives recalled memory as planning context and may emit candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AgentMemoryScope(str, Enum):
    USER = "user"
    TENANT = "tenant"
    AGENT = "agent"
    WORKFLOW = "workflow"
    CONVERSATION = "conversation"

    @classmethod
    def coerce(cls, value: Any, default: "AgentMemoryScope | None" = None) -> "AgentMemoryScope":
        """Safe enum coercion — bad/unknown data never raises (Hopper H5)."""
        default = default or cls.USER
        if isinstance(value, cls):
            return value
        if isinstance(value, str) and value:
            try:
                return cls(value)
            except ValueError:
                return default
        return default


class AgentMemoryType(str, Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"
    PREFERENCE = "preference"

    @classmethod
    def coerce(cls, value: Any, default: "AgentMemoryType | None" = None) -> "AgentMemoryType":
        default = default or cls.SEMANTIC
        if isinstance(value, cls):
            return value
        if isinstance(value, str) and value:
            try:
                return cls(value)
            except ValueError:
                return default
        return default


class AgentMemoryStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"

    @classmethod
    def coerce(cls, value: Any, default: "AgentMemoryStatus | None" = None) -> "AgentMemoryStatus":
        default = default or cls.ACTIVE
        if isinstance(value, cls):
            return value
        if isinstance(value, str) and value:
            try:
                return cls(value)
            except ValueError:
                return default
        return default


@dataclass(slots=True)
class AgentMemory:
    memory_id: str
    tenant_id: str
    scope: AgentMemoryScope
    memory_type: AgentMemoryType
    content: str

    user_id: str = ""
    agent_id: str = ""
    workflow_id: str = ""
    conv_id: str = ""

    memory_key: str = ""
    summary: str = ""

    source_conv_id: str = ""
    source_event_id: str = ""
    source_task_id: str = ""

    confidence: float = 0.8
    importance: int = 3
    sensitivity: str = "normal"
    status: AgentMemoryStatus = AgentMemoryStatus.ACTIVE

    valid_until: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0

    metadata: dict[str, Any] = field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class MemoryQuery:
    tenant_id: str
    query: str

    user_id: str = ""
    agent_id: str = ""
    workflow_id: str = ""

    memory_types: list[AgentMemoryType] = field(default_factory=list)
    scopes: list[AgentMemoryScope] = field(default_factory=list)

    top_k: int = 5


@dataclass(slots=True)
class MemoryMatch:
    memory: AgentMemory
    score: float = 0.0
    reason: str = ""


@dataclass(slots=True)
class MemoryCandidate:
    memory_type: AgentMemoryType
    scope: AgentMemoryScope
    content: str

    memory_key: str = ""
    confidence: float = 0.8
    importance: int = 3
    sensitivity: str = "normal"
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
