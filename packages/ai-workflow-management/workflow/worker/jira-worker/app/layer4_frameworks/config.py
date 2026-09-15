"""Configuration settings for the Jira worker."""

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """Jira worker configuration settings.

    Extends the base worker SDK settings with Jira-specific defaults. Note this
    is a MULTI-TYPE worker: ``WORKER_TYPE`` here is only the process identity /
    single-type fallback; each Jira operation registers its own ``jira_*``
    worker_type via ``app/node_types.py`` (see ``NODE_TYPES``).
    """

    # Worker identity
    APP_NAME: str = "Jira Worker"
    WORKER_NAME: str = "Jira"
    WORKER_TYPE: str = "jira"
    WORKER_DESCRIPTION: str = (
        "Create, read, update, search, transition, comment on and manage Jira "
        "issues via the Jira Cloud REST API v3"
    )

    # Node appearance in the workflow designer (per-node values live in node_types.py)
    WORKER_NODE_CLASS: str = "BUSINESS"
    WORKER_ICON: str = "SquareKanban"
    WORKER_COLOR: str = "#0052CC"  # Atlassian / Jira brand blue
    WORKER_TAGS: str = "jira,atlassian,issue,ticket,project-management"

    # Server configuration — 38000 (ms_teams) and 38001 (email) are taken.
    SERVER_PORT: int = 38002


settings = Settings()
