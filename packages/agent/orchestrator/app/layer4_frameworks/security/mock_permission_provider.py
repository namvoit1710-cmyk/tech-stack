class MockPermissionProvider:
    """Returns default permissions for any user."""

    def get_user_permissions(self, user_id: str) -> dict:
        return {
            "roles": ["user", "viewer"],
            "allowed_agents": [],
            "subscription_tier": "standard",
            "resource_quotas": {
                "max_requests_per_hour": 100,
            },
        }

    def get_user_features(self, user_email: str, authorization_header: str = "") -> dict:
        return {
            "email": user_email,
            "features": ["basic_chat", "workflow_access"],
            "feature_flags": {
                "beta_tools": False,
                "priority_support": False,
            },
        }
