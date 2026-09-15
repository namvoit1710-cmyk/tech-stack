"""cs-worker settings.

Deliberately SHORT compared with the log-worker's, and the difference is the point: the
log-worker declares one pair of pydantic fields per customer+environment
(``LOG_WORKER_CLIENT_ID_MCK_DEV``, ``…_ALCON_QAS``, …), which is why onboarding a project
there is a code change. Here the project credentials are never declared — ``app/registry.py``
scans ``os.environ`` for them, so a new project is four environment variables and a restart.

That trade has one hard consequence, and it is the reason for the note below: an
undeclared variable is invisible to pydantic, and a value that arrives only through a
parsed ``.env`` file never reaches ``os.environ``. So **credentials must be real
environment variables**. `docker compose` `env_file:` gives exactly that — compose reads
the file on the host and injects real variables; the file itself never enters the image
(the shared ``.dockerignore`` excludes ``**/.env``).
"""

from pydantic_settings import SettingsConfigDict

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """SMDG Customer-Service Worker — one worker that reads a customer's chat message,
    decides which project + environment + change request it is about, checks that all
    three exist, then fetches and explains that environment's logs."""

    # extra="ignore" so THIS subclass tolerates the dynamic LOG_WORKER_* project keys.
    #
    # It does NOT rescue a `.env` FILE: the shared SDK builds its own settings singleton
    # at import time with env_file=".env" and pydantic's default extra="forbid", and the
    # dotenv source hands it EVERY key in the file — so one project credential in a .env
    # sitting next to main.py aborts the process before any worker code runs. Supply
    # credentials as real environment variables (compose env_file does this for you).
    model_config = SettingsConfigDict(extra="ignore")

    APP_NAME: str = "SMDG CS Worker"
    WORKER_NAME: str = "SMDG CS Worker"
    # Base identity only; the registered node types come from app/node_types.py.
    WORKER_TYPE: str = "cs_detect_request"
    WORKER_DESCRIPTION: str = (
        "Customer-service worker: understands a chat message (project + environment + CR), "
        "validates all three against the environment-driven target registry, then fetches "
        "that target's SMDG process logs and writes a BA-readable analysis."
    )
    WORKER_NODE_CLASS: str = "TECHNICAL"
    WORKER_ICON: str = "Headset"
    WORKER_COLOR: str = "#0EA5E9"
    WORKER_TAGS: str = "smdg,cs,log,claude,support"

    SERVER_PORT: int = 36012

    # --- Cloud Logging (one instance for the whole landscape, not per project) ----
    CLS_URL: str = ""
    CLS_USERNAME: str = ""
    CLS_PASSWORD: str = ""

    # Fetches are quick but the Claude nodes are not; keep lease renewal on (this merges
    # into the SDK settings singleton at startup via main.py, so the shared SDK is untouched).
    LEASE_RENEW_ENABLED: bool = True
    LEASE_RENEW_MIN_INTERVAL_SECONDS: float = 5.0


settings = Settings()
