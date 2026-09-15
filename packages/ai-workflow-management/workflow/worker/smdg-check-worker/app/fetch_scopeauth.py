"""Scope/value authorization check for the SMDG QC Bot (SA-2493).

Takes an account (userID) and GETs AdminUserService/UserScopeAuthObjects (served by the
srv-user authorization app), returning the account's scope authorizations — the group
(scopeID), the authorization object (authObjectID) and the granted values
(authObjectValue). It parses them into a readable message plus structured outputs.

Authentication is identical to the duplication-check node: the credential comes from the
flow's environment (bind the node's "Login credential" field to {{$env.OAUTH}}). Reuses
the OAuth helpers from app.fetch_dupcheck — no dependency on the log-worker.

Query shape (the isActive filter is on by default; flip `active_only` to include inactive):
    GET <base>/AdminUserService/UserScopeAuthObjects
        ?$filter=userID eq '<X>' and isActive eq true
        &$select=scopeID,authObjectID,authObjectValue
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from worker_sdk.layer1_domain.auto_form_mapper import (
    number_field,
    text_field,
    textarea_field,
)

from app.fetch_dupcheck import _oauth_token, _resolve_credential

# S4-QAS srv-user (authorization) is the default landscape for this [S4-QAS] task. The
# host is a configurable field so the same node serves other landscapes (e.g. MCK-DEV
# mckesson-dev-simplemdg-srv-user.cfapps.us20) — the environment credential must match.
_DEFAULT_API_URL = (
    "https://smdg-s4-qas-simplemdg-srv-user.cfapps.br10.hana.ondemand.com"
    "/AdminUserService/UserScopeAuthObjects"
)
_DEFAULT_SELECT = "scopeID,authObjectID,authObjectValue"


def _rule_line(r: Any) -> str:
    if not isinstance(r, dict):
        return "  - " + str(r)
    scope = r.get("scopeID") or "?"
    obj = r.get("authObjectID") or ""
    val = r.get("authObjectValue")
    if isinstance(val, list):
        val_s = "[" + ", ".join(str(v) for v in val) + "]"
    else:
        val_s = str(val) if val not in (None, "") else "[]"
    bits = ["scope=" + str(scope)]
    if obj:
        bits.append("authObject=" + str(obj))
    bits.append("values=" + val_s)
    return "  - " + " ".join(bits)


def _build_message(user_id: str, rows: list, active_only: bool) -> str:
    scope = "active " if active_only else ""
    lines = [f"userID: {user_id}"]
    lines.append(f"{scope}scope authorizations: {len(rows)}" + ("" if rows else " — none"))
    lines.extend(_rule_line(r) for r in rows)
    lines.append("")
    if rows:
        lines.append(f"Summary: {len(rows)} {scope}scope-auth object(s)")
    else:
        lines.append("Summary: no " + (scope or "") + "scope authorizations for this account")
    return "\n".join(lines)


def _fetch(inputs: Optional[dict], parameters: Optional[dict]) -> dict[str, Any]:
    merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}

    user_id = str(merged.get("userID") or merged.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("'userID' is required (the flow input).")

    api_url = str(merged.get("api_url") or "").strip() or _DEFAULT_API_URL
    token_url = str(merged.get("token_url") or "").strip()
    filter_field = str(merged.get("filter_field") or "").strip() or "userID"
    active_raw = str(merged.get("active_only", "true")).strip().lower()
    active_only = active_raw not in ("false", "0", "no", "")
    # $select is a configurable node field (default = the three scope columns). Allow an
    # explicit empty override to fetch every column; only fall back to the default when the
    # key is entirely absent.
    select = merged["odata_select"] if "odata_select" in merged else _DEFAULT_SELECT
    select = str(select or "").strip()

    bearer, client_id, client_secret, token_url = _resolve_credential(inputs, token_url)
    if not bearer and not (client_id and client_secret):
        raise RuntimeError(
            "No credential: bind the node's 'Login credential' field to an $env "
            "credential variable (e.g. {{$env.OAUTH}}) from the flow's selected "
            "environment/Section. There is no local .env fallback."
        )

    start = time.monotonic()
    token = bearer or _oauth_token(token_url, client_id, client_secret)

    filt = filter_field + " eq '" + user_id + "'"
    if active_only:
        filt += " and isActive eq true"
    parts = ["$filter=" + urllib.parse.quote(filt)]
    if select:
        parts.append("$select=" + urllib.parse.quote(select))
    url = api_url + "?" + "&".join(parts)
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        status, resp_body = resp.status, resp.read().decode("utf-8", "replace")

    try:
        rows = json.loads(resp_body).get("value") or []
    except Exception:  # noqa: BLE001
        rows = []
    if not isinstance(rows, list):
        rows = []
    message = _build_message(user_id, rows, active_only)
    return {
        "status": "OK",
        "status_code": status,
        "userID": user_id,
        "message": message,
        "count": len(rows),
        "scopes": json.dumps(rows, ensure_ascii=False)[:190000],
        "url": url,
        "elapsed_ms": (time.monotonic() - start) * 1000,
        "error": "",
    }


def _error_output(msg: str) -> dict[str, Any]:
    return {
        "status": "FAILED", "status_code": 0, "userID": "", "message": "",
        "count": 0, "scopes": "[]", "url": "", "elapsed_ms": 0,
        "error": (msg or "unknown error")[:400],
    }


def make_handler() -> Any:
    """Handler ``(inputs, parameters) -> dict``. Non-fatal: returns a schema-complete
    error payload so the flow keeps flowing (gate downstream on ``outputs.status``)."""
    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            return _fetch(inputs, parameters)
        except urllib.error.HTTPError as exc:
            return _error_output(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the node
            return _error_output(str(exc))
    return _handler


# -- Schemas ------------------------------------------------------------------

INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("userID", "Account / user ID",
               placeholder="bound to the flow input, e.g. {{$input.userID}}"),
    text_field("credential_ref", "Login credential",
               placeholder="{{$env.OAUTH}} — a credential variable from the environment"),
    text_field("api_url", "API URL (UserScopeAuthObjects base)",
               placeholder="OData entity base URL, or {{$env.URL}}", default=_DEFAULT_API_URL),
    text_field("active_only", "Active only (isActive eq true)",
               placeholder="true | false", default="true"),
    text_field("odata_select", "$select fields (blank = all columns)",
               placeholder="comma-separated OData fields", default=_DEFAULT_SELECT),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    text_field("userID", "Account / user ID", readonly=True),
    textarea_field("message", "Parsed scope-authorization message", readonly=True),
    number_field("count", "Scope-auth object count", readonly=True),
    textarea_field("scopes", "Scope authorizations (JSON)", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
