"""Live-app reproduce node — an AGENT drives the browser; code owns the guardrails.

Why the analyse lane needs this: reading code proves what the code SAYS, not what the app
DOES. The doctrine is PROVE before SEAL — a root cause read from source is strong evidence
but it is not a reproduction.

Why an agent rather than a planned action script: the first design had Claude emit a fixed
JSON script which this module executed. It failed three times for one reason — the planner
had to guess SAPUI5 selectors before it had ever seen the page, and each guess cost a
four-minute run. `tr:contains('AE01')` is not a Playwright selector; the file input does
not exist until the dialog opens; the table renders divs, not rows. An agent with a live
browser sees the page, tries, reads the error and adjusts inside ONE run. A fixed
vocabulary of eight verbs can never do that.

So the split is:

    Python  launches Chromium, LOGS IN, installs the request guard, exposes CDP,
            downloads the attachments
    Agent   attaches over CDP and drives that browser DIRECTLY, writing its own
            Playwright, looping as long as it needs
    Python  collects the artifacts and returns the agent's verdict

The agent drives directly rather than through a tool gateway. A gateway was the first
instinct and it was wrong twice over: every call would pay a process start plus a CDP
connect, state between calls (an open dialog, a file chooser) would be lost, and a fixed
set of tool verbs is the very ceiling this design exists to remove — swapping a
vocabulary of 8 for one of 16 is not a fix.

What stays in code, and why it is code and not instruction:

* **Login.** The password is lifted from the ticket in Python and typed by Python. It
  never enters a prompt, an argv, or an output field. Not because the CLI could not do
  it, but because it does not need to.
* **The mutation guard.** ``ctx.route`` is installed by US on the shared context before
  the agent ever attaches, and it SURVIVES the handover: Playwright turns a route into
  browser-side interception whose decisions come back to this process for as long as it
  holds the connection. So the agent drives freely and a mutating call to a protected
  path still cannot leave the browser. The incentive lines up rather than fighting the
  agent — the session lives only in this context, so a context it creates itself is
  unauthenticated and useless.
  The list of protected paths is deliberately narrow: this app POSTs constantly for
  ordinary reads and for the upload itself, so a blanket "block POST" would stop the
  reproduction rather than protect anything. And the realistic worst case is one draft
  change request in a DEV tenant, not a catastrophe — the guard is proportionate, not a
  sandbox.
* **The host.** The ticket may name the ACCOUNT — that is the account QC reproduced with,
  and MDG is role-sensitive enough that using a different one can fail to reproduce a real
  bug. The ticket may NOT name the host: anyone who can edit a ticket could otherwise make
  the bot post a password to a machine they control. ``live_url`` is node config and is
  checked against ``allowed_hosts``.

Honest limit: the agent has Bash, so it could start its own browser and miss the guard
entirely. It has no reason to — that browser would not be logged in — but nothing stops
it. Closing that properly needs an egress proxy outside the container.

``app/repro_tool.py`` remains as a convenience the agent MAY call for one-liners; it
carries working JS for a DOM digest and for reading a row's UI5 binding context, which is
the observation that actually settles questions like "was this row marked as added by a
rule".
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import threading
import time
from typing import Any

from worker_sdk.layer1_domain.auto_form_mapper import (
    number_field,
    switch_field,
    text_field,
    textarea_field,
)

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    _DEFAULT_SECRETS_FILE,
)
from app.gitlab_analyze import (
    _flatten_issue,
    _usage_of,
    _json_object,
    _redact,
    _token_from_credential,
)

# The ticket template states the account QC actually reproduced with:
#   * User ID: [+alcon.dev@laidon.com+|mailto:alcon.dev@laidon.com] / <password>
# Matched on the FLATTENED text (ADF splits that line across many text nodes, so nothing
# line-oriented works on the raw value). Only the FIRST " / " separates: the live password
# ends `!/9@`, so splitting on the last slash would leak the tail.
_TICKET_LOGIN = re.compile(
    r"(?im)^.*?\b(?:User\s*ID|Username|User\s*Name|Login|Account)\b\s*:?\s*"
    r"(?P<user>[^\s/|\]]*[\w.+-]+@[\w.-]+\.\w{2,}|[\w.+-]{3,})[^/\n]*?\s/\s(?P<pw>\S+)\s*$")

_BRIEF = """You are reproducing a bug on a RUNNING SimpleMDG app. A Chromium is already
open, ALREADY LOGGED IN, and waiting for you on CDP at {cdp}. Your job is to OBSERVE,
then report.

Drive it yourself. Playwright (Python) is installed — write and run your own scripts, in
a loop: look, try, read the error, adjust. That is why you are here rather than a fixed
action script; three attempts at a pre-planned script all failed because a planner has to
guess SAPUI5 selectors before it has ever seen the page.

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp("{cdp}")
        ctx = b.contexts[0]          # <-- REUSE THIS ONE. It holds the login.
        page = ctx.pages[0]
        ...

Use `ctx = b.contexts[0]` and the existing page. A context you create yourself has NO
session and you will land back on the login form.

Two techniques matter most here:

* Dump the REAL structure before writing any selector. `page.query_selector_all` +
  reading tagName / className / role / data-* / innerText tells you what the table
  actually is. SAPUI5 frequently renders divs where you would expect `<tr>`, which is why
  `tr:has-text("AE01")` found nothing on an earlier run. Look, do not guess.
* Read the UI5 BINDING MODEL, not just the DOM. For a row:
      sap.ui.getCore().byId(id).getBindingContext().getObject()
  gives the row's actual DATA, and control properties like `getHighlight()` give what the
  renderer was told. For "was this row marked as added by a rule", the bound object
  answers outright where a screenshot needs interpreting and a CSS class may not exist.
  `app/repro_tool.py` in this repo has working JS for both if you want a starting point,
  and can also be called directly as a one-liner:
      python -m app.repro_tool {cdp} {out} '[{{"do":"ui5","selector":".sapMListItem"}}]'

Console errors, page errors and any failed or 4xx/5xx network call are ALREADY being
captured for you — you do not need to wire listeners, and they are attached to the report
either way. Spend your budget on observing the thing in dispute.

Write screenshots into {out} (`page.screenshot(path="{out}/actual.png")`) — the verdict
should have a picture behind it. Take the FIRST one as soon as the screen in
dispute is on display, before any deeper probing: a run that is cut short still has to
leave evidence.

CHECKPOINT YOUR VERDICT TO DISK. As soon as you can answer at all — even "inconclusive,
here is what I have so far" — write the JSON object below to `{out}/verdict.json`, and
rewrite that file whenever your answer improves. It is read if this session is cut off,
so a partial answer on disk is worth far more than a perfect one you never get to print.
Measured: a session drove the app for a full 20 minutes and reported NOTHING, because
the result existed only in a final message the timeout cut off.
Write it ATOMICALLY — to a temp file, then rename over `verdict.json`. It is rewritten
repeatedly and read at an arbitrary moment, so a half-written file would lose the
good answer that was there a second earlier.

Rules:
1. Selectors are PLAYWRIGHT. `:contains()` does not exist — use `:has-text("x")`.
2. OBSERVE only. Do not submit, save, approve, activate or release anything. A request
   guard on the shared context fails those calls; if you see one refused, that is working
   as intended — do not route around it, and do not report it as the bug.
3. An observation that did not capture the thing Expected talks about is INCONCLUSIVE.
   Say so. "I could not observe it" is far more useful than a confident guess, and a wrong
   `reproduced: false` would tell the team a real bug does not exist.
4. Keep it to roughly {max_calls} steps. If you run out, report what you have.

{hypothesis_block}
--- TICKET ---
{ticket}
--- END TICKET ---
{attach_block}
When done, print ONE JSON object as your FINAL output and nothing after it:
{{"reproduced": false, "inconclusive": true, "observed": "what the app actually did",
 "contradiction": "the observation vs Expected, or empty",
 "evidence": "the probe/ui5 output you relied on, quoted",
 "next_probe": "what to observe next if inconclusive",
 "steps": ["the tool calls that mattered, so this can be replayed"],
 "confidence": "high|medium|low"}}
"""


def _guard_route(route: Any, request: Any, blocked: list[str]) -> None:
    """Fail a mutating call to a protected path; let everything else through.

    Deliberately narrow. The app POSTs constantly for ordinary reads and for the upload
    itself, so a blanket "block POST" would stop the reproduction rather than protect
    anything. The list is shared with ``app.repro_tool`` so there is one definition.
    """
    from app.repro_tool import _BLOCK_METHODS, _BLOCK_URL
    try:
        if request.method in _BLOCK_METHODS and _BLOCK_URL.search(request.url):
            blocked.append(f"{request.method} {request.url[:160]}")
            route.abort("blockedbyclient")
            return
    except Exception:  # noqa: BLE001 — a guard that throws must not wedge the page
        pass
    route.continue_()


_DISPLAY_FALLBACK = {
    "login_source": "not determined",
    "observed": "not established",
    "contradiction": "none noted",
    "evidence": "none captured",
    "next_probe": "none suggested",
    "steps": "none recorded",
    "blocked_by_guard": "nothing was blocked",
    "console_errors": "none",
    "failed_requests": "none",
    "screenshots": "none",
}


def _blank(err: str = "", **over: Any) -> dict[str, Any]:
    """Schema-complete payload, SUCCESS-shaped even on failure.

    Same doctrine as the engine's ``_error_output``: the workflow keeps flowing to its
    Jira-comment node so the failure lands on the ticket. ``reproduced`` defaults FALSE and
    ``inconclusive`` TRUE — a run that broke has not reproduced anything and must never
    read as "verified". That is the one direction this node cannot get wrong.
    """
    out: dict[str, Any] = {
        "status": "FAILED" if err else "OK",
        "reproduced": False,
        "inconclusive": True,
        "observed": "",
        "contradiction": "",
        "evidence": "",
        "next_probe": "",
        "steps": "",
        "confidence": "low",
        "login_source": "",
        "blocked_by_guard": "",
        "console_errors": "",
        "failed_requests": "",
        "action_log": "",
        "screenshots": "",
        "artifact_dir": "",
        "raw": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
        "error": err,
    }
    out.update(over)

    # The platform reports an empty string as null, and the Jira comment template then
    # renders that as the literal word "None" — a live comment carried SEVEN of them, so
    # the pipeline's own output read like a bug. There is no conditional in the template,
    # so presentable text has to come from here.
    for key, fallback in _DISPLAY_FALLBACK.items():
        if not str(out.get(key) or "").strip():
            out[key] = fallback
    # An unfinished run still knows something: say what stopped it instead of "not
    # established" alone, which is the difference between a reader retrying and a reader
    # having to open the run.
    if err and out["observed"] == _DISPLAY_FALLBACK["observed"]:
        out["observed"] = f"not established — {err}"
    return out


def _cred(inputs: dict[str, Any], ref_key: str) -> dict[str, Any] | None:
    """A vault credential by the ref held in ``inputs[ref_key]``.

    The executor injects ``__credentials`` for the node's ``credential_ref`` ONLY — a live
    run showed a second ref never resolved and the map arrived empty. So a node gets
    exactly ONE vault credential; on this node that one is JIRA, for the attachments.
    """
    creds = inputs.get("__credentials")
    ref = str(inputs.get(ref_key) or "").strip()
    if isinstance(creds, dict) and ref:
        c = creds.get(ref)
        if isinstance(c, dict) and c.get("type"):
            return c
    return None


def _user_pass(cred: dict[str, Any] | None) -> tuple[str, str]:
    """``(username, password)`` from a vault entry, tolerating the shapes it may take."""
    if not isinstance(cred, dict):
        return "", ""
    cfg = cred.get("config") if isinstance(cred.get("config"), dict) else {}

    def pick(*keys: str) -> str:
        for k in keys:
            v = cfg.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""
    return (pick("username", "user", "email", "login", "j_username"),
            pick("password", "pass", "secret", "value", "j_password"))


def _login_from_ticket(flat_text: str) -> tuple[str, str]:
    """``(username, password)`` as stated on the ticket, or ``("", "")``."""
    m = _TICKET_LOGIN.search(flat_text or "")
    if not m:
        return "", ""
    user = m.group("user").strip().strip("+[]()")
    pw = m.group("pw").strip()
    return (user, pw) if user and pw else ("", "")


def _host_allowed(url: str, allow: str) -> bool:
    """Is *url*'s host one this node may send a credential to?

    The boundary that makes a ticket-supplied ACCOUNT safe. Suffix confusion is rejected:
    ``a.ondemand.com.evil.com`` does not match ``ondemand.com``.
    """
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    pats = [p.strip().lower() for p in (allow or "").replace(";", ",").split(",") if p.strip()]
    if not pats:
        return True
    return any(host == p or host.endswith("." + p.lstrip("*.")) or p == "*" for p in pats)


def _login(page: Any, url: str, user: str, pw: str, log: list[str], timeout: int) -> None:
    """Sign in through the SAP IAS form, in both shapes it comes in.

    Verified live: McKesson DEV asks username -> Continue -> password; Landis puts both on
    one page. A 2FA screen is reported rather than waited on — an unattended run cannot
    answer one, and hanging for the whole timeout tells nobody why.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    try:
        page.wait_for_selector("#j_username", timeout=timeout)
    except Exception:  # noqa: BLE001 — already signed in is fine
        log.append("login: no IAS form appeared; assuming an existing session")
        return
    page.fill("#j_username", user)
    if page.locator("#j_password").count() == 0:
        page.click("#logOnFormSubmit")
        page.wait_for_selector("#j_password", timeout=timeout)
        log.append("login: two-step form (username -> Continue -> password)")
    else:
        log.append("login: single-page form")
    page.fill("#j_password", pw)
    page.click("#logOnFormSubmit")
    page.wait_for_load_state("domcontentloaded", timeout=timeout)
    for sel in ("#j_otpcode", "#smsCode", "#secondFactorForm"):
        if page.locator(sel).count():
            raise RuntimeError(
                f"the login asked for a second factor ({sel}) — an unattended repro cannot "
                "answer it. Use an account exempt from 2FA for automation.")
    if page.locator("#j_password").count():
        raise RuntimeError("still on the password form after submitting — bad credential?")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _attachment_items(merged: dict[str, Any], email: str, token: str, domain: str,
                      note: Any) -> list[Any]:
    """The ticket's attachment list, whatever shape the input arrived in.

    Order matters: the node's own input first, then Jira. Asking Jira is not a nicety —
    it is the only source that cannot be replaced by a pointer, and the pointer case is
    exactly the one that already broke a live run.
    """
    raw = merged.get("attachments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:  # noqa: BLE001
            raw = None
    items = raw if isinstance(raw, list) else (
        raw.get("attachments") if isinstance(raw, dict) else None)
    if isinstance(items, list) and items:
        return items

    why = "claim-check pointer, not a list" if (
        isinstance(raw, dict) and raw.get("__file_ref")) else "empty or unreadable"
    key = str(merged.get("ticket_key") or "").strip()
    if not key or not email or not token or not domain:
        note(f"input was {why}; cannot re-fetch (need ticket_key + Jira credential)")
        return []

    base = domain if domain.startswith("http") else f"https://{domain}"
    try:
        import httpx
        auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        r = httpx.get(f"{base.rstrip('/')}/rest/api/2/issue/{key}",
                      params={"fields": "attachment"}, timeout=60.0,
                      headers={"Authorization": auth, "Accept": "application/json"})
        if r.status_code != 200:
            note(f"input was {why}; Jira re-fetch returned HTTP {r.status_code}")
            return []
        got = ((r.json() or {}).get("fields") or {}).get("attachment") or []
    except Exception as exc:  # noqa: BLE001
        note(f"input was {why}; Jira re-fetch failed: {str(exc)[:120]}")
        return []

    if not isinstance(got, list) or not got:
        note(f"input was {why}; Jira says the ticket has no attachments")
        return []
    note(f"input was {why}; re-fetched {len(got)} from Jira instead")
    return got


def _checkpoint(run_dir: str) -> str:
    """The verdict the agent last wrote to disk, or ``""``.

    Read on EVERY path that ends without a verdict, not just on an exception: the CLI now
    runs on a thread, so "finished with nothing to show" can also arrive with no exception
    at all. A checkpoint is worthless if only one of the failure routes looks for it.
    """
    path = os.path.join(run_dir, "verdict.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()[-8000:]
    except OSError:
        return ""


def _download_attachments(merged: dict[str, Any], out_dir: str,
                          log: list[str] | None = None) -> dict[str, str]:
    """``{filename: local path}`` for the attachments a repro step may upload.

    Uses the node's single vault credential (Jira), because attachment content URLs are
    authenticated.

    Reports what it could not get, instead of returning quietly. The old version was
    deliberately silent — "a repro that uploads nothing must not be blocked" — and that
    silence cost a whole session: the upstream list arrived as a claim-check POINTER
    (``{"__file_ref": true, "row_count": 4}``) rather than a list, so nothing downloaded,
    and the agent spent the full 1200s unable to perform the upload the reproduction is
    made of. Zero screenshots, no verdict, no clue why. A step this load-bearing has to
    say when it comes back empty.
    """
    def _note(msg: str) -> None:
        if log is not None:
            log.append(f"attachments: {msg}")

    cred = _cred(merged, "credential_ref") or _cred(merged, "jira_credential_ref")
    cfg = (cred or {}).get("config") if isinstance((cred or {}).get("config"), dict) else {}
    email = str(cfg.get("email") or cfg.get("username") or "")
    token = _token_from_credential(cred)
    domain = str(cfg.get("domain") or cfg.get("base_url") or cfg.get("url") or "").strip()

    items = _attachment_items(merged, email, token, domain, _note)
    if not items:
        return {}
    if not email or not token:
        _note("have a list but no Jira email/token on the credential — cannot download")
        return {}

    import httpx
    auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
    got: dict[str, str] = {}
    with httpx.Client(timeout=60.0, follow_redirects=True,
                      headers={"Authorization": auth}) as c:
        for a in items[:10]:
            if not isinstance(a, dict):
                continue
            name = str(a.get("filename") or a.get("name") or "")
            url = str(a.get("content") or a.get("url") or "")
            if not name or not url:
                continue
            try:
                r = c.get(url)
                if r.status_code == 200:
                    p = os.path.join(out_dir, re.sub(r"[^\w.-]", "_", name))
                    with open(p, "wb") as fh:
                        fh.write(r.content)
                    got[name] = p
            except Exception:  # noqa: BLE001
                continue
    return got


def make_repro_handler(engine: Any) -> Any:
    """Build the ``(inputs, parameters) -> dict`` handler bound to the shared engine."""

    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        text = engine._as_text

        # Flatten first: the reference hands over the whole issue and its description is
        # ADF. Then lift the login out of the flattened RAW text, and only after that
        # redact — so the password reaches the browser and never the agent.
        _raw = _flatten_issue(merged.get("ticket_text") or merged.get("ticket_description")).strip()
        ticket_text = _redact(_raw)
        live_url = text(merged.get("live_url")).strip()
        if not ticket_text:
            return _blank("'ticket_text' is empty (bind it to the Jira issue).")
        if not live_url:
            return _blank("'live_url' is empty — the environment to reproduce on.")

        def _int(k: str, d: int) -> int:
            try:
                return int(float(merged.get(k) or d))
            except Exception:  # noqa: BLE001
                return d

        timeout_ms = max(5000, min(_int("step_timeout_ms", 45000), 120000))
        budget = _int("timeout_secs", 1200)
        # 40 was measured too tight: a run that logged in fine and was still probing hit the
        # cap after 6 minutes of a 20-minute budget, so the verdict came back inconclusive
        # for lack of turns rather than lack of evidence. The timeout is the real guard —
        # and 0 now means "use the ceiling", which is what the cap's own error message
        # already promises the operator (the old clamp turned 0 into FIVE turns).
        _mc = _int("max_tool_calls", 120)
        max_calls = 400 if _mc <= 0 else max(5, min(_mc, 400))
        headless = str(merged.get("headless", True)).strip().lower() not in ("false", "0", "no")

        if not _host_allowed(live_url, text(merged.get("allowed_hosts")).strip()):
            return _blank("live_url host is not in allowed_hosts — refusing to send a "
                          "credential there.")

        prefer_ticket = str(merged.get("prefer_ticket_login", True)).strip().lower() \
            not in ("false", "0", "no")
        login_source = ""
        user = pw = ""
        if prefer_ticket:
            user, pw = _login_from_ticket(_raw)
            if user and pw:
                login_source = "ticket (the account QC reproduced with)"
        if not (user and pw):
            # Only when credential_ref really holds an app login. On this node it normally
            # holds the JIRA token, and that would otherwise be misread as email+password.
            vc = _cred(merged, "credential_ref")
            vcfg = (vc or {}).get("config") if isinstance((vc or {}).get("config"), dict) else {}
            if not (vcfg.get("apiToken") or vcfg.get("domain")):
                user, pw = _user_pass(vc)
                if user and pw:
                    login_source = "vault credential_ref"
        if not user or not pw:
            return _blank("no app login found. Either the ticket states one on a "
                          "'User ID: <user> / <password>' line, or point credential_ref at "
                          "a Generic Basic Auth credential (Automation enabled — SA-2279).")

        secrets = engine._read_secrets_file(
            text(merged.get("secrets_file")).strip() or _DEFAULT_SECRETS_FILE, None)
        tokens = [t.strip() for t in (secrets.get("claude_cli_token") or "").split(",") if t.strip()]

        try:
            from playwright.sync_api import sync_playwright
        except Exception:  # noqa: BLE001
            return _blank("playwright is not installed on this worker host. In the image: "
                          "`pip install playwright && playwright install chromium`.")

        run_dir = os.path.join(
            text(merged.get("artifact_dir")).strip() or os.path.join(os.path.expanduser("~"), ".smdg-repro"),
            re.sub(r"[^\w.-]", "_", text(merged.get("ticket_key")).strip() or "run") + f"-{int(time.time())}")
        os.makedirs(run_dir, exist_ok=True)
        log: list[str] = []
        files = _download_attachments(merged, run_dir, log)
        if not files:
            # Not fatal — plenty of tickets need no upload — but it belongs in the
            # report, because "the agent had no file to upload" and "the app did not
            # show the marker" look identical in a verdict that says inconclusive.
            log.append("attachments: none available to upload")
        blocked: list[str] = []
        # Runtime evidence buffers. Bounded on purpose: a UI5 app can emit thousands of
        # console lines, and an unbounded list would balloon the task payload.
        console_msgs: list[str] = []
        net_fail: list[str] = []
        port = _free_port()
        raw = ""
        session_err = ""
        usage_total: dict[str, Any] = {}

        hypothesis = text(merged.get("hypothesis")).strip()
        hyp_block = ""
        if hypothesis:
            # Falsifying a stated mechanism is cheaper and stronger than re-deriving the
            # symptom: it distinguishes "the write side never marked the row" from "the
            # renderer ignored the mark", which the visible symptom alone cannot.
            hyp_block = ("--- HYPOTHESIS TO TEST (from the code analysis) ---\n"
                         f"{hypothesis}\n"
                         "Prefer the observation that would FALSIFY this over re-deriving "
                         "the visible symptom. If the data behind a row answers it, read "
                         "the data.\n--- END HYPOTHESIS ---\n")
        attach_block = ""
        if files:
            attach_block = ("\nAttachments downloaded for you (use `upload` with the path):\n"
                            + "\n".join(f"  {n} -> {p}" for n, p in files.items()) + "\n")

        try:
            with sync_playwright() as pw_api:
                browser = pw_api.chromium.launch(
                    headless=headless,
                    executable_path=(text(merged.get("browser_path")).strip() or None),
                    # The port is what lets the agent attach to this ALREADY LOGGED-IN
                    # browser instead of starting its own unauthenticated one.
                    args=["--no-sandbox", f"--remote-debugging-port={port}"])
                try:
                    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
                    # The guard, installed by US on the shared context. It survives the
                    # agent attaching over CDP and driving this same page: Playwright turns
                    # a route into browser-side interception whose decisions come back to
                    # THIS handler for as long as this process holds the connection. So the
                    # agent gets to drive directly — no tool gateway, no verb ceiling — and
                    # a mutating call still cannot leave the browser.
                    # The incentive lines up too: the login lives only in this context, so
                    # a context the agent makes itself is unauthenticated and useless.
                    # Route ONLY the protected paths, never "**/*".
                    #
                    # A catch-all wedged the app completely on the first agentic run, and
                    # the reason is structural: Playwright's sync API dispatches route
                    # callbacks on THIS thread, and this thread is blocked for the whole
                    # run waiting on the `claude -p` subprocess. So an intercepted request
                    # can never be answered. With "**/*" that is every asset a UI5 app
                    # loads — the browser stayed alive while the renderer's main thread
                    # deadlocked and `document.readyState` never resolved. The agent
                    # correctly reported "inconclusive" rather than "not reproduced", but
                    # the guard had made observation impossible.
                    #
                    # Narrowing to the mutating paths fixes it without weakening it: normal
                    # traffic is never intercepted and flows at full speed, while a request
                    # to a protected path is either aborted by the handler or — if this
                    # thread is busy — simply never completes, which for a submit is the
                    # outcome we wanted anyway.
                    from app.repro_tool import _BLOCK_URL
                    ctx.route(_BLOCK_URL,
                              lambda route, request: _guard_route(route, request, blocked))

                    # Runtime evidence, collected by CODE rather than left to the agent to
                    # remember. This is the half of "reproduce first" that matters most:
                    # a console stack or a 500 on the call that builds the row is the
                    # difference between a root cause READ from the running app and one
                    # inferred from source. `GUESSED_ROOT_CAUSE` is forbidden precisely
                    # here, and an agent writing its own script has no reason to think of
                    # wiring listeners before the interesting moment has already passed.
                    ctx.on("console", lambda m: (
                        console_msgs.append(f"[{m.type}] {m.text[:300]}")
                        if m.type in ("error", "warning") and len(console_msgs) < 200 else None))
                    ctx.on("weberror", lambda e: console_msgs.append(f"[pageerror] {str(e.error)[:300]}"))
                    ctx.on("requestfailed", lambda r: net_fail.append(
                        f"{r.method} {r.url[:180]} FAILED {(r.failure or '')[:80]}")
                        if len(net_fail) < 200 else None)
                    ctx.on("response", lambda r: net_fail.append(
                        f"{r.request.method} {r.url[:180]} -> {r.status}")
                        if r.status >= 400 and len(net_fail) < 200 else None)

                    page = ctx.new_page()
                    _login(page, live_url, user, pw, log, timeout_ms)
                    brief = _BRIEF.format(
                        cdp=f"http://127.0.0.1:{port}", out=run_dir, max_calls=max_calls,
                        hypothesis_block=hyp_block, ticket=ticket_text[:12000],
                        attach_block=attach_block)
                    with open(os.path.join(run_dir, "brief.md"), "w", encoding="utf-8") as fh:
                        fh.write(brief)
                    # Run the CLI on a THREAD and keep servicing Playwright here.
                    #
                    # Blocking this thread for the whole session is what wedged the browser
                    # — the agent found the app rendering blank and its own CDP attaches
                    # hanging. The listeners above are registered on the CONTEXT, so every
                    # console message the app emits crosses this connection whether or not
                    # the callback keeps it; while nobody reads, the driver pipe fills, the
                    # driver stops, and the browser then serves NO client, the agent
                    # included. Measured with the agent taken out of it, parent blocked
                    # 120s, child attaching over CDP:
                    #
                    #   quiet page            child OK in 0.7s
                    #   20k console messages  child never finished (killed at 90s)
                    #   same 20k, but pumped  child OK in 3.9s
                    #
                    # So it is volume-dependent, which is why a short run once succeeded and
                    # a longer one on the same code did not. Only Playwright calls belong on
                    # this thread (the sync API is single-threaded); the CLI touches none.
                    cli: dict[str, Any] = {}

                    def _run_cli() -> None:
                        try:
                            cli["data"] = engine._call_claude_cli(
                                prompt=brief, model=text(merged.get("model")).strip(),
                                max_turns=max_calls, working_dir=os.getcwd(), add_dir=run_dir,
                                agentic=True, timeout_secs=budget, oauth_tokens=tokens,
                                claude_bin=text(merged.get("claude_bin")).strip() or "claude",
                                trace_file=None,
                                effort=text(merged.get("effort")).strip(),
                                fallback_model=text(merged.get("fallback_model")).strip())
                        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
                            cli["exc"] = exc

                    th = threading.Thread(target=_run_cli, name="claude-cli", daemon=True)
                    th.start()
                    # A pump that outlives the CLI's own timeout by a margin, so a hung
                    # thread cannot keep this loop alive forever.
                    pump_until = time.time() + budget + 120
                    while th.is_alive() and time.time() < pump_until:
                        try:
                            page.wait_for_timeout(250)
                        except Exception:  # noqa: BLE001 - page gone: nothing left to service
                            break
                    th.join(timeout=60)
                    if "exc" in cli:
                        raise cli["exc"]
                    data = cli.get("data")
                    raw = str((data or {}).get("result") or "")
                    usage_total = _usage_of(data)
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            session_err = str(exc).splitlines()[0][:200]
            # The agent is told to checkpoint its verdict to verdict.json and to keep
            # refining it, so a session that died still has an answer on disk. Measured:
            # a run drove the app for the full 1200s and returned NOTHING — no verdict,
            # no screenshot — because the result lived only in a final message the
            # timeout cut off. Falling through to the normal verdict path keeps the work,
            # and the `or not shots` guard below still refuses to call a checkpoint with
            # no picture behind it a reproduction.
            raw = _checkpoint(run_dir)
            if not raw.strip():
                return _blank(f"browser/agent session failed: {session_err}",
                              login_source=login_source, action_log="\n".join(log),
                              console_errors="\n".join(console_msgs[:80]),
                              failed_requests="\n".join(net_fail[:80]),
                              artifact_dir=run_dir)

        # Only the agent's OWN screenshots count. The ticket's attachments land in this same
        # directory and are frequently .png (Expected/Actual images), so the old listing let
        # a run that photographed nothing satisfy `inconclusive = ... or not shots` on the
        # strength of files the node downloaded itself. That guard exists to stop a verdict
        # with no picture behind it reading as a reproduction; counting the reporter's
        # screenshots as ours defeats exactly that.
        downloaded = {os.path.basename(v) for v in (files or {}).values()}
        shots = sorted(f for f in os.listdir(run_dir)
                       if f.endswith(".png") and f not in downloaded)
        alog = ""
        ap = os.path.join(run_dir, "actions.log")
        if os.path.exists(ap):
            with open(ap, encoding="utf-8") as fh:
                alog = fh.read()[-6000:]
        # What the guard actually stopped, from this process's own record. The tool's log
        # is only a fallback: if the agent drove Playwright directly it never wrote one,
        # and the route handler here is the authoritative account either way.
        blocked_txt = "\n".join(blocked) or "\n".join(
            l for l in alog.splitlines() if "blocked" in l.lower())

        v = _json_object(raw)
        if not v:
            # Same reasoning as the exception path: the session may have produced a usable
            # answer and simply never printed it.
            ck = _checkpoint(run_dir)
            if ck.strip() and ck.strip() != raw.strip():
                v = _json_object(ck)
                if v:
                    raw = ck
                    session_err = session_err or "the agent printed no verdict; recovered the checkpoint"
        if not v:
            return _blank("the agent returned no JSON verdict.", login_source=login_source,
                          action_log="\n".join(log) + "\n" + alog, artifact_dir=run_dir,
                          screenshots="\n".join(shots), blocked_by_guard=blocked_txt,
                          console_errors="\n".join(console_msgs[:80]),
                          failed_requests="\n".join(net_fail[:80]),
                          raw=raw[-4000:])
        steps = v.get("steps") or []
        return _blank(
            "",
            # Only a real JSON true counts. `bool(x or default)` would flip a false, and
            # that is the one direction that must never happen here.
            reproduced=v.get("reproduced") is True,
            inconclusive=(v.get("inconclusive") is True) or not shots,
            observed=str(v.get("observed") or ""),
            contradiction=str(v.get("contradiction") or ""),
            evidence=str(v.get("evidence") or ""),
            next_probe=str(v.get("next_probe") or ""),
            # Not cosmetic: a checkpoint verdict is what the agent knew when it was
            # interrupted, and a reader has to be able to tell that apart from one it
            # chose to finish on.
            error=(f"verdict recovered from a checkpoint after the session ended: "
                   f"{session_err}" if session_err else ""),
            steps="\n".join(str(s) for s in steps) if isinstance(steps, list) else str(steps),
            confidence=str(v.get("confidence") or "low"),
            login_source=login_source,
            blocked_by_guard=blocked_txt,
            console_errors="\n".join(console_msgs[:80]),
            failed_requests="\n".join(net_fail[:80]),
            action_log="\n".join(log) + "\n" + alog,
            screenshots="\n".join(shots),
            artifact_dir=run_dir,
            raw=raw[-4000:],
            **usage_total,
        )

    return _handler


INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    textarea_field("ticket_text", "Ticket (steps + expected + actual)",
                   placeholder="bind to {{$get_issue.metadata.issue}}"),
    text_field("ticket_key", "Ticket key (names the artifact folder)", placeholder="SA-2423"),
    textarea_field("hypothesis", "Hypothesis to falsify (optional, from the analysis)",
                   placeholder="bind to {{$analyze.metadata.root_cause}} to test a stated mechanism"),
    text_field("live_url", "Environment to reproduce on",
               placeholder="https://alcon-dev-simplemdg-web.cfapps.br10.hana.ondemand.com/main/index.html"),
    text_field("allowed_hosts", "Hosts a credential may be sent to (comma-separated)",
               placeholder="cfapps.br10.hana.ondemand.com"),
    textarea_field("attachments", "Attachments JSON (for upload steps)",
                   placeholder="bind to {{$get_attachments.metadata.attachments}}"),
    switch_field("prefer_ticket_login",
                 "Use the account stated on the ticket (falls back to credential_ref)",
                 default=True),
    switch_field("headless", "Run the browser headless", default=True),
    text_field("browser_path", "Browser executable (blank = Playwright's own)"),
    text_field("artifact_dir", "Where to write screenshots", placeholder="~/.smdg-repro"),
    text_field("effort", "Reasoning effort (low|medium|high|xhigh|max; blank = CLI default)",
               placeholder="medium"),
    text_field("fallback_model", "Fallback model(s) if the primary is overloaded",
               placeholder="sonnet"),
    number_field("max_tool_calls", "Agent tool-call budget (0 = use the ceiling)",
                 placeholder="120", default=120),
    number_field("step_timeout_ms", "Per-step timeout (ms)", placeholder="45000", default=45000),
    number_field("timeout_secs", "Overall timeout (seconds)", placeholder="1200", default=1200),
    text_field("model", "Claude model (blank = default)"),
    text_field("claude_bin", "Claude binary", placeholder="claude", default="claude"),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    switch_field("reproduced", "Bug reproduced on the live app", readonly=True),
    switch_field("inconclusive", "Observation did not settle it", readonly=True),
    textarea_field("observed", "What the app actually did", readonly=True),
    textarea_field("contradiction", "Observation vs Expected", readonly=True),
    textarea_field("evidence", "The probe output relied on", readonly=True),
    textarea_field("next_probe", "What to observe next", readonly=True),
    textarea_field("steps", "Tool calls that mattered (replay)", readonly=True),
    text_field("confidence", "Confidence", readonly=True),
    text_field("login_source", "Where the login came from", readonly=True),
    textarea_field("blocked_by_guard", "Requests stopped by the mutation guard", readonly=True),
    textarea_field("console_errors", "Browser console errors / page errors", readonly=True),
    textarea_field("failed_requests", "Failed or >=400 network calls", readonly=True),
    textarea_field("action_log", "What the browser did", readonly=True),
    textarea_field("screenshots", "Screenshots written", readonly=True),
    text_field("artifact_dir", "Artifact folder", readonly=True),
    textarea_field("raw", "Agent raw output (tail)", readonly=True),
    number_field("input_tokens", "Input tokens", readonly=True),
    number_field("output_tokens", "Output tokens", readonly=True),
    number_field("cache_write_tokens", "Cache write tokens", readonly=True),
    number_field("cache_read_tokens", "Cache read tokens (~90% of an agentic run)",
                 readonly=True),
    number_field("cost_usd", "Cost USD (reported by the CLI)", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
