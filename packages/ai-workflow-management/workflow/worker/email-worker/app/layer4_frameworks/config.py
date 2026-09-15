"""Configuration settings for Email worker."""

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """Email worker configuration settings.

    Extends the base worker SDK settings with e-mail-specific defaults.
    """

    # Worker identity
    APP_NAME: str = "Email Worker"
    WORKER_NAME: str = "E-Mail"
    WORKER_TYPE: str = "email"
    WORKER_DESCRIPTION: str = (
        "Send e-mail notifications via an SMTP relay or Microsoft Graph sendMail"
    )

    # Node appearance in workflow designer
    WORKER_NODE_CLASS: str = "BUSINESS"
    WORKER_ICON: str = "Mail"
    WORKER_COLOR: str = "#0F6CBD"  # Outlook-style e-mail blue
    WORKER_TAGS: str = "email,smtp,graph,outlook,notification,communication,messaging"

    # Server configuration
    SERVER_PORT: int = 38001

    # Microsoft Graph endpoint (override for sovereign clouds / tests)
    GRAPH_BASE_URL: str = "https://graph.microsoft.com/v1.0"


settings = Settings()
