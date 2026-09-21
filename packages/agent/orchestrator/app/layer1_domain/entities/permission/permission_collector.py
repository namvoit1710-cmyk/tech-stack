from app.layer1_domain.entities.permission.permission_context import PermissionContext


class PermissionCollector:
    """Builds an immutable PermissionContext from raw permission data."""

    @staticmethod
    def collect(user_id: str, raw_permissions: dict) -> PermissionContext:
        roles = tuple(raw_permissions.get("roles", []))
        allowed_agents = tuple(raw_permissions.get("allowed_agents", []))
        subscription_tier = raw_permissions.get("subscription_tier", "free")

        quotas = raw_permissions.get("resource_quotas", {})
        if not quotas:
            quotas = _default_quotas_for_tier(subscription_tier)

        return PermissionContext(
            user_id=user_id,
            roles=roles,
            allowed_agents=allowed_agents,
            subscription_tier=subscription_tier,
            resource_quotas=quotas,
        )


def _default_quotas_for_tier(tier: str) -> dict:
    tier_quotas = {
        "free": {"max_requests_per_hour": 10},
        "standard": {"max_requests_per_hour": 100},
        "premium": {"max_requests_per_hour": 1000},
    }
    return tier_quotas.get(tier, tier_quotas["free"])
