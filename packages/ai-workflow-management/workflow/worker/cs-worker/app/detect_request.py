"""The gate: read the message, decide whether there is anything real to analyse.

One node answers all four questions the operator asked for, in the cheap order:

    1. What is the user asking?           (Claude, with a deterministic regex fallback)
    2. Is that PROJECT one we serve?      (the registry — in code, no model involved)
    3. Is that ENVIRONMENT configured?    (the registry)
    4. Does that CHANGE REQUEST exist?    (one keyed OData GET, ~2 s)

and returns ``decision = run | stop``. A SWITCH on that is the whole routing.

**Why it is one node and not four.** Each answer narrows the next: without a project
there is no host to ask about the CR, and without a target there are no credentials to
ask with. Split across nodes, three of the four would be a wire carrying an empty string,
and every one of those wires is a place for the flow to run on regardless — which is the
exact failure this gate exists to stop.

**Why the CR probe is here at all.** Measured on the ALCON bot (hackathon
`onboard-project-bot`, finding F16): a CR that does not exist does **not** fail the flow.
It runs the whole thing — 187 s, two LLM calls, two uploaded files — and produces a
confident analysis whose content is "no records exist for this reqID". The keyed entity
``Request(reqID='…')/requestLog`` is what tells the three cases apart:

| reqID | status | error |
|---|---|---|
| exists | ``OK`` | — |
| does not exist | ``FAILED`` | ``HTTP 404: … Not Found`` |
| infrastructure broken | ``FAILED`` | ``<urlopen error … getaddrinfo failed>`` |

That third row is why ``FAILED`` alone cannot mean "not found".

**Fail open, deliberately.** The gate stops only on a *definite* verdict: a malformed
number, an unknown project or environment, or an explicit 404. A DNS blip, an expired
token, a moved endpoint — anything ambiguous — is allowed through, so the flow answers as
it would have anyway. A pre-check that fails closed is a wall, and it will be built on a
day when the endpoint is fine and torn down on the day it is not.

**Language.** The configuration in this file is English (workflow definitions are a shared
asset — finding F17). The *reply to the user* follows whatever language the user wrote in,
which is the same rule the existing bots' classifier uses.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.error
from typing import Any, Optional

from worker_sdk.layer1_domain.auto_form_mapper import (
    switch_field,
    text_field,
    textarea_field,
)

from app import secrets as _secrets
from app.claude_cli import as_text, call_claude, parse_json_object
from app.fetch_log import fetch as _fetch_log
from app.registry import REGISTRY
from app.worker_trace import trace as _trace

# A change request: CR followed by digits. Deliberately NOT the Jira-issue pattern
# (letters-dash-digits) — the existing classifier once matched both with one regex and fed
# "CR12345" to a flow that went looking for a Jira issue by that name.
_CR_RE = re.compile(r"\b(CR\s*[-_]?\s*\d{3,})\b", re.IGNORECASE)

_INTENTS = ("analyze_log", "cancel_request", "capabilities", "greeting", "other")

_DEFAULT_SYSTEM_PROMPT = (
    "You read one chat message sent to a customer-service bot for the SMDG master-data "
    "platform. The bot does TWO things with a change request (CR), in one project and one "
    "environment. It analyses the process logs - reading them, finding the failing "
    "validation step, explaining the root cause and suggesting the next action. And when "
    "it is plainly asked to, it CANCELS the request.\n"
    "Reply with ONLY a JSON object, nothing else:\n"
    '{"intent": "<analyze_log | cancel_request | capabilities | greeting | other>", '
    '"project": "<the project name the user named, exactly as they wrote it, or empty>", '
    '"environment": "<the environment the user named, e.g. DEV or QAS, or empty>", '
    '"reqID": "<the change request, e.g. CR0000018045, or empty>", '
    '"summarize": "<false if the user asked for the logs alone - "logs only", "no analysis", "chi lay log" - otherwise true>", '
    '"reply": "<ALWAYS write a short, friendly, direct answer to the user here - even when '
    'you cannot help; if you cannot, say what you CAN do>"}\n'
    "Use intent `analyze_log` whenever the user wants logs read, a CR investigated, or an "
    "error explained. Use `capabilities` when they ask what the bot can do. Use `greeting` "
    "for hello / thanks / small talk. Use `other` for anything else.\n"
    "Use `cancel_request` ONLY when the user plainly asks to CANCEL or abort a change "
    "request - \"cancel CR0000018045\", \"huy CR0000018045\", \"abort this request\". "
    "Cancelling cannot be undone, so the instruction has to be explicit: wanting to know "
    "why a CR failed, or what went wrong, or whether it is stuck, is `analyze_log` and "
    "NOT a cancellation. If you are unsure which of the two they meant, choose "
    "`analyze_log` - reading logs is harmless and they can ask again.\n"
    "Set `summarize` to false ONLY when the user explicitly asked for the logs "
    "without the analysis. Wanting a CR investigated is NOT such a request - the "
    "analysis is the point of the bot, so `true` is the answer unless they said "
    "otherwise.\n"
    "Do NOT invent a project, an environment or a CR number that the user did not write. "
    "An empty field is correct and useful; a guessed one sends the answer to the wrong "
    "customer's system.\n"
    "A change request always starts with CR followed by digits. A Jira issue key has a "
    "dash (e.g. MCKES-360) and is NOT a change request - never put one in the reqID field.\n"
    "Write `reply` in the SAME language the user wrote in - Vietnamese if they wrote "
    "Vietnamese, English if they wrote English. Keep technical identifiers (CR numbers, "
    "project and environment names, log field names) exactly as they are; do not translate "
    "them."
)

_FALLBACK_REPLY = "Sorry, I didn't quite catch that. Could you rephrase it?"

# "Logs only" said in so many words. Matched against the message with its diacritics
# stripped, so `khong phan tich` and `kh\u00f4ng ph\u00e2n t\u00edch` are the same request -
# people type Vietnamese both ways in Teams, and the unaccented half would otherwise
# silently get an analysis it asked not to have.
_NO_SUMMARY_RE = re.compile(
    r"\bno\s+(summary|analysis)\b"
    r"|\bwithout\s+(a\s+)?(summary|analysis)\b"
    r"|\bskip\s+(the\s+)?(summary|analysis)\b"
    r"|\b(just|only)\s+(the\s+)?(raw\s+)?logs?\b"
    r"|\blogs?\s+only\b"
    r"|\braw\s+logs?\b"
    r"|\bdon'?t\s+(analy[sz]e|summari[sz]e)\b"
    r"|khong\s+(can\s+)?(phan\s+tich|tom\s+tat)"
    r"|khoi\s+(phan\s+tich|tom\s+tat)"
    r"|chi\s+(can\s+|lay\s+|xem\s+)?log"
    r"|log\s+thoi"
    r"|lay\s+log\s+thoi",
    re.IGNORECASE)


def _strip_marks(s: str) -> str:
    """`kh\u00f4ng ph\u00e2n t\u00edch` -> `khong phan tich`. NFD splits a letter from its
    accents; dropping the combining marks leaves the ASCII skeleton the patterns match."""
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if not unicodedata.combining(c))


def _wants_summary(message: str, said: Any) -> str:
    """`"true"` / `"false"` - a STRING, because that is what the SWITCH in the log flow
    compares against. The user's own words win over the model: Claude is asked for a
    judgement, but a message that plainly says "logs only" is not a judgement."""
    if _NO_SUMMARY_RE.search(_strip_marks(message)):
        return "false"
    if str(said).strip().lower() in ("false", "no", "0", "skip"):
        return "false"
    return "true"



def _normalise_cr(raw: str) -> str:
    """``cr 18045`` / ``CR-18045`` -> ``CR18045``. A separator is not a reason to call a
    message CR-less; the systems all key on the compact form."""
    s = (raw or "").strip().upper()
    s = re.sub(r"\s+|[-_]", "", s)
    return s


def _looks_like_cr(req: str) -> bool:
    up = _normalise_cr(req)
    tail = up[2:] if up.startswith("CR") else ""
    return up.startswith("CR") and tail.isdigit() and len(tail) >= 4


def _words(message: str) -> str:
    """The message as ' WORD WORD ' — punctuation flattened, so a whole-word test is a
    substring test and "MCK-DEV" or "(MCK)" match as readily as "MCK"."""
    return " " + re.sub(r"[^A-Za-z0-9]+", " ", message or "").upper() + " "


def _scan_project(message: str) -> str:
    """Deterministic project scan over the raw message — the fallback for when Claude is
    unavailable. Only names the registry already knows are searched for, so this can
    never invent a target."""
    text = _words(message)
    for candidate in REGISTRY.projects():
        if f" {candidate} " in text:
            return candidate
    for alias, project in REGISTRY.aliases.items():
        if f" {alias} " in text and project in REGISTRY.projects():
            return project
    return ""


# "project VINFAST", "VINFAST project", "du an VINFAST" — used ONLY to name the project a
# user asked for when we cannot serve it, never to resolve a target. Observed 2026-08-24
# on "xem log CR0000004540 cua project VINFAST moi truong DEV": told not to invent a
# project, the model returns an EMPTY field for one it does not recognise, so the
# rejection came out as "Which project should I look in?" rather than naming the one the
# user actually typed.
_NAMED_PROJECT_RE = re.compile(
    r"(?:project|du\s*an|dự\s*án)\s+([A-Za-z][A-Za-z0-9_-]{1,19})"
    r"|([A-Za-z][A-Za-z0-9_-]{1,19})\s+project",
    re.IGNORECASE)


def _named_project(message: str) -> str:
    """Whatever the user called their project, verbatim — for the rejection sentence."""
    m = _NAMED_PROJECT_RE.search(message or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip().upper()


def _scan_environment(message: str, project: str) -> str:
    """The same scan for an environment, restricted to those *project* actually has.
    Also accepts the spellings the registry normalises (QA -> QAS, PRODUCTION -> PRD)."""
    if not project:
        return ""
    text = _words(message)
    for candidate in REGISTRY.environments(project):
        if f" {candidate} " in text:
            return candidate
    for word in text.split():
        got = REGISTRY.normalise_environment(project, word)
        if got:
            return got
    return ""


def _probe_cr(project: str, environment: str, req_id: str,
              timeout_note: list[str]) -> tuple[str, str]:
    """(verdict, detail) where verdict is ``exists`` | ``missing`` | ``unknown``.

    ``unknown`` is the fail-open answer and covers everything that is not a clean 200 or a
    clean 404 — a token failure, DNS, a moved endpoint. The caller must let those run."""
    try:
        out = _fetch_log("request_log", {"project": project, "environment": environment,
                                         "reqID": req_id})
    except urllib.error.HTTPError as exc:
        code = exc.code
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        if code == 404:
            return "missing", f"HTTP 404 {body}"
        timeout_note.append(f"HTTP {code}")
        return "unknown", f"HTTP {code} {body}"
    except Exception as exc:  # noqa: BLE001 — network, OAuth, anything: fail open
        timeout_note.append(str(exc)[:120])
        return "unknown", str(exc)[:200]

    if str(out.get("status") or "").upper() == "OK":
        return "exists", f"HTTP {out.get('status_code')}"
    err = str(out.get("error") or "")
    if "404" in err or "not found" in err.lower():
        return "missing", err[:200]
    timeout_note.append(err[:120])
    return "unknown", err[:200]


def _out(**kw: Any) -> dict[str, Any]:
    """Schema-complete output. Every field is always present, so a downstream expression
    never resolves to nothing (an unresolved ``{{$node.field}}`` becomes the literal text
    in a reply, which is how "🤖 None" once reached a Teams channel)."""
    base: dict[str, Any] = {
        "status": "OK", "decision": "stop", "intent": "other", "message_to_user": "",
        "reply": "", "project": "", "environment": "", "target": "", "reqID": "",
        "api_url": "", "token_url": "", "abap_url": "", "cr_url": "",
        "cr_check": "skipped", "summarize": "true",
        "cr_detail": "", "known_targets": REGISTRY.catalogue(), "raw": "", "error": "",
    }
    base.update(kw)
    return base


def make_detect_handler() -> Any:
    """Handler ``(inputs, parameters) -> dict``. Never raises."""

    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        message = as_text(merged.get("message") or merged.get("text")).strip()
        catalogue = REGISTRY.catalogue()

        if not catalogue:
            # No target configured at all: an operator problem, not a user one. Say so
            # plainly rather than telling the user their project does not exist.
            return _out(status="FAILED", decision="stop", intent="other",
                        message_to_user=("The log-analysis service has no project configured "
                                         "yet. Please contact the SMDG-AI team."),
                        error="registry is empty: no $LOG_WORKER_CLIENT_ID_<PROJECT>_<ENV> "
                              "in the environment. " + "; ".join(REGISTRY.problems()))

        if not message:
            return _out(status="FAILED", decision="stop",
                        message_to_user="I did not receive any message to read.",
                        error="'message' is empty (bind it to the trigger's text field).")

        # -- 1. what is being asked ------------------------------------------------
        model = as_text(merged.get("model")).strip()
        claude_bin = as_text(merged.get("claude_bin")).strip() or "claude"
        system_prompt = as_text(merged.get("system_prompt")).strip() or _DEFAULT_SYSTEM_PROMPT
        try:
            timeout_secs = int(float(merged.get("timeout_secs") or 90))
        except (TypeError, ValueError):
            timeout_secs = 90

        prompt = (f"{system_prompt}\n\n--- PROJECTS AND ENVIRONMENTS THIS BOT SERVES ---\n"
                  f"{catalogue}\n--- MESSAGE ---\n{message}\n--- END ---")

        raw, err = "", ""
        try:
            raw = call_claude(prompt, _secrets.claude_tokens(), model=model,
                              claude_bin=claude_bin, timeout_secs=timeout_secs)
        except Exception as exc:  # noqa: BLE001 — fall through to the regex scan
            err = f"claude call failed: {exc}"
            _trace(f"detect: claude failed -> regex fallback: {exc}")

        obj = parse_json_object(raw)
        intent = str(obj.get("intent") or "").strip().lower()
        if intent not in _INTENTS:
            # No usable intent: if the message names a CR, treat it as a log request —
            # that is what a message with a CR in it almost always is.
            intent = "analyze_log" if _CR_RE.search(message) else "other"
        reply = str(obj.get("reply") or "").strip()

        said_project = str(obj.get("project") or "").strip()
        said_env = str(obj.get("environment") or "").strip()
        said_cr = _normalise_cr(str(obj.get("reqID") or ""))
        summarize = _wants_summary(message, obj.get("summarize"))

        # The model's fields are a HINT, never the authority. Anything it names has to
        # exist in the registry to survive; anything it missed is recovered from the
        # message itself, so a Claude outage degrades the wording, not the routing.
        project = REGISTRY.normalise_project(said_project) or _scan_project(message)
        environment = (REGISTRY.normalise_environment(project, said_env)
                       or _scan_environment(message, project))
        if not said_cr:
            m = _CR_RE.search(message)
            said_cr = _normalise_cr(m.group(1)) if m else ""
        req_id = said_cr

        # Defaults from the node, for a channel that is dedicated to one project.
        project = project or REGISTRY.normalise_project(as_text(merged.get("default_project")))
        environment = environment or REGISTRY.normalise_environment(
            project, as_text(merged.get("default_environment")))

        if intent in ("greeting", "capabilities") and not req_id:
            text = reply or (
                "I analyse the process logs of a change request: I read them, find the "
                "failing validation step, explain the root cause and suggest what to do "
                f"next. I cover {catalogue}. Send me something like "
                "\"CR0000018045 in MCK project with DEV environment\".")
            return _out(intent=intent, decision="stop", message_to_user=text, reply=reply,
                        project=project, environment=environment, reqID=req_id,
                        raw=raw[:2000], error=err)

        # -- 2/3. is the project, and that environment, one we serve? --------------
        if not project:
            # Name what they asked for, if we can tell what that was — from the model's
            # own field, or failing that from the message itself.
            asked = said_project or _named_project(message)
            if asked:
                text = (f"I don't have access to a project called **{asked}**. "
                        f"I can analyse logs for: {catalogue}. Which one did you mean?")
            else:
                text = ("Which project should I look in? I can analyse logs for: "
                        f"{catalogue}.")
            return _out(intent=intent, decision="stop", message_to_user=text, reply=reply,
                        reqID=req_id, raw=raw[:2000], error=err)

        if not environment:
            available = REGISTRY.environments(project)
            if len(available) == 1:
                # Only one environment configured for this project — asking would be noise.
                environment = available[0]
            else:
                if said_env:
                    text = (f"**{project}** is not set up for the **{said_env}** environment. "
                            f"I have {', '.join(available)} for {project}. "
                            "Which one should I check?")
                else:
                    text = (f"Which environment of **{project}**? I have "
                            f"{', '.join(available)}.")
                return _out(intent=intent, decision="stop", message_to_user=text, reply=reply,
                            project=project, reqID=req_id, raw=raw[:2000], error=err)

        target = REGISTRY.resolve(project, environment)
        if target is None:                      # belt and braces — normalise said yes
            return _out(intent=intent, decision="stop", project=project,
                        environment=environment, reqID=req_id, raw=raw[:2000],
                        message_to_user=(f"I don't have credentials for {project} "
                                         f"{environment} yet. I can analyse: {catalogue}."),
                        error=f"registry.resolve({project},{environment}) returned nothing")

        resolved = {"project": target.project, "environment": target.environment,
                    "target": target.key, "api_url": target.api_url,
                    "token_url": target.token_url, "abap_url": target.abap_url}
        # Resolved once here so the reply can link straight to the CR. Filled in after
        # the reqID is known and normalised, below.

        # -- 4. does the change request exist? ------------------------------------
        if not req_id:
            return _out(intent=intent, decision="stop", reply=reply, raw=raw[:2000],
                        error=err, **resolved,
                        message_to_user=("I need a change request number to pull the "
                                         f"{target.project} {target.environment} logs - "
                                         "something like **CR0000018045**. Which one should "
                                         "I look at?"))

        if not _looks_like_cr(req_id):
            return _out(intent=intent, decision="stop", reply=reply, reqID=req_id,
                        raw=raw[:2000], error=err, **resolved,
                        message_to_user=(f"**{req_id}** does not look like a change request "
                                         "number. They start with `CR` followed by digits, "
                                         "like **CR0000018045**. Could you check it?"))

        req_id = _normalise_cr(req_id)
        resolved["cr_url"] = target.cr_url(req_id)
        if not _as_bool(merged.get("check_cr_exists"), True):
            return _out(intent=intent, decision="run", reply=reply, reqID=req_id,
                        summarize=summarize, cr_check="skipped", raw=raw[:2000], error=err, **resolved,
                        message_to_user="")

        notes: list[str] = []
        verdict, detail = _probe_cr(target.project, target.environment, req_id, notes)
        _trace(f"detect: {req_id} in {target.key} -> {verdict} ({detail[:80]})")

        if verdict == "missing":
            return _out(intent=intent, decision="stop", reply=reply, reqID=req_id,
                        cr_check="missing", cr_detail=detail, raw=raw[:2000], error=err,
                        **resolved,
                        message_to_user=(f"I could not find **{req_id}** in the "
                                         f"{target.project} {target.environment} system - "
                                         "there is no request with that number. Could you "
                                         "double-check it?\n\nIf the number is right, the "
                                         "request may live in a different environment."))

        # exists, or unknown -> run. See the module docstring on failing open.
        return _out(intent=intent, decision="run", reply=reply, reqID=req_id,
                    summarize=summarize, cr_check=verdict, cr_detail=detail, message_to_user="",
                    raw=raw[:2000],
                    error=err or ("; ".join(notes) if verdict == "unknown" else ""),
                    **resolved)

    return _handler


def _as_bool(value: Any, default: bool) -> bool:
    """A switch field arrives as a real bool, but a workflow expression resolves to text."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _prefill(field: Any, value: str) -> dict[str, Any]:
    """Pre-fill a textarea. ``textarea_field`` takes no ``default``; the engine's own
    schema builder pre-fills the same way, by writing into fieldConfig.controlProps."""
    d = field.to_dict()
    d.setdefault("fieldConfig", {}).setdefault("controlProps", {})["value"] = value
    return d


INPUT_SCHEMA: list[dict[str, Any]] = [
    textarea_field("message", "User message",
                   placeholder="bind to the trigger, e.g. {{$input.message}}").to_dict(),
    switch_field("check_cr_exists", "Check that the change request exists", default=True).to_dict(),
    text_field("default_project", "Default project (blank = the user must name one)",
               placeholder="e.g. MCK - for a channel dedicated to one project").to_dict(),
    text_field("default_environment", "Default environment (blank = the user must name one)",
               placeholder="e.g. DEV").to_dict(),
    text_field("model", "Claude model (blank = default)",
               placeholder="e.g. claude-sonnet-4-6").to_dict(),
    text_field("claude_bin", "Claude binary", placeholder="claude", default="claude").to_dict(),
    text_field("timeout_secs", "Timeout (seconds)", placeholder="90", default="90").to_dict(),
    _prefill(textarea_field("system_prompt", "Reading instruction",
                            placeholder="how to read the message", rows=4),
             _DEFAULT_SYSTEM_PROMPT),
]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    text_field("decision", "Decision - run or stop (match this in the SWITCH)", readonly=True),
    text_field("intent", "Intent (analyze_log / cancel_request / capabilities / "
                         "greeting / other)", readonly=True),
    text_field("summarize", "Summarize? (false when the user asked for logs only)", readonly=True),
    textarea_field("message_to_user", "What to say when the decision is stop", readonly=True),
    textarea_field("reply", "Direct reply drafted by the model", readonly=True),
    text_field("project", "Project (resolved)", readonly=True),
    text_field("environment", "Environment (resolved)", readonly=True),
    text_field("target", "Registry target key", readonly=True),
    text_field("reqID", "Change request (normalised)", readonly=True),
    text_field("api_url", "Resolved API base", readonly=True),
    text_field("token_url", "Resolved OAuth token URL", readonly=True),
    text_field("abap_url", "Resolved ABAP log URL", readonly=True),
    text_field("cr_url", "Link to the change request in the web app", readonly=True),
    text_field("cr_check", "CR existence check (exists / missing / unknown / skipped)",
               readonly=True),
    text_field("cr_detail", "CR check detail", readonly=True),
    text_field("known_targets", "Projects and environments configured", readonly=True),
    textarea_field("raw", "Claude raw reply", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
