"""RequestEventData duplicate-config fetcher for the SMDG QC Bot (SA-2494).

A self-authenticating node: it resolves an OAuth credential from the workflow environment
(SA-2365 — bind the node's "Login credential" field to a ``{{$env.OAUTH}}`` credential
variable), then GET ``CommonProcessService/RequestEventData?$filter=reqID eq '<id>'`` and
parses the two duplicate-config fields that live NESTED at
``templateConfigData.templateData.DuplicationCheck`` and
``...DuplicationCheckWithCondition`` (each a JSON-**string**-encoded array). It emits a
readable ``message`` plus structured outputs.

Credential handling is identical to the stock log-worker (post SA-2365): the credential
comes ONLY from the selected environment's vault credential — there is NO local
``.env`` / secrets-file fallback. A generic-oauth2-app credential resolves to a ready
bearer token (the vault does the ``client_credentials`` exchange); a basic-auth credential
carries the client id/secret (and optionally a token URL) and this node does the exchange.

Two RequestEventData gotchas this node bakes in as overridable defaults:

* The S4-QAS subaccount has its OWN XSUAA token host — the shared ``single-ams-simplemdg``
  host returns ``invalid_client`` for the S4-QAS client. So the default ``token_url``
  points at the ``smdg-s4-qas-simplemdg`` auth host (used only for a basic-auth credential
  that doesn't carry its own token URL).
* ``DuplicationCheck`` is NOT a top-level RequestEventData field; it is nested inside the
  string-encoded ``templateConfigData.templateData`` object.
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

# Standalone: this v2 worker does NOT import anything from the log-worker's fetch_log —
# it is its own node, not a log-worker node. The tiny OAuth exchange lives here.
_DEFAULT_API_URL = (
    "https://smdg-s4-qas-simplemdg-srv-process-system.cfapps.br10.hana.ondemand.com"
    "/CommonProcessService/RequestEventData"
)
# S4-QAS has its own XSUAA host — the shared single-ams host 401s (invalid_client). Used
# only when a basic-auth credential does not carry its own token URL.
_DEFAULT_TOKEN_URL = (
    "https://smdg-s4-qas-simplemdg.authentication.br10.hana.ondemand.com/oauth/token"
)


def _oauth_token(token_url: str, client_id: str, client_secret: str,
                 grant_type: str = "client_credentials", timeout: int = 60) -> str:
    """OAuth 2.0 client_credentials token request → access_token (raises on failure)."""
    data = urllib.parse.urlencode({
        "grant_type": grant_type or "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(
        token_url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8") or "{}")
    tok = body.get("access_token")
    if not tok:
        raise RuntimeError("OAuth login returned no access_token")
    return str(tok)


def _as_json(v: Any) -> Any:
    """Return v parsed if it's a JSON-encoded string; the value itself if already a
    dict/list; else None (so callers can fall back)."""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s and s[0] in "[{":
            try:
                return json.loads(s)
            except Exception:  # noqa: BLE001
                return None
    return None


def _extract_dupe(resp_body: str) -> tuple[list, list]:
    """(DuplicationCheck[], DuplicationCheckWithCondition[]) dug out of the nested,
    string-encoded ``templateConfigData.templateData`` object. Empty lists if absent."""
    try:
        data = json.loads(resp_body)
    except Exception:  # noqa: BLE001
        return [], []
    rows = data.get("value") if isinstance(data, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else {}
    tcd = _as_json(row.get("templateConfigData")) if isinstance(row, dict) else None
    td = _as_json(tcd.get("templateData")) if isinstance(tcd, dict) else None
    if not isinstance(td, dict):
        return [], []
    dc = _as_json(td.get("DuplicationCheck"))
    dcc = _as_json(td.get("DuplicationCheckWithCondition"))
    return (dc if isinstance(dc, list) else []), (dcc if isinstance(dcc, list) else [])


def _rule_line(r: Any) -> str:
    if not isinstance(r, dict):
        return "  - " + str(r)
    field = r.get("businessField") or r.get("field") or "?"
    table = r.get("businessTable") or r.get("table") or ""
    active = r.get("isActive")
    group = r.get("andConditionGroup")
    bits = [str(field)]
    if table:
        bits.append("(" + str(table) + ")")
    if active is not None:
        bits.append("[active]" if active else "[inactive]")
    if group is not None:
        bits.append("group=" + str(group))
    return "  - " + " ".join(bits)


def _build_message(req_id: str, dc: list, dcc: list) -> str:
    lines = [f"reqID: {req_id}"]
    lines.append(f"DuplicationCheck: {len(dc)} rule(s)" + ("" if dc else " — none"))
    lines.extend(_rule_line(r) for r in dc)
    lines.append(
        f"DuplicationCheckWithCondition: {len(dcc)} rule(s)" + ("" if dcc else " — none")
    )
    lines.extend(_rule_line(r) for r in dcc)
    lines.append("")
    lines.append(
        "Summary: " + ("duplicate config PRESENT" if (dc or dcc) else "NO duplicate config")
    )
    return "\n".join(lines)


def _resolve_credential(inputs: Optional[dict], token_url: str) -> tuple[str, str, str, str]:
    """(bearer, client_id, client_secret, token_url) from the environment credential.

    Mirrors the stock log-worker: a bearer credential is used as-is (vault did the OAuth);
    a basic/oauth2 credential yields client id/secret (+ an optional token URL override).
    Fails closed when neither is present — the credential comes ONLY from the selected
    environment, never from a local file."""
    bearer = client_id = client_secret = ""
    try:
        from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth
        cred = resolve_effective_auth(inputs or {})
    except Exception:  # noqa: BLE001 — treat resolution failure as "no credential"
        cred = None
    if isinstance(cred, dict):
        ctype = (cred.get("type") or "").lower()
        cfg = cred.get("config") or {}
        if ctype == "bearer" and str(cfg.get("token") or "").strip():
            bearer = str(cfg.get("token")).strip()
        else:
            client_id = str(cfg.get("clientId") or cfg.get("client_id") or cfg.get("username") or "").strip()
            client_secret = str(cfg.get("clientSecret") or cfg.get("client_secret") or cfg.get("password") or "").strip()
            ctok = str(cfg.get("tokenUrl") or cfg.get("token_url") or "").strip()
            if ctok:
                token_url = ctok
    return bearer, client_id, client_secret, token_url


def _fetch(inputs: Optional[dict], parameters: Optional[dict]) -> dict[str, Any]:
    merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}

    req_id = str(merged.get("reqID") or merged.get("req_id") or "").strip()
    if not req_id:
        raise ValueError("'reqID' is required (the flow input).")

    api_url = str(merged.get("api_url") or "").strip() or _DEFAULT_API_URL
    token_url = str(merged.get("token_url") or "").strip() or _DEFAULT_TOKEN_URL
    grant_type = str(merged.get("grant_type") or "").strip() or "client_credentials"
    filter_field = str(merged.get("filter_field") or "").strip() or "reqID"

    bearer, client_id, client_secret, token_url = _resolve_credential(inputs, token_url)
    if not bearer and not (client_id and client_secret):
        raise RuntimeError(
            "No credential: bind the node's 'Login credential' field to an $env "
            "credential variable (e.g. {{$env.OAUTH}}) from the flow's selected "
            "environment/Section. There is no local .env fallback."
        )

    start = time.monotonic()
    token = bearer or _oauth_token(token_url, client_id, client_secret, grant_type)
    filt = urllib.parse.quote(filter_field + " eq '" + req_id + "'")
    url = api_url + "?$filter=" + filt
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        status, resp_body = resp.status, resp.read().decode("utf-8", "replace")

    dc, dcc = _extract_dupe(resp_body)
    message = _build_message(req_id, dc, dcc)
    return {
        "status": "OK",
        "status_code": status,
        "reqID": req_id,
        "message": message,
        "has_duplication": "true" if (dc or dcc) else "false",
        "duplication_check_count": len(dc),
        "duplication_check_with_condition_count": len(dcc),
        "duplication_check": json.dumps(dc, ensure_ascii=False)[:190000],
        "duplication_check_with_condition": json.dumps(dcc, ensure_ascii=False)[:190000],
        "url": url,
        "elapsed_ms": (time.monotonic() - start) * 1000,
        "error": "",
    }


def _error_output(msg: str) -> dict[str, Any]:
    return {
        "status": "FAILED", "status_code": 0, "reqID": "", "message": "",
        "has_duplication": "false", "duplication_check_count": 0,
        "duplication_check_with_condition_count": 0, "duplication_check": "[]",
        "duplication_check_with_condition": "[]", "url": "", "elapsed_ms": 0,
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

# One credential field to bind the environment credential ({{$env.OAUTH}}) so the node can
# log in — but NO raw OAuth token-URL / grant-type fields (the token URL comes from that
# credential, or the built-in S4-QAS default).
INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("reqID", "Request ID (reqID)",
               placeholder="bound to the flow input, e.g. {{$input.reqID}}"),
    text_field("credential_ref", "Login credential",
               placeholder="{{$env.OAUTH}} — a credential variable from the environment"),
    text_field("api_url", "API URL (RequestEventData base)",
               placeholder="OData entity base URL", default=_DEFAULT_API_URL),
    text_field("filter_field", "Filter field",
               placeholder="OData field to filter reqID on", default="reqID"),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    text_field("reqID", "Request ID", readonly=True),
    textarea_field("message", "Parsed duplicate-config message", readonly=True),
    text_field("has_duplication", "Has duplicate config (true/false)", readonly=True),
    number_field("duplication_check_count", "DuplicationCheck rule count", readonly=True),
    number_field("duplication_check_with_condition_count",
                 "DuplicationCheckWithCondition rule count", readonly=True),
    textarea_field("duplication_check", "DuplicationCheck (JSON)", readonly=True),
    textarea_field("duplication_check_with_condition",
                   "DuplicationCheckWithCondition (JSON)", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
