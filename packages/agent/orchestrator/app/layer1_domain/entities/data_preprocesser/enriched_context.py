"""Enriched context entity (ADR-005).

Carries context-aware information into the orchestration pipeline,
including user expertise, preferred communication style, proactive
suggestions, and recent activity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.permission.user_profile import ExpertiseLevel


@dataclass
class Suggestion:
    """A proactive suggestion based on user history."""

    suggestion_type: str  # "incomplete_workflow" | "recommended_action" | "insight"
    title: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: float = 0.5  # 0.0-1.0


@dataclass(frozen=True)
class EnrichedContext:
    """Immutable context enrichment for a single orchestration request.

    Built by the ContextEnricher from user profile and history,
    then attached to the orchestration state for use by intent
    classification and agent selection.
    """

    user_id: str
    expertise_level: ExpertiseLevel = ExpertiseLevel.BEGINNER
    preferred_domains: tuple[str, ...] = ()
    recent_agents: tuple[str, ...] = ()
    recent_intents: tuple[str, ...] = ()
    interaction_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "expertise_level": self.expertise_level.value,
            "preferred_domains": list(self.preferred_domains),
            "recent_agents": list(self.recent_agents),
            "recent_intents": list(self.recent_intents),
            "interaction_count": self.interaction_count,
        }
