from pydantic_settings import SettingsConfigDict

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """Claude Worker — a MULTI-TYPE worker (jira-worker style) exposing three Claude-CLI
    node types (claude_check / claude_implement / claude_code). Behaviour is fixed per
    node type, so there is no ``mode`` field. Runs locally and shells out to the Claude
    Code CLI (``claude -p``), switching across OAuth tokens on a usage/rate limit.
    """

    # extra="ignore" so a .env with unknown keys doesn't abort startup; env_file lets
    # credentials come from .env too (pydantic parses .env but never exports to os.environ,
    # so app/secrets.py reads them off Settings — see its _from_settings).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Claude Worker"
    WORKER_NAME: str = "Claude Worker"
    # Base identity only; the registered node types come from app/node_types.py.
    WORKER_TYPE: str = "claude_code"
    WORKER_DESCRIPTION: str = (
        "LOCAL multi-type worker driving the Claude Code CLI (claude -p): check a ticket, "
        "implement it, or both. Switches OAuth tokens on a usage/rate limit like the pipeline."
    )
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "SquareTerminal"
    WORKER_COLOR: str = "#16A34A"
    WORKER_TAGS: str = "ai,agent,coder,claude,cli,local"

    SERVER_PORT: int = 36000

    # --- Credentials (env-first; blank = fall back to the secrets file) ----------
    # Declared so a .env value is READ (extra="ignore" only stops the crash; a dotenv
    # value never reaches os.environ). app/secrets.py reads these off Settings. Never
    # merged into the SDK singleton (see runner._merge_app_settings…).
    CLAUDE_WORKER_GITHUB_PAT: str = ""
    CLAUDE_WORKER_GITLAB_PAT: str = ""
    CLAUDE_WORKER_CLAUDE_CLI_TOKEN: str = ""

    # Keep the lease alive during long agentic runs (rootcause ~14min) so the executor
    # doesn't supersede it and 409 the completion callback (re-lease loop). Overriding here
    # (not in the shared worker-sdk) auto-effects: main.py merges app settings into the SDK
    # singleton that the pull runner reads.
    LEASE_RENEW_ENABLED: bool = True
    LEASE_RENEW_MIN_INTERVAL_SECONDS: float = 5.0


settings = Settings()
