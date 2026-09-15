"""Configuration settings for Code worker."""

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """Code worker configuration settings.

    Extends the base worker SDK settings with Code-node-specific defaults.
    """

    # Worker identity
    APP_NAME: str = "Code Worker"
    WORKER_NAME: str = "Code"
    WORKER_TYPE: str = "code"
    WORKER_DESCRIPTION: str = (
        "Run custom JavaScript or Python scripts against the workflow data"
    )

    # Node appearance in workflow designer
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "Braces"  # lucide "{ }" braces icon
    WORKER_COLOR: str = "#8B5CF6"  # Violet — distinguishes the code/script node
    WORKER_TAGS: str = "code,script,javascript,python,technical"

    # Server configuration
    SERVER_PORT: int = 35000


settings = Settings()
