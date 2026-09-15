"""Tests for cs_cancel_request. Offline: OAuth and HTTP are both stubbed.

The response bodies below were captured from the live API on 2026-09-07 — S4 QAS and
MCK DEV — rather than composed. They are copied because the shapes are not guessable: the
success body is a sentence and not a code, and the error envelope hides its reason either
in a sibling ``code`` field or inside a JSON string nested in ``error.message``. A test
written against a guess at those passes happily while the node misreads production.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             os.pardir, "worker-sdk")]

import pytest  # noqa: E402

from app import cancel_request as cr  # noqa: E402

# What CR0000031234 on S4 QAS returned when it was really cancelled.
OK_BODY = ('{"@odata.context":"$metadata#core.common.CommonActionOutput",'
           '"data":"\\"CR0000031234\\"","message":"Request has been cancelled"}')

# A reqID that exists nowhere: the action ran and could not lock it. The reason is in a
# SIBLING `code` field, not inside the message.
CANNOT_LOCK_BODY = ('{"error":{"message":"CAN_NOT lock request",'
                    '"code":"CAN_NOT_LOCK_REQUEST","@Common.numericSeverity":4}}')

# A landscape whose service does not carry the action. The service resolved and NAMED it,
# which is what separates this from a wrong host.
NO_ACTION_BODY = (
    '{"error":{"message":"Invalid resource path '
    '\\"core.service.common.process.CommonProcessService.cancelRequest\\"",'
    '"code":"404","@Common.numericSeverity":4}}')

# The other envelope shape: a domain code inside a JSON string inside error.message.
NESTED_CODE_BODY = (
    '{"error":{"message":"{\\"code\\":\\"CR_HEALTH_REQUEST_STILL_RUNNING\\",'
    '\\"reqID\\":\\"CR0000005212\\",\\"itemsRemaining\\":0}",'
    '"code":"400","@Common.numericSeverity":4}}')


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Target:
    project, environment, key = "S4", "QAS", "S4_QAS"
    client_id, client_secret = "cid", "sec"
    token_url = "https://smdg-s4-qas-simplemdg.authentication.br10.hana.ondemand.com/oauth/token"
    requestor_url = ("https://smdg-s4-qas-simplemdg-srv-process-requestor.cfapps.br10."
                     "hana.ondemand.com/RequestorService")

    def cr_url(self, req_id):
        return "https://web.example/main/index.html#/requestHistory/" + req_id


@pytest.fixture
def sent(monkeypatch):
    """Stub the target, the login and the POST. Returns the captured request."""
    box = {}
    monkeypatch.setattr(cr, "resolve_target", lambda merged: (_Target(), ""))
    monkeypatch.setattr(cr, "oauth_token", lambda *a, **k: "jwt")

    def _urlopen(req, timeout=None):
        box["url"] = req.full_url
        box["method"] = req.get_method()
        box["headers"] = {k.lower(): v for k, v in req.header_items()}
        box["body"] = json.loads(req.data.decode("utf-8"))
        kind, code, body = box.get("outcome", ("ok", 200, OK_BODY))
        if kind == "http_error":
            raise urllib.error.HTTPError(req.full_url, code, "err", {},
                                         io.BytesIO(body.encode("utf-8")))
        if kind == "boom":
            raise OSError("connection reset")
        return _Resp(code, body)

    monkeypatch.setattr(cr.urllib.request, "urlopen", _urlopen)
    return box


# ------------------------------------------------------------------ error envelope
def test_unwrap_prefers_a_named_sibling_code():
    """`error.code` is normally the HTTP status; when it is not numeric it IS the reason,
    and that beats parsing the message."""
    code, message = cr.unwrap_error(CANNOT_LOCK_BODY)
    assert code == cr.CANNOT_LOCK
    assert "lock" in message.lower()


def test_unwrap_digs_a_code_out_of_a_nested_json_message():
    code, _ = cr.unwrap_error(NESTED_CODE_BODY)
    assert code == "CR_HEALTH_REQUEST_STILL_RUNNING"


def test_unwrap_garbage_does_not_raise():
    assert cr.unwrap_error("<html>gateway timeout</html>") == ("", "<html>gateway timeout</html>")
    assert cr.unwrap_error("") == ("", "")


# ------------------------------------------------------------------------- guards
def test_reqid_is_required():
    out = cr.cancel({"project": "S4", "environment": "QAS"})
    assert out["status"] == "FAILED" and "reqID" in out["error"]


def test_no_target_reports_what_is_configured(monkeypatch):
    monkeypatch.setattr(cr, "resolve_target",
                        lambda merged: (None, "No credentials configured for project 'X'"))
    out = cr.cancel({"reqID": "CR1"})
    assert out["status"] == "FAILED" and "No credentials configured" in out["error"]


def test_missing_requestor_url_names_the_variables(monkeypatch):
    class _NoUrl(_Target):
        requestor_url = ""
    monkeypatch.setattr(cr, "resolve_target", lambda merged: (_NoUrl(), ""))
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["status"] == "FAILED"
    assert "APP_PREFIX" in out["error"] and "REQUESTOR_URL" in out["error"]


# ---------------------------------------------------------------- the request sent
def test_request_shape(sent):
    cr.cancel({"reqID": "CR0000031234", "project": "S4", "environment": "QAS"})
    assert sent["url"].endswith("/RequestorService/cancelRequest")
    assert "srv-process-requestor" in sent["url"]      # not srv-process-system
    assert sent["method"] == "POST"
    assert sent["headers"]["authorization"] == "Bearer jwt"
    assert sent["headers"]["content-type"] == "application/json"
    # {reqID, comment} and nothing else -- no `force`, which belonged to the action this
    # node deliberately does not use.
    assert sent["body"] == {"reqID": "CR0000031234",
                            "comment": "Cancelled via ai-workflow"}


def test_comment_is_passed_through(sent):
    cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS",
               "comment": "stuck since Friday"})
    assert sent["body"]["comment"] == "stuck since Friday"


def test_requestor_url_override_wins(sent):
    cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS",
               "requestor_url": "https://one-off.example/RequestorService"})
    assert sent["url"] == "https://one-off.example/RequestorService/cancelRequest"


# ------------------------------------------------------------------------ outcomes
def test_cancelled(sent):
    out = cr.cancel({"reqID": "CR0000031234", "project": "S4", "environment": "QAS"})
    assert out["status"] == "OK"
    assert out["ok"] is True
    assert out["code"] == cr.CANCELLED
    assert out["message"] == "Request has been cancelled"
    assert out["status_code"] == 200
    assert out["error"] == ""
    assert out["cr_url"].endswith("CR0000031234")
    assert out["project"] == "S4" and out["environment"] == "QAS"


def test_refusal_is_data_not_a_failure(sent):
    sent["outcome"] = ("http_error", 400, CANNOT_LOCK_BODY)
    out = cr.cancel({"reqID": "CR9999999999", "project": "S4", "environment": "QAS"})
    # The node goes GREEN: a refusal is an answer. The flow gates on `ok`.
    assert out["status"] == "OK"
    assert out["ok"] is False
    assert out["code"] == cr.CANNOT_LOCK
    assert out["status_code"] == 400
    assert out["error"] == ""


def test_landscape_without_the_action_is_data(sent):
    """A 404 that NAMES the action is the landscape saying it does not have it — not a
    defect in the call, so the node stays green and says which."""
    sent["outcome"] = ("http_error", 404, NO_ACTION_BODY)
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["status"] == "OK"
    assert out["ok"] is False
    assert out["code"] == cr.ACTION_NOT_AVAILABLE
    assert out["error"] == ""


def test_404_from_a_wrong_host_is_still_a_fault(sent):
    """Only a 404 naming the action is a capability answer. A router 404 means the endpoint
    is wrong, and dressing that up as "not available here" would hide a typo."""
    sent["outcome"] = ("http_error", 404,
                       "404 Not Found: Requested route does not exist.")
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["status"] == "FAILED"
    assert out["code"] != cr.ACTION_NOT_AVAILABLE


def test_401_is_a_real_fault(sent):
    sent["outcome"] = ("http_error", 401, '{"error":{"message":"Unauthorized","code":"401"}}')
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["status"] == "FAILED"
    assert out["ok"] is False
    assert "401" in out["error"]


def test_500_is_a_real_fault(sent):
    sent["outcome"] = ("http_error", 500, '{"error":{"message":"boom","code":"500"}}')
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["status"] == "FAILED" and out["ok"] is False


def test_200_without_a_json_body_still_reads_as_cancelled(sent):
    """The action answered 2xx. An unparseable body must not turn that into a failure —
    the request is cancelled either way."""
    sent["outcome"] = ("ok", 204, "")
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["ok"] is True and out["code"] == cr.CANCELLED


def test_network_error_is_data_and_keeps_the_context(sent):
    """This path regression-tested itself once: it raised TypeError because the context
    block and the failure helper both set reqID."""
    sent["outcome"] = ("boom", 0, "")
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["status"] == "FAILED"
    assert "connection reset" in out["error"]
    assert out["reqID"] == "CR1" and out["project"] == "S4"
    assert out["url"].endswith("/cancelRequest")


def test_login_failure_is_data_and_keeps_the_context(monkeypatch, sent):
    def _boom(*a, **k):
        raise RuntimeError("OAuth login returned no access_token")
    monkeypatch.setattr(cr, "oauth_token", _boom)
    out = cr.cancel({"reqID": "CR1", "project": "S4", "environment": "QAS"})
    assert out["status"] == "FAILED"
    assert "OAuth login failed" in out["error"]
    assert out["reqID"] == "CR1"


# -------------------------------------------------------------------- registry wiring
def test_registry_derives_the_requestor_host():
    """The host that took nine wrong guesses to find, pinned so it cannot drift."""
    import os as _os
    from importlib import reload
    for k, v in (("LOG_WORKER_CLIENT_ID_S4_QAS", "x"),
                 ("LOG_WORKER_CLIENT_SECRET_S4_QAS", "y"),
                 ("LOG_WORKER_APP_PREFIX_S4_QAS", "smdg-s4-qas-simplemdg"),
                 ("LOG_WORKER_REGION_S4_QAS", "br10")):
        _os.environ[k] = v
    from app import registry
    reload(registry)
    t = registry.load_registry().resolve("S4", "QAS")
    assert t is not None
    assert t.requestor_url == ("https://smdg-s4-qas-simplemdg-srv-process-requestor"
                               ".cfapps.br10.hana.ondemand.com/RequestorService")
    # and the CommonProcessService one is untouched
    assert "srv-process-system" in t.api_url


# ------------------------------------------------------------------- registration
def test_node_is_registered_with_the_expected_contract():
    from app import node_types
    node = next(n for n in node_types.NODE_TYPES if n.worker_type == "cs_cancel_request")
    ins = {f.get("key") or f.get("name") for f in node.input_schema}
    outs = {f.get("key") or f.get("name") for f in node.output_schema}
    assert {"reqID", "project", "environment", "comment"} <= ins
    assert "force" not in ins          # the fallback, and its flag, are gone
    assert {"ok", "code", "message", "status", "error"} <= outs


# ------------------------------------------------------------------------- the sentence
# `reply` moved OUT of the workflow and into the node, which took a 4-way SWITCH and three
# SET_VARIABLE nodes out of the cancel flow (9 nodes -> 5). The engine's expression grammar
# is pure path substitution, so in a flow every variant of a sentence costs its own node
# plus a branch to select it. These tests are what makes that trade safe: the wording is
# now under the same suite as the parsing, where it used to be under nothing at all.
CTX = {"reqID": "CR0000031234", "project": "S4", "environment": "QAS"}


def _reply_for(**over):
    from app.cancel_request import _output
    return _output(**{**CTX, **over})["reply"]


def test_reply_is_present_on_every_path():
    """The point of composing it inside _output: a path added later cannot forget it."""
    from app.cancel_request import _output
    for kwargs in ({}, {"ok": True}, {"status": "FAILED", "error": "boom"},
                   {"code": "ANYTHING_NEW"}):
        assert _output(**kwargs)["reply"].strip()


def test_reply_cancelled_names_the_request_and_the_landscape():
    r = _reply_for(ok=True, code="CANCELLED")
    assert "CR0000031234" in r and "S4 QAS" in r and "cancelled" in r


def test_reply_refused_carries_the_backends_own_code_and_words():
    """The catch-all case, and the one that has to survive a refusal nobody has seen yet."""
    r = _reply_for(code="CR_HEALTH_REQUEST_STILL_RUNNING", message="still running")
    assert "CR_HEALTH_REQUEST_STILL_RUNNING" in r and "still running" in r
    assert "not cancelled" in r


def test_reply_not_available_points_at_the_web_app():
    from app.cancel_request import ACTION_NOT_AVAILABLE
    r = _reply_for(code=ACTION_NOT_AVAILABLE)
    assert "web app" in r and "cancelRequest" in r
    # deliberately does NOT list which landscapes have it — that goes stale
    assert "MCK" not in r


def test_reply_failed_is_asked_before_the_refusals():
    """A fault also has ok=false; if the order were wrong it would read as a refusal."""
    r = _reply_for(status="FAILED", error="HTTP 401: unauthorized", status_code=401,
                   code="401")
    assert "did not go through" in r and "401" in r
    assert "answered" not in r


def test_reply_degrades_when_there_is_no_request_id():
    """The early guard returns before any context exists; the sentence must still read."""
    from app.cancel_request import _output
    r = _output(status="FAILED", error="reqID is required")["reply"]
    assert "reqID is required" in r
    assert "None" not in r and "  " not in r


def test_reply_is_declared_in_the_output_schema():
    """A field a flow binds to and the schema does not declare is invisible in the editor."""
    from app import node_types
    node = next(n for n in node_types.NODE_TYPES if n.worker_type == "cs_cancel_request")
    assert "reply" in {f.get("key") or f.get("name") for f in node.output_schema}


# ------------------------------------------------------------------- the prompt's identity
# Found live on 2026-09-08: "cancel CR0000031234 S4 QAS" came back as intent `other`, the
# model explaining "I'm not able to cancel change requests - I can only read process logs".
# The rules named cancel_request; the OPENING sentence still defined the bot as a log
# reader. A model follows the identity statement over a rule beneath it, so the vocabulary
# and the self-description have to agree. No unit test could have caught the routing, but
# this catches the contradiction that caused it.
def test_prompt_describes_cancelling_as_something_the_bot_does():
    from app.detect_request import _DEFAULT_SYSTEM_PROMPT as P
    head = P[:P.index("Reply with ONLY a JSON object")]
    assert "CANCEL" in head.upper(), (
        "the bot's self-description must mention cancelling; a cancel_request rule "
        "further down cannot override an identity that says it only reads logs")


def test_prompt_still_makes_analyze_log_the_safe_default():
    """The guard against over-triggering must survive any rewording of the identity."""
    from app.detect_request import _DEFAULT_SYSTEM_PROMPT as P
    assert "unsure" in P and "analyze_log" in P
    assert "cannot be undone" in P


# --------------------------------------------------------------------------- the CR link
# The log analysis has always replied with a clickable CR; a cancellation replied with a
# bare number. cr_url was already in the output -- only the sentence ignored it.
CR_URL = ("https://smdg-s4-qas-simplemdg-web.cfapps.br10.hana.ondemand.com"
          "/main/index.html#/requestHistory/CR0000031234")


def test_reply_links_the_cr_on_every_outcome_that_names_it():
    from app.cancel_request import ACTION_NOT_AVAILABLE
    for over in ({"ok": True, "code": "CANCELLED"},
                 {"code": "CAN_NOT_LOCK_REQUEST", "message": "locked"},
                 {"code": ACTION_NOT_AVAILABLE},
                 {"status": "FAILED", "error": "HTTP 401"}):
        r = _reply_for(cr_url=CR_URL, **over)
        assert "[CR0000031234](%s)" % CR_URL in r, over


def test_reply_falls_back_to_the_bare_number_when_there_is_no_url():
    """A blank cr_url is a real answer -- no web app on that landscape -- not an error."""
    r = _reply_for(ok=True, code="CANCELLED", cr_url="")
    assert "CR0000031234" in r
    assert "[" not in r and "](" not in r


def test_a_fault_before_any_req_id_does_not_mention_a_request():
    """Otherwise it reads 'the cancel call for the request did not go through'."""
    from app.cancel_request import _output
    r = _output(status="FAILED", error="reqID is required")["reply"]
    assert "did not go through" in r
    assert "for the request" not in r
