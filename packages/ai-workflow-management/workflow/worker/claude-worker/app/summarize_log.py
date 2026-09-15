"""Log-summary node — lives in the claude-worker so it reuses the engine's
token-rotating ``_call_claude_cli`` (rotates across ``claude_cli_token`` accounts).

Reads the collected process logs (RequestActionLog + SubmitEventLog + Cloud Logging,
already prettified) and produces a SHORT, business-analyst-friendly summary: for every
FAILED / error step it says (in plain English) what went wrong, which TEAM owns it, that
the detailed error is in the log comment above, and a concrete next-step suggestion for
the BA. Pure text (non-agentic, no repo clone). Regex-free; the model does the analysis.
"""

from __future__ import annotations

from typing import Any

from worker_sdk.layer1_domain.auto_form_mapper import text_field, textarea_field

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    _DEFAULT_SECRETS_FILE,
)

# The step -> owning team + service mapping the BA cares about. Baked into the default
# system prompt (still an editable node field).
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
    "📄 The detailed error logs are in the comment below.\n\n"
    "Every issue block has exactly these four lines: Step, Short answer, Deep answer, Action — no "
    "'Owner' line and no per-block 'Error detail' line (the '📄 ... comment below' line appears "
    "ONCE at the very end, because on the Jira UI this newer comment sits above the older log "
    "comment).\n"
    "Only report steps that actually FAILED or show an error — do NOT invent problems. If every "
    "step passed, output the header + 'Result: All validations passed — no action needed.'"
)


def make_summary_handler(engine: Any) -> Any:
    """Build the ``(inputs, parameters) -> dict`` handler bound to the shared engine."""

    def _handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {**(parameters or {}), **(inputs or {})}
        logs = engine._as_text(merged.get("logs") or merged.get("log_text")).strip()
        if not logs:
            return {"status": "FAILED", "summary": "", "error": "'logs' is empty (bind it to the log nodes)."}

        system_prompt = engine._as_text(merged.get("system_prompt")).strip() or _DEFAULT_SYSTEM_PROMPT
        model = engine._as_text(merged.get("model")).strip()
        claude_bin = engine._as_text(merged.get("claude_bin")).strip() or "claude"
        try:
            timeout_secs = int(float(merged.get("timeout_secs") or 180))
        except Exception:  # noqa: BLE001
            timeout_secs = 180

        secrets = engine._read_secrets_file(
            engine._as_text(merged.get("secrets_file")).strip() or _DEFAULT_SECRETS_FILE, None)
        tokens = [t.strip() for t in (secrets.get("claude_cli_token") or "").split(",") if t.strip()]

        prompt = f"{system_prompt}\n\n--- PROCESS LOGS ---\n{logs}\n--- END LOGS ---"
        try:
            data = engine._call_claude_cli(
                prompt=prompt, model=model, max_turns=0, working_dir=None, add_dir=None,
                agentic=False, timeout_secs=timeout_secs, oauth_tokens=tokens,
                claude_bin=claude_bin, trace_file=None,
            )
            summary = str((data or {}).get("result") or "").strip()
        except Exception as exc:  # noqa: BLE001 — surface as a graceful payload, don't crash the node
            return {"status": "FAILED", "summary": "", "error": f"claude call failed: {exc}"}

        if not summary:
            return {"status": "FAILED", "summary": "", "error": "Claude returned an empty summary."}
        return {"status": "OK", "summary": summary, "error": ""}

    return _handler


INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    textarea_field("logs", "Process logs (bind to the log nodes)",
                   placeholder="e.g. {{$submit_event_log.pretty}} + {{$req_action_log.pretty}}"),
    text_field("model", "Claude model (blank = default)", placeholder="e.g. claude-sonnet-4-6"),
    text_field("claude_bin", "Claude binary", placeholder="claude", default="claude"),
    text_field("timeout_secs", "Timeout (seconds)", placeholder="180", default="180"),
    text_field("system_prompt", "Analysis instruction (step->team mapping)",
               placeholder="how to summarize", default=_DEFAULT_SYSTEM_PROMPT),
]]

OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in [
    text_field("status", "Status", readonly=True),
    textarea_field("summary", "BA summary", readonly=True),
    text_field("error", "Error (if any)", readonly=True),
]]
