"""GitLab read-only analyse node — root-cause a ticket WITHOUT cloning the repo.

Why this exists rather than reusing the implement node's clone:

* ``git clone`` of the customer UI repos fails to check out on Windows —
  ``Filename too long`` on paths under ``admin/src/test/unit/controller/...``
  (measured: clone "succeeded", 2271 files left unchecked-out). The clone reports
  success, so a cloning analyse node reasons over an EMPTY tree and never notices.
* Analyse is read-only, so it needs ``read_api`` only — no ``write_repository``, no
  disk, no long-path config on the worker host.
* Reading 3 files beats cloning ~2500 to read 3.

The node is deliberately NOT agentic: the PAT stays inside Python and never reaches a
prompt, a CLI argument, or the node's output. Adaptivity is kept by asking Claude for a
SEARCH PLAN first, then executing that plan against the GitLab API:

    1. PLAN     — Claude reads the ticket (+ optional FS rule) and returns search terms
    2. FETCH    — this module runs those searches and downloads the matching files
    3. ANALYSE  — Claude root-causes from the real file contents

Load-bearing detail: **GitLab blob search searches exactly ONE ref — the project default
unless ``ref`` is passed.** Measured on the core repo
(``simplemdg-dashboard/ui-group/simplemdg_ui_typescript``, default ``uat``):
``massProcessingUpload`` returns 0 hits with no ``ref`` and 3 with ``ref=staging``. On a
customer fork where both branches carry the code the counts can match (Alcon: 20 vs 20),
so the trap is silent exactly where it bites — a caller who omits ``ref`` gets "the code
does not exist" from the wrong branch and then a fabricated root cause. Every request
here passes ``ref`` explicitly.

Second live trap: tickets use abbreviations the code does not. ``DSEWC`` finds nothing;
``DefaultSectionWithCondition`` finds the real file. Expanding abbreviations is an
explicit instruction in the PLAN step, not a hope.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote

from worker_sdk.layer1_domain.auto_form_mapper import (
    number_field,
    switch_field,
    text_field,
    textarea_field,
)
from worker_sdk.layer2_application.services.effective_auth import borrow_credential

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    _DEFAULT_SECRETS_FILE,
)

# Lock files, bundles and minified assets match almost any search term and carry no
# reasoning value — they only burn context. Skipped before a file is ever downloaded.
_SKIP_RE = re.compile(
    r"(pnpm-lock\.yaml|package-lock\.json|yarn\.lock|\.min\.(js|css)$|"
    r"\.umd\.js$|/dist/|/node_modules/|\.(png|jpe?g|gif|svg|woff2?|ttf|ico|pdf|zip)$)",
    re.IGNORECASE,
)

_PLAN_PROMPT = (
    "You are locating the code behind a SimpleMDG customer-UI bug, in a repository you "
    "will NOT be given directly — another process will run your searches against the "
    "GitLab search API for you.\n\n"
    "Return search terms that will actually match SOURCE CODE, not ticket prose:\n"
    "1. EXPAND every abbreviation to the identifier a developer would have typed. Ticket "
    "abbreviations do not exist in code — 'DSEWC' returns nothing, "
    "'DefaultSectionWithCondition' returns the real file. Emit BOTH the expansion and any "
    "camelCase/PascalCase variants.\n"
    "2. Prefer identifiers over English: class names, method names, enum members, i18n keys.\n"
    "3. For a VISUAL symptom, search the framework property that produces it, not the "
    "colour. In SAPUI5 a blue stripe on a row's left edge is the 'highlight' property with "
    "MessageType.Information; a status icon is a 'sap-icon://...' name.\n"
    "4. Include the screen/route name from the reproduce steps.\n\n"
    "Reply with ONE JSON object and NOTHING else (no prose, no code fences):\n"
    '{"terms": ["...", "..."], "why": "max 20 words"}\n'
    "Give between 3 and 8 terms, most specific first. Keep `why` to one short clause — a "
    "long explanation is where the JSON gets malformed, and only the terms matter."
)

_ANALYSE_PROMPT = (
    "You are root-causing a SimpleMDG customer-UI bug from real repository source, quoted "
    "below. You did not clone the repo; you are reading the files that matched the search.\n\n"
    "The FS (functional spec) is the source of truth and is assumed CORRECT:\n"
    "  - FS rule differs from what the code does  -> a real bug; explain the mechanism\n"
    "  - FS rule matches what the code does       -> the ticket is likely WRONG; say so plainly\n"
    "  - FS rule not supplied or indeterminate    -> say that; do NOT guess\n\n"
    "Rules:\n"
    "1. Never assert a cause you cannot point at in the quoted source. Unsupported "
    "suspicions belong in 'open_questions', not in 'root_cause'.\n"
    "2. If the files you were given are not enough, say which file or symbol you still "
    "need — a further search is cheap, a fabricated cause is not.\n"
    "3. Shared logic in these repos often has a WEB-WORKER twin as well as a main-thread "
    "copy. If you name a file under shared-ui5lib, say whether a twin likely exists, "
    "because fixing one and not the other ships a half-fix.\n\n"
    "WHAT YOU CAN AND CANNOT SEE. Exactly ONE repository was searched - the one quoted "
    "above. SimpleMDG\'s backend is not in it: per customer it is ~50 separate services "
    "(be-group/master-data/<object>/simplemdg_{db,srv,srv_*_process}, be-group/core/*, "
    "be-group/helper/*), none of which you were given. So:\n"
    "* Never describe backend code you have not been shown, and never propose a change "
    "to a file that is not in the quoted source. A plausible backend fix with no source "
    "behind it is worse than admitting you cannot see it - it sends a developer to "
    "rewrite the wrong thing, on a customer ticket.\n"
    "* Evidence pointing OUT of this repository is a real and useful answer. Set `layer` "
    "accordingly, put in `root_cause` only what the quoted source and the observation "
    "actually prove, return `solutions` EMPTY rather than speculative ones, and name the "
    "repositories that must be read next in `open_questions`.\n"
    "* `not-a-bug` is held to the same standard. Source showing a fix EXISTS does not "
    "show it RUNS: where the observation contradicts the code, the observation wins and "
    "the ticket is a real bug whose fix is incomplete.\n\n"
    "Also judge the TICKET, not only the code:\n"
    "* `enough` — is the ticket itself complete enough to act on? It needs reproduce "
    "steps, an Expected AND an Actual, an environment, and enough identifying data (object "
    "type / record / CR) to reproduce. Missing FS or a missing root cause do NOT make it "
    "incomplete — that is the analysis's job, not the reporter's.\n"
    "* `questions` — what the REPORTER must add. Empty when `enough` is true. Keep these "
    "separate from `open_questions`, which is what YOU still need from the CODE.\n"
    "* `solutions` — one entry per genuinely distinct way to fix the proven cause, most "
    "recommended first, each with its blast radius. Do NOT invent alternatives to look "
    "thorough: if only one sane fix exists, return one. If the cause is not proven, return "
    "an empty list rather than speculative fixes.\n\n"
    "Reply with ONE JSON object and NOTHING else (no prose, no code fences):\n"
    '{"layer": "frontend|backend|data|config|not-a-bug", "enough": true, "questions": ["..."], '
    '"root_cause": "...", "files": ["path:line", "..."], '
    '"evidence": "what in the source proves it", '
    '"solutions": [{"summary": "one line", "change": "what to change, where", '
    '"blast_radius": "who else is affected", "risk": "high|medium|low"}], '
    '"recommended": "why the first solution is the one to take, or why none can be chosen yet", '
    '"open_questions": ["..."], "confidence": "high|medium|low"}'
)


# SimpleMDG bug tickets carry the test account IN THE BODY, e.g.
#   * User ID: [+alcon.dev@laidon.com+|mailto:alcon.dev@laidon.com] / iN06i*I4LFit0o5'…
# The bug-fix doctrine treats those as *observed data* — never entered, never written to
# a file or a memory. Feeding the ticket verbatim into a prompt breaks that: the password
# reaches the model, lands in the node's `raw` output, and can be echoed back into the
# Jira comment. So it is stripped on the way in, at the one place every prompt reads from.
#
# The email is KEPT: it identifies which environment/account class the ticket is about,
# which is routing information, not a secret.
_REDACT = (
    # The template's own credential line. Matched as a WHOLE LINE rather than by what
    # follows the email, because the email arrives wrapped in Jira wiki markup —
    # `[+alcon.dev@laidon.com+|mailto:alcon.dev@laidon.com] / secret` — so the separator
    # is not adjacent to the address. Everything after the FIRST " / " goes; the password
    # itself may contain slashes (the live one ends `!/9@`), so only the first counts.
    re.compile(r"(?im)^(.*\b(?:User\s*ID|Username|User\s*Name|Login|Account)\b.*?\s/\s).*$"),
    # "password: x" / "pwd = x" and friends
    re.compile(r"(?i)\b(password|passwd|pwd|pass)(\s*[:=]\s*)\S+"),
    # "<email> / <secret>", tolerating wiki-markup closers between the two
    re.compile(r"([\w.+-]+@[\w.-]+\.\w{2,})([\]\)+|>]*\s*/\s*)\S+"),
)


# ADF block types: their content ends a line. Anything else is inline.
_ADF_BLOCKS = frozenset((
    "paragraph", "heading", "listItem", "blockquote", "codeBlock", "tableRow",
    "rule", "panel", "mediaSingle", "bulletList", "orderedList", "table",
))


def _adf_text(o: Any) -> str:
    """Plain text out of an Atlassian Document Format tree."""
    if isinstance(o, str):
        return o
    if isinstance(o, list):
        return "".join(_adf_text(x) for x in o)
    if not isinstance(o, dict):
        return ""
    t = o.get("type")
    if t == "text":
        txt = str(o.get("text") or "")
        # A link keeps its URL in marks[].attrs.href, NOT in `text`. Dropping it cost a
        # live run: the ticket's deep link vanished, the planner had no URL to use, and
        # its `goto` failed with "Cannot navigate to invalid URL". That route is the most
        # reliable repro instruction these tickets carry, so it has to survive.
        for mk in (o.get("marks") or []):
            if isinstance(mk, dict) and mk.get("type") == "link":
                href = str((mk.get("attrs") or {}).get("href") or "")
                if href and href not in txt:
                    return f"{txt} <{href}>"
        return txt
    if t == "hardBreak":
        return "\n"
    if t in ("inlineCard", "blockCard", "embedCard"):
        url = str((o.get("attrs") or {}).get("url") or "")
        return f"<{url}>" if url else ""
    inner = _adf_text(o.get("content"))
    return inner + "\n" if t in _ADF_BLOCKS else inner


def _split_leading_json(s: str) -> tuple[Any, str]:
    """``(parsed, remainder)`` for a string that STARTS with a JSON value.

    Brace-counting that skips over string literals, so a ``{`` or ``}`` inside a ticket
    description does not end the scan early. Returns ``(None, s)`` when the leading value
    will not parse, leaving the caller to treat the whole thing as plain text.
    """
    opener = s[0]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[:i + 1]), s[i + 1:]
                except Exception:  # noqa: BLE001
                    return None, s
    return None, s


def _flatten_issue(value: Any) -> str:
    """Readable text from whatever ``{{$get_issue.metadata.issue}}`` hands over.

    That reference resolves to the WHOLE Jira issue, and its description is ADF — a JSON
    tree whose prose is split across many ``{"type":"text"}`` nodes. Measured on SA-2423:
    31 fragments, with ``"User ID: "``, the address and ``" / <password>"`` each a
    separate node. So no line-oriented logic can work on the raw value: the credential
    line does not exist as a line, which is why both the redaction and the login
    extraction silently matched nothing while passing every test written against the
    REST-v2 wiki-markup description.

    Flattening first also makes the prompt cheaper and clearer — the model was being fed
    ``marks``/``attrs`` scaffolding it has no use for.

    Comments are included: the ticket thread is where previous automated passes left
    their findings, and reading them is what lets a later run correct an earlier one.
    """
    if isinstance(value, str):
        # An http_request node hands the Jira REST body over as a JSON STRING, and a
        # workflow template may concatenate one with trailing text. Left as-is that is a
        # single long line whose newlines are literal `\n` escapes, so every line-anchored
        # pattern downstream — the credential line, the redaction — silently matches
        # nothing. That is exactly how a live run reported "no app login found" while the
        # password sat in its input. Parse the leading JSON, keep any trailing text.
        # A workflow template interleaves JSON bodies with its own text ("=== OBSERVED ===",
        # a thread separator, and so on), so scan the whole string: flatten each JSON blob
        # found, keep the prose between them. Doing only the leading blob left the appended
        # comment thread as raw JSON with escaped newlines.
        s = value.strip()
        if "{" not in s and "[" not in s:
            return value
        out: list[str] = []
        i = 0
        while i < len(s):
            ch = s[i]
            if ch in "{[":
                obj, rest = _split_leading_json(s[i:])
                if obj is not None:
                    out.append(_flatten_issue(obj))
                    i = len(s) - len(rest)
                    continue
            out.append(ch)
            i += 1
        return "".join(out).strip()
    if not isinstance(value, dict):
        return str(value or "")
    # A comment-list response (`/issue/{key}/comment`) carries the prose under
    # `comments[].body`, not in ADF text nodes, so the generic walk would return nothing.
    if isinstance(value.get("comments"), list) and "fields" not in value:
        out: list[str] = []
        for cm in value["comments"][:12]:
            if not isinstance(cm, dict):
                continue
            who = (cm.get("author") or {}).get("displayName") or "?"
            body = _adf_text(cm.get("body")).strip() or str(cm.get("body") or "").strip()
            if body:
                out.append(f"--- comment by {who} ---\n{body}")
        return "\n\n".join(out)
    fields = value.get("fields") if isinstance(value.get("fields"), dict) else None
    if fields is None:
        return _adf_text(value)          # already a description / ADF doc

    parts: list[str] = []
    head = f"{value.get('key') or ''}: {fields.get('summary') or ''}".strip(": ").strip()
    if head:
        parts.append(head)
    parts.append(_adf_text(fields.get("description")))
    names = [str(a.get("filename")) for a in (fields.get("attachment") or [])
             if isinstance(a, dict) and a.get("filename")]
    if names:
        # Repro steps reference attachments BY FILENAME, so the names must survive.
        parts.append("Attachments: " + ", ".join(names))
    comments = (fields.get("comment") or {}).get("comments") if isinstance(fields.get("comment"), dict) else None
    for cm in (comments or [])[-12:]:
        if not isinstance(cm, dict):
            continue
        who = (cm.get("author") or {}).get("displayName") or "?"
        body = _adf_text(cm.get("body")).strip()
        if body:
            parts.append(f"--- comment by {who} ---\n{body}")
    return "\n\n".join(p for p in parts if p and p.strip())


def _redact(text: str) -> str:
    """Remove test-account secrets from ticket text before it reaches a model."""
    out = text or ""
    out = _REDACT[0].sub(r"\1[redacted]", out)
    out = _REDACT[1].sub(r"\1\2[redacted]", out)
    out = _REDACT[2].sub(r"\1\2[redacted]", out)
    return out


def _token_from_credential(cred: dict[str, Any] | None) -> str:
    """Pull the PAT out of a vault ``{type, config}`` entry.

    The kind a ``gitlab-pat`` credential resolves to is the vault's choice, not ours, and
    the SDK's own docstring lists several shapes (``api_key`` -> ``config['value']``,
    ``bearer`` -> ``config['token']``, ``basic`` -> ``config['password']``). Reading a
    fixed key would fail closed on a credential that is perfectly valid, so every
    plausible field is tried and the first non-empty one wins.
    """
    if not isinstance(cred, dict):
        return ""
    cfg = cred.get("config")
    if not isinstance(cfg, dict):
        return ""
    for key in ("value", "token", "pat", "personal_access_token", "api_key",
                "apiToken", "password", "secret"):
        v = cfg.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _format_solutions(sols: list[Any]) -> str:
    """Render the solutions list as numbered Jira-readable text.

    Kept as text rather than a structured output because the node's consumer is a Jira
    comment, and the workflow branches on ``solution_count`` (a number a CONDITION can
    compare) rather than on the list itself. Tolerates a plain list of strings, which is
    what a model returns when it ignores the object shape.
    """
    out: list[str] = []
    for i, s in enumerate(sols, 1):
        if not isinstance(s, dict):
            if str(s).strip():
                out.append(f"{i}. {s}")
            continue
        head = str(s.get("summary") or s.get("change") or "").strip()
        line = f"{i}. {head}" if head else f"{i}."
        change = str(s.get("change") or "").strip()
        if change and change != head:
            line += f"\n   change: {change}"
        for key, label in (("blast_radius", "blast radius"), ("risk", "risk")):
            val = str(s.get(key) or "").strip()
            if val:
                line += f"\n   {label}: {val}"
        out.append(line)
    return "\n".join(out)


# Same reason as claude_repro's copy: the platform reports an empty string as null and the
# Jira template has no conditional, so an empty field prints as the literal word "None".
# Measured on a live comment: an "h4. Needed from the reporter" heading followed by a lone
# "None", and a footer ending "status OK None". Only free-text fields belong here — `enough`
# and `solution_count` are a bool and an int the gate reads.
_DISPLAY_FALLBACK = {
    "questions": "nothing — the ticket had what was needed",
    "root_cause": "not established",
    "files": "none identified",
    "evidence": "none quoted",
    "solutions": "none proposed",
    "recommended": "no recommendation",
    "open_questions": "none",
    "search_terms": "none",
    "error": "none",
}


def _usage_of(data: Any) -> dict[str, int | float]:
    """Token counts and cost from one CLI reply, as flat numbers.

    Cache reads are kept separate rather than folded into a total: they are priced at about
    a tenth of an input token but dominate the volume of any agentic run, so a single number
    would be both wrong and unactionable. ``cost_usd`` is the CLI's own figure when it
    reports one - not an estimate of ours.
    """
    d = data if isinstance(data, dict) else {}
    u = d.get("usage") or {}

    def _n(key: str) -> int:
        try:
            return int(u.get(key) or 0)
        except Exception:  # noqa: BLE001
            return 0

    return {
        "input_tokens": _n("input_tokens"),
        "output_tokens": _n("output_tokens"),
        "cache_write_tokens": _n("cache_creation_input_tokens"),
        "cache_read_tokens": _n("cache_read_input_tokens"),
        "cost_usd": round(float(d.get("total_cost_usd") or 0.0), 4),
    }


def _usage_add(into: dict[str, Any], add: dict[str, int | float]) -> None:
    """Accumulate usage across the several CLI calls one node makes (plan + analyse)."""
    for k, v in add.items():
        into[k] = round(float(into.get(k, 0)) + float(v), 4) if k == "cost_usd" \
            else int(into.get(k, 0)) + int(v)


def _blank(err: str = "", **over: Any) -> dict[str, Any]:
    """Schema-complete payload for a failed run, returned as a SUCCESS-shaped dict.

    Same doctrine as the engine's ``_error_output``: the workflow must keep flowing to
    its Jira-comment node so the failure lands on the ticket, rather than the run dying
    here with no trace. The failure is unmistakable in the payload — empty root_cause,
    layer 'unknown', the reason in 'error'.
    """
    out: dict[str, Any] = {
        "status": "FAILED" if err else "OK",
        "layer": "unknown",
        # The ticket-completeness gate. FALSE when the run failed: a run that could not
        # read the code has not established that the ticket is actionable, and defaulting
        # to true would wave a broken analysis straight through the gate.
        "enough": False,
        "questions": "",
        "root_cause": "",
        "files": "",
        "evidence": "",
        "solutions": "",
        "solution_count": 0,
        "recommended": "",
        "open_questions": "",
        "confidence": "low",
        "search_terms": "",
        "files_read": 0,
        "ref": "",
        "token_source": "",
        # Without these, "searched the wrong repo" and "the backend is not involved" look
        # identical in the output - the exact confusion that had an earlier run call this
        # ticket not-a-bug.
        "projects_searched": "",
        "projects_read": "",
        "searches_used": 0,
        "be_notes": "",
        # The CLI reports these and the engine was discarding them. cache_read is its own
        # field because it is ~90% of an agentic run's volume at ~1/10 the price.
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
        "raw": "",
        "error": err,
    }
    out.update(over)
    for key, fallback in _DISPLAY_FALLBACK.items():
        if not str(out.get(key) or "").strip():
            out[key] = fallback
    return out


def _repair_json(s: str) -> str:
    """Fix mismatched closers, the one JSON slip a model actually makes.

    Observed live: a plan reply closed an ARRAY with ``}]`` —
    ``[... "sap-icon://message-information"}]`` — one stray brace, which made the whole
    run fail with "no search terms produced" even though the terms were perfectly good.

    Rather than special-casing that string, track the open containers and, when a closer
    does not match the innermost one, emit the closer that does. Braces inside string
    literals are ignored, escapes included, so a ``}`` in a URL or a sentence is safe.
    """
    out: list[str] = []
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "{[":
            stack.append(ch)
            out.append(ch)
        elif ch in "}]":
            if not stack:
                # Surplus closer. Dropping it matters: emitting it leaves trailing data
                # after the object and json.loads fails on the very reply we are repairing
                # (a stray `}` inside an array shifts every later closer, so the input has
                # one closer more than it has openers).
                continue
            out.append("}" if stack.pop() == "{" else "]")
        else:
            out.append(ch)
    # Anything still open was never closed — close it, innermost first.
    for opener in reversed(stack):
        out.append("}" if opener == "{" else "]")
    return "".join(out)


def _json_object(text: str) -> dict[str, Any]:
    """First JSON object in a model reply. Tolerates fences, prose and a stray closer.

    Four attempts, cheapest first, because a formatting slip must degrade rather than
    kill the run: the whole string, the first balanced ``{...}``, first ``{`` to last
    ``}``, and each of those again after ``_repair_json``.
    """
    if not text:
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    start = t.find("{")
    if start < 0:
        return {}

    candidates: list[str] = [t[start:]]
    depth = 0
    for i, ch in enumerate(t[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidates.append(t[start:i + 1])
                break
    last = t.rfind("}")
    if last > start:
        candidates.append(t[start:last + 1])

    for cand in candidates:
        for attempt in (cand, _repair_json(cand)):
            try:
                obj = json.loads(attempt)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(obj, dict):
                return obj
    return {}


# Identifiers a developer would have typed, harvested from the ticket itself. Used only
# when the PLAN step's reply cannot be parsed at all — a model formatting slip should
# degrade the search, not abort the analysis.
_IDENT_RE = re.compile(r"\b([a-z]+(?:[A-Z][a-z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")


def _fallback_terms(*texts: str) -> list[str]:
    """CamelCase/PascalCase identifiers from the ticket, longest first, deduped."""
    seen: dict[str, None] = {}
    for t in texts:
        for m in _IDENT_RE.finditer(t or ""):
            w = m.group(1)
            if len(w) >= 8:
                seen.setdefault(w, None)
    return sorted(seen, key=len, reverse=True)[:8]


# Generated CAP/CDS output. Every symbol appears in these, so a hit here is noise: on the
# first probe `applyDefaultTemplateConfig` matched srv/external/srv_system_config.csn in four
# repos that merely CONSUME the service, which says nothing about where it is implemented.
_GEN_RE = re.compile(r"/(external|gen|node_modules)/|\.(csn|xml|json|md|pdf|drawio|lock)$",
                     re.IGNORECASE)
_CODE_EXT = (".ts", ".js", ".cds", ".sql", ".py")

# CAP boilerplate and generated entity dumps. `srv/entities.ts` names every entity in the
# model, so a search for an entity name matches in every service that imports it - measured:
# `DefaultSectionEntry` hit srv_user, which has nothing to do with default sections. These
# disqualify a seed AND are never read as neighbours.
_NOISE_RE = re.compile(
    r"(^|/)(entities|constants|server|index|types?|enums?)\.ts$"
    r"|(^|/)[\w.-]*-service\.cds$"
    r"|(^|/)package(-lock)?\.json$",
    re.IGNORECASE)


def _is_test(path: str) -> bool:
    p = "/" + str(path or "")
    return bool(re.search(r"/(test|tests|spec|__tests__)/|\.(test|spec)\.[a-z]+$", p, re.I))


def _useful(path: str) -> bool:
    """A file worth spending budget on: real code, not generated or boilerplate."""
    p = "/" + str(path or "")
    return (p.endswith(_CODE_EXT) and not _GEN_RE.search(p) and not _NOISE_RE.search(p))

# Pre-selection order for the backend sweep. core/* first because that is where the
# cross-cutting logic actually lived; the object's own master-data repos next; `_db_` repos
# last because they carry schema plus generated artifacts, not behaviour.
_BE_PREFER = ("/be-group/core/", "/be-group/consolidate/", "/be-group/master-data/",
              "/be-group/helper/")


# Path fragments worth matching against a term. Kept short and generic on purpose: this is
# a tie-breaker inside a tier, not a routing table to maintain.
_PATH_WORDS = ("config", "process", "consolidate", "integration", "migration", "user",
               "common", "template", "quality", "custom", "background", "portal", "ai")


def _term_words(terms: list[str]) -> set[str]:
    """Path-ish words that appear as WORDS in the terms, not as substrings.

    Substring matching read "process" out of `processData` and reordered the whole sweep
    around it. Terms are split on camelCase and separators first, so only a standalone
    component counts.
    """
    parts: set[str] = set()
    for t in terms:
        for chunk in re.split(r"[^A-Za-z]+", t):
            for piece in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+", chunk):
                parts.add(piece.lower())
    return {w for w in _PATH_WORDS if w in parts}


def _be_rank(path: str, obj_hint: str, words: set[str] = frozenset()) -> int:
    """Lower sorts first.

    Within a tier the deciding signal is vocabulary: a term like
    `applyDefaultTemplateConfig` names the repo family that implements it, and using that
    is what separates a sweep that finds core/config from one that walks past it.
    """
    tier = next((i for i, p in enumerate(_BE_PREFER) if p in "/" + path + "/"), len(_BE_PREFER))
    obj = 0 if (obj_hint and obj_hint.lower() in path.lower()) else 1
    db = 1 if "_db_" in path else 0
    voc = 0 if any(w in path.lower() for w in words) else 1
    return tier * 100 + db * 50 + voc * 10 + obj


class _RateLimited(Exception):
    """GitLab throttled the search endpoint (HTTP 429).

    Distinct from "no hits" on purpose: the two are opposite answers and the old code could
    not tell them apart.
    """


class _GitLab:
    """The three read endpoints this node needs. ``ref`` is never optional."""

    def __init__(self, host: str, token: str, timeout: float = 30.0) -> None:
        import httpx

        self._host = host.rstrip("/")
        self._c = httpx.Client(
            timeout=timeout,
            headers={"PRIVATE-TOKEN": token, "Accept": "application/json"},
        )

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:  # noqa: BLE001
            pass

    def project_id(self, project: str) -> str:
        """Numeric id for a path, or the value unchanged when already numeric."""
        p = project.strip().strip("/")
        if p.isdigit():
            return p
        r = self._c.get(f"{self._host}/api/v4/projects/{quote(p, safe='')}")
        r.raise_for_status()
        return str(r.json().get("id") or "")

    def search(self, pid: str, term: str, ref: str, per_page: int = 20) -> list[dict[str, Any]]:
        r = self._c.get(
            f"{self._host}/api/v4/projects/{pid}/search",
            # ``ref`` is the whole point — without it GitLab searches only the default
            # branch and a term that exists on `staging` reads as absent.
            params={"scope": "blobs", "search": term, "ref": ref, "per_page": per_page},
        )
        if r.status_code == 429:
            raise _RateLimited(f"429 on project {pid} for {term!r}")
        if r.status_code != 200:
            return []
        body = r.json()
        return body if isinstance(body, list) else []

    def group_projects(self, group: str, path_filter: str = "") -> list[dict[str, Any]]:
        """Every project under a group, with the ref each one actually defaults to.

        The ref matters more than it looks: of 66 backend repos, 55 default to `uat` and 11
        to `staging`. One global ref would search the wrong branch in a fifth of them and
        return zero without saying why.
        """
        out: list[dict[str, Any]] = []
        for page in range(1, 6):
            r = self._c.get(
                f"{self._host}/api/v4/groups/{quote(group.strip('/'), safe='')}/projects",
                params={"include_subgroups": "true", "simple": "true",
                        "per_page": 100, "page": page},
            )
            if r.status_code != 200:
                break
            body = r.json()
            if not isinstance(body, list) or not body:
                break
            out += body
            if len(body) < 100:
                break
        picked = []
        for p in out:
            path = str(p.get("path_with_namespace") or "")
            if path_filter and path_filter not in path:
                continue
            picked.append({"id": str(p.get("id") or ""), "path": path,
                           "ref": str(p.get("default_branch") or "") or "uat"})
        return picked

    def tree(self, pid: str, path: str, ref: str) -> list[dict[str, Any]]:
        """Directory listing. Not subject to the search rate limit (measured: 30/30 OK),
        which is why exploration happens here rather than with more searches."""
        r = self._c.get(
            f"{self._host}/api/v4/projects/{pid}/repository/tree",
            params={"ref": ref, "path": path, "per_page": 100},
        )
        if r.status_code != 200:
            return []
        body = r.json()
        return body if isinstance(body, list) else []

    def read(self, pid: str, path: str, ref: str, max_bytes: int) -> str:
        r = self._c.get(
            f"{self._host}/api/v4/projects/{pid}/repository/files/{quote(path, safe='')}/raw",
            params={"ref": ref},
        )
        if r.status_code != 200:
            return ""
        return r.text[:max_bytes]


# How this frontend calls the backend. Deliberately narrow: these two shapes produced every
# real seam name on the measured ticket, and a looser pattern would flood the probe list with
# local helper calls that no backend repo contains.
_SEAM_RES = (
    re.compile(r"await\s+[A-Z]\w*Service\.(\w{5,})\s*\("),   # await UploadMassService.applyX(
    re.compile(r"""path:\s*["'`]/(\w{5,})["'`]"""),            # path: "/applyDefaultTemplateConfig"
)
# Names that are calls but say nothing about a specific backend service.
_SEAM_STOP = frozenset({"getData", "getItems", "getList", "getValue", "getConfig", "getState",
                        "setData", "toString", "getInstance", "getModel", "getView"})


def _seam_terms(blobs: list[str], limit: int = 4) -> list[str]:
    """Backend call names the FRONTEND source actually uses, most frequent first.

    This is the half of the sweep that does not depend on the planner. On the measured
    ticket it returns `applyDefaultTemplateConfig` first (8 occurrences), which is exactly
    the term that made the backend sweep land on the implementing repo.
    """
    fe = "\n".join(blobs)
    counts: dict[str, int] = {}
    for rx in _SEAM_RES:
        for m in rx.finditer(fe):
            name = m.group(1)
            if len(name) < 5 or name in _SEAM_STOP or name.isupper():
                continue
            counts[name] = counts.get(name, 0) + 1
    # Frequency first, then length: a name used eight times across the upload pipeline is a
    # better bet than one used once, and a longer identifier is more distinctive.
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))][:limit]


def _be_sweep(gl: "_GitLab", group: str, terms: list[str], obj_hint: str,
              max_files: int, max_bytes: int, max_searches: int,
              note: Any, seam: list[str] | None = None) -> tuple[list[str], list[str], list[str], int]:
    """``(blobs, searched, read, searches_used)`` for the backend side.

    Two stages, because the search endpoint is the scarce resource and the tree endpoint is
    not. Stage one spends at most ``max_searches`` searches to find WHICH repos speak the
    ticket's vocabulary; stage two never searches again - it lists the directory around each
    hit and reads the code there. A 429 stops stage one immediately rather than hammering:
    being throttled mid-sweep would make the remaining repos look empty.
    """
    projects = gl.group_projects(group, "/be-group/")
    if not projects:
        note(f"no backend repos under '{group}' (token may not see that group)")
        return [], [], [], 0
    # Seam names name real backend endpoints, so their vocabulary is meaningful. Planner
    # terms are frontend words and their substrings mislead: `MassStrategy.processData`
    # contributed "process", which promoted five core/process/* repos over core/config and
    # cost the sweep the file that implements the call (rank 2 -> 7, 8 hits unread).
    words = _term_words(list(seam) if seam else terms)
    projects.sort(key=lambda p: _be_rank(p["path"], obj_hint, words))

    # The most distinctive terms first: a long CamelCase identifier is far likelier to be a
    # unique symbol than a short common word, and each search costs budget.
    # A flag name lives in one layer; a call crosses layers. Measured: ranking by "most
    # uppercase" chose isApplyDefaultSectionEntryCondition (frontend-only) over
    # applyDefaultTemplateConfig (8 hits in the backend repo that implements it).
    def _probe_key(t: str) -> tuple[int, int, int, int, str]:
        flagish = 1 if re.match(r"^(is|has|should|allow|can|enable|use)[A-Z_]", t) else 0
        dotted = 1 if ("." in t or "-" in t) else 0   # MassStrategy.processData, flat-to-deep
        # Length is the strongest distinctiveness signal and dropping it cost a whole sweep:
        # `addedInUI` and `applyDefaultTemplateConfig` both carry three capitals, so ranking
        # by capitals alone tied them - and `addedInUI` appears in NO backend repo, so the
        # sweep read nothing. The trailing `t` makes the order deterministic; without it the
        # tie was broken by set iteration, which varies per process, and the node picked a
        # different probe on identical input from one run to the next.
        return (flagish, dotted, -len(t), -sum(c.isupper() for c in t), t)

    ranked_terms = sorted({t for t in terms if len(t) >= 8}, key=_probe_key)
    # Seam names lead. They come from the frontend source rather than a word list, so they
    # name things the backend can actually contain; FS vocabulary usually cannot.
    probes = list(dict.fromkeys([*(seam or []), *ranked_terms]))[:3] or terms[:1]
    if seam:
        note(f"seam terms from the frontend source: {seam}")

    # One probe per repo: two probes bought depth on repos that mostly did not matter while
    # leaving a third of the group unsearched, and the repo that mattered sat at the edge.
    seeds: dict[str, tuple[dict[str, Any], str, int, int, int]] = {}
    searched: list[str] = []
    used = 0
    throttled = False
    # Hold a slice of the budget back. The fallback below exists precisely for the case where
    # probe #1 is the wrong vocabulary for the backend, and with the whole budget spent on
    # pass one it could never run - measured: 25 repos searched with a term no backend repo
    # contains, 0 files read, and a second probe sitting unused.
    first_pass = max(1, (max_searches * 2) // 3) if len(probes) > 1 else max_searches
    for p in projects:
        if throttled:
            break
        if used >= first_pass:
            note(f"probe #1 {probes[0]!r} covered {len(searched)} repo(s); "
                 f"{len(projects)-len(searched)} not searched")
            break
        searched.append(p["path"])
        # A small gap between searches. 528 calls in 11 seconds got every one of them 429'd;
        # a sweep that finishes slightly slower and completely beats one that finishes fast
        # and wrong.
        if len(searched) > 1:
            time.sleep(0.35)
        for ti, term in enumerate(probes[:1] if len(projects) > max_searches else probes):
            if used >= max_searches:
                break
            used += 1
            try:
                hits = gl.search(p["id"], term, p["ref"], per_page=10)
            except _RateLimited:
                # One pause, one retry. If it is still throttled the sweep stops: carrying
                # on would silently label every remaining repo as having no match.
                time.sleep(3.0)
                try:
                    hits = gl.search(p["id"], term, p["ref"], per_page=10)
                except _RateLimited:
                    throttled = True
                    note(f"rate limited at repo {len(searched)} of {len(projects)} "
                         f"({p['path'].split('/be-group/')[-1]}) - sweep stopped early, "
                         f"the repos after it were NOT searched and are not 'empty'")
                    break
            except Exception as exc:  # noqa: BLE001
                note(f"search failed on {p['path']}: {str(exc)[:80]}")
                hits = []
            code = [h for h in hits if _useful(h.get("path"))]
            if code:
                # Prefer a non-test hit as the seed: a test file proves the vocabulary is
                # known, not that the behaviour lives here.
                code.sort(key=lambda h: (1 if _is_test(h.get("path")) else 0,
                                         len(str(h.get("path") or ""))))
                h = code[0]
                seeds[p["path"]] = (p, str(h["path"]), int(h.get("startline") or 0),
                                    ti, len(code))
                break            # this repo is in; stop spending searches on it

    # A single probe can be the wrong vocabulary for the whole backend. If it found almost
    # nothing and budget survives, try the second probe on the repos that came up empty
    # rather than reporting "the backend is not involved" on one word's evidence.
    if len(seeds) < 3 and len(probes) > 1 and used < max_searches:
        for p in projects:
            if used >= max_searches or p["path"] not in searched or p["path"] in seeds:
                continue
            used += 1
            try:
                hits = gl.search(p["id"], probes[1], p["ref"], per_page=10)
            except Exception:  # noqa: BLE001
                hits = []
            code = [h for h in hits if _useful(h.get("path"))]
            if code:
                code.sort(key=lambda h: (1 if _is_test(h.get("path")) else 0,
                                         len(str(h.get("path") or ""))))
                h = code[0]
                seeds[p["path"]] = (p, str(h["path"]), int(h.get("startline") or 0),
                                    1, len(code))
        note(f"second probe {probes[1]!r} used; seeds now {len(seeds)}")

    if not seeds:
        note(f"searched {len(searched)} backend repo(s) with {probes} - no code matched")
        return [], searched, [], used

    # Neighbours are listed once per repo; the search budget is already spent so tree calls
    # are free, and they are what turn one hit into readable context.
    # Rank what was found. term_rank first (probe 0 is the most distinctive identifier the
    # planner produced), then non-test over test, then how many code hits the repo had.
    ranked = sorted(
        seeds.items(),
        key=lambda kv: (kv[1][3], 1 if _is_test(kv[1][1]) else 0, -kv[1][4], kv[0]))
    note("seed ranking: " + "; ".join(
        f"{k.split('/be-group/')[-1]}({'test' if _is_test(v[1]) else 'code'},"
        f"t{v[3]},{v[4]}h)" for k, v in ranked[:6]))

    # Depth over breadth: the answer needed the handler AND its helpers, which one file per
    # repo cannot give. Top repos get read properly; the rest only if budget remains.
    plan: list[tuple[str, dict[str, Any], str, int, bool]] = []
    per_repo = max(2, max_files // 3)
    for path, (p, seed, line, _ti, _n) in ranked:
        plan.append((path, p, seed, line, True))
        d = seed.rsplit("/", 1)[0] if "/" in seed else ""
        extra = [str(e.get("path") or "") for e in gl.tree(p["id"], d, p["ref"])
                 if e.get("type") == "blob" and str(e.get("path")) != seed
                 and _useful(e.get("path"))]
        for fp in extra[:per_repo - 1]:
            plan.append((path, p, fp, 0, False))
        if len(plan) >= max_files + 2:
            break

    blobs: list[str] = []
    read_paths: list[str] = []
    for path, p, fp, line, is_seed in plan:
        if len(blobs) >= max_files:
            break
        body = gl.read(p["id"], fp, p["ref"], max_bytes)
        if not body:
            continue
        read_paths.append(f"{path}@{p['ref']}:{fp}")
        tag = (" (search hit near line %d)" % line) if is_seed else " (same directory)"
        blobs.append(f"=== FILE [{path}@{p['ref']}] {fp}{tag} ===\n{body}")
    return blobs, searched, read_paths, used


def make_gitlab_analyze_handler(engine: Any) -> Any:
    """Build the ``(inputs, parameters) -> dict`` handler, bound to the shared engine so
    the Claude calls reuse its token-rotating ``_call_claude_cli`` and secrets reader."""

    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        text = engine._as_text

        # Flatten BEFORE stringifying: the reference hands over the whole issue object,
        # and _as_text would turn it into JSON that no line-based logic can read.
        ticket_text = _redact(_flatten_issue(
            merged.get("ticket_text") or merged.get("ticket_description")).strip())
        if not ticket_text:
            return _blank("'ticket_text' is empty (bind it to the Jira issue).")

        project = text(merged.get("project")).strip()
        if not project:
            return _blank("'project' is empty (e.g. simplemdg-alcon/ui-group/simplemdg_ui_typescript).")

        host = text(merged.get("gitlab_host")).strip() or "https://gitlab.simplemdg.com"
        # The integration branch, NOT the repo default. These repos default to `uat`
        # while customer work lives on `staging`; defaulting to the wrong one is the
        # 0-hit trap this module exists to avoid.
        ref = text(merged.get("ref")).strip() or "staging"
        fs_rule = text(merged.get("fs_rule")).strip()
        model = text(merged.get("model")).strip()
        effort = text(merged.get("effort")).strip()
        fallback_model = text(merged.get("fallback_model")).strip()
        claude_bin = text(merged.get("claude_bin")).strip() or "claude"

        def _int(key: str, default: int) -> int:
            try:
                return int(float(merged.get(key) or default))
            except Exception:  # noqa: BLE001
                return default

        max_files = max(1, min(_int("max_files", 8), 30))
        max_bytes = max(2000, min(_int("max_bytes_per_file", 40000), 200000))
        timeout_secs = _int("timeout_secs", 600)
        be_group = text(merged.get("be_group")).strip()
        obj_hint = text(merged.get("object_hint")).strip()
        # Separate budgets on purpose: the backend read must not be able to crowd out the
        # frontend files, which are the ones the ticket's symptom actually points at.
        be_max_files = max(0, min(_int("be_max_files", 8), 30))
        # 25 searches is the working figure: the endpoint 429'd at 528 in 11 seconds, and a
        # sweep that gets throttled halfway makes the untouched repos look empty.
        be_max_searches = max(0, min(_int("be_max_searches", 25), 60))

        secrets = engine._read_secrets_file(
            text(merged.get("secrets_file")).strip() or _DEFAULT_SECRETS_FILE, None)

        # The GitLab token comes from the VAULT, via the node's credential_ref — the run
        # lends it as inputs['__credentials'][ref] = {type, config} (T5 removed inline
        # secrets, so credential_ref is the supported path). Choosing it per node matters
        # because the customer groups are DISJOINT: a token that reaches
        # simplemdg-dashboard returns 404 on every simplemdg-alcon path and vice versa,
        # so one shared token cannot serve both.
        #
        # The local secrets file stays as a FALLBACK only, for running this worker
        # detached from a run that carries credentials (the pull-mode dev loop). It is not
        # the intended production path.
        pat = _token_from_credential(borrow_credential(merged))
        source = "vault credential_ref"
        if not pat:
            pat_key = text(merged.get("pat_secret_key")).strip() or "gitlab_pat"
            pat = (secrets.get(pat_key) or "").strip() or (secrets.get("gitlab_pat") or "").strip()
            source = f"secrets file key '{pat_key}'"
        if not pat:
            return _blank(
                "no GitLab token: set credential_ref on this node to a vault credential "
                "of type gitlab-pat (Credentials page -> the token must have Automation "
                "enabled, or an event/schedule run cannot resolve it — SA-2279). Local "
                "fallback: a 'gitlab_pat' key in the worker secrets file. The token needs "
                "read_api on THIS customer's group — a token for another customer's group "
                "answers 404, not 403.",
                ref=ref)
        tokens = [t.strip() for t in (secrets.get("claude_cli_token") or "").split(",") if t.strip()]

        usage_total: dict[str, Any] = {}

        def _claude(prompt: str, budget: int) -> str:
            data = engine._call_claude_cli(
                prompt=prompt, model=model, max_turns=0, working_dir=None, add_dir=None,
                agentic=False, timeout_secs=budget, oauth_tokens=tokens,
                claude_bin=claude_bin, trace_file=None,
                effort=effort, fallback_model=fallback_model,
            )
            _usage_add(usage_total, _usage_of(data))
            return str((data or {}).get("result") or "")

        # ---- 1. PLAN -----------------------------------------------------------
        manual = [t.strip() for t in text(merged.get("search_terms")).split(",") if t.strip()]
        plan_raw = ""
        if manual:
            terms = manual
        else:
            try:
                plan_raw = _claude(
                    f"{_PLAN_PROMPT}\n\n--- TICKET ---\n{ticket_text}\n--- END ---"
                    + (f"\n\n--- FS RULE ---\n{fs_rule}\n--- END ---" if fs_rule else ""),
                    min(timeout_secs, 180),
                )
            except Exception as exc:  # noqa: BLE001
                return _blank(f"search-plan step failed: {exc}", ref=ref)
            plan = _json_object(plan_raw)
            terms = [str(t).strip() for t in (plan.get("terms") or []) if str(t).strip()]
            if not terms:
                # The plan reply was unusable. Harvest identifiers from the ticket instead
                # of aborting: a wrong-shaped reply is a formatting slip, and the ticket
                # text already names most of what a developer would grep for.
                terms = _fallback_terms(ticket_text, fs_rule)
        if not terms:
            return _blank("no search terms produced, and no identifier could be harvested "
                          "from the ticket — cannot locate code.",
                          ref=ref, raw=plan_raw[:2000])

        # ---- 2. FETCH ----------------------------------------------------------
        gl = _GitLab(host, pat)
        try:
            try:
                pid = gl.project_id(project)
            except Exception as exc:  # noqa: BLE001
                return _blank(
                    f"cannot resolve project '{project}': {exc}. A 404 here usually means "
                    "the token has no access to that group, not that the path is wrong.",
                    ref=ref, search_terms=", ".join(terms))

            hits: dict[str, int] = {}
            for term in terms[:8]:
                for h in gl.search(pid, term, ref):
                    path = str(h.get("path") or "")
                    if not path or _SKIP_RE.search(path):
                        continue
                    hits.setdefault(path, int(h.get("startline") or 0))
                if len(hits) >= max_files * 2:
                    break
            if not hits:
                return _blank(
                    f"no source matched {terms} on ref '{ref}'. If the code should exist, "
                    "check the ref (these repos default to 'uat' but customer work is on "
                    "'staging') before concluding it is absent.",
                    ref=ref, search_terms=", ".join(terms))

            picked = list(hits.items())[:max_files]
            blobs: list[str] = []
            for path, line in picked:
                body = gl.read(pid, path, ref, max_bytes)
                if body:
                    blobs.append(f"=== FILE {path} (first search hit near line {line}) ===\n{body}")
        finally:
            gl.close()

        if not blobs:
            return _blank("search matched paths but no file could be read (check read_api scope).",
                          ref=ref, search_terms=", ".join(terms))

        # ---- 2b. BACKEND ------------------------------------------------------
        be_blobs: list[str] = []
        be_searched: list[str] = []
        be_read: list[str] = []
        be_used = 0
        be_notes: list[str] = []
        if be_group and be_max_files:
            gl2 = _GitLab(host, pat)
            try:
                be_blobs, be_searched, be_read, be_used = _be_sweep(
                    gl2, be_group, terms, obj_hint, be_max_files, max_bytes,
                    be_max_searches, be_notes.append, seam=_seam_terms(blobs))
            except Exception as exc:  # noqa: BLE001 - the FE answer must still land
                be_notes.append(f"backend sweep failed: {str(exc)[:140]}")
            finally:
                gl2.close()

        # ---- 3. ANALYSE --------------------------------------------------------
        parts = [_ANALYSE_PROMPT, f"\n--- TICKET ---\n{ticket_text}\n--- END ---"]
        if fs_rule:
            parts.append(f"\n--- FS RULE (source of truth) ---\n{fs_rule}\n--- END ---")
        parts.append(f"\n--- FRONTEND REPOSITORY {project} @ {ref} ---\n"
                     + "\n\n".join(blobs) + "\n--- END ---")
        if be_blobs:
            parts.append("\n--- BACKEND REPOSITORIES (each file is labelled repo@ref) ---\n"
                         + "\n\n".join(be_blobs) + "\n--- END ---")
        if be_group:
            # State the sweep's reach inside the prompt: the scope paragraph tells the model
            # never to describe code it was not shown, so it has to know what WAS shown.
            parts.append("\n--- BACKEND SWEEP ---\n"
                         + f"searched {len(be_searched)} backend repo(s) with "
                         + f"{be_used} search call(s); read {len(be_read)} file(s).\n"
                         + ("repos read: " + ", ".join(sorted({p.split('@')[0] for p in be_read}))
                            + "\n" if be_read else "no backend file matched.\n")
                         + ("notes: " + " | ".join(be_notes) + "\n" if be_notes else "")
                         + "--- END ---")
        try:
            raw = _claude("\n".join(parts), timeout_secs)
        except Exception as exc:  # noqa: BLE001
            return _blank(f"analyse step failed: {exc}", ref=ref,
                          search_terms=", ".join(terms),
                          files_read=len(blobs) + len(be_blobs),
                          projects_searched=", ".join(be_searched),
                          projects_read="\n".join(be_read),
                          searches_used=be_used,
                          be_notes=" | ".join(be_notes),
                          **usage_total)

        v = _json_object(raw)
        if not v:
            return _blank("Claude did not return a JSON verdict.", ref=ref,
                          search_terms=", ".join(terms), files_read=len(blobs), raw=raw[:4000])

        def _lines(val: Any) -> str:
            if isinstance(val, list):
                return "\n".join(str(x) for x in val if str(x).strip())
            return str(val or "")

        sols = v.get("solutions") or []
        sols = sols if isinstance(sols, list) else []
        return _blank(
            "",
            layer=str(v.get("layer") or "unknown"),
            # A JSON `false` must stay false — `bool(v.get(...) or default)` would flip it.
            enough=bool(v.get("enough")) if isinstance(v.get("enough"), bool) else False,
            questions=_lines(v.get("questions")),
            root_cause=str(v.get("root_cause") or ""),
            files=_lines(v.get("files")),
            evidence=str(v.get("evidence") or ""),
            solutions=_format_solutions(sols),
            solution_count=len(sols),
            recommended=str(v.get("recommended") or ""),
            open_questions=_lines(v.get("open_questions")),
            confidence=str(v.get("confidence") or "low"),
            search_terms=", ".join(terms),
            files_read=len(blobs) + len(be_blobs),
            ref=ref,
            token_source=source,
            projects_searched=", ".join(be_searched),
            projects_read="\n".join(be_read),
            searches_used=be_used,
            be_notes=" | ".join(be_notes),
            **usage_total,
            raw=raw[:4000],
        )

    return _handler


INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    textarea_field("ticket_text", "Ticket text (title + description)",
                   placeholder="bind to the Jira issue, e.g. {{$get_issue.metadata.issue}}"),
    textarea_field("fs_rule", "FS rule (source of truth, optional)",
                   placeholder="e.g. BR-04: a line added by Default Section Entries carries a blue stripe…"),
    text_field("project", "GitLab project (path or numeric id)",
               placeholder="simplemdg-alcon/ui-group/simplemdg_ui_typescript"),
    text_field("ref", "Branch / ref to read",
               placeholder="staging", default="staging"),
    text_field("gitlab_host", "GitLab host",
               placeholder="https://gitlab.simplemdg.com", default="https://gitlab.simplemdg.com"),
    text_field("pat_secret_key", "Local-fallback secrets key (prefer credential_ref)",
               placeholder="gitlab_pat_alcon — only used when no credential_ref is lent",
               default="gitlab_pat"),
    text_field("search_terms", "Search terms (comma-separated; blank = let Claude plan them)",
               placeholder="DefaultSectionWithCondition, highlight, MessageType.Information"),
    number_field("max_files", "Max files to read", placeholder="8", default=8),
    number_field("max_bytes_per_file", "Max bytes per file", placeholder="40000", default=40000),
    # Raised for the backend fan-out: one pass now reads FE and BE source, and the old
    # 900s ceiling was already missed by 46 seconds on an FE-only run.
    number_field("timeout_secs", "Timeout (seconds)", placeholder="1800", default=1800),
    text_field("be_group", "Backend group to sweep (blank = frontend only)",
               placeholder="simplemdg-alcon"),
    text_field("object_hint", "Master-data object, to rank its repos first",
               placeholder="product"),
    number_field("be_max_files", "Max backend files to read", placeholder="8", default=8),
    number_field("be_max_searches", "Max backend search calls (endpoint is rate limited)",
                 placeholder="25", default=25),
    text_field("effort", "Reasoning effort (low|medium|high|xhigh|max; blank = CLI default)",
               placeholder="medium"),
    text_field("fallback_model", "Fallback model(s) if the primary is overloaded",
               placeholder="sonnet"),
    text_field("model", "Claude model (blank = default)", placeholder="e.g. claude-sonnet-4-6"),
    text_field("claude_bin", "Claude binary", placeholder="claude", default="claude"),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    text_field("layer", "Layer (frontend|backend|data|config|not-a-bug)", readonly=True),
    switch_field("enough", "Ticket complete enough to act on", readonly=True),
    textarea_field("questions", "What the reporter must add", readonly=True),
    textarea_field("root_cause", "Root cause", readonly=True),
    textarea_field("files", "Implicated files", readonly=True),
    textarea_field("evidence", "Evidence from the source", readonly=True),
    textarea_field("solutions", "Proposed solutions (most recommended first)", readonly=True),
    number_field("solution_count", "Number of distinct solutions", readonly=True),
    textarea_field("recommended", "Why that solution", readonly=True),
    textarea_field("open_questions", "What the analysis still needs from the code", readonly=True),
    text_field("confidence", "Confidence", readonly=True),
    text_field("search_terms", "Search terms used", readonly=True),
    number_field("files_read", "Files read", readonly=True),
    text_field("ref", "Ref read", readonly=True),
    text_field("token_source", "Where the GitLab token came from", readonly=True),
    textarea_field("projects_searched", "Backend repos searched", readonly=True),
    textarea_field("projects_read", "Backend files read (repo@ref:path)", readonly=True),
    number_field("searches_used", "Search calls spent on the backend", readonly=True),
    textarea_field("be_notes", "Backend sweep notes", readonly=True),
    number_field("input_tokens", "Input tokens", readonly=True),
    number_field("output_tokens", "Output tokens", readonly=True),
    number_field("cache_write_tokens", "Cache write tokens", readonly=True),
    number_field("cache_read_tokens", "Cache read tokens (~90% of volume, ~1/10 price)",
                 readonly=True),
    number_field("cost_usd", "Cost USD (reported by the CLI)", readonly=True),
    textarea_field("raw", "Claude raw reply", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
