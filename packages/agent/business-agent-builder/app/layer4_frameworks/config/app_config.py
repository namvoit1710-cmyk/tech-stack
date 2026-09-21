from pydantic_settings import SettingsConfigDict

from agent_sdk import Settings as _BaseSettings


class Settings(_BaseSettings):
    model_config = SettingsConfigDict(extra="allow")

    APP_NAME: str = "Business Agent Builder"
    AGENT_TYPE: str = "business-agent-builder"
    AGENT_DOMAIN: str = "orchestration"
    DESCRIPTION: str = (
        "Creates a business agent from a natural-language prompt (chat box) instead of "
        "manually filling every field. Powers the Agent Hub 'Create via chat' button."
    )
    SERVER_PORT: int = 36011
    CAPABILITIES: list = ["create_business_agent", "chat", "openai"]
    DEFAULT_QUEUE_NAME: str = "agent.builder.queue"
    DEFAULT_QUEUE_TYPE: str = "event"
    REGISTRY_API_URL: str = ""

    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = ""


settings = Settings()
