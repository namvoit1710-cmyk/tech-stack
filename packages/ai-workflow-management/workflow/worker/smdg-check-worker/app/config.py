from pydantic_settings import SettingsConfigDict

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """SMDG Check Worker — a standalone multi-type worker exposing three read/check node
    types against the SMDG backend: duplicate-config on a change request
    (``qc_duplication_check`` → RequestEventData), an account's scope authorizations
    (``qc_scope_auth`` → UserScopeAuthObjects) and an object's on-premise data
    (``qc_onprem_data`` → getObjectOnPremiseData).

    Each node resolves its OAuth credential from the flow's environment/Section
    (``credential_ref`` bound to ``{{$env.OAUTH}}``) — there are NO local credentials on
    this worker. Endpoint URLs are configurable node fields (typically bound to
    ``{{$env.*_BASE_API_URL}}``).
    """

    # extra="ignore" so this subclass tolerates any base .env keys it doesn't declare.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "SMDG Check Worker"
    WORKER_NAME: str = "SMDG Check Worker"
    # Base identity only; the registered node types come from app/node_types.py.
    WORKER_TYPE: str = "qc_duplication_check"
    WORKER_DESCRIPTION: str = (
        "LOCAL worker that authenticates to SMDG backend services and reads/checks MDG "
        "data: duplicate-config on a change request (RequestEventData), an account's scope "
        "authorizations (UserScopeAuthObjects), and an object's on-premise data "
        "(getObjectOnPremiseData). Credentials come from the flow environment."
    )
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "FileText"
    WORKER_COLOR: str = "#0EA5E9"
    WORKER_TAGS: str = "smdg,check,mdg,http,oauth"

    SERVER_PORT: int = 36011

    # No credential fields: the node credential comes from the flow environment
    # ({{$env.OAUTH}}) via resolve_effective_auth — nothing is read from a local .env or
    # secrets file.

    # Fetches are quick, but keep lease renewal on for safety.
    LEASE_RENEW_ENABLED: bool = True
    LEASE_RENEW_MIN_INTERVAL_SECONDS: float = 5.0


settings = Settings()
