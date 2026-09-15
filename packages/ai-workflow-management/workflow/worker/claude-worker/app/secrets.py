"""Credential loading for the claude-worker — environment first, secrets file second.

Mirrors the log-worker's ``app.secrets``: the GitHub / GitLab PATs and the Claude CLI
token(s) can come from three layered sources (lowest-to-highest precedence), so the
worker runs the same whether on a laptop (JSON file) or in a container (env vars):

1. **The secrets file** — ``~/.smdg-worker-secrets.json`` or ``$CLAUDE_WORKER_SECRETS``.
   Unchanged default, so nothing that works today stops working.
2. **``SMDG_WORKER_SECRETS_JSON``** — the whole JSON document in one env value
   (shared with the log-worker; handy for ``cf set-env``).
3. **Individual env vars** — most specific wins:

| env var | key |
|---|---|
| ``CLAUDE_WORKER_GITHUB_PAT``       | ``github_pat``       |
| ``CLAUDE_WORKER_GITLAB_PAT``       | ``gitlab_pat``       |
| ``CLAUDE_WORKER_CLAUDE_CLI_TOKEN`` | ``claude_cli_token`` |

``claude_cli_token`` may be a comma-separated string OR a JSON array of tokens (the
CLI-call layer rotates across them). Nothing here logs a value.
"""

from __future__ import annotations

import json
import os
from typing import Any

_ENV_JSON = "SMDG_WORKER_SECRETS_JSON"

# env var -> canonical secrets key
_ENV_KEYS: dict[str, str] = {
    "CLAUDE_WORKER_GITHUB_PAT": "github_pat",
    "CLAUDE_WORKER_GITLAB_PAT": "gitlab_pat",
    "CLAUDE_WORKER_CLAUDE_CLI_TOKEN": "claude_cli_token",
}

DEFAULT_SECRETS_FILE = os.environ.get(
    "CLAUDE_WORKER_SECRETS",
    os.path.join(os.path.expanduser("~"), ".smdg-worker-secrets.json"),
)


def _from_file(path: str) -> dict[str, Any]:
    """Secrets file contents; ``{}`` on any problem (a missing file is normal once
    everything comes from the environment — it must not be an error)."""
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
    """Values pydantic resolved onto the worker's Settings — needed because a ``.env``
    file is parsed by pydantic-settings and NEVER exported to ``os.environ``."""
    try:
        from app.layer4_frameworks.config import settings as _s
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


def sources_hint(path: str = "") -> str:
    """Where a caller could have put the credential — for error messages only."""
    p = path or DEFAULT_SECRETS_FILE
    present = [n for n in _ENV_KEYS if os.environ.get(n)]
    bits = [f"secrets file '{p}'", f"${_ENV_JSON}"]
    bits.append("env " + ", ".join("$" + n for n in present) + " (set)" if present
                else "or the individual env vars ($CLAUDE_WORKER_GITHUB_PAT, …)")
    return "; ".join(bits)
