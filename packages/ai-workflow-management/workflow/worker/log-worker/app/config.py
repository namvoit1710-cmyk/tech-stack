from pydantic_settings import SettingsConfigDict

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """SMDG Log Worker — a standalone multi-type worker exposing two self-authenticating
    log-fetcher node types (RequestActionLog / SubmitEventLog). Each node logs in via
    OAuth client_credentials (creds from the local secrets file) and GETs a
    CommonProcessService OData endpoint. Endpoint URL / token URL / $select / $orderby
    are all configurable node fields.
    """

    # extra="ignore" so THIS subclass tolerates unknown keys.
    #
    # It does NOT rescue a `.env` file. The shared SDK builds its own singleton at import
    # time (`app_config.py:148  settings = Settings()`) with `env_file=".env"` and
    # pydantic's default extra="forbid"; the dotenv source hands it EVERY key in the file,
    # so one undeclared key aborts the process before any worker code runs. That is an SDK
    # limitation affecting every worker in this repo, not something this worker can fix
    # from here. **Supply credentials as real environment variables, not via .env.**
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "SMDG Log Worker"
    WORKER_NAME: str = "SMDG Log Worker"
    # Base identity only; the registered node types come from app/node_types.py.
    WORKER_TYPE: str = "request_action_log"
    WORKER_DESCRIPTION: str = (
        "LOCAL worker that fetches SMDG CommonProcessService logs (RequestActionLog / "
        "SubmitEventLog): self-login via OAuth client_credentials, then OData GET by reqID."
    )
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "FileText"
    WORKER_COLOR: str = "#0EA5E9"
    WORKER_TAGS: str = "smdg,log,http,oauth"

    SERVER_PORT: int = 36010

    # --- Credentials -------------------------------------------------------------
    # Declared so they are READ, not merely tolerated: `extra="ignore"` above stops the
    # crash, but an undeclared key is silently dropped, and a dotenv value never reaches
    # os.environ — so without these five fields a .env-supplied credential would vanish
    # and every node would report "not found". Blank default = "not configured here",
    # which lets app/secrets.py fall back to the secrets file. These are read off THIS
    # subclass, never merged into the SDK singleton (see runner._merge_app_settings…).
    CLS_URL: str = ""
    CLS_USERNAME: str = ""
    CLS_PASSWORD: str = ""
    # OAuth client credentials are now CUSTOMER-scoped as well as env-scoped:
    # LOG_WORKER_CLIENT_ID_<CUSTOMER>_<ENV>. fetch_log._fetch chooses the set from the
    # target link (or an explicit `cred_env` node field). Blank = not configured here →
    # app/secrets.py falls back to the secrets file; a set with no creds errors clearly.
    #
    # MCK (McKesson) — the original default customer. DEV was formerly the un-suffixed
    # LOG_WORKER_CLIENT_ID / _SECRET, QAS was LOG_WORKER_CLIENT_ID_QAS / _SECRET_QAS; both
    # legacy names are still accepted below so nothing already deployed breaks.
    LOG_WORKER_CLIENT_ID_MCK_DEV: str = ""
    LOG_WORKER_CLIENT_SECRET_MCK_DEV: str = ""
    LOG_WORKER_CLIENT_ID_MCK_QAS: str = ""
    LOG_WORKER_CLIENT_SECRET_MCK_QAS: str = ""
    # ALCON — get-log clients (SA-2438), used when the target link host contains "alcon"
    # (alcon-dev-simplemdg… / alcon-qas-simplemdg…) or cred_env=alcon_dev / alcon_qas.
    # "-qas" in the link picks QAS, else DEV. Values supplied by the operator in the .env.
    LOG_WORKER_CLIENT_ID_ALCON_DEV: str = ""
    LOG_WORKER_CLIENT_SECRET_ALCON_DEV: str = ""
    LOG_WORKER_CLIENT_ID_ALCON_QAS: str = ""
    LOG_WORKER_CLIENT_SECRET_ALCON_QAS: str = ""
    # S4-QAS OAuth client — used when the target link contains "s4-qas" (checked BEFORE
    # "-qas" in fetch_log._fetch, since an S4 host also contains "-qas"). Separate account.
    LOG_WORKER_CLIENT_ID_S4_QAS: str = ""
    LOG_WORKER_CLIENT_SECRET_S4_QAS: str = ""
    # --- Legacy MCK aliases (deprecated) -----------------------------------------
    # Still declared + read so an existing .env / CF env using the old names keeps
    # working; app/secrets.py maps them onto the MCK dev/qas sets as a lower-priority
    # fallback. Prefer the *_MCK_DEV / *_MCK_QAS names above for anything new.
    LOG_WORKER_CLIENT_ID: str = ""
    LOG_WORKER_CLIENT_SECRET: str = ""
    LOG_WORKER_CLIENT_ID_QAS: str = ""
    LOG_WORKER_CLIENT_SECRET_QAS: str = ""

    # Fetches are quick, but keep renewal on for safety (overriding here auto-effects via
    # main.py's _merge_app_settings_into_singleton — the shared worker-sdk is untouched).
    LEASE_RENEW_ENABLED: bool = True
    LEASE_RENEW_MIN_INTERVAL_SECONDS: float = 5.0


settings = Settings()
