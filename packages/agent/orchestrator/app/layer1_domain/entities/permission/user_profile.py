"""User profile entity (ADR-005).

Maintains user expertise level, communication preferences, and
behavioural metadata derived from journey history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class ExpertiseLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


@dataclass
class PrivacySettings:
    """Granular privacy controls for user data collection."""

    track_journey: bool = True
    track_preferences: bool = True
    track_errors: bool = True
    retention_days: int = 90
    anonymize_after_days: Optional[int] = None


@dataclass
class UserProfile:
    """Aggregated user profile built from journey history."""

    user_id: str
    tenant_id: str
    expertise_level: ExpertiseLevel = ExpertiseLevel.BEGINNER
    preferred_domains: list[str] = field(default_factory=list)
    interaction_count: int = 0
    successful_workflows: int = 0
    failed_workflows: int = 0
    most_used_agents: list[str] = field(default_factory=list)
    privacy_settings: PrivacySettings = field(default_factory=PrivacySettings)
    preferences: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def update_expertise(self) -> None:
        """Recalculate expertise level based on interaction metrics."""
        total = self.interaction_count
        if total >= 200 and self.successful_workflows >= 50:
            self.expertise_level = ExpertiseLevel.EXPERT
        elif total >= 50 and self.successful_workflows >= 15:
            self.expertise_level = ExpertiseLevel.ADVANCED
        elif total >= 10:
            self.expertise_level = ExpertiseLevel.INTERMEDIATE
        else:
            self.expertise_level = ExpertiseLevel.BEGINNER
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def record_interaction(self, agent_id: Optional[str] = None) -> None:
        """Record an interaction and update agent usage stats."""
        self.interaction_count += 1
        if agent_id and agent_id not in self.most_used_agents:
            self.most_used_agents.append(agent_id)
            # Keep only top 10
            if len(self.most_used_agents) > 10:
                self.most_used_agents = self.most_used_agents[-10:]
        self.update_expertise()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "expertise_level": self.expertise_level.value,
            "preferred_domains": self.preferred_domains,
            "interaction_count": self.interaction_count,
            "successful_workflows": self.successful_workflows,
            "failed_workflows": self.failed_workflows,
            "most_used_agents": self.most_used_agents,
            "privacy_settings": {
                "track_journey": self.privacy_settings.track_journey,
                "track_preferences": self.privacy_settings.track_preferences,
                "track_errors": self.privacy_settings.track_errors,
                "retention_days": self.privacy_settings.retention_days,
                "anonymize_after_days": self.privacy_settings.anonymize_after_days,
            },
            "preferences": self.preferences,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
