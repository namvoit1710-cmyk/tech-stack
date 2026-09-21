"""Pool-visibility filtering for agent read use cases (UC7 available / UC8 all).

Agent reads are scoped by RBAC pool visibility (SA RBAC v1, Task 5). The pre-RBAC
`user_email` / `user_roles` convenience filter was removed in SA-1991 cleanup: it was a
client-supplied narrowing over the legacy owner-tag columns, NOT access control, and only
duplicated/confused the real scoping done here.
"""

from app.layer1_domain.entities.agent import Agent


def filter_agents_by_visibility(
    agents: list[Agent], visible_ids: set[str] | None
) -> list[Agent]:
    """Restrict agents to the pool-visible id set (SA RBAC v1, Task 5).

    `visible_ids is None` means NO restriction — the caller bypasses pool visibility
    (is_super / system / holds `agent.view_all`). A set (possibly empty) restricts to
    agents in the caller's pools.
    """
    if visible_ids is None:
        return agents
    return [a for a in agents if a.id in visible_ids]
