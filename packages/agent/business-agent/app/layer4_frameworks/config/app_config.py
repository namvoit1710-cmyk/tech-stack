from pydantic_settings import SettingsConfigDict

from agent_sdk import Settings as _BaseSettings


class Settings(_BaseSettings):
    model_config = SettingsConfigDict(extra="allow")

    APP_NAME: str = "Business Agent"
    AGENT_TYPE: str = "Business-agent"
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
    AGENT_HUB_API_URL: str = ""

    # HANA RAG service settings
    RAG_API_URL: str = ""
    RAG_RETRIEVAL_QUERY_PATH: str = "/api/v1/retrieval/query"
    RAG_TENANT_ID: str = "local-dev-tenant"
    RAG_TOP_K: int = 10
    RAG_TIMEOUT_SECONDS: float = 30.0
    MESSAGING_MODE: str = "local"  # or "in-memory"
    
    # Kafka settings
    KAFKA_BOOTSTRAP_SERVERS: str = ""
    KAFKA_REQUEST_TOPIC: str =""
    KAFKA_RESPONSE_TOPIC: str = ""
    KAFKA_GROUP_ID: str = ""
    KAFKA_AUTO_OFFSET_RESET: str = ""
    CONSUMER_MAX_IN_FLIGHT_MESSAGES: int = 1

    # Push gateway settings
    PUSH_GATEWAY_URL: str = ""
    PUSH_GATEWAY_TRANSPORT: str = ""

    # LLM settings
    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = ""
    LLM_TEMPERATURE: float = 0.01
    LLM_PROVIDER: str = ""
    LLM_TIMEOUT: float = 60.0
    LLM_MAX_TOKENS: int = 4096
    LLM_MAX_RETRIES: int = 6
    LLM_MODEL_KWARGS: dict = {}
    EXECUTOR_TOPIC: str = "executor.topic"
    BUSINESS_TOPIC: str = "business.topic"
    FILE_CLIENT_URL: str = ""


settings = Settings()
