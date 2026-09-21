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

    LOG_LEVEL: str = "INFO"
    APP_NAME: str = "AI Workflow Service"
    APP_MODE: str = "REST"

    # Infrastructure mode toggle: "mock" | "local" | "sap"
    INFRA_MODE: str = "mock"

    AGENT_REGISTRY_URL: str = "http://localhost:8003"
    WORKFLOW_API_BASE_URL: str = "http://localhost:8005"

    # Profile API base URL for user permission and feature lookups
    PROFILE_API_BASE_URL: str = ""
    # LLM provider: "mock" | "openai"
    LLM_PROVIDER: str = "mock"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Input Guard configuration
    INPUT_GUARD_CONFIG: dict = {
        "max_message_length": 10000,
        "injection_detection_enabled": True,
        "pii_masking_enabled": True,
    }

    # Output Guard configuration
    OUTPUT_GUARD_CONFIG: dict = {
        "grounding_enabled": False,
        "pii_masking_enabled": True,
        "content_policy_enabled": True,
    }

    # Push Gateway base URL (REST API, POST /v1/pushgateway/send); empty = disabled
    PUSH_GATEWAY_URL: str = ""

    # SA-1948: conversation-history compaction (reuses agent-sdk ContextBudgetManager).
    # All ENV-tunable per deployment. CONTEXT_COMPACTION_STRATEGY="none" disables it
    # entirely (single toggle — no separate _ENABLED flag).
    CONTEXT_COMPACTION_STRATEGY: str = "tiered"
    CONTEXT_MAX_TOTAL_TOKENS: int = 16000
    CONTEXT_COMPACTION_THRESHOLD: float = 0.80
    CONTEXT_MAX_MESSAGE_HISTORY: int = 10
    CONTEXT_AGGRESSIVE_THRESHOLD: float = 0.85
    CONTEXT_DANGER_THRESHOLD: float = 0.95
    # Model used ONLY for tiktoken token counting (the budget estimate) — dedicated and
    # decoupled from LLM_MODEL so an exotic summarize model (e.g. gpt-5, which tiktoken may
    # not recognize) never breaks token counting. Keep it a tiktoken-known model.
    CONTEXT_TOKEN_COUNT_MODEL: str = "gpt-4o-mini"

    # Checkpointing (for conversational flows — uses HANA via HanaConnectionManager)
    CHECKPOINT_TTL_HOURS: int = 24
    AGENT_REQUEST_TOPIC: str = "business.topic"
    ORCHESTRATOR_REQUEST_TOPIC: str = "orchestrator.topic"
    ORCHESTRATOR_EVENT_TYPE: str = "orchestrator_event"

    # SA-892: route the workflow STOP through the executor TOOL (mirrors how the
    # business-agent dispatches workflow_start_run). The stop use case publishes a
    # workflow_stop STEP BATCH to the executor's step-intake topic — the SAME topic
    # the business-agent publishes step batches to (its EXECUTOR_TOPIC) — with the
    # SAME envelope shape (type=agent.request.executor, payload.type=step_batch,
    # payload.to=executor.runtime). Must match the business-agent EXECUTOR_TOPIC.
    EXECUTOR_TOPIC: str = "executor.topic"
    # SA-892: the stop step's ``execute_by`` capability id. By DEFAULT this is EMPTY
    # and the stop use case DISCOVERS the workflow-stop tool's ``tool.<uuid>`` from
    # the agent registry BY NAME at runtime (see WORKFLOW_STOP_TOOL_NAME) — the same
    # way ``workflow_start_run``'s ``tool.<uuid>`` is discovered from the registry
    # catalog at plan time, so NO per-environment deploy value is required. Set this
    # ONLY as an explicit override (e.g. tests, or pinning a specific ``tool.<uuid>``
    # in an environment); when set it short-circuits the registry lookup. The
    # executor's validator/resolver require the dotted UUID form ``tool.<uuid>``.
    WORKFLOW_STOP_EXECUTE_BY: str = ""
    # SA-892: the registered tool name the stop resolves to a ``tool.<uuid>`` via the
    # registry (matches the executor's local tool name after ``_normalize_tool_name``
    # strips ``_tool`` from ``workflow_stop_tool``).
    WORKFLOW_STOP_TOOL_NAME: str = "workflow_stop"


settings = Settings()
