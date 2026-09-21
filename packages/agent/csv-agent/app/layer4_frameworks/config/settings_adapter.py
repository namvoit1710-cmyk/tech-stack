from agent_sdk import Settings as _BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(_BaseSettings):
    """CSV Agent configuration.

    Extends the SDK settings so the same object can drive both the CSV-specific
    services and the SDK-native HTTP/queue runtime.
    """

    model_config = SettingsConfigDict(extra="allow")

    # Standard agent metadata
    APP_NAME: str = "CSV Agent"
    AGENT_TYPE: str = "csv-agent"
    AGENT_ID: str = "csv-agent"
    AGENT_VERSION: str = "0.1.0"
    AGENT_DOMAIN: str = "data-processing"
    DESCRIPTION: str = (
        "AI-powered CSV processing agent with natural language interface for "
        "data transformation."
    )
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 36002
    CAPABILITIES: list[str] = [
        "csv_processing",
        "data_transformation",
        "workflow",
        "tools",
        "openai",
    ]

    # SDK-native runtime mode: SERVER, CONSUMER, or HYBRID.
    APP_MODE: str = "SERVER"

    # SDK-native queue metadata.
    MESSAGING_MODE: str = ""
    INFRA_MODE: str = "mock"
    QUEUE_NAME: str = ""
    QUEUE_REQUEST_TOPIC: str = ""
    QUEUE_REPLY_TOPIC: str = ""
    QUEUE_REQUEST_MESSAGE_TYPE: str = ""
    QUEUE_RESPONSE_MESSAGE_TYPE: str = ""
    DELEGATION_REQUEST_TOPIC: str = ""
    DELEGATION_REQUEST_MESSAGE_TYPE: str = ""
    DELEGATION_RESPONSE_MESSAGE_TYPE: str = ""

    # Kafka local mode defaults.
    KAFKA_GROUP_ID: str = ""
    KAFKA_REQUEST_TOPIC: str = ""
    KAFKA_RESPONSE_TOPIC: str = ""

    # SAP Event Mesh defaults.
    EVENT_MESH_REQUEST_TOPIC: str = ""
    EVENT_MESH_REPLY_TOPIC: str = ""

    # Registry metadata.
    REGISTRY_URL: str = ""
    AGENT_KIND: str = "TECHNICAL"
    IS_PUBLISHED: bool = True

    # Legacy aliases kept for old CSV code/config.
    DEFAULT_QUEUE_NAME: str = "csv.default.queue"
    DEFAULT_QUEUE_TYPE: str = "event"

    # Registry and workflow URLs
    REGISTRY_API_URL: str = ""
    WORKFLOW_API_URL: str = ""
    WORKFLOW_API_BASE_URL: str = "default_workflow_api_base_url"

    # CSV-specific service URLs.
    FILE_URL: str = ""
    DATA_FACTORY_URL: str = ""
    DATA_FACTORY_MCP_TRANSPORT: str = "streamable-http"
    DATA_FACTORY_MCP_SERVER_NAME: str = "data_factory"
    DATA_FACTORY_EXPOSE_RAW_MCP_TOOLS: bool = False
    SCHEMA_TRANSFORM_URL: str = ""
    SCHEMA_TRANSFORM_QUERY_DATA_URL: str = ""

    DEFAULT_MAX_WORKERS: int = 10
    DEFAULT_MAX_REVIEW_ITERATIONS: int = 3
    MIN_GROUPS_FOR_PARALLEL: int = 2
    DEFAULT_FILENAME: str = "converted_data.csv"
    REQUEST_TIMEOUT_SECONDS: int = 30
    FILE_SERVICE_UPLOAD_URL: str = ""
    TOOL_ROOT_PACKAGE: str = "app.layer4_frameworks.tools"


settings = Settings()
