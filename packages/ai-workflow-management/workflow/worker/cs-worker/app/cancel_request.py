"""Cancel a change request through ``RequestorService/cancelRequest``.

Addressed by PROJECT + ENVIRONMENT like every other node here: the endpoint and the OAuth
client both come from ``app/registry.py``, so cancelling on a new landscape is four
environment variables rather than a code change.

**This is the only node in this worker that changes anything.** Everything else reads.

The endpoint, and how it was found
----------------------------------

``POST <prefix>-srv-process-requestor.cfapps.<region>…/RequestorService/cancelRequest``
with ``{reqID, comment}``. This is the action the UI's own Cancel button performs, it
accepts a plain ``client_credentials`` token, and it exists on every landscape checked --
S4 QAS and MCK DEV both answered ``200 {"message": "Request has been cancelled"}`` on
2026-09-07, and a real request went from ``FAILED`` to ``CANCELLED``.

Worth writing down because it cost an afternoon: the first implementation used
``CommonProcessService/cancelStuckRequest``, which the ticket documented. That action
exists on MCK DEV and MCK QAS but **not** on ALCON DEV or S4 QAS, so the work looked like
it could only ever serve MCK. It was not a landscape limitation at all -- the app serving
``RequestorService`` is ``srv-process-requestor``, the same shape as
``srv-process-system``, and nine hand-guessed host names missed it. The name comes from
the CDS module, ``srv_process_requestor``. ``cancelStuckRequest`` is deliberately NOT used
here: one action that works everywhere beats two with a fallback between them.

The contract
------------

The handler **never raises**. Every outcome is data on the success port, the same as the log
fetchers, so a flow gates on ``ok`` rather than on ports or HTTP codes:

* ``ok=true``, ``code=CANCELLED`` -- cancelled.
* ``ok=false`` with the backend's own code (e.g. ``CAN_NOT_LOCK_REQUEST``) -- refused.
  Nothing is broken; the answer is no.
* ``ok=false``, ``code=CANCEL_ACTION_NOT_AVAILABLE`` -- this landscape's service does not
  carry the action. That is a 404 which NAMES the action, distinguishable from a wrong
  host: a wrong host gives a router error with no action name in it.
* ``status=FAILED`` with ``error`` set -- a real fault (401, 500, a wrong endpoint).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from worker_sdk.layer1_domain.auto_form_mapper import number_field, text_field, textarea_field

from app.fetch_log import oauth_token, resolve_target
from app.worker_trace import trace as _trace

ACTION = "cancelRequest"

# Normalised success: the backend answers with a sentence
# ({"message": "Request has been cancelled"}), not a code, and a flow should compare a
# code rather than an English string that a future release may reword.
CANCELLED = "CANCELLED"

# The backend's own refusal, kept as a constant because a flow compares it and a typo in a
# comparison is invisible.
CANNOT_LOCK = "CAN_NOT_LOCK_REQUEST"

# Ours, not the backend's: the landscape does not have the action at all.
ACTION_NOT_AVAILABLE = "CANCEL_ACTION_NOT_AVAILABLE"


def unwrap_error(body: str) -> tuple[str, str]:
    """``(code, message)`` out of the backend's error envelope.

    ``error.code`` is usually the HTTP status rather than the domain code. When it is not
    numeric it IS the domain code (``CAN_NOT_LOCK_REQUEST``), and that is preferred --
    it is the backend naming its own reason. Otherwise the code hides inside
    ``error.message``, which is EITHER a JSON document::

        {"code":"CR_HEALTH_REQUEST_STILL_RUNNING","reqID":"…",…}

    ...or a bare string. Both shapes come off the live API, so both are handled: a flow
    that has to regex a message to tell one refusal from another will get it wrong.
    """
    try:
        outer = json.loads(body or "{}")
    except (ValueError, TypeError):
        return "", (body or "")[:400]
    inner = outer.get("error") if isinstance(outer, dict) else None
    if not isinstance(inner, dict):
        return "", (body or "")[:400]
    raw = inner.get("message")
    sibling = str(inner.get("code") or "").strip()
    if sibling and not sibling.isdigit():
        return sibling, str(raw if raw is not None else sibling)[:400]
    if raw is None:
        return "", (body or "")[:400]
    raw = str(raw)
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return raw.strip(), raw.strip()
    if isinstance(doc, dict):
        return str(doc.get("code") or "").strip(), raw
    return "", raw


def _reply(o: dict[str, Any]) -> str:
    """The sentence a human reads, composed from the outcome this node just reported.

    Same four cases the flow used to switch on, in the same order -- FAILED is asked before
    the refusals because a fault also has ``ok=false`` and would otherwise read as one.

    It lives here rather than in the flow because the engine's expression grammar is pure
    path substitution: ``{{$source.path}}`` and nothing else, no conditional. So in a flow
    each of these four sentences costs its own node plus a branch to select it. Here they
    cost four lines and are covered by the unit tests that already exercise these paths.

    The trade is real and worth stating: rewording now needs a worker deploy, where before
    it was a flow PUT. These four are contract-shaped -- they mirror the backend's own codes
    -- so they are not the copy anyone tunes weekly.
    """
    req = o.get("reqID") or ""
    url = str(o.get("cr_url") or "").strip()
    # Markdown, which is what Teams renders. A blank cr_url is a real answer (no web app on
    # that landscape, or the target never resolved), so fall back to the bare number.
    label = "[%s](%s)" % (req, url) if (req and url) else (req or "the request")
    where = " ".join(x for x in (o.get("project"), o.get("environment")) if x) or "that landscape"
    if o.get("status") == "FAILED":
        # Named only when known: before a reqID is parsed this would read "the cancel call
        # for the request", which is worse than not mentioning it.
        subject = " for %s" % label if req else ""
        return ("❌ The cancel call%s did not go through: %s (HTTP %s)."
                % (subject, o.get("error") or "unknown error", o.get("status_code")))
    if o.get("ok"):
        return "✅ %s has been cancelled on %s." % (label, where)
    if o.get("code") == ACTION_NOT_AVAILABLE:
        return ("ℹ️ %s was not cancelled: %s does not offer the cancelRequest "
                "action — its service is a build without it. Cancel the request in "
                "the web app instead." % (label, where))
    # The catch-all, and the useful one: the backend's own code and words, so a refusal
    # this worker has never seen still reaches the user intact.
    return ("⚠️ %s was not cancelled. %s answered %s: %s"
            % (label, where, o.get("code") or "no code", o.get("message") or ""))


def _output(**over: Any) -> dict[str, Any]:
    base = {
        "status": "OK", "status_code": 0, "ok": False, "message": "", "code": "",
        "reqID": "", "project": "", "environment": "", "cr_url": "", "url": "",
        "response_body": "", "elapsed_ms": 0, "error": "",
    }
    base.update(over)
    # Last, and from the merged dict: the sentence has to describe what is actually being
    # returned, not what the caller passed in.
    base["reply"] = _reply(base)
    return base


def _failed(msg: str, merged: Optional[dict[str, Any]] = None, **over: Any) -> dict[str, Any]:
    """A fault, as data. Built as a dict rather than as keyword arguments because callers
    pass a context block that also carries reqID/project/environment, and duplicating those
    as kwargs is a TypeError on exactly the paths that only run when something has already
    gone wrong."""
    m = merged or {}
    fields: dict[str, Any] = {
        "reqID": str(m.get("reqID") or ""),
        "project": str(m.get("project") or ""),
        "environment": str(m.get("environment") or m.get("env") or ""),
    }
    fields.update(over)
    return _output(status="FAILED", error=(msg or "unknown error")[:400], **fields)


def cancel(merged: dict[str, Any]) -> dict[str, Any]:
    """Resolve the target, log in, POST the action. Never raises."""
    req_id = str(merged.get("reqID") or "").strip()
    if not req_id:
        return _failed("'reqID' is required -- bind it to the flow input.", merged)

    target, err = resolve_target(merged)
    if target is None:
        return _failed(err, merged)

    comment = str(merged.get("comment") or "").strip() or "Cancelled via ai-workflow"
    requestor_url = str(merged.get("requestor_url") or "").strip() or target.requestor_url
    token_url = str(merged.get("token_url") or "").strip() or target.token_url

    ctx = {"reqID": req_id, "project": target.project,
           "environment": target.environment, "cr_url": target.cr_url(req_id)}

    if not requestor_url:
        return _failed(
            "No RequestorService URL for %s. Set $LOG_WORKER_APP_PREFIX_%s + "
            "$LOG_WORKER_REGION_%s, or $LOG_WORKER_REQUESTOR_URL_%s."
            % (target.key, target.key, target.key, target.key), merged, **ctx)

    try:
        token = oauth_token(token_url, target.client_id, target.client_secret,
                            str(merged.get("grant_type") or "client_credentials"))
    except Exception as exc:  # noqa: BLE001 -- a login failure is data, not a crash
        return _failed("OAuth login failed: %s" % exc, merged, **ctx)

    url = requestor_url.rstrip("/") + "/" + ACTION
    payload = json.dumps({"reqID": req_id, "comment": comment}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "Accept": "application/json"})

    _trace("CALL %s reqID=%s host=%s"
           % (ACTION, req_id, urllib.parse.urlparse(url).netloc))
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status_code, body = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        elapsed = (time.monotonic() - start) * 1000
        code, message = unwrap_error(body)
        if exc.code == 404 and "invalid resource path" in (message or "").lower():
            code = ACTION_NOT_AVAILABLE
        _trace("DONE %s reqID=%s -> HTTP %s %s" % (ACTION, req_id, exc.code, code or "?"))
        # A 400 is the backend REFUSING, and a 404 naming the action is the landscape
        # saying it does not have it. Both are answers about the request, so both are
        # data. Anything else (401, 500, a router 404) is a fault.
        refused = exc.code == 400 or code == ACTION_NOT_AVAILABLE
        return _output(
            status="OK" if refused else "FAILED", status_code=exc.code, ok=False,
            code=code or message, message=message, url=url,
            response_body=body[:200000], elapsed_ms=elapsed,
            error="" if refused else ("HTTP %s: %s" % (exc.code, (message or body)[:300])),
            **ctx)
    except Exception as exc:  # noqa: BLE001
        return _failed("%s: %s" % (type(exc).__name__, exc), merged, url=url,
                       elapsed_ms=(time.monotonic() - start) * 1000, **ctx)

    elapsed = (time.monotonic() - start) * 1000
    try:
        doc = json.loads(body or "{}")
    except (ValueError, TypeError):
        doc = {}
    _trace("DONE %s reqID=%s -> HTTP %s" % (ACTION, req_id, status_code))
    return _output(
        status="OK", status_code=status_code, ok=True, code=CANCELLED,
        message=str(doc.get("message") or "Request has been cancelled"),
        url=url, response_body=body[:200000], elapsed_ms=elapsed, **ctx)


def make_handler() -> Any:
    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        return cancel({**(parameters or {}), **(inputs or {})})
    return _handler


INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("reqID", "Change request to cancel (reqID)",
               placeholder="bound to the flow input, e.g. {{$input.reqID}}"),
    text_field("project", "Project",
               placeholder="e.g. {{$input.project}} - MCK, ALCON, S4 …"),
    text_field("environment", "Environment",
               placeholder="e.g. {{$input.environment}} - DEV, QAS …"),
    textarea_field("comment", "Cancellation comment (stored on the request)",
                   placeholder="why it is being cancelled", rows=2),
    text_field("requestor_url", "RequestorService URL override (blank = project registry)",
               placeholder="only for a one-off endpoint"),
    text_field("token_url", "OAuth Token URL override (blank = from the project registry)",
               placeholder="only for a one-off endpoint"),
    text_field("grant_type", "OAuth grant_type",
               placeholder="OAuth grant type", default="client_credentials"),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status (OK even when the cancel was refused)", readonly=True),
    text_field("ok", "Cancelled? (the flag a flow should gate on)", readonly=True),
    text_field("code", "CANCELLED / the backend's refusal code / "
                       "CANCEL_ACTION_NOT_AVAILABLE", readonly=True),
    text_field("message", "The backend's own words", readonly=True),
    number_field("status_code", "HTTP Status Code", readonly=True),
    text_field("reqID", "Change request", readonly=True),
    text_field("project", "Project", readonly=True),
    text_field("environment", "Environment", readonly=True),
    text_field("cr_url", "Link to the change request in the web app", readonly=True),
    text_field("url", "Resolved URL", readonly=True),
    textarea_field("response_body", "Response Body (JSON)", readonly=True),
    number_field("elapsed_ms", "Elapsed (ms)", readonly=True),
    text_field("error", "Error (only for a real fault, not a refusal)", readonly=True),
    textarea_field("reply", "The sentence to show the user (every outcome, including "
                            "refusals and faults)", readonly=True, rows=2),
]]
