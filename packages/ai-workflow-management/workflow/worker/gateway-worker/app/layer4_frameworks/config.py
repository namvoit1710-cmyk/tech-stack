"""Settings for the gateway-worker.

Extends the SDK base settings. Adds the control-plane URL we call for
`_resolve` and the path to the external-host allowlist YAML.
"""
from __future__ import annotations

from worker_sdk.layer4_frameworks.config.app_config import Settings as _BaseSettings


class Settings(_BaseSettings):
    APP_NAME: str = "Gateway Worker"
    WORKER_NAME: str = "Gateway Worker"
    WORKER_TYPE: str = "gateway-worker"
    SERVER_PORT: int = 35001

    # Control-plane base URL — the worker POSTs to `${CONTROL_PLANE_URL}/api/v1/gateway/services/{name}/_resolve`
    CONTROL_PLANE_URL: str = "http://localhost:8001"

    # Resolve-response cache TTL (seconds).
    RESOLVE_CACHE_TTL_S: float = 60.0

    # External allowlist YAML (for the legacy `call_external` primitive only).
    # Relative paths resolved from the worker's working directory.
    EXTERNAL_ALLOWLIST_PATH: str = "config/external.yaml"

    # Allowed URL schemes for `invoke_app_function`'s external targets (the
    # registry-driven path; per-app hosts come from the CP _resolve allowlist).
    # Comma-separated; https-only by default.
    INVOKE_ALLOWED_SCHEMES: str = "https"

    # Upstream HTTP defaults.
    UPSTREAM_DEFAULT_TIMEOUT_S: float = 30.0
    UPSTREAM_MAX_RESPONSE_BYTES: int = 5 * 1024 * 1024  # 5 MiB


settings = Settings()
