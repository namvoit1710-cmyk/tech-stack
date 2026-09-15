"""SAP BTP Cloud Logging (OpenSearch) log search — the PULL side of the log worker.

Sibling of ``fetch_log`` (which reads CommonProcessService OData). This one reads the
**Cloud Logging** instance (`simplemdg-logging`, offering `cloud-logging`, plan `large`
— see ``.github/services/required-services.yml``). Cloud Logging is OpenSearch under the
hood, so a search here is an OpenSearch query.

⚠ SUPPORT STATUS — read before relying on this node.
   SAP does **not** currently expose the OpenSearch REST API for reading. Their official
   procedure ("Access and Analyze Observability Data") is: create a service key → open
   the dashboards URL **in a browser**. The Cloud Logging team's own answer (2024-11-28,
   restated 2025-12-02) is that direct API access is only *"schedule[d] to work on
   H2,2026"*, and a 2026-06-01 follow-up asking for progress went unanswered.
   So both routes below are UNSUPPORTED and may break on any service upgrade:
     ``console_proxy`` — OpenSearch Dashboards' Dev-Tools proxy (usually the only one open)
     ``direct``        — plain OpenSearch REST, if the instance exposes it
   Run ``cls_probe.py`` against your instance to find out which (if either) works.
   If the instance uses SAML (``saml.enabled=true``), basic auth is off and NEITHER works.

MEASURED against the live us10 instance (2026-08-06) — do not re-derive these:
   * The instance IS SAML-protected: unauthenticated ``GET /`` serves OpenSearch
     Dashboards' "OSD SAML Capture" page. An XSUAA client_credentials JWT is rejected
     401 "Authentication Exception" on EVERY route — wrong credential class entirely.
   * ``POST <base>/logs-*/_search`` → 404: plain OpenSearch REST is NOT exposed on the
     dashboards host, so ``access_mode="direct"`` will not work there.
   * ``POST /api/console/proxy?path=<index>/_search&method=POST`` → **200**, but only
     when the request carries a logged-in SAML **session cookie** (same-origin fetch
     from an authenticated dashboards tab). Headers needed: ``osd-xsrf: true``.
   * Real indices: ``logs-cfsyslog-*`` (CF app logs), ``metrics-otel-v1-*``,
     ``security-auditlog-*``. Time field ``@timestamp``; message field ``msg``; also
     ``level``, ``component_name``, ``space_name``, ``logger``, ``correlation_id``.
   * A 1-hour window returned 1.65M hits in ~1.2s — always bound ``size`` and the range.

   ⚠ CONSEQUENCE FOR THIS NODE: a SAML session cannot be obtained non-interactively, so
   a headless worker still has NO way in. This node is only usable once the instance is
   given a machine credential — either ``oidc`` enabled on the Cloud Logging instance,
   or a basic-auth dashboards user that survives SAML being on (untested — needs the
   service key's dashboards-username/password). Until then use ``access_mode`` values
   above only from a context that already holds a session.

Credentials: the same local secrets JSON the OData nodes use
(``~/.smdg-worker-secrets.json``, or ``LOG_WORKER_SECRETS`` / ``CLAUDE_WORKER_SECRETS``),
keys ``cls_username`` / ``cls_password`` (the ``dashboards-username`` /
``dashboards-password`` from the service key)::

    {"cls_username": "...", "cls_password": "...", "cls_url": "https://<dashboards-endpoint>"}

Note on injection: unlike the OData path, the query here is assembled as a **JSON
document via json.dumps**, so a hostile search value cannot escape into query syntax.
Keep it that way — do not build OpenSearch queries by string concatenation.
"""

import base64
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
# Local-only trace with per-worker identity (host/ip/pid/instance) — see app.worker_trace.
from app.worker_trace import trace as _trace

# CLS (Dashboards) keeps its own basic-auth secrets path; the OAuth client-cred
# fallback was removed from fetch_log (SA-2365 — MCK log credential comes from the
# workflow environment), so source this default here rather than from that module.
_DEFAULT_SECRETS_FILE = _secrets.DEFAULT_SECRETS_FILE

# Cloud Logging's default retention is 7 days (configurable 1-90 via retention_period),
# so a search window wider than that is silently empty, not wrong.
_DEFAULT_INDEX = "logs-cfsyslog-*"
_DEFAULT_SIZE = 100
_MAX_SIZE = 1000
_BODY_CAP = 200000
_PRETTY_CAP = 8000


def _read_basic_creds(path: str) -> tuple[str, str, str]:
    """(username, password, url) from env or the secrets JSON; ('','','') if absent.

    Env wins over the file — see ``app.secrets``. ``$CLS_USERNAME`` / ``$CLS_PASSWORD`` /
    ``$CLS_URL``, or the whole document in ``$SMDG_WORKER_SECRETS_JSON``."""
    raw = _secrets.load_secrets(path)
    return (_secrets.pick(raw, "cls_username", "dashboards_username", "dashboards-username"),
            _secrets.pick(raw, "cls_password", "dashboards_password", "dashboards-password"),
            _secrets.pick(raw, "cls_url", "dashboards_endpoint", "dashboards-endpoint"))


def _basic_header(user: str, password: str) -> str:
    """Basic-auth header. Shared with ``fetch_cf_dashboard`` so there is one impl."""
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _build_query(search_field: str, search_value: str, raw_dsl: str,
                 time_field: str, time_from: str, time_to: str, size: int) -> dict[str, Any]:
    """Assemble the OpenSearch request body.

    ``raw_dsl`` (a JSON object) wins outright when supplied — that is the power-user
    escape hatch. Otherwise a ``match_phrase`` on one field plus an optional time range,
    both carried as JSON *values*, never as concatenated syntax.
    """
    if raw_dsl.strip():
        parsed = json.loads(raw_dsl)
        if not isinstance(parsed, dict):
            raise ValueError("'query_dsl' must be a JSON object")
        parsed.setdefault("size", size)
        return parsed

    must: list[dict[str, Any]] = []
    if search_value:
        must.append({"match_phrase": {search_field: search_value}})
    if time_from or time_to:
        rng: dict[str, str] = {}
        if time_from:
            rng["gte"] = time_from
        if time_to:
            rng["lte"] = time_to
        must.append({"range": {time_field: rng}})

    return {
        "size": size,
        "sort": [{time_field: {"order": "asc"}}],
        "query": {"bool": {"must": must}} if must else {"match_all": {}},
    }


def _endpoint(base: str, index: str, access_mode: str) -> tuple[str, dict[str, str], bool]:
    """(url, extra_headers, body_goes_in_query) for the chosen access route."""
    base = base.rstrip("/")
    if access_mode == "direct":
        # Plain OpenSearch REST — only if the instance actually exposes it.
        return f"{base}/{urllib.parse.quote(index)}/_search", {}, False
    # Dev-Tools console proxy. The real path travels in ?path=, the DSL in the body.
    path = urllib.parse.quote(f"{index}/_search", safe="")
    return (f"{base}/api/console/proxy?path={path}&method=POST",
            {"osd-xsrf": "true"}, False)


def _prettify(payload: dict[str, Any], time_field: str,
              limit: int = _PRETTY_CAP) -> tuple[str, int]:
    """OpenSearch ``hits.hits[]`` -> one readable line per entry. ('', 0) if unparseable.

    Mirrors ``fetch_log._prettify`` so both log sources render the same way downstream.
    """
    hits = (payload.get("hits") or {}).get("hits")
    if not isinstance(hits, list):
        return "", 0
    lines: list[str] = []
    for h in hits:
        src = h.get("_source") if isinstance(h, dict) else None
        if not isinstance(src, dict):
            continue
        ts = str(src.get(time_field) or src.get("@timestamp") or "")[:23].replace("T", " ")
        level = str(src.get("level") or src.get("severity") or "")
        comp = str(src.get("component_name") or src.get("container_name") or "")
        msg = str(src.get("msg") or src.get("message") or "").strip()
        msg = msg.replace("\r", " ").replace("\n", " ")
        parts = [p for p in [ts, level, comp, msg] if p]
        if parts:
            lines.append(" | ".join(parts))
    total = (payload.get("hits") or {}).get("total")
    if isinstance(total, dict):
        total = total.get("value")
    return "\n".join(lines)[:limit], int(total if isinstance(total, int) else len(hits))


def _search(inputs: Optional[dict], parameters: Optional[dict]) -> dict[str, Any]:
    merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}

    secrets_file = str(merged.get("secrets_file") or "").strip() or _DEFAULT_SECRETS_FILE
    user, password, url_from_secrets = _read_basic_creds(secrets_file)

    # SA-2365: a vault credential bound via the node's "Login credential" field
    # (credential_ref, e.g. {{$env.CLS_LOGIN}}) WINS over the local secrets file.
    # A generic-basic-auth credential carries the dashboards username/password, so
    # switching the run's environment switches which account this node uses.
    try:
        from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth
        _cred = resolve_effective_auth(inputs or {})
    except Exception:  # noqa: BLE001 — never break the node on a resolver hiccup
        _cred = None
    if isinstance(_cred, dict):
        _cfg = _cred.get("config") or {}
        _u = str(_cfg.get("username") or _cfg.get("clientId") or "").strip()
        _p = str(_cfg.get("password") or _cfg.get("clientSecret") or "").strip()
        if _u and _p:
            user, password = _u, _p

    base = str(merged.get("dashboards_url") or "").strip() or url_from_secrets
    if not base:
        raise ValueError(
            "'dashboards_url' is required (the Cloud Logging dashboards-endpoint) — "
            "set it on the node, or $CLS_URL, or \"cls_url\" in the secrets file."
        )
    if not (user and password):
        raise RuntimeError(
            "cls_username/cls_password not found. Set $CLS_USERNAME + $CLS_PASSWORD, or "
            "add them to the secrets file. These are the OpenSearch INTERNAL user, NOT "
            f"the service key's dashboards user. Looked in: {_secrets.sources_hint(secrets_file)}"
        )

    access_mode = (str(merged.get("access_mode") or "").strip() or "console_proxy").lower()
    if access_mode not in ("console_proxy", "direct"):
        raise ValueError("'access_mode' must be 'console_proxy' or 'direct'")

    index = str(merged.get("index") or "").strip() or _DEFAULT_INDEX
    time_field = str(merged.get("time_field") or "").strip() or "@timestamp"
    search_field = str(merged.get("search_field") or "").strip() or "msg"
    search_value = str(merged.get("search_value") or merged.get("reqID") or "").strip()
    raw_dsl = str(merged.get("query_dsl") or "")
    time_from = str(merged.get("time_from") or "").strip()
    time_to = str(merged.get("time_to") or "").strip()
    try:
        size = int(merged.get("size") or _DEFAULT_SIZE)
    except (TypeError, ValueError):
        size = _DEFAULT_SIZE
    size = max(1, min(size, _MAX_SIZE))

    body = _build_query(search_field, search_value, raw_dsl,
                        time_field, time_from, time_to, size)
    url, extra, _ = _endpoint(base, index, access_mode)

    _trace(f"CALL cloud_logging_search value={search_value!r} host={urllib.parse.urlparse(base).netloc}")
    start = time.monotonic()
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": _basic_header(user, password),
            "Content-Type": "application/json",
            "Accept": "application/json",
            **extra,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        status, resp_body = resp.status, resp.read().decode("utf-8", "replace")

    try:
        payload = json.loads(resp_body)
    except Exception:  # noqa: BLE001
        payload = {}
    pretty, count = _prettify(payload if isinstance(payload, dict) else {}, time_field)
    _trace(f"DONE cloud_logging_search -> HTTP {status} count={count}")

    return {
        "status": "OK",
        "status_code": status,
        "response_body": resp_body[:_BODY_CAP],
        "pretty": pretty,
        "count": count,
        "query": json.dumps(body),
        "url": url,
        "elapsed_ms": (time.monotonic() - start) * 1000,
        "error": "",
    }


def _error_output(msg: str) -> dict[str, Any]:
    return {"status": "FAILED", "status_code": 0, "response_body": "", "pretty": "", "count": 0,
            "query": "", "url": "", "elapsed_ms": 0, "error": (msg or "unknown error")[:400]}


def make_handler() -> Any:
    """Handler ``(inputs, parameters) -> dict``.

    Matches ``fetch_log``'s contract: never raises, always returns a schema-complete
    payload. CAVEAT (same as the OData nodes): the executor therefore marks the task
    SUCCESS even on a 401/404/timeout — downstream MUST gate on ``status``/``error``.
    """
    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            return _search(inputs, parameters)
        except urllib.error.HTTPError as exc:
            _trace(f"ERROR cloud_logging_search -> HTTP {exc.code}")
            detail = exc.read().decode("utf-8", "replace")[:300]
            hint = ""
            if exc.code in (401, 403):
                hint = (" — basic auth rejected; if this instance has SAML enabled "
                        "(saml.enabled=true) there is no basic-auth read path at all.")
            elif exc.code == 404:
                hint = (" — route not found; try access_mode='direct', or the console "
                        "proxy is disabled on this instance.")
            return _error_output(f"HTTP {exc.code}: {detail}{hint}")
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the node
            _trace(f"ERROR cloud_logging_search -> {str(exc)[:120]}")
            return _error_output(str(exc))
    return _handler


# -- Schemas ------------------------------------------------------------------

INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("search_value", "Search value",
               placeholder="e.g. {{$input.reqID}} — falls back to the reqID field"),
    text_field("search_field", "Search field",
               placeholder="log field to match on", default="msg"),
    text_field("dashboards_url", "Cloud Logging dashboards URL",
               placeholder="dashboards-endpoint from the service key (or cls_url in secrets)"),
    text_field("credential_ref", "Login credential",
               placeholder="{{$env.CLS_LOGIN}} — a basic-auth credential from the environment"),
    text_field("access_mode", "Access route",
               placeholder="console_proxy | direct", default="console_proxy"),
    text_field("index", "Index pattern", placeholder="OpenSearch index", default=_DEFAULT_INDEX),
    text_field("time_field", "Time field", placeholder="timestamp field", default="@timestamp"),
    text_field("time_from", "From (ISO or now-7d)", placeholder="e.g. now-24h"),
    text_field("time_to", "To (ISO or now)", placeholder="e.g. now"),
    number_field("size", "Max hits", default=_DEFAULT_SIZE),
    textarea_field("query_dsl", "Raw OpenSearch query DSL (JSON, overrides the fields above)",
                   placeholder='{"query":{"match_all":{}}}'),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    number_field("count", "Hit count", readonly=True),
    textarea_field("pretty", "Readable log (one line per entry)", readonly=True),
    textarea_field("response_body", "Response Body (JSON)", readonly=True),
    textarea_field("query", "Query sent (JSON)", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
