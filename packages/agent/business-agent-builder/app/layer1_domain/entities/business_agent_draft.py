"""Domain entity: a drafted business-agent configuration.

Produced by the builder from a natural-language prompt so the user does not have to
fill every field by hand (SA-1431). The draft mirrors the agent-registry
``RegisterAgentRequest`` contract so ``to_register_payload()`` can be POSTed directly
to ``/api/v1/agents/register`` (or used to pre-fill the Agent Hub builder form).

Layer 1: standard-library only. No pydantic, no framework imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.base_entity import BaseEntity

VALID_KINDS = ("business", "technical")
VALID_STATUSES = ("active", "inactive")
VALID_CONFIG_TYPES = ("default", "custom")

_NAME_MAX_LEN = 255


@dataclass
class BusinessAgentDraft(BaseEntity):
    """A fully-populated business-agent configuration drafted from a prompt."""

    name: str
    description: str
    business: str
    kind: str = "business"
    status: str = "active"
    config_type: str = "custom"
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    knowledge_base: list[str] = field(default_factory=list)
    model: str | None = None
    provider: str | None = None
    temperature: float | None = None
    is_published: bool = False
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        self.name = (self.name or "").strip()
        self.description = (self.description or "").strip()
        self.business = (self.business or "").strip()

        if not self.name:
            raise ValueError("business agent draft requires a non-empty name")
        if len(self.name) > _NAME_MAX_LEN:
            raise ValueError(f"name must be <= {_NAME_MAX_LEN} characters")
        if not self.description:
            raise ValueError("business agent draft requires a non-empty description")
        if not self.business:
            raise ValueError("business agent draft requires a non-empty business context")

        self.kind = self.kind.lower()
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {VALID_KINDS}")

        self.status = self.status.lower()
        if self.status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")

        self.config_type = self.config_type.lower()
        if self.config_type not in VALID_CONFIG_TYPES:
            raise ValueError(f"config_type must be one of {VALID_CONFIG_TYPES}")

        if self.temperature is not None and not (0.0 <= self.temperature <= 1.0):
            raise ValueError("temperature must be between 0.0 and 1.0")

    def to_register_payload(self) -> dict[str, Any]:
        """Serialize to the agent-registry ``RegisterAgentRequest`` shape."""
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "status": self.status,
            "config_type": self.config_type,
            "business": self.business,
            "tools": list(self.tools),
            "workflows": list(self.workflows),
            "agents": list(self.agents),
            "knowledge_base": list(self.knowledge_base),
            "is_published": self.is_published,
            "version": self.version,
        }
        if self.model is not None:
            payload["model"] = self.model
        if self.provider is not None:
            payload["provider"] = self.provider
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.capabilities:
            payload["metadata"] = {"capabilities": list(self.capabilities)}
        return payload
