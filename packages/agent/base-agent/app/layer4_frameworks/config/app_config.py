from pydantic_settings import SettingsConfigDict

from agent_sdk import Settings as _BaseSettings


class Settings(_BaseSettings):
    model_config = SettingsConfigDict(extra="allow")

    APP_NAME: str = "Base Agent"
    AGENT_TYPE: str = "base-agent"
    AGENT_DOMAIN: str = "blueprints"
    DESCRIPTION: str = (
        "Reusable Python agent blueprint with clean layers, graph registration, and SDK integration."
    )
    SERVER_PORT: int = 36001
    CAPABILITIES: list = ["workflow", "tools", "openai"]
    DEFAULT_QUEUE_NAME: str = "workflow.default.queue"
    DEFAULT_QUEUE_TYPE: str = "event"
    REGISTRY_API_URL: str = ""
    WORKFLOW_API_URL: str = ""

    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = ""


settings = Settings()
