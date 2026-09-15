"""Self-authenticating SMDG log fetchers, addressed by PROJECT + ENVIRONMENT.

Same four services as the log-worker — ``request_log`` (what the user sees),
``request_action_log``, ``submit_event_log``, ``abap_log`` — and the same OAuth
``client_credentials`` login inside the node. One thing is different, and it is the whole
reason this worker exists:

    log-worker:  the node carries the api_url and the token_url, and the credential set
                 is GUESSED from that url ("alcon" in the host => the Alcon client,
                 "-qas" => the QAS client, checked in a specific order because an
                 Alcon-QAS host contains both).
    cs-worker:   the node carries ``project`` + ``environment``; the url AND the
                 credentials both come from ``app/registry.py``.

That inversion is what lets ONE workflow serve every project. It also removes the
host-token guessing entirely — a class of bug where a URL typo silently authenticates
against a different customer's tenant.

An explicit ``api_url`` / ``token_url`` on the node still wins, so a one-off endpoint can
be pointed at without touching the registry. The credentials are NEVER overridable from
the node: a workflow definition is a shared, exportable document, and a client secret does
not belong in one.

Failure contract, unchanged from the log-worker and relied on by the flows: handlers never
raise. A 401, a 404 or a network blip returns ``{"status": "FAILED", "error": …}`` as
DATA, and the node goes green. Every downstream node must gate on ``status`` / ``error``
— nothing in the platform does it for you. (This is exactly why ``cs_detect_request``
probes the CR before the flow spends two LLM calls on an empty answer.)
"""

from __future__ import annotations

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

from app import secrets as _secrets
from app.registry import REGISTRY, Target
from app.worker_trace import trace as _trace

_DEFAULT_SECRETS_FILE = _secrets.DEFAULT_SECRETS_FILE

# Per-service OData shaping. The URL is NOT here — it comes from the target (see
# ``Target.url_for``); only the query shape and the per-service defaults are.
_SERVICES: dict[str, dict[str, str]] = {
    "request_action_log": {"select": "modifiedBy,createdAt,createdBy,action,log",
                           "orderby": "createdAt asc"},
    "submit_event_log": {"select": "", "orderby": ""},
    # Request-level log via OData navigation: <base>/Request(reqID='<id>')/requestLog.
    # Returns a SINGLE string in `value` (the concise request error), not an array, so it
    # is not filter/select/orderby based. `nav` marks that style.
    "request_log": {"nav": "requestLog", "select": "", "orderby": ""},
    # ABAP-side MD API log — a different app (srv-config-admin) and service (AdminService),
    # same filter-by-reqID shape. The entity name differs per landscape, which is why the
    # registry carries the whole abap_url rather than deriving it here.
    "abap_log": {"select": "", "orderby": ""},
}


def resolve_target(merged: dict[str, Any]) -> tuple[Optional[Target], str]:
    """(target, error). The error is the sentence the operator needs — which project and
    environment were asked for, and what IS configured — never a bare KeyError."""
    project = str(merged.get("project") or "").strip()
    environment = str(merged.get("environment") or merged.get("env") or "").strip()
    if not project or not environment:
        return None, ("'project' and 'environment' are both required "
                      f"(got project={project!r}, environment={environment!r}). "
                      f"Configured: {REGISTRY.catalogue() or 'nothing'}.")
    target = REGISTRY.resolve(project, environment)
    if target is None:
        return None, (f"No credentials configured for project '{project}' environment "
                      f"'{environment}'. Configured: {REGISTRY.catalogue() or 'nothing'}. "
                      f"Add $LOG_WORKER_CLIENT_ID_<PROJECT>_<ENV>, "
                      f"$LOG_WORKER_CLIENT_SECRET_<PROJECT>_<ENV>, "
                      f"$LOG_WORKER_APP_PREFIX_<PROJECT>_<ENV> and "
                      f"$LOG_WORKER_REGION_<PROJECT>_<ENV>, then restart the worker.")
    return target, ""


def oauth_token(token_url: str, client_id: str, client_secret: str,
                grant_type: str = "client_credentials", timeout: int = 60) -> str:
    """OAuth 2.0 token request -> access_token (raises on failure)."""
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
    """``<base>?$select=..&$filter=<field> eq '<reqID>'&$orderby=..`` (encoded)."""
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
    """An OData ``{value:[…]}`` payload as readable one-line-per-entry text
    (``time | action/step | status | log``). ('', 0) if it can't parse."""
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
    """The mdgLogID to carry downstream: the FAILED row's (which isolates the failed run
    when a reqID has several submit attempts); else the last row that has one; else ''."""
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
    """The FULL ``value[]`` as indented JSON — every field per entry, for the .md file
    (which, unlike a chat reply, can hold large multi-line content)."""
    try:
        val = json.loads(resp_body).get("value")
    except Exception:  # noqa: BLE001
        return resp_body[:limit]
    if val is None:
        return resp_body[:limit]
    return json.dumps(val, indent=2, ensure_ascii=False)[:limit]


def _extract_pretty(resp_body: str, limit: int = 8000) -> tuple[str, int]:
    """pretty/count from either an array ``value`` (the log entities) OR a single string
    ``value`` (the requestLog navigation returns one string)."""
    try:
        v = json.loads(resp_body).get("value")
    except Exception:  # noqa: BLE001
        return _prettify(resp_body, limit)
    if isinstance(v, str):
        # This value gets embedded into a JSON body downstream (the upload node, and
        # historically a Jira ADF comment). A raw newline / double quote / backslash there
        # breaks that JSON, so flatten to one line and drop the JSON-hostile characters.
        v = " ".join(v.split()).replace('"', "'").replace("\\", "/").strip()
        # Show "None" rather than a blank when there is no request-level error, so the
        # reply reads "What user see: None".
        return (v[:limit] if v else "None"), (1 if v else 0)
    return _prettify(resp_body, limit)


def fetch(service: str, merged: dict[str, Any]) -> dict[str, Any]:
    """Resolve the target, log in, GET. Raises; ``make_handler`` turns that into data."""
    dflt = _SERVICES.get(service, {})

    req_id = str(merged.get("reqID") or merged.get("req_id") or "").strip()
    if not req_id:
        raise ValueError("'reqID' is required (the flow input).")

    target, err = resolve_target(merged)
    if target is None:
        # An explicit api_url + token_url is still allowed to stand alone — but without a
        # target there are no credentials, so this is genuinely unrecoverable. Say which
        # of the two things is missing rather than failing on the OAuth call.
        raise RuntimeError(err)

    token_url = str(merged.get("token_url") or "").strip() or target.token_url
    base = str(merged.get("api_url") or "").strip() or target.url_for(service)
    if service == "abap_log" and not base:
        # Not every landscape serves the ABAP MD API log — see the measured table in
        # app/registry.py. Say that in a sentence. The alternative, which the published
        # per-project flows do today, is a raw OData 404 on a node that still goes green.
        raise RuntimeError(
            f"The ABAP MD API log is not available for {target.project} "
            f"{target.environment} — that landscape's AdminService does not serve it. "
            f"(Set $LOG_WORKER_ABAP_ENTITY_{target.key} if it gains one.)")
    grant_type = str(merged.get("grant_type") or "").strip() or "client_credentials"
    filter_field = str(merged.get("filter_field") or "").strip() or "reqID"
    # Optional raw OData $filter override (e.g. "mdgLogID eq '..' or reqID eq '..'").
    # When set it wins over filter_field/reqID — the ABAP node uses it to match either id.
    raw_filter = str(merged.get("filter") or merged.get("odata_filter") or "").strip()
    # select/orderby: allow an explicit empty-string override from the field; fall back to
    # the service default only when the key is entirely absent.
    select = merged["odata_select"] if "odata_select" in merged else dflt.get("select", "")
    orderby = merged["odata_orderby"] if "odata_orderby" in merged else dflt.get("orderby", "")
    select = str(select or "").strip()
    orderby = str(orderby or "").strip()

    if not (target.client_id and target.client_secret):
        raise RuntimeError(
            f"client_id/client_secret not found for {target.key}. Set "
            f"$LOG_WORKER_CLIENT_ID_{target.key} + $LOG_WORKER_CLIENT_SECRET_{target.key}, "
            f"or add them to the secrets file. Looked in: {_secrets.sources_hint()}")

    _trace(f"CALL {service} reqID={req_id} target={target.key} "
           f"host={urllib.parse.urlparse(base).netloc}")
    start = time.monotonic()
    token = oauth_token(token_url, target.client_id, target.client_secret, grant_type)

    nav = dflt.get("nav")
    if nav:
        # <base>/Request(reqID='<id>')/<nav> — keyed navigation, no $filter/$select. This
        # is the shape that distinguishes "no such request" (404) from "request with no
        # error" (200 + empty), which is what makes the CR existence probe possible.
        url = base.rstrip("/") + "/Request(reqID='" + urllib.parse.quote(req_id) + "')/" + nav
    elif raw_filter:
        q = urllib.parse.quote
        parts = (["$select=" + select] if select else []) + ["$filter=" + q(raw_filter)]
        if orderby:
            parts.append("$orderby=" + q(orderby))
        url = base + "?" + "&".join(parts)
    else:
        url = _build_url(base, filter_field, req_id, select, orderby)

    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        status, resp_body = resp.status, resp.read().decode("utf-8", "replace")
    pretty, count = _extract_pretty(resp_body)
    _trace(f"DONE {service} reqID={req_id} target={target.key} -> HTTP {status} count={count}")
    return {
        "status": "OK",
        "status_code": status,
        "response_body": resp_body[:200000],
        "pretty": pretty,
        "response_pretty": _pretty_json(resp_body),
        "count": count,
        "mdgLogID": _pick_mdglog(resp_body),
        "reqID": req_id,
        "project": target.project,
        "environment": target.environment,
        # The link a human opens to see this CR in that landscape's web app. Empty when
        # the landscape has no web app - never a guessed URL.
        "cr_url": target.cr_url(req_id),
        "url": url,
        "elapsed_ms": (time.monotonic() - start) * 1000,
        "error": "",
    }


def _error_output(msg: str, merged: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    m = merged or {}
    return {"status": "FAILED", "status_code": 0, "response_body": "", "pretty": "",
            "response_pretty": "", "count": 0, "mdgLogID": "",
            "reqID": str(m.get("reqID") or ""), "project": str(m.get("project") or ""),
            "environment": str(m.get("environment") or ""), "cr_url": "",
            "url": "", "elapsed_ms": 0, "error": (msg or "unknown error")[:400]}


def make_handler(service: str) -> Any:
    """Handler ``(inputs, parameters) -> dict`` for one log service. Non-fatal: returns a
    schema-complete error payload so the flow keeps flowing to its reply node."""
    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        try:
            return fetch(service, merged)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            _trace(f"ERROR {service} -> HTTP {exc.code}")
            return _error_output(f"HTTP {exc.code}: {body}", merged)
        except Exception as exc:  # noqa: BLE001 — surface as data, don't crash the node
            _trace(f"ERROR {service} -> {str(exc)[:120]}")
            return _error_output(str(exc), merged)
    return _handler


# -- Schemas ------------------------------------------------------------------

_COMMON_TARGET_FIELDS = [
    text_field("reqID", "Change request (reqID)",
               placeholder="bound to the flow input, e.g. {{$input.reqID}}"),
    text_field("project", "Project",
               placeholder="bound to the flow input, e.g. {{$input.project}} - MCK, ALCON, S4 …"),
    text_field("environment", "Environment",
               placeholder="bound to the flow input, e.g. {{$input.environment}} - DEV, QAS …"),
]

_COMMON_OVERRIDES = [
    text_field("api_url", "API URL override (blank = from the project registry)",
               placeholder="only for a one-off endpoint"),
    text_field("token_url", "OAuth Token URL override (blank = from the project registry)",
               placeholder="only for a one-off endpoint"),
    text_field("grant_type", "OAuth grant_type",
               placeholder="OAuth grant type", default="client_credentials"),
]


def input_schema_for(service: str) -> list[dict[str, Any]]:
    dflt = _SERVICES.get(service, {})
    if dflt.get("nav"):
        # Navigation-style endpoint (requestLog): no $filter/$select/$orderby.
        return [f.to_dict() for f in (_COMMON_TARGET_FIELDS + _COMMON_OVERRIDES)]
    return [f.to_dict() for f in (_COMMON_TARGET_FIELDS + _COMMON_OVERRIDES + [
        text_field("filter_field", "Filter field",
                   placeholder="OData field to filter reqID on", default="reqID"),
        text_field("filter", "Raw $filter (optional, overrides field+reqID)",
                   placeholder="e.g. mdgLogID eq '..' or reqID eq '..'"),
        text_field("odata_select", "$select (blank = none)",
                   placeholder="comma-separated fields", default=dflt.get("select", "")),
        text_field("odata_orderby", "$orderby (blank = none)",
                   placeholder="e.g. createdAt asc", default=dflt.get("orderby", "")),
    ])]


OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    number_field("count", "Entry count", readonly=True),
    text_field("mdgLogID", "mdgLogID (from FAILED row)", readonly=True),
    textarea_field("pretty", "Readable log (one line per entry)", readonly=True),
    textarea_field("response_pretty", "Full response (indented JSON, all fields)", readonly=True),
    textarea_field("response_body", "Response Body (JSON)", readonly=True),
    text_field("reqID", "Change request", readonly=True),
    text_field("project", "Project", readonly=True),
    text_field("environment", "Environment", readonly=True),
    text_field("cr_url", "Link to the change request in the web app", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
