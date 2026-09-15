"""Credential loading for the cs-worker — environment first, secrets file second.

Same three layered sources as the log-worker and the claude-worker, so one
``.smdg-worker-secrets.json`` or one compose ``env_file`` serves all three:

1. the secrets file (``$LOG_WORKER_SECRETS`` / ``$CLAUDE_WORKER_SECRETS`` /
   ``~/.smdg-worker-secrets.json``),
2. ``$SMDG_WORKER_SECRETS_JSON`` — the whole document in one variable,
3. individual environment variables (most specific wins).

**What is NOT here: the per-project OAuth clients.** Those are dynamic — see
``app/registry.py``, which discovers ``LOG_WORKER_CLIENT_ID_<PROJECT>_<ENV>`` by scanning
the environment. This module keeps only the credentials that are the same for every
project: the Cloud Logging reader and the Claude CLI token(s).

| env var | key |
|---|---|
| ``CLS_URL``                        | ``cls_url``          |
| ``CLS_USERNAME``                   | ``cls_username``     |
| ``CLS_PASSWORD``                   | ``cls_password``     |
| ``CLAUDE_WORKER_CLAUDE_CLI_TOKEN`` | ``claude_cli_token`` |

``claude_cli_token`` may be a comma-separated string OR a JSON array; ``app/claude_cli.py``
rotates across them on a rate-limit or auth failure.

There is no ``_from_settings`` layer here (the log-worker has one). It existed to rescue
credentials supplied through a ``.env`` file, which pydantic parses without exporting to
``os.environ``. The cs-worker's registry reads ``os.environ`` directly and cannot see a
dotenv value at all, so a ``.env``-only credential would half-work — the CLS user found,
the project targets not. Rather than that, the contract is one line: **supply real
environment variables**. `docker compose`'s ``env_file:`` does exactly that (the file is
read by compose, never by the container), which is how this worker is run.

Nothing here logs a value.
"""

from __future__ import annotations

import json
import os
from typing import Any

_ENV_JSON = "SMDG_WORKER_SECRETS_JSON"

# env var -> canonical secrets key. Project credentials are NOT in this map (registry.py).
_ENV_KEYS: dict[str, str] = {
    "CLS_URL": "cls_url",
    "CLS_USERNAME": "cls_username",
    "CLS_PASSWORD": "cls_password",
    "CLAUDE_WORKER_CLAUDE_CLI_TOKEN": "claude_cli_token",
    "CS_WORKER_CLAUDE_CLI_TOKEN": "claude_cli_token",
}

DEFAULT_SECRETS_FILE = os.environ.get(
    "LOG_WORKER_SECRETS",
    os.environ.get(
        "CLAUDE_WORKER_SECRETS",
        os.path.join(os.path.expanduser("~"), ".smdg-worker-secrets.json"),
    ),
)


def _from_file(path: str) -> dict[str, Any]:
    """Secrets file contents; ``{}`` on any problem (a missing file is the normal case
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


def _from_env_vars() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for env_name, key in _ENV_KEYS.items():
        val = os.environ.get(env_name)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def load_secrets(path: str = "") -> dict[str, Any]:
    """Merged credentials. Later layers win:
    file < SMDG_WORKER_SECRETS_JSON < process env vars."""
    merged: dict[str, Any] = {}
    merged.update(_from_file(path or DEFAULT_SECRETS_FILE))
    merged.update(_from_env_json())
    merged.update(_from_env_vars())
    return merged


def pick(raw: dict[str, Any], *keys: str) -> str:
    """First non-blank value among *keys*, else ''. A list value (``claude_cli_token``
    as a JSON array) is joined with commas, which is what the rotator expects."""
    for k in keys:
        v = raw.get(k)
        if isinstance(v, list):
            v = ",".join(str(x).strip() for x in v if str(x).strip())
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def claude_tokens(path: str = "") -> list[str]:
    """The Claude CLI OAuth tokens, in the order the rotator should try them."""
    raw = load_secrets(path)
    blob = pick(raw, "claude_cli_token", "oauth_token", "oauth_tokens", "claude_token")
    return [t.strip() for t in blob.split(",") if t.strip()]


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
