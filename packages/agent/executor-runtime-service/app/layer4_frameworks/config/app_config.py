from pydantic_settings import SettingsConfigDict

from agent_sdk import Settings as _BaseSettings


class Settings(_BaseSettings):
    model_config = SettingsConfigDict(extra="allow")

    APP_NAME: str = "Executor Runtime Service"
    APP_MODE: str = "CONSUMER"
    AGENT_TYPE: str = "executor-runtime"
    AGENT_DOMAIN: str = "execution"
    DESCRIPTION: str = (
        "Queue-first step execution runtime. Consumes agent.request.executor, "
        "validates, dispatches local tools or delegates to technical agents, "
        "and publishes executor.step.status."
    )
    SERVER_PORT: int = 36100
    CONSUMER_OPS_ENABLED: bool = False

    # Executor does not use LLM directly
    LLM_MODEL: str = ""

    # Queue topics
    QUEUE_REQUEST_TOPIC: str = "agent.request.executor"
    QUEUE_REPLY_TOPIC: str = "executor.step.status"
    STATUS_PUBLISH_TOPIC: str = "executor.step.status"
    KAFKA_REQUEST_TOPIC: str = "agent.request.executor"
    EVENT_MESH_REQUEST_TOPIC: str = "agent.request.executor"
    EXECUTOR_AGENT_REPLY_TOPIC: str = "agent.reply.executor"
    DELEGATION_REQUEST_MESSAGE_TYPE: str = "executor.request.agent"
    DELEGATION_RESPONSE_MESSAGE_TYPE: str = "agent.step.status"
    QUEUE_CONSUME_TOPICS: list[str] = [
        "agent.request.executor",
        "agent.reply.executor",
    ]
    EXECUTOR_AGENT_REPLY_TIMEOUT_SECONDS: float = 60.0
    AGENT_REQUEST_COMPATIBILITY_TOPIC: str = "agent.request.agent"

    # Step-execution retry policy (SA-1716). MAX_ATTEMPTS=1 disables retry (today's behavior).
    EXECUTOR_MAX_ATTEMPTS: int = 3
    EXECUTOR_RETRY_BACKOFF_BASE_SECONDS: float = 0.5
    EXECUTOR_RETRY_BACKOFF_CAP_SECONDS: float = 30.0
    EXECUTOR_RETRY_JITTER: bool = True
    EXECUTOR_LOCAL_TOOL_TIMEOUT_SECONDS: float = 30.0
    # Retrying a delegated reply TIMEOUT can double-execute a non-idempotent agent, so it is
    # OFF by default. Send-failures (before the agent runs) and local-tool failures always retry.
    EXECUTOR_RETRY_DELEGATED_ON_TIMEOUT: bool = False

    # Registry
    REGISTRY_URL: str = ""

    # Workflow control plane
    WORKFLOW_CONTROL_PLANE_URL: str = ""
    WORKFLOW_CONTROL_PLANE_TIMEOUT_SECONDS: float = 30.0
    TARGET_TOPIC: str = "business.topic"
    SOURCE: str = "agent"


settings = Settings()
