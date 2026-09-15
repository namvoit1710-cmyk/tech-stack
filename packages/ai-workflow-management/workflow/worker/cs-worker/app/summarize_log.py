"""BA-readable analysis of the collected process logs.

The same node as the claude-worker's ``summarize_log``, and deliberately the same default
system prompt — that prompt is a tuned artefact (the step -> owning-team mapping, the
BA-fix-vs-escalate rule, the exact output layout) and rewording it would quietly change
what every existing log flow produces. What differs here is only the plumbing: the call
goes through ``app/claude_cli.py`` rather than the 1600-line coding engine, because this
worker has no repo to clone and no pull request to open.

Non-agentic: one ``claude -p`` call over text. The model does the analysis; there is no
regex.
"""

from __future__ import annotations

from typing import Any

from worker_sdk.layer1_domain.auto_form_mapper import text_field, textarea_field

from app import secrets as _secrets
from app.claude_cli import as_text, call_claude

# Verbatim from the claude-worker node, so a CS answer and a Jira-flow answer read alike.
_DEFAULT_SYSTEM_PROMPT = (
    "You help a Business Analyst (BA) understand why a Change Request (CR) failed in the SMDG "
    "submit/validation process and what to do next. You are given the process step logs "
    "(SubmitEventLog validation steps with PASSED/FAILED + RequestActionLog actions + any Cloud "
    "Logging hits). Write in ENGLISH. NEVER use the word 'customer' — only refer to the BA, the "
    "NodeJS team, or the ABAP team.\n\n"
    "CLASSIFY each FAILED step to decide the Action:\n"
    "  * BA — a business-DATA / template problem the BA can fix in the template: e.g. a duplicate "
    "business partner, a missing/invalid field, a data-quality rejection, or ANY "
    "validateTemplatePayloadData failure. The log names a concrete data problem (a partner, ERP "
    "number, field, value). Do NOT route these to a team — the Action is for the BA to fix it.\n"
    "  * A dev TEAM — only a genuine technical/SYSTEM error (service error, 404, timeout, the "
    "step stuck with no data reason). Map the step to its team+service:\n"
    "      - validateTestrun -> ABAP team ({OT}-process service); when a CR is stuck here, the BA "
    "should get the mdgLogID from the logs and send it to the ABAP team for checking.\n"
    "      - validateAuthorization -> NodeJS team (srv-user service)\n"
    "      - validateDuplication -> NodeJS team ({OT}-process service)\n"
    "      - validateParallelChange -> NodeJS team (process-system service)\n"
    "      - validateDataQuality -> NodeJS team (srv-dqm service)\n"
    "      - validateTemplatePayloadData -> NodeJS team (config-system service)\n"
    "  * ONLY validateTestrun goes to the ABAP team; every other step is either NodeJS (system "
    "error) or a BA fix (data/template).\n"
    "  * validateTemplatePayloadData is NEVER ABAP — normally a BA template-data fix; route it to "
    "the NodeJS team only if it is a genuine system error.\n\n"
    "Output PLAIN TEXT ONLY — no markdown, no asterisks, no backticks. Keep the emojis and the "
    "'──' separators. Use EXACTLY this layout:\n\n"
    "🧭 CR Triage — <reqID from the logs>\n"
    "Result: <one line: passed, or which step blocked it; mention if it was cancelled/stalled>\n\n"
    "(then one block per FAILED step, numbered:)\n"
    "── Issue <n> ───────────────────────\n"
    "Step: <step name>    Status: ❌ Failed\n"
    "Short answer: <one concise sentence a BA can grasp at a glance>\n"
    "Deep answer: <a thorough explanation of what went wrong and why, including the concrete data "
    "or error text from the logs>\n"
    "Action: <ALWAYS present. For a BA/data-template fix: 'BA to <correct exactly what> in the "
    "template, then resubmit'. For a system error: 'Escalate to the NodeJS team|ABAP team "
    "(<service>) with reqID <reqID>' (and the mdgLogID for validateTestrun)>\n\n"
    "(after all issues:)\n"
    "── Passed ─────────────────────────\n"
    "✅ <comma-separated names of the steps that passed>\n"
    "👉 Next step: <the single most important next action>\n"
    "📄 The detailed error logs are in the attachment below.\n\n"
    "Every issue block has exactly these four lines: Step, Short answer, Deep answer, Action — no "
    "'Owner' line and no per-block 'Error detail' line (the '📄 ...' line appears ONCE at the very "
    "end).\n"
    "Only report steps that actually FAILED or show an error — do NOT invent problems. If every "
    "step passed, output the header + 'Result: All validations passed — no action needed.'"
)


def make_summary_handler() -> Any:
    """Handler ``(inputs, parameters) -> dict``. Never raises."""

    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        logs = as_text(merged.get("logs") or merged.get("log_text")).strip()
        if not logs:
            return {"status": "FAILED", "summary": "",
                    "error": "'logs' is empty (bind it to the log nodes)."}

        system_prompt = as_text(merged.get("system_prompt")).strip() or _DEFAULT_SYSTEM_PROMPT
        model = as_text(merged.get("model")).strip()
        claude_bin = as_text(merged.get("claude_bin")).strip() or "claude"
        try:
            timeout_secs = int(float(merged.get("timeout_secs") or 240))
        except (TypeError, ValueError):
            timeout_secs = 240

        prompt = f"{system_prompt}\n\n--- PROCESS LOGS ---\n{logs}\n--- END LOGS ---"
        try:
            summary = call_claude(prompt, _secrets.claude_tokens(), model=model,
                                  claude_bin=claude_bin, timeout_secs=timeout_secs).strip()
        except Exception as exc:  # noqa: BLE001 — a graceful payload, not a dead run
            return {"status": "FAILED", "summary": "", "error": f"claude call failed: {exc}"}

        if not summary:
            return {"status": "FAILED", "summary": "",
                    "error": "Claude returned an empty summary."}
        return {"status": "OK", "summary": summary, "error": ""}

    return _handler


def _prefill(field: Any, value: str) -> dict[str, Any]:
    d = field.to_dict()
    d.setdefault("fieldConfig", {}).setdefault("controlProps", {})["value"] = value
    return d


INPUT_SCHEMA: list[dict[str, Any]] = [
    textarea_field("logs", "Process logs (bind to the log nodes)",
                   placeholder="e.g. {{$submit_event_log.pretty}} + {{$req_action_log.pretty}}",
                   rows=4).to_dict(),
    text_field("model", "Claude model (blank = default)",
               placeholder="e.g. claude-sonnet-4-6").to_dict(),
    text_field("claude_bin", "Claude binary", placeholder="claude", default="claude").to_dict(),
    text_field("timeout_secs", "Timeout (seconds)", placeholder="240", default="240").to_dict(),
    _prefill(textarea_field("system_prompt", "Analysis instruction (step->team mapping)",
                            placeholder="how to summarize", rows=4), _DEFAULT_SYSTEM_PROMPT),
]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    textarea_field("summary", "BA summary", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
