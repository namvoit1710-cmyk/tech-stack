"""Configuration settings for MS Teams worker."""

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """MS Teams worker configuration settings.

    Extends the base worker SDK settings with MS Teams-specific defaults.
    """

    # Worker identity
    APP_NAME: str = "MS Teams Worker"
    WORKER_NAME: str = "Microsoft Teams"
    WORKER_TYPE: str = "ms_teams"
    WORKER_DESCRIPTION: str = (
        "Send messages and Adaptive Cards to Microsoft Teams channels via webhooks"
    )

    # Node appearance in workflow designer
    WORKER_NODE_CLASS: str = "BUSINESS"
    WORKER_ICON: str = "MessageSquare"
    WORKER_COLOR: str = "#6264A7"  # Microsoft Teams brand color
    WORKER_TAGS: str = "teams,microsoft,communication,notification,messaging"

    # Server configuration
    SERVER_PORT: int = 38000

    # Webhook defaults (can be overridden via env vars)
    # WEBHOOK_TIMEOUT: float = 30.0


settings = Settings()
