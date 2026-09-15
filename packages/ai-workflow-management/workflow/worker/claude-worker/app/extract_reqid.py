"""reqID extractor node — lives in the claude-worker so it reuses the engine's
``_call_claude_cli`` (which ROTATES across the comma-separated ``claude_cli_token``
accounts on rate-limit / auth failure). Pure text extraction: NON-agentic, NO repo
clone — just ``claude -p --output-format json`` on the ticket text, returning ONLY the
reqID mentioned (regex fallback over the reply then the raw text).
"""

from __future__ import annotations

import re
from typing import Any

from worker_sdk.layer1_domain.auto_form_mapper import text_field, textarea_field

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    _DEFAULT_SECRETS_FILE,
)

# reqID shapes seen in tickets: "CR0000018045" and, generally, 2 letters + digits.
_REQID_RE = re.compile(r"\b([A-Z]{2}\d{6,})\b")

_DEFAULT_SYSTEM_PROMPT = (
    "You are given a bug/change ticket (title + description). Find the SMDG request id "
    "(reqID) it mentions — it looks like 'CR0000018045' (two letters followed by digits). "
    "Reply with ONLY that id and nothing else. If there is no such id, reply exactly NONE."
)


def _first_reqid(*texts: str) -> str:
    for t in texts:
        if not t:
            continue
        m = _REQID_RE.search(t)
        if m:
            return m.group(1)
    return ""


def make_extract_handler(engine: Any) -> Any:
    """Build the ``(inputs, parameters) -> dict`` handler, bound to the shared engine so
    it reuses the token-rotating ``_call_claude_cli`` + secrets reader."""

    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        ticket_text = engine._as_text(merged.get("ticket_text") or merged.get("ticket_description")).strip()
        if not ticket_text:
            return {"status": "FAILED", "reqID": "", "raw": "",
                    "error": "'ticket_text' is empty (bind it to the Jira issue)."}

        system_prompt = engine._as_text(merged.get("system_prompt")).strip() or _DEFAULT_SYSTEM_PROMPT
        model = engine._as_text(merged.get("model")).strip()
        claude_bin = engine._as_text(merged.get("claude_bin")).strip() or "claude"
        try:
            timeout_secs = int(float(merged.get("timeout_secs") or 120))
        except Exception:  # noqa: BLE001
            timeout_secs = 120

        # Secrets (claude_cli_token = comma-separated accounts) from the local file — the
        # SAME source the engine's implement/check nodes use, so rotation is shared.
        secrets = engine._read_secrets_file(
            engine._as_text(merged.get("secrets_file")).strip() or _DEFAULT_SECRETS_FILE, None)
        tokens = [t.strip() for t in (secrets.get("claude_cli_token") or "").split(",") if t.strip()]

        prompt = f"{system_prompt}\n\n--- TICKET ---\n{ticket_text}\n--- END ---"
        raw = ""
        err = ""
        try:
            data = engine._call_claude_cli(
                prompt=prompt, model=model, max_turns=0, working_dir=None, add_dir=None,
                agentic=False, timeout_secs=timeout_secs, oauth_tokens=tokens,
                claude_bin=claude_bin, trace_file=None,
            )
            raw = str((data or {}).get("result") or "")
        except Exception as exc:  # noqa: BLE001 — regex fallback below, never crash the node
            err = f"claude call failed: {exc} — regex fallback only."

        req_id = _first_reqid(raw, ticket_text)
        status = "OK" if req_id else "FAILED"
        if not req_id and not err:
            err = "no reqID found in the ticket (Claude replied NONE / no match)."
        return {"status": status, "reqID": req_id, "raw": raw[:2000], "error": err}

    return _handler


INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    textarea_field("ticket_text", "Ticket text (title + description)",
                   placeholder="bind to the Jira issue, e.g. {{$get_issue.metadata.issue}}"),
    text_field("model", "Claude model (blank = default)", placeholder="e.g. claude-sonnet-4-6"),
    text_field("claude_bin", "Claude binary", placeholder="claude", default="claude"),
    text_field("timeout_secs", "Timeout (seconds)", placeholder="120", default="120"),
    text_field("system_prompt", "Extraction instruction",
               placeholder="how to find the reqID", default=_DEFAULT_SYSTEM_PROMPT),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    text_field("reqID", "Extracted reqID", readonly=True),
    textarea_field("raw", "Claude raw reply", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
