"""Self-authenticating SMDG log fetchers — RequestActionLog / SubmitEventLog.

Each node type takes a ``reqID`` and, INSIDE the node, does an OAuth
``client_credentials`` login (client_id + client_secret read from the local
secrets file) then GETs a CommonProcessService OData endpoint with the bearer
token. No separate HTTP/token node — the login is internal.

Nothing about the endpoint is hard-coded into the *logic*: the token URL, the API
base URL, the OData ``$select`` / ``$orderby`` and the filter field are all exposed
as **configurable node fields** (with sensible per-service defaults you can override
on the node without touching code).

client_id / client_secret live in the local secrets JSON (``~/.smdg-worker-secrets.json``
or ``LOG_WORKER_SECRETS`` / ``CLAUDE_WORKER_SECRETS``), e.g.::

    {"client_id": "...", "client_secret": "...", ...}
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

# Local-only trace with per-worker identity (host/ip/pid/instance) — see app.worker_trace.
from app.worker_trace import trace as _trace

from worker_sdk.layer1_domain.auto_form_mapper import (
    number_field,
    text_field,
    textarea_field,
)

_DEFAULT_TOKEN_URL = (
    "https://single-ams-simplemdg.authentication.br10.hana.ondemand.com/oauth/token"
)

# Per-service DEFAULTS only — every one of these is also a configurable node field,
# so the values below are just the pre-filled defaults, not baked-in behaviour.
# NOTE: both endpoints are served by the *process-system* host (srv-user 404s).
_SERVICES: dict[str, dict[str, str]] = {
    "request_action_log": {
        "url": "https://pre-stage-2-simplemdg-srv-process-system.cfapps.br10.hana.ondemand.com"
               "/CommonProcessService/RequestActionLog",
        "select": "modifiedBy,createdAt,createdBy,action,log",
        "orderby": "createdAt asc",
    },
    "submit_event_log": {
        "url": "https://pre-stage-2-simplemdg-srv-process-system.cfapps.br10.hana.ondemand.com"
               "/CommonProcessService/SubmitEventLog",
        "select": "",
        "orderby": "",
    },
    # Request-level log via OData navigation: <base>/Request(reqID='<id>')/requestLog.
    # Returns a SINGLE string in `value` (the concise request error), not an array — so it
    # is NOT filter/select/orderby based. `nav` marks this style; `url` is the base up to
    # /CommonProcessService (overridable on the node).
    "request_log": {
        "url": "https://pre-stage-2-simplemdg-srv-process-system.cfapps.br10.hana.ondemand.com"
               "/CommonProcessService",
        "nav": "requestLog",
        "select": "",
        "orderby": "",
    },
    # ABAP-side "MD API Log" (AdminService/MDApiLog) — a DIFFERENT app (srv-config-admin)
    # + service (AdminService), but same filter-by-reqID shape. Rows carry mdgLogID /
    # objectType / step / status / messageLog. Host is per-env, configured on the node.
    "abap_log": {
        "url": "https://mckesson-dev-simplemdg-srv-config-admin.cfapps.us20.hana.ondemand.com"
               "/AdminService/MDApiLog",
        "select": "",
        "orderby": "",
    },
}


def _oauth_token(token_url: str, client_id: str, client_secret: str,
                 grant_type: str = "client_credentials", timeout: int = 60) -> str:
    """OAuth 2.0 token request (grant_type configurable) → access_token (raises on failure)."""
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


def _build_url(base: str, filter_field: str, req_id: str, select: str, orderby: str) -> str:
    """OData URL: ``<base>?$select=..&$filter=<field> eq '<reqID>'&$orderby=..`` (encoded).

    Every part is caller-supplied (from the node's configurable fields); nothing about
    the endpoint is fixed here."""
    q = urllib.parse.quote
    filt = q(filter_field + " eq '" + req_id + "'")
    parts: list[str] = []
    if select:
        parts.append("$select=" + select)
    parts.append("$filter=" + filt)
    if orderby:
        parts.append("$orderby=" + q(orderby))
    return base + "?" + "&".join(parts)


def _prettify(resp_body: str, limit: int = 8000) -> tuple[str, int]:
    """Turn an OData ``{value:[...]}`` payload into readable one-line-per-entry text
    (``time | action/step | status | log``) so a Jira comment is legible instead of raw
    minified JSON. Returns (pretty_text, entry_count); ('', 0) if it can't parse."""
    try:
        data = json.loads(resp_body)
    except Exception:  # noqa: BLE001
        return "", 0
    rows = data.get("value") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return "", 0
    lines: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        # Field fallbacks span RequestActionLog / SubmitEventLog (createdAt/action/log)
        # AND the ABAP MDApiLog (changedDateTime/step/messageLog + mdgLogID).
        ts = str(r.get("createdAt") or r.get("modifiedAt") or r.get("changedDateTime") or "")[:19].replace("T", " ")
        action = str(r.get("action") or r.get("stepID") or r.get("step") or "")
        status = str(r.get("status") or "")
        log = str(r.get("log") or r.get("messageLog") or "").strip().replace("\r", " ").replace("\n", " ")
        parts = [p for p in [ts, action, status, log] if p]
        mdg = str(r.get("mdgLogID") or "")
        if mdg:
            parts.append("mdgLogID=" + mdg)
        if parts:
            lines.append(" | ".join(parts))
    text = "\n".join(lines)
    return text[:limit], len(rows)


def _pick_mdglog(resp_body: str) -> str:
    """The mdgLogID to carry downstream: the FAILED row's mdgLogID (isolates the failed
    run when a reqID has several submit attempts); else the last row that has one; else ''.
    Used off SubmitEventLog (its rows carry mdgLogID)."""
    try:
        rows = json.loads(resp_body).get("value")
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(rows, list):
        return ""
    for r in rows:
        if isinstance(r, dict) and str(r.get("status") or "").upper() in ("FAILED", "F") and r.get("mdgLogID"):
            return str(r.get("mdgLogID"))
    for r in reversed(rows):
        if isinstance(r, dict) and r.get("mdgLogID"):
            return str(r.get("mdgLogID"))
    return ""


def _pretty_json(resp_body: str, limit: int = 190000) -> str:
    """The FULL response value[] as indented JSON — every field per entry, for the md
    file (which, unlike the ADF comment, can hold large multi-line content)."""
    try:
        val = json.loads(resp_body).get("value")
    except Exception:  # noqa: BLE001
        return resp_body[:limit]
    if val is None:
        return resp_body[:limit]
    return json.dumps(val, indent=2, ensure_ascii=False)[:limit]


def _extract_pretty(resp_body: str, limit: int = 8000) -> tuple[str, int]:
    """pretty/count from either an array ``value`` (RequestActionLog/SubmitEventLog) OR a
    single string ``value`` (the requestLog navigation returns one string)."""
    try:
        v = json.loads(resp_body).get("value")
    except Exception:  # noqa: BLE001
        return _prettify(resp_body, limit)
    if isinstance(v, str):
        # This value is embedded into the Jira comment's ADF-JSON body. A raw newline /
        # double-quote / backslash there breaks the JSON, and jira_add_comment then dumps
        # the raw ADF as text. So flatten to ONE line and drop the JSON-hostile chars.
        v = " ".join(v.split()).replace('"', "'").replace("\\", "/").strip()
        # requestLog: show "None" when there is no request-level error, so the comment
        # reads "What user see: None" instead of a blank line.
        return (v[:limit] if v else "None"), (1 if v else 0)
    return _prettify(resp_body, limit)


def _fetch(service: str, inputs: Optional[dict], parameters: Optional[dict]) -> dict[str, Any]:
    """Login (client_credentials) then GET the log endpoint for reqID. All endpoint
    knobs come from the merged (parameters < inputs) config, defaulting per service."""
    dflt = _SERVICES.get(service, {})
    merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}

    req_id = str(merged.get("reqID") or merged.get("req_id") or "").strip()
    if not req_id:
        raise ValueError("'reqID' is required (the flow input).")

    token_url = str(merged.get("token_url") or "").strip() or _DEFAULT_TOKEN_URL
    base = str(merged.get("api_url") or "").strip() or dflt.get("url", "")
    if not base:
        raise ValueError("'api_url' is required (no default for this service).")
    grant_type = str(merged.get("grant_type") or "").strip() or "client_credentials"
    filter_field = str(merged.get("filter_field") or "").strip() or "reqID"
    # Optional raw OData $filter override (e.g. "mdgLogID eq '..' or reqID eq '..'").
    # When set it wins over filter_field/reqID — used by the ABAP node to match either id.
    raw_filter = str(merged.get("filter") or merged.get("odata_filter") or "").strip()
    # select/orderby: allow explicit empty-string override via the field; only fall back
    # to the service default when the key is entirely absent.
    select = merged["odata_select"] if "odata_select" in merged else dflt.get("select", "")
    orderby = merged["odata_orderby"] if "odata_orderby" in merged else dflt.get("orderby", "")
    select = str(select or "").strip()
    orderby = str(orderby or "").strip()

    # SA-2365: the credential comes ONLY from the workflow environment — bind the
    # node's "Login credential" field (credential_ref) to an $env credential
    # variable, e.g. {{$env.OAUTH}} (or {{$env.OAUTH_DEV}} / {{$env.OAUTH_QAS}} to
    # switch between several). There is NO local .env / secrets-file fallback:
    # deleting LOG_WORKER_CLIENT_ID_* changes nothing, and switching the run's
    # environment switches which credential this node uses.
    #
    # A generic-oauth2-app credential resolves to a READY BEARER TOKEN (the vault
    # performs the client_credentials OAuth and hands back a short-lived JWT), so
    # the client secret NEVER leaves the vault. A basic-auth credential (used to
    # hold distinct per-env OAuth clients — the vault caps oauth2-app at one per
    # tenant) carries username/password, and this worker does the OAuth exchange.
    _bearer = ""
    client_id = ""
    client_secret = ""
    try:
        from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth
        _cred = resolve_effective_auth(inputs or {})
    except Exception:
        _cred = None
    if isinstance(_cred, dict):
        _ctype = (_cred.get("type") or "").lower()
        _cfg = _cred.get("config") or {}
        if _ctype == "bearer" and str(_cfg.get("token") or "").strip():
            _bearer = str(_cfg.get("token")).strip()  # vault-issued JWT — no local OAuth
        else:
            client_id = str(_cfg.get("clientId") or _cfg.get("client_id") or _cfg.get("username") or "").strip()
            client_secret = str(_cfg.get("clientSecret") or _cfg.get("client_secret") or _cfg.get("password") or "").strip()
            _ctok = str(_cfg.get("tokenUrl") or _cfg.get("token_url") or "").strip()
            if _ctok:
                token_url = _ctok
    if not _bearer and not (client_id and client_secret):
        raise RuntimeError(
            "No credential: bind the node's 'Login credential' field to an $env "
            "credential variable (e.g. {{$env.OAUTH}}) — a generic-oauth2-app vault "
            "credential from the selected environment. There is no local .env fallback."
        )

    _trace(f"CALL {service} reqID={req_id} host={urllib.parse.urlparse(base).netloc}")
    start = time.monotonic()
    token = _bearer or _oauth_token(token_url, client_id, client_secret, grant_type)
    nav = dflt.get("nav")
    if nav:
        # <base>/Request(reqID='<id>')/<nav> — single-value navigation, no $filter/$select.
        url = base.rstrip("/") + "/Request(reqID='" + urllib.parse.quote(req_id) + "')/" + nav
    elif raw_filter:
        q = urllib.parse.quote
        parts = (["$select=" + select] if select else []) + ["$filter=" + q(raw_filter)]
        if orderby:
            parts.append("$orderby=" + q(orderby))
        url = base + "?" + "&".join(parts)
    else:
        url = _build_url(base, filter_field, req_id, select, orderby)
    req = urllib.request.Request(url, method="GET",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        status, resp_body = resp.status, resp.read().decode("utf-8", "replace")
    pretty, count = _extract_pretty(resp_body)
    _trace(f"DONE {service} reqID={req_id} -> HTTP {status} count={count}")
    return {
        "status": "OK",
        "status_code": status,
        "response_body": resp_body[:200000],
        "pretty": pretty,
        "response_pretty": _pretty_json(resp_body),
        "count": count,
        "mdgLogID": _pick_mdglog(resp_body),
        "reqID": req_id,
        "url": url,
        "elapsed_ms": (time.monotonic() - start) * 1000,
        "error": "",
    }


def _error_output(msg: str) -> dict[str, Any]:
    return {"status": "FAILED", "status_code": 0, "response_body": "", "pretty": "", "response_pretty": "",
            "count": 0, "mdgLogID": "", "reqID": "", "url": "", "elapsed_ms": 0, "error": (msg or "unknown error")[:400]}


def make_handler(service: str) -> Any:
    """Handler ``(inputs, parameters) -> dict`` for one log service. Non-fatal:
    returns a schema-complete error payload so the flow keeps flowing."""
    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        # The node FAILS (with the error surfaced) when the log fetch fails —
        # a 401/403/5xx or a missing credential is a real error the operator
        # must see, not a silent "completed with count 0". HTTPError bodies are
        # unwrapped into the message; any other exception propagates as-is.
        try:
            return _fetch(service, inputs, parameters)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            # A missing entity is NOT a fault: several landscapes serve no ABAP log at
            # all, and an optional branch must not take the run down with it. 404/400
            # ("Invalid resource path") therefore return the schema-complete empty
            # payload the pre-SA-2365 worker returned, so the flow keeps flowing and the
            # summary simply has no ABAP section. Auth and server faults still raise.
            if exc.code in (400, 404):
                return _error_output(
                    f"{service}: this landscape does not serve that entity — "
                    f"HTTP {exc.code}: {body}"
                )
            raise RuntimeError(f"{service} log fetch failed — HTTP {exc.code}: {body}") from exc
    return _handler


# -- Schemas ------------------------------------------------------------------

def input_schema_for(service: str) -> list[dict[str, Any]]:
    """Every endpoint knob is a configurable field, pre-filled with the service default."""
    dflt = _SERVICES.get(service, {})
    # SA-2365: the login credential is a configurable field — bind it to an $env
    # credential variable (e.g. {{$env.OAUTH}}) so switching the run's environment,
    # or dragging a different {{$env.OAUTH_*}} in, switches which OAuth client the
    # node authenticates with. No secret is stored on the node — only the ref.
    _login = text_field(
        "credential_ref", "Login credential",
        placeholder="{{$env.OAUTH}} — a credential variable from the environment",
    )
    if dflt.get("nav"):
        # Navigation-style endpoint (requestLog): no $filter/$select/$orderby — just the
        # base + reqID + login knobs.
        return [f.to_dict() for f in [
            text_field("reqID", "Request ID (reqID)",
                       placeholder="bound to the flow input, e.g. {{$input.reqID}}"),
            text_field("api_url", "API URL (base up to /CommonProcessService)",
                       placeholder="OData service base URL", default=dflt.get("url", "")),
            _login,
        ]]
    return [f.to_dict() for f in [
        text_field("reqID", "Request ID (reqID)",
                   placeholder="bound to the flow input, e.g. {{$input.reqID}}"),
        text_field("api_url", "API URL (base)",
                   placeholder="OData entity base URL", default=dflt.get("url", "")),
        _login,
        text_field("filter_field", "Filter field",
                   placeholder="OData field to filter reqID on", default="reqID"),
        text_field("filter", "Raw $filter (optional, overrides field+reqID)",
                   placeholder="e.g. mdgLogID eq '..' or reqID eq '..'"),
        text_field("odata_select", "$select (blank = none)",
                   placeholder="comma-separated fields", default=dflt.get("select", "")),
        text_field("odata_orderby", "$orderby (blank = none)",
                   placeholder="e.g. createdAt asc", default=dflt.get("orderby", "")),
        text_field("cred_env", "Credential set (blank = auto-detect from URL)",
                   placeholder="mck_dev | mck_qas | alcon_dev | alcon_qas | s4_qas"),
    ]]


OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    number_field("count", "Entry count", readonly=True),
    text_field("mdgLogID", "mdgLogID (from FAILED row)", readonly=True),
    textarea_field("pretty", "Readable log (one line per entry)", readonly=True),
    textarea_field("response_pretty", "Full response (indented JSON, all fields)", readonly=True),
    textarea_field("response_body", "Response Body (JSON)", readonly=True),
    text_field("reqID", "Request ID", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
