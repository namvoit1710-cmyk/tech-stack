from agent_sdk import Settings


class AppSettingsAdapter(Settings):
    """Extend the SDK Settings with this agent's configuration fields.

    Inherits the generic LLM config (LLM_MODEL / LLM_PROVIDER / LLM_TEMPERATURE, etc.)
    from the SDK, so the model (e.g. gpt-5.4-nano) is set purely via .env.
    """

    WORKFLOW_API_BASE_URL: str = "default_workflow_api_base_url"
    WORKFLOW_API_URL: str = ""  # accepted as an alias for WORKFLOW_API_BASE_URL
    WORKFLOW_APP_URL: str = ""  # frontend base for building clickable view links
    OPENAI_API_KEY: str = ""
    # Bounds the ValidateRepairGate's auto-repair loop (RepairPolicy constructor default 3).
    WORKFLOW_REPAIR_MAX_ATTEMPTS: int = 3
    # Base URL of the troubleshooting-agent (SA-1438) whose collect->diagnose->report graph
    # the diagnose_failure tool calls over HTTP (POST /api/v1/execute). Empty = diagnosis is
    # disabled and the tool degrades gracefully (never raises into the design flow).
    TROUBLESHOOTING_AGENT_URL: str = ""


settings = Settings()
