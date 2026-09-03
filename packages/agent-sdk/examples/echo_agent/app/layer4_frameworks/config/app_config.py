from pydantic_settings import SettingsConfigDict

from agent_sdk import Settings as _BaseSettings


class Settings(_BaseSettings):
    model_config = SettingsConfigDict(extra="allow")

    APP_NAME: str = "Echo Agent"
    AGENT_TYPE: str = "echo"
    AGENT_DOMAIN: str = "examples"
    DESCRIPTION: str = (
        "Simple agent that echoes back the user message with HITL and OpenAI."
    )
    SERVER_PORT: int = 36001
    CAPABILITIES: list = ["echo", "hitl", "openai"]
    QUEUE_RESPONSE_MESSAGE_TYPE: str = "echo.response.agent"

    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"


settings = Settings()
