"""CF Requests and Logs — the dashboard's three saved searches, as one queryable node.

Reproduces SAP's "CF Requests and Logs" dashboard
(``maintained-by-perfx_cf-content-package_CF-Requests-and-Logs``) as an API call, so a
workflow can ask it the same questions the UI does: **a time window plus a query
context**.

The three contexts and their columns are lifted verbatim from the dashboard's own saved
searches (read from ``/api/saved_objects/search/…`` on the live instance, 2026-08-06) —
so results line up with what a human sees in the UI:

  ``requests``     type=request                       — app_name, correlation_id,
                                                        trace_id, method, request,
                                                        response_status, response_time_ms
  ``application``  (type=log OR type=trace) AND NOT source_type=RTR
                                                      — app_name, correlation_id,
                                                        trace_id, level, logger, msg
  ``runtime``      NOT (source_type=APP* OR source_type=RTR)
                                                      — app_name, source_type, msg
  ``all``          no context filter

All three sit on index pattern ``logs-cfsyslog-*`` and sort ``@timestamp desc``.

Auth: basic auth against the instance's ``basic_internal_auth_domain`` (verified enabled,
order 0, ``backend=internal``) — i.e. an **OpenSearch internal user**, NOT the service
key's dashboards credentials. Creds come from the shared local secrets file as
``cls_username`` / ``cls_password`` (see ``fetch_cls``).

Query language: the dashboard uses KQL; this node sends Lucene ``query_string`` instead,
because the ``_search`` API speaks Lucene, not KQL. For simple field:value expressions
they are identical — the difference is boolean keywords, which Lucene wants UPPERCASE
(``AND`` / ``OR`` / ``NOT``). A raw DSL escape hatch is provided for anything harder.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from worker_sdk.layer1_domain.auto_form_mapper import (
    number_field,
    text_field,
    textarea_field,
)

from app import secrets as _secrets
from app.fetch_cls import (
    _BODY_CAP,
    _DEFAULT_SECRETS_FILE,
    _MAX_SIZE,
    _PRETTY_CAP,
    _basic_header,
    _endpoint,
    _read_basic_creds,
)

_INDEX = "logs-cfsyslog-*"
_TIME = "@timestamp"
_DEFAULT_SIZE = 100

# Verbatim from the dashboard's saved searches. NOTE: the runtime context's
# ``source_type: "APP*"`` is a match_phrase, so the ``*`` is a literal, not a wildcard —
# that is SAP's own quirk and it is reproduced faithfully so this node and the UI agree.
_CONTEXTS: dict[str, dict[str, Any]] = {
    "requests": {
        "must": [{"match_phrase": {"type": {"query": "request"}}}],
        "must_not": [],
        "columns": ["app_name", "correlation_id", "trace_id", "method", "request",
                    "response_status", "response_time_ms"],
    },
    "application": {
        "must": [{"bool": {"should": [{"match_phrase": {"type": "log"}},
                                      {"match_phrase": {"type": "trace"}}],
                           "minimum_should_match": 1}}],
        "must_not": [{"match_phrase": {"source_type": "RTR"}}],
        "columns": ["app_name", "correlation_id", "trace_id", "level", "logger", "msg"],
    },
    "runtime": {
        "must": [],
        "must_not": [{"bool": {"should": [{"match_phrase": {"source_type": "APP*"}},
                                          {"match_phrase": {"source_type": "RTR"}}],
                               "minimum_should_match": 1}}],
        "columns": ["app_name", "source_type", "msg"],
    },
    "all": {"must": [], "must_not": [],
            "columns": ["app_name", "type", "source_type", "level", "msg"]},
}


def _build_body(context: str, query_text: str, raw_dsl: str, app_name: str,
                time_from: str, time_to: str, size: int, order: str) -> dict[str, Any]:
    if raw_dsl.strip():
        parsed = json.loads(raw_dsl)
        if not isinstance(parsed, dict):
            raise ValueError("'query_dsl' must be a JSON object")
        parsed.setdefault("size", size)
        return parsed

    spec = _CONTEXTS[context]
    must: list[dict[str, Any]] = list(spec["must"])
    must_not: list[dict[str, Any]] = list(spec["must_not"])

    if time_from or time_to:
        rng: dict[str, str] = {}
        if time_from:
            rng["gte"] = time_from
        if time_to:
            rng["lte"] = time_to
        must.append({"range": {_TIME: rng}})
    if app_name:
        must.append({"match_phrase": {"app_name": app_name}})
    if query_text:
        # Lucene. Carried as a JSON value, so it cannot restructure the query.
        must.append({"query_string": {"query": query_text, "default_operator": "AND"}})

    bool_q: dict[str, Any] = {}
    if must:
        bool_q["must"] = must
    if must_not:
        bool_q["must_not"] = must_not

    return {
        "size": size,
        "track_total_hits": True,
        "sort": [{_TIME: {"order": order}}],
        "query": {"bool": bool_q} if bool_q else {"match_all": {}},
    }


def _rows(payload: dict[str, Any], columns: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in (payload.get("hits") or {}).get("hits") or []:
        src = h.get("_source") if isinstance(h, dict) else None
        if not isinstance(src, dict):
            continue
        row: dict[str, Any] = {"@timestamp": src.get(_TIME)}
        for c in columns:
            row[c] = src.get(c)
        out.append(row)
    return out


def _prettify(rows: list[dict[str, Any]], columns: list[str],
              limit: int = _PRETTY_CAP) -> str:
    """One line per hit, in the dashboard's own column order."""
    lines: list[str] = []
    for r in rows:
        cells = [str(r.get(_TIME) or "")[:23].replace("T", " ")]
        for c in columns:
            v = r.get(c)
            if v is None or v == "":
                continue
            cells.append(str(v).replace("\r", " ").replace("\n", " ")[:200])
        lines.append(" | ".join(cells))
    return "\n".join(lines)[:limit]


def _total(payload: dict[str, Any], fallback: int) -> int:
    t = (payload.get("hits") or {}).get("total")
    if isinstance(t, dict):
        t = t.get("value")
    return int(t) if isinstance(t, int) else fallback


def _search(inputs: Optional[dict], parameters: Optional[dict]) -> dict[str, Any]:
    merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}

    context = (str(merged.get("context") or "").strip() or "application").lower()
    if context not in _CONTEXTS:
        raise ValueError(f"'context' must be one of {sorted(_CONTEXTS)}")

    secrets_file = str(merged.get("secrets_file") or "").strip() or _DEFAULT_SECRETS_FILE
    user, password, url_from_secrets = _read_basic_creds(secrets_file)
    base = str(merged.get("dashboards_url") or "").strip() or url_from_secrets
    if not base:
        raise ValueError(
            "'dashboards_url' is required — set it on the node, or $CLS_URL, or "
            "\"cls_url\" in the secrets file."
        )
    if not (user and password):
        raise RuntimeError(
            "cls_username/cls_password not found. Set $CLS_USERNAME + $CLS_PASSWORD, or "
            "add them to the secrets file. These are the OpenSearch INTERNAL user, NOT "
            f"the service key's dashboards user. Looked in: {_secrets.sources_hint(secrets_file)}"
        )

    order = (str(merged.get("sort_order") or "").strip() or "desc").lower()
    if order not in ("asc", "desc"):
        raise ValueError("'sort_order' must be 'asc' or 'desc'")
    try:
        size = int(merged.get("size") or _DEFAULT_SIZE)
    except (TypeError, ValueError):
        size = _DEFAULT_SIZE
    size = max(1, min(size, _MAX_SIZE))

    body = _build_body(
        context,
        str(merged.get("query") or "").strip(),
        str(merged.get("query_dsl") or ""),
        str(merged.get("app_name") or "").strip(),
        str(merged.get("time_from") or "").strip() or "now-15m",
        str(merged.get("time_to") or "").strip() or "now",
        size, order,
    )
    index = str(merged.get("index") or "").strip() or _INDEX
    access_mode = (str(merged.get("access_mode") or "").strip() or "console_proxy").lower()
    url, extra, _ = _endpoint(base, index, access_mode)

    start = time.monotonic()
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": _basic_header(user, password),
                 "Content-Type": "application/json", "Accept": "application/json", **extra},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        status, resp_body = resp.status, resp.read().decode("utf-8", "replace")

    try:
        payload = json.loads(resp_body)
    except Exception:  # noqa: BLE001
        payload = {}
    columns = _CONTEXTS[context]["columns"]
    rows = _rows(payload if isinstance(payload, dict) else {}, columns)

    return {
        "status": "OK",
        "status_code": status,
        "count": _total(payload if isinstance(payload, dict) else {}, len(rows)),
        "returned": len(rows),
        "pretty": _prettify(rows, columns),
        "rows": json.dumps(rows)[:_BODY_CAP],
        "response_body": resp_body[:_BODY_CAP],
        "query": json.dumps(body),
        "url": url,
        "elapsed_ms": (time.monotonic() - start) * 1000,
        "error": "",
    }


def _error_output(msg: str) -> dict[str, Any]:
    return {"status": "FAILED", "status_code": 0, "count": 0, "returned": 0, "pretty": "",
            "rows": "", "response_body": "", "query": "", "url": "", "elapsed_ms": 0,
            "error": (msg or "unknown error")[:400]}


def make_handler() -> Any:
    """Handler ``(inputs, parameters) -> dict``. Never raises; see the SUCCESS caveat
    in ``fetch_cls`` — downstream must gate on ``status``/``error``, because a rotated
    password shows up here as 401 and the task is still reported SUCCESS."""
    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            return _search(inputs, parameters)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            hint = ""
            if exc.code == 401:
                hint = " — wrong/rotated internal-user password."
            elif exc.code == 403:
                hint = (" — authenticated but not authorised; the role is missing read on "
                        f"'{_INDEX}' (a PUT on the user can silently wipe its roles).")
            return _error_output(f"HTTP {exc.code}: {detail}{hint}")
        except Exception as exc:  # noqa: BLE001
            return _error_output(str(exc))
    return _handler


# -- Schemas ------------------------------------------------------------------

INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("context", "Context (dashboard section)",
               placeholder="requests | application | runtime | all", default="application"),
    text_field("time_from", "From", placeholder="ISO or date math", default="now-15m"),
    text_field("time_to", "To", placeholder="ISO or date math", default="now"),
    text_field("query", "Query (Lucene; AND/OR uppercase)",
               placeholder='e.g. level:ERROR AND app_name:"simplemdg-srv"'),
    text_field("app_name", "App name (optional narrowing)", placeholder="exact app_name"),
    number_field("size", "Max hits", default=_DEFAULT_SIZE),
    text_field("sort_order", "Sort by time", placeholder="asc | desc", default="desc"),
    text_field("dashboards_url", "Cloud Logging dashboards URL",
               placeholder="or cls_url in the secrets file"),
    text_field("index", "Index pattern", default=_INDEX),
    text_field("access_mode", "Access route", placeholder="console_proxy | direct",
               default="console_proxy"),
    textarea_field("query_dsl", "Raw OpenSearch DSL (JSON, overrides everything above)",
                   placeholder='{"query":{"match_all":{}}}'),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    number_field("count", "Total matching hits", readonly=True),
    number_field("returned", "Hits returned", readonly=True),
    textarea_field("pretty", "Readable log (dashboard column order)", readonly=True),
    textarea_field("rows", "Rows (JSON, selected columns)", readonly=True),
    textarea_field("response_body", "Raw response (JSON)", readonly=True),
    textarea_field("query", "Query sent (JSON)", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
