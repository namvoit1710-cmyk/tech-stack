"""Intent classifier node — routes a chat message to one of the workflow's branches.

Lives in the claude-worker so it reuses the engine's ``_call_claude_cli`` (which
ROTATES across the comma-separated ``claude_cli_token`` accounts on rate-limit /
auth failure). NON-agentic, no repo clone: one ``claude -p`` call on the message.

Why a node and not a SWITCH on the raw text: a SWITCH can only compare strings, so
"hi", "Hi there" and "chào bạn" are three different worlds to it. This node returns a
stable ``route`` key that the SWITCH then matches exactly — the fuzzy part stays in
the model, the routing stays inspectable in the graph.

The route list is CONFIGURED ON THE FORM, one per line::

    greeting: user says hello / hi / chào
    how_are_you: user asks how the bot is doing

Anything the model can't place returns ``unknown`` — the workflow should keep a
default branch for it (a SWITCH with no matching branch and no default edge stalls
the run).
"""

from __future__ import annotations

import json
import re
from typing import Any

from worker_sdk.layer1_domain.auto_form_mapper import text_field, textarea_field

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    _DEFAULT_SECRETS_FILE,
)

_ROUTE_KEY_RE = re.compile(r"^[a-z0-9_]+$")

_DEFAULT_ROUTES = (
    "greeting: the user greets — hi, hello, chào, good morning\n"
    "how_are_you: the user asks how you are / how it's going"
)

_DEFAULT_SYSTEM_PROMPT = (
    "You route a chat message. Reply with ONLY a JSON object, nothing else:\n"
    '{"route": "<one route key from the list, lowercase>", '
    # ALWAYS, khong phai "or empty": nhanh default gio ganh toan bo hoi thoai
    # thong thuong — reply rong o day nghia la bot im lang voi nguoi dung.
    '"reply": "<ALWAYS write a short, friendly, direct answer to the user here — '
    'even when the route is unknown; if you cannot help, say what you CAN do>", '
    '"ticket_key": "<Jira issue id mentioned, e.g. MCKES-360 or SA-1234, or empty>", '
    '"cr_number": "<MDG change request mentioned, e.g. CR12345 or CR0000018045, or empty>"}\n'
    "A Jira issue always has a dash; a change request always starts with CR followed by "
    "digits — never put one in the other's field.\n"
    # Truoc day ep ENGLISH vi kenh Dept-AI thong nhat tieng Anh. Bo tu 2026-08-14:
    # bot gio chay ca trong chat rieng va group chat, noi nguoi dung nhan tieng
    # Viet la chinh — tra loi tieng Anh cho mot cau hoi tieng Viet lam nguoi doc
    # phai dich nguoc, va no lam bot co ve khong hieu minh.
    #
    # Chi bao "cung ngon ngu voi nguoi dung". Khong dua vao locale cua Teams: rat
    # nhieu nguoi dung giao dien tieng Anh nhung nhan tin tieng Viet.
    "Write `reply` in the SAME language the user wrote in — Vietnamese if they "
    "wrote Vietnamese, English if they wrote English, and so on. Keep technical "
    "identifiers (ticket ids, CR numbers, environment names, log field names) "
    "exactly as they are; do not translate them.\n"
    # Bot khong chi TAI log ve — no doc log, tim buoc that bai, giai thich nguyen
    # nhan va de xuat viec can lam. Neu khong noi ro, model tu mo ta minh la
    # "fetch logs" (dung theo nghia den cua ten route) va ban than cau gioi
    # thieu lam nguoi dung danh gia thap thu ho nhan duoc.
    "When you describe what you do, say you ANALYSE logs — you read them, find "
    "the failing step, explain the root cause and suggest the next action. "
    "Do not describe yourself as merely fetching or pulling logs.\n"
    'If none of the routes fit, use "unknown" as the route.'
)

UNKNOWN = "unknown"

# `reply` KHÔNG BAO GIỜ được rỗng. Quan sát thật 2026-08-12: model bỏ trống
# `reply` cho route unknown, node SET_VARIABLE gán `{{$classify.reply}}` = None,
# và node ms_teams đăng nguyên chữ "🤖 None" vào kênh Dept-AI. Một trường rỗng ở
# đây đi thẳng ra mặt người dùng, nên nó phải có đáy.
# Giu TIENG ANH ngay ca khi `reply` da theo ngon ngu nguoi dung. Day la duong
# CHOT — no chi chay khi model khong tra ra `reply`, tuc la luc ta khong con biet
# nguoi dung viet tieng gi. Mot cau song ngu o day lam roi mat va bao hieu "bot
# hong" ro hon la mot cau doan sai tieng. Quyet dinh cua operator 2026-08-14.
_DEFAULT_FALLBACK_REPLY = (
    "Sorry, I didn't quite catch that. Could you rephrase it?"
)

# TWO different things, deliberately matched apart:
#   ticket_key — a Jira issue: SA-2229, MDO-1787, MCKES-360 (letters DASH digits)
#   cr_number  — an MDG change request: CR12345, CR0000018045 (CR then digits)
# They used to share one regex, which made "CR12345" land in ticket_key and get
# fed to a workflow that then looked for a Jira issue by that name.
_TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")
_CR_RE = re.compile(r"\b(CR\d{3,})\b", re.IGNORECASE)


def parse_routes(raw: str) -> list[tuple[str, str]]:
    """``"key: description"`` lines -> ``[(key, description)]``.

    Tolerates ``-`` bullets, blank lines and a missing description. Keys are
    normalised to lowercase; a key with characters a SWITCH branch can't hold
    (spaces, punctuation) is dropped rather than silently mismatching later.
    """
    routes: list[tuple[str, str]] = []
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        key, _, desc = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        if not key or not _ROUTE_KEY_RE.match(key):
            continue
        routes.append((key, desc.strip()))
    return routes


def pick_route(reply: str, valid_keys: list[str]) -> str:
    """Map the model's reply onto one of ``valid_keys``; ``unknown`` when it can't.

    Defensive on purpose — the CLI sometimes wraps the answer in JSON or a
    sentence. Exact match first, then whole-word search, so ``how_are_you`` is not
    matched by a stray ``how``.
    """
    text = (reply or "").strip().strip('"\'` ').lower()
    if not text:
        return UNKNOWN
    if text in valid_keys:
        return text
    try:  # some CLI versions answer {"result": "greeting"}
        as_json = json.loads(text)
        if isinstance(as_json, str) and as_json.lower() in valid_keys:
            return as_json.lower()
        if isinstance(as_json, dict):
            for field in ("route", "result", "answer"):
                val = str(as_json.get(field) or "").lower()
                if val in valid_keys:
                    return val
    except (ValueError, TypeError):
        pass
    for key in valid_keys:
        if re.search(rf"\b{re.escape(key)}\b", text):
            return key
    return UNKNOWN


def parse_payload(raw: str, valid_keys: list[str],
                  message: str) -> tuple[str, str, str, str]:
    """Model reply -> ``(route, reply, ticket_key, cr_number)``.

    Accepts the JSON object the prompt asks for, but never DEPENDS on it: a model
    that answers with a bare route key (the older contract, and what happens when
    it ignores the format) still routes correctly — only ``reply`` comes back
    empty. ``ticket_key`` falls back to a regex over the model reply then the
    original message, so a ticket id is never lost to a formatting slip.
    """
    text = (raw or "").strip()
    route, reply, ticket, cr = "", "", "", ""

    body = text
    if "{" in body and "}" in body:  # tolerate prose wrapped around the JSON
        body = body[body.index("{"): body.rindex("}") + 1]
    try:
        obj = json.loads(body)
        if isinstance(obj, dict):
            route = str(obj.get("route") or "").strip().lower()
            reply = str(obj.get("reply") or "").strip()
            ticket = str(obj.get("ticket_key") or "").strip().upper()
            cr = str(obj.get("cr_number") or "").strip().upper()
    except (ValueError, TypeError):
        pass

    if route not in valid_keys:
        route = pick_route(text, valid_keys)
    if not ticket:
        # CHỈ quét câu của người dùng, KHÔNG quét lời model. Quan sát thật
        # 2026-08-12: với "bot làm được những gì?" (không có ticket nào) model
        # trả lời "...cho mình biết mã ticket (ví dụ SA-1234)..." và regex nhặt
        # luôn cái VÍ DỤ đó. Một ticket bịa ở đây sẽ được truyền thẳng vào
        # sub-workflow get-log — nó sẽ đi tìm log của một ticket không liên quan.
        m = _TICKET_RE.search(message or "")
        ticket = m.group(1) if m else ""
    if not cr:
        m = _CR_RE.search(message or "")
        cr = m.group(1).upper() if m else ""
    return route, reply, ticket, cr


def make_classify_handler(engine: Any) -> Any:
    """Build the ``(inputs, parameters) -> dict`` handler bound to the shared engine."""

    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        message = engine._as_text(merged.get("message")).strip()
        if not message:
            return {"status": "FAILED", "route": UNKNOWN, "reply": "",
                    "ticket_key": "", "cr_number": "",
                    "message": "", "raw": "",
                    "error": "'message' is empty (bind it to the trigger's text field)."}

        routes = parse_routes(engine._as_text(merged.get("routes")) or _DEFAULT_ROUTES)
        if not routes:
            return {"status": "FAILED", "route": UNKNOWN, "reply": "",
                    "ticket_key": "", "cr_number": "",
                    "message": message, "raw": "",
                    "error": "no valid routes configured (use 'key: description' per line)."}
        valid_keys = [k for k, _ in routes]

        system_prompt = engine._as_text(merged.get("system_prompt")).strip() or _DEFAULT_SYSTEM_PROMPT
        model = engine._as_text(merged.get("model")).strip()
        claude_bin = engine._as_text(merged.get("claude_bin")).strip() or "claude"
        try:
            timeout_secs = int(float(merged.get("timeout_secs") or 60))
        except (TypeError, ValueError):
            timeout_secs = 60

        secrets = engine._read_secrets_file(
            engine._as_text(merged.get("secrets_file")).strip() or _DEFAULT_SECRETS_FILE, None)
        tokens = [t.strip() for t in (secrets.get("claude_cli_token") or "").split(",") if t.strip()]

        route_lines = "\n".join(f"- {k}: {d}" if d else f"- {k}" for k, d in routes)
        prompt = (
            f"{system_prompt}\n\n--- ROUTES ---\n{route_lines}\n- {UNKNOWN}: none of the above\n"
            f"--- MESSAGE ---\n{message}\n--- END ---"
        )

        raw = ""
        err = ""
        try:
            data = engine._call_claude_cli(
                prompt=prompt, model=model, max_turns=0, working_dir=None, add_dir=None,
                agentic=False, timeout_secs=timeout_secs, oauth_tokens=tokens,
                claude_bin=claude_bin, trace_file=None,
            )
            raw = str((data or {}).get("result") or "")
        except Exception as exc:  # noqa: BLE001 — never crash the node; fall through to unknown
            err = f"claude call failed: {exc}"

        route, reply, ticket_key, cr_number = parse_payload(raw, valid_keys, message)
        if not reply.strip():
            reply = engine._as_text(merged.get("fallback_reply")).strip() or _DEFAULT_FALLBACK_REPLY
        if route == UNKNOWN and not err:
            err = f"model reply {raw[:120]!r} matched no configured route."
        # status stays OK for an unknown route: 'I could not classify this' is a
        # legitimate answer the workflow routes on, not a node failure.
        return {"status": "FAILED" if err and not raw else "OK",
                "route": route, "reply": reply,
                # Cùng nội dung, đã escape sẵn để nhét AN TOÀN vào giữa một chuỗi
                # JSON. Node http_request thay {{...}} vào `body` NGUYÊN VĂN, nên
                # một dấu " trong câu trả lời là body hỏng và request 400. Câu
                # trả lời của model có dấu ngoặc kép thật (vd: cho SA-1234 để
                # "lấy log DEV"), nên đây không phải phòng xa.
                # json.dumps rồi bỏ hai dấu nháy bao ngoài -> phần ruột đã escape.
                "reply_escaped": json.dumps(reply, ensure_ascii=False)[1:-1],
                "ticket_key": ticket_key, "cr_number": cr_number,
                "message": message, "raw": raw[:2000], "error": err}

    return _handler


def _prefill(field: Any, value: str) -> dict[str, Any]:
    """Pre-fill a textarea. ``textarea_field`` has no ``default`` parameter — the
    engine's own ``_schema_for`` pre-fills the same way, by writing the value into
    ``fieldConfig.controlProps.value``. Same trick, same place."""
    d = field.to_dict()
    d.setdefault("fieldConfig", {}).setdefault("controlProps", {})["value"] = value
    return d


INPUT_SCHEMA: list[dict[str, Any]] = [
    textarea_field("message", "User message",
                   placeholder="bind to the trigger, e.g. {{$start.output.body.text}}").to_dict(),
    _prefill(textarea_field("routes", "Routes (one 'key: description' per line)",
                            placeholder=_DEFAULT_ROUTES, rows=4), _DEFAULT_ROUTES),
    text_field("model", "Claude model (blank = default)", placeholder="e.g. claude-sonnet-4-6").to_dict(),
    text_field("claude_bin", "Claude binary", placeholder="claude", default="claude").to_dict(),
    text_field("timeout_secs", "Timeout (seconds)", placeholder="60", default="60").to_dict(),
    _prefill(textarea_field("fallback_reply", "Reply used when the model drafts none",
                            placeholder=_DEFAULT_FALLBACK_REPLY, rows=2),
             _DEFAULT_FALLBACK_REPLY),
    _prefill(textarea_field("system_prompt", "Classification instruction",
                            placeholder="how to choose a route", rows=3), _DEFAULT_SYSTEM_PROMPT),
]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    text_field("route", "Route (match this in the SWITCH)", readonly=True),
    textarea_field("reply", "Direct reply drafted by the model", readonly=True),
    textarea_field("reply_escaped", "Same reply, JSON-escaped (safe inside a raw_json body)",
                   readonly=True),
    text_field("ticket_key", "Jira ticket key found in the message", readonly=True),
    text_field("cr_number", "MDG change request (CR…) found in the message", readonly=True),
    text_field("message", "Message classified", readonly=True),
    textarea_field("raw", "Claude raw reply", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
