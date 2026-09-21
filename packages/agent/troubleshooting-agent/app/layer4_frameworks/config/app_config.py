from pydantic_settings import SettingsConfigDict

from agent_sdk import Settings as _BaseSettings


class Settings(_BaseSettings):
    model_config = SettingsConfigDict(extra="allow")

    APP_NAME: str = "Troubleshooting Workflow Agent"
    AGENT_TYPE: str = "troubleshooting-agent"
    AGENT_DOMAIN: str = "workflow-ops"
    DESCRIPTION: str = (
        "Diagnoses Workflow Service run failures from the control-plane execution log "
        "(SA-1438). Diagnose-only: reads run history, never mutates the control plane."
    )
    SERVER_PORT: int = 36010
    CAPABILITIES: list = ["workflow-troubleshooting", "tools"]
    DEFAULT_QUEUE_NAME: str = "workflow.troubleshoot.queue"
    DEFAULT_QUEUE_TYPE: str = "event"

    # Offline by default: no registry sync unless REGISTRY_URL is explicitly set.
    REGISTRY_URL: str = ""
    REGISTRY_API_URL: str = ""
    # Root URL of the workflow-control-plane-service; empty → client returns an empty log.
    WORKFLOW_API_URL: str = ""
    # Opt-in offline fallback: serve a canned failed-run when WORKFLOW_API_URL is unset.
    # Off in real deploys so a missing URL fails loudly instead of returning fake data.
    WORKFLOW_API_MOCK: bool = False
    # Path for the diagnosis cache (A==B replay). Empty → in-memory only (per-process).
    DIAGNOSIS_CACHE_PATH: str = ""

    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = ""


settings = Settings()
