"""Agent pool repository port (SA RBAC v1, Task 4)."""

from typing import Protocol

from app.layer2_application.dtos.rbac_read_models import AgentPoolDetail


class IAgentPoolRepository(Protocol):
    """Persistence contract for agent pools + their agent/member links.

    All reads exclude soft-deleted pools (`status = 'deleted'`). Pools are the aggregate
    root and are SOFT-deleted; the junction rows must be cleared explicitly on delete
    (FK cascade does not fire on a soft delete).
    """

    # ----- pool CRUD -----
    def create_pool(self, name: str, description: str | None, created_by: str | None) -> AgentPoolDetail:
        ...

    def get_pool(self, pool_id: str) -> AgentPoolDetail | None:
        """An active pool by id, or None (excludes soft-deleted)."""
        ...

    def list_pools(self) -> list[AgentPoolDetail]:
        """All active pools."""
        ...

    def list_pools_for_user(self, user_id: str) -> list[AgentPoolDetail]:
        """Active pools the user is a member of."""
        ...

    def update_pool(self, pool_id: str, name: str, description: str | None) -> AgentPoolDetail | None:
        """Update an active pool and return its new state, or None if not found/deleted."""
        ...

    def soft_delete_pool(self, pool_id: str) -> None:
        """Set status='deleted' AND explicitly clear its agent/member link rows."""
        ...

    # ----- membership / agent links (idempotent) -----
    def add_agent(self, pool_id: str, agent_id: str) -> None:
        """Attach an agent (no-op if already attached)."""
        ...

    def remove_agent(self, pool_id: str, agent_id: str) -> None:
        """Detach an agent (no-op if not attached)."""
        ...

    def add_member(self, pool_id: str, user_id: str) -> None:
        """Add a member (no-op if already a member)."""
        ...

    def remove_member(self, pool_id: str, user_id: str) -> None:
        """Remove a member (no-op if not a member)."""
        ...

    def is_member(self, pool_id: str, user_id: str) -> bool:
        ...

    def list_agent_ids(self, pool_id: str) -> list[str]:
        ...

    def list_member_ids(self, pool_id: str) -> list[str]:
        ...

    # ----- visibility (used by T5 agent reads) -----
    def find_agent_ids_visible_to(self, user_id: str) -> set[str]:
        """Ids of agents in any active pool the user is a member of (single JOIN)."""
        ...
