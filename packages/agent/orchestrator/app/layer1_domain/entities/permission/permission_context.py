from dataclasses import dataclass, field


@dataclass(frozen=True)
class PermissionContext:
    user_id: str
    roles: tuple[str, ...] = ()
    allowed_agents: tuple[str, ...] = ()
    subscription_tier: str = "free"
    resource_quotas: dict = field(default_factory=dict)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def can_access_agent(self, agent_id: str) -> bool:
        if not self.allowed_agents:
            return True
        return agent_id in self.allowed_agents
