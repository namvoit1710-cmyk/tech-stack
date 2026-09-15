"""Credential loading for the log worker — environment first, secrets file second.

The worker originally read everything from a local JSON file. That is fine on an
operator's laptop but useless in Cloud Foundry / a container, where credentials arrive as
environment variables. So the same values can now come from three places, layered
lowest-to-highest:

1. **The secrets file** — ``~/.smdg-worker-secrets.json``, or whatever
   ``LOG_WORKER_SECRETS`` / ``CLAUDE_WORKER_SECRETS`` points at. Unchanged; still the
   default, so nothing that works today stops working.
2. **``SMDG_WORKER_SECRETS_JSON``** — the whole JSON document as one env value. Handy for
   ``cf set-env`` where you want to ship every credential in a single variable.
3. **Individual env vars** — the most specific wins, so you can override exactly one
   value without restating the rest.

OAuth client credentials are CUSTOMER + ENV scoped — ``LOG_WORKER_CLIENT_ID_<CUSTOMER>_<ENV>``:

| env var | key |
|---|---|
| ``LOG_WORKER_CLIENT_ID_MCK_DEV``      | ``client_id_mck_dev``      |
| ``LOG_WORKER_CLIENT_SECRET_MCK_DEV``  | ``client_secret_mck_dev``  |
| ``LOG_WORKER_CLIENT_ID_MCK_QAS``      | ``client_id_mck_qas``      |
| ``LOG_WORKER_CLIENT_SECRET_MCK_QAS``  | ``client_secret_mck_qas``  |
| ``LOG_WORKER_CLIENT_ID_ALCON_DEV``     | ``client_id_alcon_dev``     |
| ``LOG_WORKER_CLIENT_SECRET_ALCON_DEV`` | ``client_secret_alcon_dev`` |
| ``LOG_WORKER_CLIENT_ID_ALCON_QAS``     | ``client_id_alcon_qas``     |
| ``LOG_WORKER_CLIENT_SECRET_ALCON_QAS`` | ``client_secret_alcon_qas`` |
| ``LOG_WORKER_CLIENT_ID_S4_QAS``     | ``client_id_s4_qas``     |
| ``LOG_WORKER_CLIENT_SECRET_S4_QAS`` | ``client_secret_s4_qas`` |
| ``CLS_URL``                  | ``cls_url``       |
| ``CLS_USERNAME``             | ``cls_username``  |
| ``CLS_PASSWORD``             | ``cls_password``  |

The old un-suffixed ``LOG_WORKER_CLIENT_ID`` / ``_SECRET`` (→ ``client_id`` / ``client_secret``)
and ``LOG_WORKER_CLIENT_ID_QAS`` / ``_SECRET_QAS`` (→ ``client_id_qas`` / ``client_secret_qas``)
are still accepted as deprecated MCK dev/qas aliases (see ``fetch_log._read_client_creds``).

Layering (not "env OR file") is deliberate: a common setup is the OData client in the
file and the Cloud Logging user in the environment, or vice versa. Either half can come
from either source.

Nothing here logs a value. Callers must not either — a credential in a log line is a
credential leaked.
"""

from __future__ import annotations

import json
import os
from typing import Any

_ENV_JSON = "SMDG_WORKER_SECRETS_JSON"

# env var -> canonical secrets key
_ENV_KEYS: dict[str, str] = {
    # MCK (McKesson)
    "LOG_WORKER_CLIENT_ID_MCK_DEV": "client_id_mck_dev",
    "LOG_WORKER_CLIENT_SECRET_MCK_DEV": "client_secret_mck_dev",
    "LOG_WORKER_CLIENT_ID_MCK_QAS": "client_id_mck_qas",
    "LOG_WORKER_CLIENT_SECRET_MCK_QAS": "client_secret_mck_qas",
    # ALCON / Alcon (SA-2438) — host token is "alcon"
    "LOG_WORKER_CLIENT_ID_ALCON_DEV": "client_id_alcon_dev",
    "LOG_WORKER_CLIENT_SECRET_ALCON_DEV": "client_secret_alcon_dev",
    "LOG_WORKER_CLIENT_ID_ALCON_QAS": "client_id_alcon_qas",
    "LOG_WORKER_CLIENT_SECRET_ALCON_QAS": "client_secret_alcon_qas",
    # S4
    "LOG_WORKER_CLIENT_ID_S4_QAS": "client_id_s4_qas",
    "LOG_WORKER_CLIENT_SECRET_S4_QAS": "client_secret_s4_qas",
    # Deprecated MCK aliases (old un-suffixed dev / _QAS names)
    "LOG_WORKER_CLIENT_ID": "client_id",
    "LOG_WORKER_CLIENT_SECRET": "client_secret",
    "LOG_WORKER_CLIENT_ID_QAS": "client_id_qas",
    "LOG_WORKER_CLIENT_SECRET_QAS": "client_secret_qas",
    "CLS_URL": "cls_url",
    "CLS_USERNAME": "cls_username",
    "CLS_PASSWORD": "cls_password",
}

DEFAULT_SECRETS_FILE = os.environ.get(
    "LOG_WORKER_SECRETS",
    os.environ.get(
        "CLAUDE_WORKER_SECRETS",
        os.path.join(os.path.expanduser("~"), ".smdg-worker-secrets.json"),
    ),
)


def _from_file(path: str) -> dict[str, Any]:
    """Secrets file contents; ``{}`` on any problem (missing file is the normal case
    once everything comes from the environment — it must not be an error)."""
    if not path:
        return {}
    try:
        with open(os.path.expanduser(os.path.expandvars(path)), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:  # noqa: BLE001 — missing/unreadable/malformed => contribute nothing
        return {}
    return raw if isinstance(raw, dict) else {}


def _from_env_json() -> dict[str, Any]:
    """``SMDG_WORKER_SECRETS_JSON`` parsed; ``{}`` if unset or not a JSON object."""
    blob = os.environ.get(_ENV_JSON) or ""
    if not blob.strip():
        return {}
    try:
        raw = json.loads(blob)
    except Exception:  # noqa: BLE001
        return {}
    return raw if isinstance(raw, dict) else {}


def _from_settings() -> dict[str, Any]:
    """Values pydantic resolved onto the worker's Settings.

    Needed because a ``.env`` file is parsed by pydantic-settings and **never exported to
    ``os.environ``** — reading the process environment alone would miss every
    dotenv-supplied credential. Settings covers real env vars *and* ``.env``; the
    ``os.environ`` layer below still runs, for anything set after import."""
    try:
        from app.config import settings as _s
    except Exception:  # noqa: BLE001 — config import must never break credential loading
        return {}
    out: dict[str, Any] = {}
    for env_name, key in _ENV_KEYS.items():
        val = getattr(_s, env_name, "")
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def _from_env_vars() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for env_name, key in _ENV_KEYS.items():
        val = os.environ.get(env_name)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def load_secrets(path: str = "") -> dict[str, Any]:
    """Merged credentials. Later layers win:
    file < SMDG_WORKER_SECRETS_JSON < .env/Settings < process env vars."""
    merged: dict[str, Any] = {}
    merged.update(_from_file(path or DEFAULT_SECRETS_FILE))
    merged.update(_from_env_json())
    merged.update(_from_settings())
    merged.update(_from_env_vars())
    return merged


def pick(raw: dict[str, Any], *keys: str) -> str:
    """First non-blank string value among *keys*, else ''."""
    for k in keys:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def sources_hint(path: str = "") -> str:
    """Where a caller could have put the credential — for error messages only.
    Never includes a value."""
    p = path or DEFAULT_SECRETS_FILE
    present = [n for n in _ENV_KEYS if os.environ.get(n)]
    bits = [f"secrets file '{p}'", f"${_ENV_JSON}"]
    if present:
        bits.append("env " + ", ".join("$" + n for n in present) + " (set)")
    else:
        bits.append("or the individual env vars ($CLS_USERNAME, $CLS_PASSWORD, …)")
    return "; ".join(bits)
