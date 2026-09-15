from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    APP_NAME: str = "Agent Worker"
    WORKER_TYPE: str = "agent"
    WORKER_NAME: str = "AI Agent"
    WORKER_DESCRIPTION: str = "Integrates with AI providers (OpenAI) for chat completions."
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "Bot"
    WORKER_COLOR: str = "#8B5CF6"
    WORKER_TAGS: str = "ai,agent,llm,openai"


settings = Settings()
