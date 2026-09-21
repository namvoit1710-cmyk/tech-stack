"""Context enricher domain service (ADR-005).

Builds an EnrichedContext from user profile and journey history
to enhance intent classification and agent selection with
user-specific context.
"""

from __future__ import annotations


from app.layer1_domain.entities.data_preprocesser.enriched_context import EnrichedContext, Suggestion
from app.layer1_domain.entities.permission.user_profile import ExpertiseLevel, UserProfile


class ContextEnricherService:
    """Builds enriched context from user profile and history."""

    def __init__(
        self,
        user_profile_repo,
        logger,
    ) -> None:
        self._profile_repo = user_profile_repo
        self._logger = logger

    def enrich(
        self,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
    ) -> EnrichedContext:
        """Build enriched context for a user request."""
        profile = self._profile_repo.get(user_id, tenant_id)
        if profile is None:
            return EnrichedContext(user_id=user_id)

        # TODO: enrich other infos

        return EnrichedContext(
            user_id=user_id,
            expertise_level=profile.expertise_level,
            preferred_domains=tuple(profile.preferred_domains),
            recent_agents=tuple(profile.most_used_agents[:5]),
            recent_intents=tuple(""),
            interaction_count=profile.interaction_count,
        )
