"""Agentic Coder — one bounded coding pass over a Jira ticket, via the Claude CLI.

Like the ticket-automation pipeline, this shells out to the local **Claude Code
CLI** (`claude -p --output-format json`) instead of calling the Anthropic HTTP API.
That means it uses the operator's logged-in Claude Code session (or a
`CLAUDE_CODE_OAUTH_TOKEN`), can run agentically over a real checkout when given a
working dir, and reports the CLI's own `total_cost_usd`.

Given a ticket (summary + description + acceptance criteria) and optional repo
context / rulebook, one call asks Claude to either (a) block with questions when
the spec is not enough, or (b) produce the smallest implementation as a unified
diff, list the changed files, and self-score against the acceptance criteria.

Design notes
------------
* Transport is the **CLI**, mirroring the pipeline's `run_claude_once`:
  `claude -p --output-format json --dangerously-skip-permissions [--model M]
  [--max-turns N] [--add-dir DIR]`, prompt piped on STDIN (avoids Windows
  arg-quoting), `cwd = working_dir`. On Windows the `claude` shim is invoked via
  `cmd /C` (same as the pipeline).
* Auth is the CLI's own: the logged-in session, or an optional `claude_cli_token`
  input set as `CLAUDE_CODE_OAUTH_TOKEN` for the child (ANTHROPIC_API_KEY is
  dropped so only the OAuth token authenticates). No API key is used.
* Single pass per call. Iteration ("keep trying until score >= 100") is the
  workflow `LOOP` node's job.
* The understanding gate (`enough` / `questions`) is answered in the SAME call,
  so a downstream `CONDITION` can route to "ask + Blocked" without a second call.
* `provider = mock` keeps a deterministic, CLI-free path for keyless demos.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlparse, urlunparse


# Default trace file: the operator's Desktop (worker host). Overridable per node
# via the `trace_file` field, or globally via env CLAUDE_WORKER_TRACE.
_DEFAULT_TRACE_FILE = os.environ.get(
    "CLAUDE_WORKER_TRACE",
    os.path.join(os.path.expanduser("~"), "Desktop", "claude-worker-trace.log"),
)

# Secrets (GitHub PAT + Claude CLI token[s]) live in a LOCAL JSON file — never in the
# node/workflow config. The node just carries the file PATH; the worker reads it. Shape:
#   {"github_pat": "ghp_...", "claude_cli_token": "sk-...,sk-..."}
# (claude_cli_token may be a comma-separated string OR a JSON array of tokens.)
_DEFAULT_SECRETS_FILE = os.environ.get(
    "CLAUDE_WORKER_SECRETS",
    os.path.join(os.path.expanduser("~"), ".smdg-worker-secrets.json"),
)


def _trace(path: Optional[str], msg: str) -> None:
    """Best-effort append a timestamped line to the trace file. Never raises —
    tracing must never break a run. Blank path disables tracing."""
    if not path:
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    number_field,
    select_field,
    switch_field,
    text_field,
    textarea_field,
)


# -- Default system prompt ----------------------------------------------------
# NOT baked into behavior: it only seeds the node's editable "System Prompt" field
# below. The per-node value (set in the UI) wins; this is just the fallback used
# when that field is left blank. Change the behavior from the node, not here.
# Default prompts (fallbacks ONLY). The REAL prompt lives on the node's "System Prompt"
# field — each node type pre-fills the matching default below, and the worker uses the
# node's value verbatim; it only falls back to these when the field is blank. No prompt
# text is appended in code anymore (behaviour like mode/precheck stays in code).
_CHECK_PROMPT = """You are reviewing a Jira ticket. The required fields were already verified in code.
Judge whether the ticket is clear and CONSISTENT enough to implement: flag any AMBIGUITY,
CONTRADICTION or MISMATCH between the summary, description, acceptance criteria and design.
If a repository is provided in your workspace, you MAY read it (read-only — inspect files, search
the code) to judge whether the ticket is feasible and consistent with the real codebase. Do NOT
modify any file. When no repository is provided, answer from the ticket alone.

Reply with ONE JSON object and NOTHING else (no prose, no code fences):
{"enough": bool, "questions": [str], "mismatches": [str], "summary": str, "patch": "", "files_changed": [], "score": 0}"""

_IMPLEMENT_PROMPT = """You are an autonomous coding agent. The ticket has ALREADY been approved as
sufficient — do NOT ask questions. Implement the SMALLEST change that satisfies every acceptance
criterion, following the coding guidelines and reusing existing abstractions. Return the change as
one unified-diff "patch" (git diff), list touched paths in "files_changed", self-assess "score"
0-100 (100 = every criterion demonstrably met, tests included).

Reply with ONE JSON object and NOTHING else (no prose, no code fences):
{"enough": true, "questions": [], "mismatches": [], "summary": str, "patch": str, "files_changed": [str], "score": int}"""

_BOTH_PROMPT = """You are an autonomous coding agent working one Jira ticket at a time.
CHECK first: verify the ticket is internally CONSISTENT and clear; if anything is missing, ambiguous,
or contradictory set "enough": false, put open questions in "questions" and inconsistencies in
"mismatches", leave "patch" empty. Otherwise set "enough": true and IMPLEMENT the SMALLEST change
that satisfies every acceptance criterion — return a unified-diff "patch", "files_changed" and a
"score" 0-100. Do NOT use tools unless a workspace is provided; answer directly.

Reply with ONE JSON object and NOTHING else (no prose, no code fences):
{"enough": bool, "questions": [str], "mismatches": [str], "summary": str, "patch": str, "files_changed": [str], "score": int}"""

# Field-default fallback for the shared schema (node types override per mode via node_types.py).
_DEFAULT_SYSTEM_PROMPT = _BOTH_PROMPT

# Appended to the prompt when the create_hackathon toggle is ON — so ONE Claude run does the
# node's main job (e.g. root-cause analysis) AND scaffolds a hackathon. Only NEW files under
# hackathons/<slug>/ are created; existing code is never modified. The node still returns its
# usual JSON; the worker then collects hackathons/** and (if push_hackathon) pushes it to the hub.
_HACKATHON_SCAFFOLD_INSTRUCTION = (
    "ADDITIONALLY — before your final JSON reply — scaffold a hackathon in your workspace under "
    "hackathons/<slug>/ (slug = the ticket key + a short kebab title). Capture THIS ticket and your "
    "findings: at minimum a README.md (goal/scope from the ticket) and a findings.md (your root-cause "
    "analysis). CREATE only new files under that hackathons/<slug>/ folder — do NOT modify or delete any "
    "existing file. This scaffolding is a side task; still end with the single JSON object specified above."
)


def _default_prompt_for(mode: str) -> str:
    return {"check": _CHECK_PROMPT, "implement": _IMPLEMENT_PROMPT}.get(mode, _BOTH_PROMPT)


# -- Input Schema -------------------------------------------------------------

def _req() -> list[ValidationRule]:
    return [ValidationRule(method="required")]


def _password_field(key: str, label: str, placeholder: str = "", required: bool = False) -> AutoFormField:
    """Masked token input — the platform's standard secret-masking idiom:
    ``fieldControl=InputControl`` + ``controlProps.type="password"``. This is exactly
    what the credentials-management service's own forms use to mask the GitHub PAT,
    Jira API token, DB/SMTP passwords, JWT, client secret, etc. The FE ``InputControl``
    spreads controlProps onto the native ``<input>``, so ``type=password`` renders masked.
    (``PasswordControl`` is NOT in the control-plane fieldControl enum — do not use it.)"""
    cp: dict[str, Any] = {"type": "password"}
    if placeholder:
        cp["placeholder"] = placeholder
    return AutoFormField(
        key=key,
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props=cp,
            rules=_req() if required else None,
        ),
    )


_INPUT_FIELDS: list[AutoFormField] = [
    # ── TOGGLES (grouped at the top) ─────────────────────────────────────────
    # Hackathon capability (bind to setup toggles). create_hackathon ON => Claude also
    # scaffolds hackathons/<slug>/ during this run; push_hackathon ON => push those files
    # to the hub (hub URL from Hub Dir; base branch main) via the GitHub API + a PR.
    switch_field("create_hackathon", "Create hackathon (scaffold hackathons/<slug>/ in this run)", default=True),
    switch_field("push_hackathon", "Push hackathon (push the scaffolded files to the hub)", default=True),
    # CHECK mode: which ticket fields MUST be present (add more with the ``require_<field>``
    # naming — picked up automatically). The check fails the ticket if any ON field is missing.
    switch_field("require_title", "Require: Title", default=True),
    switch_field("require_description", "Require: Description", default=True),

    # ── CONFIG ───────────────────────────────────────────────────────────────
    # ``mode`` is filtered out per node-type (each type fixes its own mode), so it never
    # shows on a real node — kept here only so the base schema is complete.
    select_field(
        "mode", "Mode",
        options=[
            ("check", "Check only (is the ticket enough? mismatches?)"),
            ("implement", "Implement only (assume already approved)"),
            ("both", "Check + implement (single node)"),
        ],
        placeholder="Select mode",
    ),
    text_field(
        "model", "Model",
        placeholder="claude-opus-4-8 (blank = CLI default)",
    ),
    text_field(
        "claude_bin", "Claude Binary",
        placeholder="claude", default="claude", readonly=True,
    ),
    # (secrets_file + trace_file are NOT node fields — the worker reads them from its
    #  defaults / container env CLAUDE_WORKER_SECRETS + CLAUDE_WORKER_TRACE.)
    # The ticket/issue from the previous node (bind to the Jira issue output). Read-only.
    textarea_field(
        "ticket_description", "Ticket",
        placeholder="Bound to the previous node's issue output, e.g. {{$get_issue.issue}}",
        rows=8, rules=_req(), readonly=True,
    ),
    # Hub git URL: NOT cloned and NOT used as a cwd — it's the hackathon push target (the hub).
    text_field(
        "hub_dir", "Hub Repo (git URL — hackathon push target)",
        placeholder="git URL of the hub",
        default="https://github.com/thomas-tran_laidon/smdg-ai-hub",
    ),
    text_field(
        "repo_link", "Repo Link (clone & analyze)",
        placeholder="git URL to clone & analyze",
        default="https://github.com/thomas-tran_laidon/simplemdg_ai",
    ),
    text_field(
        "branch", "Clone From Branch",
        placeholder="base branch to clone", default="solace",
    ),
    text_field(
        "hackathon_branch", "Hub Base Branch (hackathon PR)",
        placeholder="base branch on the hub for the hackathon PR (blank = the hub's default)",
        default="",
    ),
    number_field(
        "max_turns", "Max Turns",
        placeholder="0 = uncapped", default=0,
        rules=[ValidationRule(method="min", value=0, message="Minimum is 0")],
    ),
    number_field(
        "timeout_secs", "Timeout (seconds)",
        placeholder="1800", default=1800,   # 30 minutes
        rules=[
            ValidationRule(method="min", value=10, message="Minimum is 10 seconds"),
            ValidationRule(method="max", value=3600, message="Maximum is 3600 seconds"),
        ],
    ),

    # ── PROMPT FIELDS (last — the big text) ──────────────────────────────────
    textarea_field(
        "rulebook", "Coding Guidelines",
        placeholder="Project coding rules the patch must follow (optional)", rows=4,
    ),
    # The system prompt that shapes behavior — editable per node, pre-filled per mode.
    AutoFormField(
        key="system_prompt",
        output_type=OutputType.STRING,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "System Prompt"},
            control_props={
                "rowsLength": 12,
                "value": _DEFAULT_SYSTEM_PROMPT,
                "placeholder": "Instruction that makes the model return the JSON contract (blank = default)",
            },
        ),
    ),
]

AGENTIC_CODER_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


# -- Output Schema ------------------------------------------------------------

def _o_array(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


def _o_bool(key: str, label: str) -> AutoFormField:
    return AutoFormField(
        key=key,
        output_type=OutputType.BOOLEAN,
        field_config=FieldConfig(
            field_control=FieldControl.SWITCH,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": label},
            control_props={"readonly": True},
        ),
    )


_OUTPUT_FIELDS: list[AutoFormField] = [
    text_field("status", "Status", readonly=True),
    _o_bool("enough", "Enough To Implement"),
    _o_array("questions", "Blocking Questions"),
    _o_array("mismatches", "Mismatches / Inconsistencies"),
    textarea_field("summary", "Summary", readonly=True),
    textarea_field("patch", "Patch (unified diff)", readonly=True),
    _o_array("files_changed", "Files Changed"),
    text_field("pr_url", "Pull Request URL", readonly=True),
    text_field("pr_error", "PR Error (if any)", readonly=True),
    number_field("score", "Self Score (0-100)", readonly=True),
    text_field("model_used", "Model Used", readonly=True),
    text_field("finish_reason", "Finish Reason", readonly=True),
    number_field("input_tokens", "Input Tokens", readonly=True),
    number_field("output_tokens", "Output Tokens", readonly=True),
    number_field("cache_read_tokens", "Cache Read Tokens", readonly=True),
    number_field("total_tokens", "Total Tokens", readonly=True),
    number_field("cost_usd", "Cost (USD)", readonly=True),
    number_field("num_turns", "Agentic Turns", readonly=True),
    number_field("elapsed_ms", "Elapsed Time (ms)", readonly=True),
]

AGENTIC_CODER_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Pricing (approximate, USD per 1M tokens) — fallback only -----------------
# The CLI reports the authoritative ``total_cost_usd``; this map is a fallback
# used only when the CLI returns 0 (e.g. offline mock estimates).
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
}
_DEFAULT_RATE: tuple[float, float] = (15.0, 75.0)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate = _DEFAULT_RATE
    for prefix, r in _PRICING.items():
        if model.startswith(prefix):
            rate = r
            break
    cost = (input_tokens / 1_000_000) * rate[0] + (output_tokens / 1_000_000) * rate[1]
    return round(cost, 6)


# --- OAuth token switching (pipeline parity) --------------------------------
# Sticky start index across tasks so a token found rate-limited/invalid isn't
# re-hit first every run (mirrors the pipeline's CLAUDE_TOKEN_IDX). List (not int)
# so the closure in _call_claude_cli can mutate it in place.
_TOKEN_IDX: list[int] = [0]

# Classification markers ported verbatim from the pipeline's looks_rate_limited /
# looks_like_auth_failure — decide when to switch to the next token.
_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "usage limit", "rate limit", "rate_limit", "429", "too many requests",
    "overloaded", "capacity", "quota", "credit balance", "insufficient credit",
    # "You've hit your <session|weekly|monthly|daily|usage> limit · resets …".
    # "hit your" alone catches every variant; the rest are belt-and-suspenders. Safe
    # because this is matched only against the CLI's own error channel, never tool output.
    "hit your", "session limit", "weekly limit", "monthly limit", "daily limit",
    "limit reached", "limit resets", "limit will reset", "resets ",
)
_AUTH_FAIL_MARKERS: tuple[str, ...] = (
    "authentication", "unauthorized", "401", "oauth", "token expired",
    "expired token", "please run /login", "not logged in", "login expired",
    "invalid token", "revoked",
)


class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor

    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        self.logger.info(
            "Executing agentic-coder task",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("agentic_coder_task_requests", 1)
        start = time.monotonic()

        try:
            # Inputs win over static node parameters (runtime can override config).
            inputs: dict[str, Any] = {**request.parameters, **request.inputs}

            provider = (self._as_text(inputs.get("provider")).strip().lower() or "claude_cli")
            model = self._as_text(inputs.get("model")).strip()
            description = self._as_text(inputs.get("ticket_description")).strip()
            if not description:
                raise ValueError("'ticket_description' (ticket content) is required.")

            # Mock: deterministic, CLI-free — for local/browser demos.
            if provider == "mock":
                duration_ms = (time.monotonic() - start) * 1000
                outputs = self._mock_result(inputs, model or "mock", description)
                outputs["elapsed_ms"] = duration_ms
                self.monitor.track("agentic_coder_task_success", 1)
                return ExecuteTaskResult(
                    task_id=request.task_id,
                    status=TaskStatus.SUCCESS,
                    outputs=outputs,
                    duration_ms=duration_ms,
                )

            # --- Claude CLI path (like the pipeline) ---
            mode = (self._as_text(inputs.get("mode")).strip().lower() or "both")

            # Deterministic pre-check FIRST (no LLM): if a toggled-required field is
            # missing from the ticket, block immediately — don't spend a Claude call.
            # Only when required fields are present do we ask Claude to judge ambiguity/
            # conflicts. Applies to check + both modes.
            if mode in ("check", "both"):
                missing = self._precheck_required(inputs, description)
                if missing:
                    duration_ms = (time.monotonic() - start) * 1000
                    self.monitor.track("agentic_coder_task_success", 1)
                    return ExecuteTaskResult(
                        task_id=request.task_id,
                        status=TaskStatus.SUCCESS,
                        outputs={
                            "status": "BLOCKED",
                            "enough": False,
                            "questions": [f"Missing required field: {m}" for m in missing],
                            "mismatches": [],
                            "summary": "Blocked by pre-check (code): required field(s) missing — no LLM call made.",
                            "patch": "", "files_changed": [], "score": 0,
                            "pr_url": "", "pr_error": "",
                            "model_used": "precheck (no LLM)", "finish_reason": "precheck",
                            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                            "total_tokens": 0, "cost_usd": 0.0, "num_turns": 0,
                            "elapsed_ms": duration_ms,
                        },
                        duration_ms=duration_ms,
                    )

            # GitHub (pipeline model): a repo_link => clone the repo @ branch and run Claude
            # agentically with cwd=hub (context) and --add-dir=clone. check READS it to judge
            # ambiguity; implement/both EDIT it and we read the change back as `git diff`.
            hub_dir = self._as_text(inputs.get("hub_dir")).strip() or None
            # hub_dir is the hub git URL: NOT used as a cwd (a URL can't be one) and NOT cloned —
            # it just doubles as the hackathon push target. A local-path hub_dir still works as cwd.
            hackathon_repo = hub_dir if (hub_dir and self._is_http_url(hub_dir)) else None
            if hub_dir and self._is_http_url(hub_dir):
                hub_dir = None
            repo_link = self._as_text(inputs.get("repo_link")).strip() or None
            branch = self._as_text(inputs.get("branch")).strip() or "solace"
            timeout_secs = self._to_int(inputs.get("timeout_secs", 1800), 1800)
            # Hackathon capability (toggle-gated; default ON): create_hackathon => Claude also
            # scaffolds hackathons/<slug>/ during this run; push_hackathon => push those files to
            # the hub (hackathon_repo = the hub URL, base branch main) via the Contents API + a PR.
            want_hackathon = self._as_bool(inputs.get("create_hackathon"))
            push_hackathon = self._as_bool(inputs.get("push_hackathon"))
            # Base branch for the hub PR — configurable (GitHub hubs vary main/master). Blank
            # or a non-existent branch => _push_via_api falls back to the hub's default branch.
            hackathon_branch = self._as_text(inputs.get("hackathon_branch")).strip() or ""
            # Default-ON: trace to the Desktop file even if the node didn't send the field
            # (set trace_file to the literal "off" to disable).
            _tf = self._as_text(inputs.get("trace_file")).strip()
            trace_file = None if _tf.lower() == "off" else (_tf or _DEFAULT_TRACE_FILE)
            # Secrets come from a LOCAL JSON file (path from the node, else the worker
            # default) — never from a node field. git PAT: file's github_pat wins; else a
            # vault credential fallback. Claude token(s): file's claude_cli_token (below).
            secrets = self._read_secrets_file(
                self._as_text(inputs.get("secrets_file")).strip() or _DEFAULT_SECRETS_FILE, trace_file)
            # Token per host: the clone (repo_link) and the hackathon push (hackathon_repo)
            # can live on DIFFERENT services — e.g. clone a GitLab bug repo (gitlab_pat) and
            # push the hackathon to a GitHub hub (github_pat) in the same run.
            git_token = self._token_for(repo_link, secrets, inputs)
            hub_token = self._token_for(hackathon_repo, secrets, inputs)
            _trace(trace_file, "=" * 70)
            _trace(trace_file, f"TASK START task={request.task_id} mode={mode} model={model or 'default'} "
                               f"repo={repo_link} branch={branch} token={'yes' if git_token else 'no'} "
                               f"hub={hub_dir} timeout={timeout_secs}s")

            # Clone whenever a repo is configured — every mode benefits: check READS it
            # to judge ambiguity against real code; implement/both EDIT it. A blank
            # repo_link => ticket-only (no clone), a fast single-shot.
            workspace: Optional[str] = None
            if repo_link:
                workspace = self._clone_repo(repo_link, branch, git_token, timeout_secs, trace_file)
            agentic = bool(workspace)
            working_dir = hub_dir or workspace           # Claude's cwd (hub = context)
            add_dir = workspace if (workspace and hub_dir and workspace != hub_dir) else None

            # The prompt is the NODE's System Prompt verbatim — NO code fallback. The
            # per-mode constants exist only to PRE-FILL/display the field; at runtime we
            # always use what the node carries. Blank => error (set it on the node).
            system_prompt = self._as_text(inputs.get("system_prompt")).strip()
            if not system_prompt:
                raise ValueError(
                    "System Prompt is empty on the node — set it (this worker uses the node's "
                    "prompt only; the code default is for display, not a runtime fallback)."
                )
            prompt = system_prompt + "\n\n" + self._build_user_prompt(inputs, description)
            if want_hackathon and workspace:
                prompt += "\n\n" + _HACKATHON_SCAFFOLD_INSTRUCTION
            try:
                data = self._call_claude_cli(
                    prompt=prompt,
                    model=model,
                    max_turns=self._to_int(inputs.get("max_turns", 3), 3),
                    working_dir=working_dir,
                    add_dir=add_dir,
                    agentic=agentic,
                    timeout_secs=timeout_secs,
                    oauth_tokens=[t.strip() for t in (secrets.get("claude_cli_token") or "").split(",") if t.strip()],
                    claude_bin=self._as_text(inputs.get("claude_bin")).strip() or "claude",
                    trace_file=trace_file,
                )

                result_text = str(data.get("result", ""))
                usage = data.get("usage", {}) or {}
                input_tokens = int(usage.get("input_tokens", 0) or 0)
                output_tokens = int(usage.get("output_tokens", 0) or 0)
                cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
                cost_usd = float(data.get("total_cost_usd", 0.0) or 0.0)
                if cost_usd <= 0.0:
                    cost_usd = _estimate_cost(model or "claude-opus-4", input_tokens, output_tokens)

                parsed = self._parse_result(result_text)
                # Only the implementing modes produce a patch; check just READS the repo
                # (any incidental writes there are discarded with the workspace).
                if workspace and mode in ("implement", "both"):
                    diff, files = self._git_diff(workspace)
                    if diff:
                        parsed["patch"] = diff
                    if files:
                        parsed["files_changed"] = files
                    _trace(trace_file, f"git diff: {len(files)} file(s) changed, patch {len(diff)} chars")
                    # Open a PR with Claude's edits (needs a write+PR-scoped token). Non-fatal:
                    # a push/API failure still returns the diff so the node succeeds.
                    if files and git_token:
                        ticket_key = self._as_text(self._extract_ticket_fields(description).get("key"))
                        try:
                            pr_url = self._open_pr(workspace, repo_link, branch, git_token,
                                                   ticket_key, self._as_text(parsed.get("summary")), trace_file)
                            if pr_url:
                                parsed["pr_url"] = pr_url
                        except Exception as exc:  # noqa: BLE001 — surface, don't crash the node
                            parsed["pr_error"] = str(exc)[:300]
                            _trace(trace_file, f"PR ERROR: {str(exc)[:300]}")
                    elif files and not git_token:
                        parsed["pr_error"] = "no GitHub token — set a PAT with 'repo' (write) scope to open a PR"
                        _trace(trace_file, "PR skipped: no GitHub token (need write scope to push + open PR)")

                # Hackathon capability (toggle-gated; runs for ANY mode incl. check). Claude
                # scaffolded files under hackathons/ during this run when create_hackathon is ON;
                # collect them and, if push_hackathon is ON, push to the hub (hackathon_repo) via
                # the Contents API + a PR. push OFF => created but not pushed.
                if want_hackathon and workspace:
                    hfiles = self._collect_new_files(workspace, "hackathons")
                    _trace(trace_file, f"hackathon: {len(hfiles)} scaffolded file(s) under hackathons/")
                    if hfiles:
                        parsed["files_changed"] = sorted(set((parsed.get("files_changed") or []) + hfiles))
                    if hfiles and push_hackathon and hub_token and hackathon_repo:
                        ticket_key = self._as_text(self._extract_ticket_fields(description).get("key"))
                        try:
                            pr_url = self._push_via_api(
                                workspace, hfiles, hackathon_repo, hackathon_branch, hub_token, ticket_key,
                                self._as_text(parsed.get("summary")) or f"hackathon {ticket_key}", trace_file)
                            if pr_url:
                                parsed["pr_url"] = pr_url
                        except Exception as exc:  # noqa: BLE001 — non-fatal, node still returns analysis
                            parsed["pr_error"] = str(exc)[:300]
                            _trace(trace_file, f"hackathon push ERROR: {str(exc)[:300]}")
                    elif hfiles and not push_hackathon:
                        _trace(trace_file, "hackathon: push_hackathon OFF -> created, not pushed")
            finally:
                if workspace:
                    self._safe_rmtree(workspace)

            duration_ms = (time.monotonic() - start) * 1000

            self.monitor.track("agentic_coder_task_duration_ms", duration_ms)
            self.monitor.track("agentic_coder_task_success", 1)
            self.monitor.track("agentic_coder_total_tokens", float(input_tokens + output_tokens))

            # Keep the stored output SMALL. A large output (the full 18KB patch + 21KB raw
            # response) spills to a file-backed artifact, which downstream {{$node.output.X}}
            # expressions can't resolve ("Key 'output' not found"). The full diff lives in
            # the PR, so cap patch + raw_response to short previews.
            def _cap(s: Any, n: int, note: str = "") -> str:
                s = self._as_text(s)
                return s if len(s) <= n else (s[:n] + note)

            outputs: dict[str, Any] = {
                "enough": bool(parsed.get("enough", False)),
                "questions": parsed.get("questions", []) or [],
                "mismatches": parsed.get("mismatches", []) or [],
                "summary": _cap(parsed.get("summary", ""), 2000, " …(truncated)"),
                "patch": _cap(parsed.get("patch", ""), 1200, "\n… (truncated — full diff in the PR)"),
                "files_changed": parsed.get("files_changed", []) or [],
                "score": self._to_int(parsed.get("score", 0), 0),
                "model_used": model or "claude-cli-default",
                "finish_reason": str(data.get("stop_reason") or data.get("subtype") or "unknown"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "total_tokens": input_tokens + output_tokens,
                "cost_usd": round(cost_usd, 6),
                "num_turns": int(data.get("num_turns", 0) or 0),
                "elapsed_ms": duration_ms,
                "pr_url": parsed.get("pr_url", "") or "",
                "pr_error": parsed.get("pr_error", "") or "",
                "status": ("IMPLEMENTED" if parsed.get("pr_url")
                           else "FAILED" if parsed.get("pr_error")
                           else "OK" if parsed.get("enough") else "NEEDS-INFO"),
            }

            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS,
                outputs=outputs,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            self.logger.error("Agentic-coder task failed", task_id=request.task_id, error=str(e))
            self.monitor.track("agentic_coder_task_error", 1)
            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.ERROR,
                error=str(e),
                outputs={"enough": False, "score": 0, "error_type": type(e).__name__},
                duration_ms=duration_ms,
            )

    # -- Claude CLI --------------------------------------------------------------

    def _call_claude_cli(
        self,
        prompt: str,
        model: str,
        max_turns: int,
        working_dir: Optional[str],
        add_dir: Optional[str],
        agentic: bool,
        timeout_secs: int,
        oauth_tokens: list[str],
        claude_bin: str,
        trace_file: Optional[str] = None,
        effort: str = "",
        fallback_model: str = "",
    ) -> dict[str, Any]:
        """Run the Claude CLI, switching across OAuth tokens on a rate-limit / auth
        failure — a direct port of the pipeline's ``run_claude``.

        Prompt on STDIN (multi-line safe). Windows launches the shim via ``cmd /C``.
        agentic mode adds ``--dangerously-skip-permissions`` / ``--max-turns`` / ``--add-dir``.
        When agentic AND a trace file is set, we run with ``--output-format stream-json
        --verbose`` and tee every event (tool call, text, result) to the trace file so a
        stuck run is visible live; otherwise a single ``--output-format json`` shot.

        ``effort`` maps to the CLI's --effort (low|medium|high|xhigh|max) and is the knob
        that bounds how long a single call thinks; ``fallback_model`` maps to
        --fallback-model so an overloaded primary does not cost the whole run. Both are
        omitted when empty, which keeps the CLI's own defaults.

        Token fallback (pipeline parity): a module-level sticky index remembers the last
        working token; rate-limit (any exit) or auth-failure (non-zero exit) rolls to the
        next; spawn/timeout surfaces as-is; all exhausted => "all N unavailable".
        """
        stream = bool(agentic and trace_file)
        args = [claude_bin, "-p"]
        args += ["--output-format", "stream-json", "--verbose"] if stream else ["--output-format", "json"]
        if agentic:
            args.append("--dangerously-skip-permissions")
            if max_turns and max_turns > 0:
                args += ["--max-turns", str(max_turns)]
            if add_dir:
                args += ["--add-dir", add_dir]
        # Fleet default: a node that leaves model/effort blank inherits the
        # container's CLAUDE_WORKER_DEFAULT_MODEL / _EFFORT, so moving the whole
        # fleet is one compose change instead of editing every node in every
        # workflow. A value set ON the node still wins. Blank env = CLI default,
        # i.e. exactly the previous behaviour.
        model = model or os.environ.get("CLAUDE_WORKER_DEFAULT_MODEL", "").strip()
        effort = effort or os.environ.get("CLAUDE_WORKER_DEFAULT_EFFORT", "").strip()
        if model:
            args += ["--model", model]
        # Validated against the installed CLI rather than trusted: an unknown value here
        # would be a hard argument error on every call.
        if effort and effort.lower() in ("low", "medium", "high", "xhigh", "max"):
            args += ["--effort", effort.lower()]
        if fallback_model:
            args += ["--fallback-model", fallback_model]
        cmd = (["cmd", "/C", *args] if os.name == "nt" else args)

        tokens: list[Optional[str]] = list(oauth_tokens) if oauth_tokens else [None]
        n = len(tokens)
        start = _TOKEN_IDX[0] % n

        for offset in range(n):
            idx = (start + offset) % n
            tok = tokens[idx]
            env = os.environ.copy()
            if tok:
                # Only the selected OAuth token authenticates this child; drop any API key.
                env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
                env.pop("ANTHROPIC_API_KEY", None)

            _trace(trace_file, f"claude INVOKE token #{idx + 1}/{n} stream={stream} agentic={agentic} "
                               f"cwd={working_dir} add_dir={add_dir} model={model or 'default'} "
                               f"effort={effort or 'default'} "
                               f"max_turns={max_turns} timeout={timeout_secs}s")
            t0 = time.monotonic()
            try:
                data, rc, combined, stderr = self._invoke_claude_once(
                    cmd, prompt, env, timeout_secs, working_dir, stream, trace_file,
                )
            except FileNotFoundError as exc:
                _trace(trace_file, f"claude ERROR: CLI not found ('{claude_bin}')")
                raise RuntimeError(
                    f"claude CLI not found ('{claude_bin}'); install Claude Code or set 'claude_bin'."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                _trace(trace_file, f"claude TIMEOUT after {timeout_secs}s (killed)")
                raise RuntimeError(f"claude CLI timed out after {timeout_secs}s") from exc

            dt = time.monotonic() - t0
            subtype = self._as_text((data or {}).get("subtype")).lower()
            _trace(trace_file, f"claude RETURNED rc={rc} in {dt:.1f}s is_error={(data or {}).get('is_error')} "
                               f"subtype={subtype or '-'} result_len={len(self._as_text((data or {}).get('result')))}")

            # success — stick with this token for next time
            if data is not None and not data.get("is_error"):
                _TOKEN_IDX[0] = idx
                return data

            # Max-turns cap is NOT a token problem — surface a clear, actionable message
            # (raising the cap or setting 0 = uncapped) instead of the misleading
            # "token unavailable". Checked BEFORE the rate/auth heuristic.
            if data is not None and data.get("is_error") and "max_turns" in subtype:
                _trace(trace_file, f"claude hit max_turns cap ({max_turns})")
                raise RuntimeError(
                    f"Claude reached the Max Turns cap ({max_turns}) before finishing — raise "
                    f"'Max Turns' on the node (0 = uncapped; the timeout is the real guard)."
                )

            # pipeline heuristic: rate-limit (any exit) OR auth-failure (non-zero exit).
            # ``combined`` is the CLI's OWN error channel only (stderr + result subtype/
            # error) — NOT tool output — so repo text containing words like "oauth"/"401"
            # can't false-positive this into a bogus "token unavailable".
            unavailable = self._looks_rate_limited(combined) or (
                rc != 0 and self._looks_auth_failure(combined)
            )
            if unavailable and offset + 1 < n:
                _trace(trace_file, f"claude token #{idx + 1}/{n} unavailable -> next token")
                if self.logger:
                    self.logger.info(
                        f"claude token #{idx + 1}/{n} unavailable (rate-limit/auth) - falling back to next"
                    )
                _TOKEN_IDX[0] = (idx + 1) % n
                continue
            if unavailable:
                raise RuntimeError(
                    f"all {n} Claude token(s) unavailable (usage/rate/session limit or auth) - refresh/rotate and re-run."
                )
            # a genuine, non-token error — surface it (don't waste the remaining tokens)
            if data is not None and data.get("is_error"):
                _trace(trace_file, f"claude ERROR result: {self._as_text(data.get('result'))[:300]}")
                raise RuntimeError(f"claude CLI error: {data.get('result') or data.get('subtype')}")
            _trace(trace_file, f"claude NO-JSON exit={rc} stderr={stderr[:300]}")
            raise RuntimeError(
                f"claude CLI produced no parseable JSON (exit {rc}). stderr: {stderr[:600]}"
            )

        raise RuntimeError(f"all {n} Claude token(s) unavailable")

    def _invoke_claude_once(
        self, cmd: list, prompt: str, env: dict, timeout_secs: int,
        working_dir: Optional[str], stream: bool, trace_file: Optional[str],
    ) -> tuple[Optional[dict], int, str, str]:
        """One CLI invocation. Returns (parsed_data, returncode, combined_text, stderr).
        Non-stream: blocking run + json parse. Stream: Popen + tee events to the trace."""
        if not stream:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_secs,
                cwd=working_dir, env=env,
            )
            stdout = (proc.stdout or "").strip()
            stderr = proc.stderr or ""
            data = self._parse_cli_json(stdout)
            combined = f"{stdout}\n{stderr}\n" + (self._as_text(data.get("result")) if data else "")
            return data, proc.returncode, combined, stderr

        # Streaming: line-delimited JSON events; tee a summary of each to the trace.
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", cwd=working_dir, env=env, bufsize=1,
        )
        err_chunks: list[str] = []
        killed = {"v": False}

        def _drain_err() -> None:
            try:
                assert proc.stderr is not None
                for ln in proc.stderr:
                    err_chunks.append(ln)
            except Exception:
                pass

        def _kill() -> None:
            killed["v"] = True
            self._terminate_tree(proc)

        et = threading.Thread(target=_drain_err, daemon=True)
        et.start()
        timer = threading.Timer(timeout_secs, _kill)
        timer.start()
        result_ev: Optional[dict] = None
        out_lines: list[str] = []
        try:
            try:
                assert proc.stdin is not None
                proc.stdin.write(prompt)
                proc.stdin.close()
            except Exception:
                pass
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                out_lines.append(line)
                try:
                    ev = json.loads(line)
                except Exception:
                    _trace(trace_file, "  claude> " + line[:500])
                    continue
                _trace(trace_file, "  claude> " + self._summarize_event(ev))
                if isinstance(ev, dict) and ev.get("type") == "result":
                    result_ev = ev
            proc.wait()
        finally:
            timer.cancel()
        et.join(timeout=2)
        stderr = "".join(err_chunks)
        if killed["v"]:
            raise subprocess.TimeoutExpired(cmd, timeout_secs)
        # Rate-limit/auth signal = the CLI's OWN error channel only (stderr + the result
        # event's subtype/result/error). Deliberately EXCLUDES tool output / assistant
        # text so repo files Claude reads can't trip the token-unavailable heuristic.
        signal = [stderr]
        if result_ev:
            signal += [self._as_text(result_ev.get("subtype")),
                       self._as_text(result_ev.get("result")),
                       self._as_text(result_ev.get("error"))]
        combined = "\n".join(p for p in signal if p)
        return result_ev, proc.returncode, combined, stderr

    @staticmethod
    def _summarize_event(ev: dict) -> str:
        """One-line human summary of a stream-json event for the trace file."""
        t = ev.get("type")
        if t == "system":
            return f"[system/{ev.get('subtype', '')}] model={ev.get('model', '')} cwd={ev.get('cwd', '')}"
        if t == "assistant":
            msg = ev.get("message", {}) or {}
            parts = []
            for c in (msg.get("content") or []):
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    parts.append("text: " + (c.get("text") or "")[:200])
                elif c.get("type") == "tool_use":
                    parts.append(f"tool_use {c.get('name')}: {json.dumps(c.get('input', {}), ensure_ascii=False)[:200]}")
            return "[assistant] " + (" | ".join(parts) if parts else "(no content)")
        if t == "user":
            msg = ev.get("message", {}) or {}
            for c in (msg.get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    r = c.get("content", "")
                    if not isinstance(r, str):
                        r = json.dumps(r, ensure_ascii=False)
                    return "[tool_result] " + r[:200]
            return "[user]"
        if t == "result":
            return (f"[result] subtype={ev.get('subtype')} is_error={ev.get('is_error')} "
                    f"turns={ev.get('num_turns')} cost={ev.get('total_cost_usd')} dur={ev.get('duration_ms')}ms")
        return f"[{t}]"

    # ---- GitHub implement: clone -> (Claude edits) -> read the real diff ----

    @staticmethod
    def _read_secrets_file(path: str, trace_file: Optional[str] = None) -> dict[str, str]:
        """Read the GitHub / GitLab PAT + Claude CLI token(s) — ENVIRONMENT FIRST, secrets
        file SECOND (see ``app/secrets.py``). Individual env vars
        ($CLAUDE_WORKER_GITHUB_PAT / _GITLAB_PAT / _CLAUDE_CLI_TOKEN) and the whole-doc
        $SMDG_WORKER_SECRETS_JSON override the JSON file, so the worker runs the same on a
        laptop (file) or in a container (env). File shape (still supported)::

            {"github_pat": "ghp_...", "claude_cli_token": "sk-...,sk-..."}

        ``claude_cli_token`` may be a comma-separated string OR a JSON array of tokens.
        Common key aliases are accepted. Nothing configured => ``{}`` and the worker falls
        back to the CLI's logged-in session (Claude) / a vault credential (git). Secret
        VALUES are never traced — only presence."""
        from app import secrets as _worker_secrets
        raw = _worker_secrets.load_secrets(path or _DEFAULT_SECRETS_FILE)
        if not isinstance(raw, dict):
            raw = {}

        def pick(*keys: str) -> str:
            for k in keys:
                v = raw.get(k)
                if isinstance(v, list):
                    v = ",".join(str(x).strip() for x in v if str(x).strip())
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""

        github_pat = pick("github_pat", "git_token", "pat", "gh_pat", "github_token")
        gitlab_pat = pick("gitlab_pat", "gitlab_token", "gl_pat")
        claude = pick("claude_cli_token", "oauth_token", "oauth_tokens", "claude_token")
        n_tok = len([t for t in claude.split(",") if t.strip()])
        _trace(trace_file, f"secrets (env+file): github_pat={'yes' if github_pat else 'no'} "
                           f"gitlab_pat={'yes' if gitlab_pat else 'no'} "
                           f"claude_cli_token={('yes(%d)' % n_tok) if n_tok else 'no'}")
        return {"github_pat": github_pat, "gitlab_pat": gitlab_pat, "claude_cli_token": claude}

    @staticmethod
    def _git_token_from_credential(inputs: dict[str, Any]) -> Optional[str]:
        """Pull the git PAT (GitHub or GitLab) out of the credential the workflow lent
        this node (picked in the UI as ``credential_ref`` → run injects ``__credentials``).
        We don't require it: no credential => public/ambient-creds clone. Accepts the
        common shapes — bearer(token) / api_key(value) / basic(password) / raw PAT keys."""
        try:
            from worker_sdk.layer2_application.services.effective_auth import borrow_credential
            cred = borrow_credential(inputs)
        except Exception:
            cred = None
        if not isinstance(cred, dict):
            return None
        cfg = cred.get("config") if isinstance(cred.get("config"), dict) else {}
        for k in ("token", "value", "password", "apiToken", "api_token", "pat"):
            v = cfg.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    @staticmethod
    def _is_gitlab(url: Optional[str]) -> bool:
        """True if the URL points at a GitLab host (github vs gitlab differ in auth/API)."""
        try:
            return "gitlab" in (urlparse(url or "").hostname or "").lower()
        except Exception:
            return False

    def _token_for(self, url: Optional[str], secrets: dict[str, str],
                   inputs: dict[str, Any]) -> Optional[str]:
        """Pick the PAT for *url*. The vault credential the operator picked in the UI
        (lent to the node via ``credential_ref`` → ``__credentials``) is the source of
        truth and wins; the worker-env/file secret (gitlab_pat / github_pat) is only a
        fallback for runs with no credential selected (e.g. local dev). Host still routes
        which env secret is the fallback, so one flow can clone a GitLab repo AND push a
        GitHub hub."""
        credential = self._git_token_from_credential(inputs)
        if credential:
            return credential
        env_secret = secrets.get("gitlab_pat" if self._is_gitlab(url) else "github_pat")
        return (env_secret or "").strip() or None

    @staticmethod
    def _auth_url(repo_link: str, git_token: Optional[str]) -> str:
        """Embed a PAT into an https clone URL so a private repo clones headlessly. GitHub
        uses ``x-access-token:<PAT>@``; GitLab uses ``oauth2:<PAT>@``. No token / non-https
        => returned unchanged (ambient git creds / public repo)."""
        if not git_token:
            return repo_link
        try:
            parts = urlparse(repo_link)
            if parts.scheme != "https":
                return repo_link
            user = "oauth2" if "gitlab" in (parts.hostname or "").lower() else "x-access-token"
            netloc = f"{user}:{quote(git_token, safe='')}@{parts.hostname}"
            if parts.port:
                netloc += f":{parts.port}"
            return urlunparse(parts._replace(netloc=netloc))
        except Exception:
            return repo_link

    def _clone_repo(self, repo_link: str, branch: str, git_token: Optional[str],
                    timeout_secs: int, trace_file: Optional[str] = None) -> str:
        """Shallow-clone ``repo_link`` @ ``branch`` into a temp workspace; return its path.
        Raises with a token-scrubbed message on failure."""
        url = self._auth_url(repo_link, git_token)
        # Never let git block on an interactive credential prompt (GCM popup / stdin) —
        # a missing/invalid PAT must FAIL FAST, not hang the node.
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = ""
        env["GCM_INTERACTIVE"] = "never"
        env["GIT_CONFIG_PARAMETERS"] = "'credential.interactive=false'"
        clone_timeout = min(timeout_secs, 300)
        # This machine's DNS is intermittently flaky ("Could not resolve host: github.com"):
        # the SAME clone succeeds seconds later. Retry transient DNS/network failures with
        # backoff (mirrors the httpx lease-client retries); fail fast on real errors (auth,
        # missing branch) so we don't waste time.
        _TRANSIENT = (
            "could not resolve host", "couldn't resolve host", "getaddrinfo",
            "temporary failure in name resolution", "name or service not known",
            "network is unreachable", "connection timed out", "connection reset",
            "failed to connect", "timed out", "tls", "ssl",
        )
        attempts = 4
        last_err = ""
        _trace(trace_file, f"git clone {repo_link}@{branch} (token={'yes' if git_token else 'no'}) "
                           f"timeout={clone_timeout}s, up to {attempts} attempts")
        for attempt in range(1, attempts + 1):
            ws = tempfile.mkdtemp(prefix="claude_impl_")
            cmd = ["git", "clone", "--depth", "1", "--branch", branch, url, ws]
            t0 = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=clone_timeout, env=env, stdin=subprocess.DEVNULL,
                )
            except FileNotFoundError as exc:
                self._safe_rmtree(ws)
                _trace(trace_file, "git clone ERROR: git not found on PATH")
                raise RuntimeError("git not found on PATH — install git to use the GitHub implement node.") from exc
            except subprocess.TimeoutExpired:
                self._safe_rmtree(ws)
                last_err = f"timed out after {clone_timeout}s"
                _trace(trace_file, f"git clone attempt {attempt}/{attempts} TIMEOUT — retrying")
                if attempt < attempts:
                    time.sleep(1.5 * attempt)
                    continue
                raise RuntimeError(f"git clone timed out ({clone_timeout}s) cloning branch '{branch}'.")
            if proc.returncode == 0:
                _trace(trace_file, f"git clone OK in {time.monotonic() - t0:.1f}s (attempt {attempt}) -> {ws}")
                if self.logger:
                    self.logger.info(f"cloned {repo_link}@{branch} into workspace")
                return ws
            # non-zero: classify transient vs terminal
            self._safe_rmtree(ws)
            err = (proc.stderr or proc.stdout or "").strip()
            if git_token:
                err = err.replace(git_token, "***")
            last_err = err
            transient = any(m in err.lower() for m in _TRANSIENT)
            if transient and attempt < attempts:
                _trace(trace_file, f"git clone attempt {attempt}/{attempts} transient error -> retry: {err[:150]}")
                time.sleep(1.5 * attempt)
                continue
            _trace(trace_file, f"git clone FAILED rc={proc.returncode} (attempt {attempt}): {err[:400]}")
            raise RuntimeError(f"git clone failed (branch '{branch}'): {err[:500]}")
        raise RuntimeError(f"git clone failed after {attempts} attempts (branch '{branch}'): {last_err[:400]}")

    def _git_diff(self, workspace: str) -> tuple[str, list[str]]:
        """Stage everything Claude touched and return (unified_diff, files_changed)."""
        try:
            subprocess.run(["git", "-C", workspace, "add", "-A"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
            diff = subprocess.run(["git", "-C", workspace, "diff", "--cached"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=120)
            names = subprocess.run(["git", "-C", workspace, "diff", "--cached", "--name-only"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=120)
        except Exception as exc:  # noqa: BLE001 — diff is best-effort; the CLI result still stands
            if self.logger:
                self.logger.warning(f"git diff failed after implement: {exc}")
            return "", []
        files = [ln.strip() for ln in (names.stdout or "").splitlines() if ln.strip()]
        return (diff.stdout or "").strip(), files

    @staticmethod
    def _owner_repo(repo_link: str) -> Optional[tuple[str, str]]:
        """Parse (owner, repo) from a GitHub URL. None if it isn't parseable."""
        try:
            path = urlparse(repo_link).path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                return parts[0], parts[1]
        except Exception:
            pass
        return None

    @staticmethod
    def _is_http_url(s: str) -> bool:
        s = (s or "").strip().lower()
        return s.startswith(("http://", "https://", "git@", "ssh://")) or s.endswith(".git")

    @staticmethod
    def _as_bool(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes", "on")

    @staticmethod
    def _list_files(root: str) -> list[str]:
        """Relative POSIX paths of every file under *root* (skips .git)."""
        out: list[str] = []
        for dirpath, _dirs, fnames in os.walk(root):
            for fn in fnames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                if rel.startswith(".git/") or rel == ".git":
                    continue
                out.append(rel)
        return sorted(out)

    def _collect_new_files(self, workspace: str, subdir: str) -> list[str]:
        """POSIX paths of NEW (untracked) files Claude created under *subdir* in a git clone.
        Uses ``git ls-files --others`` so only Claude's additions are collected, never the
        repo's existing files. Falls back to a filesystem walk if git isn't usable."""
        try:
            proc = subprocess.run(
                ["git", "-C", workspace, "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            rels = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        except Exception:  # noqa: BLE001 — no git / not a repo => walk the subdir
            base = os.path.join(workspace, subdir)
            rels = [f"{subdir}/{p}" for p in self._list_files(base)] if os.path.isdir(base) else []
        pref = subdir.strip("/") + "/"
        return sorted(r for r in rels if r == subdir or r.startswith(pref))

    def _push_via_api(self, scratch: str, files: list[str], repo_link: str, base_branch: str,
                      git_token: str, ticket_key: str, summary: str, trace_file: Optional[str]) -> str:
        """Push every file in *files* (relative to *scratch*) to *repo_link* on a new branch via
        the GitHub Contents API and open a PR — WITHOUT cloning the repo. Returns the PR URL."""
        import base64
        import urllib.request
        import urllib.error

        owner_repo = self._owner_repo(repo_link)
        if not owner_repo:
            raise RuntimeError(f"cannot parse owner/repo from repo_link '{repo_link}'")
        owner, repo = owner_repo
        api = f"https://api.github.com/repos/{owner}/{repo}"
        hdr = {"Authorization": f"token {git_token}", "Accept": "application/vnd.github+json",
               "User-Agent": "claude-worker"}
        safe_key = re.sub(r"[^A-Za-z0-9._-]+", "-", (ticket_key or "ticket")).strip("-").lower() or "ticket"
        head = f"claude/{safe_key}"

        def call(method: str, path: str, body: Optional[dict] = None) -> Any:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = urllib.request.Request(api + path, data=data, method=method,
                                         headers={**hdr, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}

        # Resolve the base branch: use the given one, but fall back to the repo's default
        # branch when it's blank or doesn't exist (handles main vs master across hubs).
        if not base_branch:
            base_branch = (call("GET", "") or {}).get("default_branch") or "main"
        try:
            base = call("GET", f"/git/ref/heads/{base_branch}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                base_branch = (call("GET", "") or {}).get("default_branch") or base_branch
                _trace(trace_file, f"api-push: base branch not found -> default '{base_branch}'")
                base = call("GET", f"/git/ref/heads/{base_branch}")
            else:
                raise
        base_sha = (base.get("object") or {}).get("sha")
        if not base_sha:
            raise RuntimeError(f"cannot read base branch '{base_branch}' SHA")
        try:
            call("POST", "/git/refs", {"ref": f"refs/heads/{head}", "sha": base_sha})
        except urllib.error.HTTPError as exc:
            if exc.code == 422:  # branch exists → point it back at base
                call("PATCH", f"/git/refs/heads/{head}", {"sha": base_sha, "force": True})
            else:
                raise
        for rel in files:
            with open(os.path.join(scratch, *rel.split("/")), "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            sha = None
            try:
                existing = call("GET", f"/contents/{rel}?ref={head}")
                sha = existing.get("sha") if isinstance(existing, dict) else None
            except urllib.error.HTTPError:
                pass  # new file
            payload = {"message": f"{ticket_key or 'ticket'}: add {rel}", "content": b64, "branch": head}
            if sha:
                payload["sha"] = sha
            call("PUT", f"/contents/{rel}", payload)
        _trace(trace_file, f"api-push: {len(files)} file(s) pushed to {owner}/{repo}@{head}")
        first = (summary or "automated hackathon").strip().splitlines()[0][:70] if summary else "hackathon"
        title = f"{ticket_key or 'Ticket'}: {first}"
        body = ((summary or "").strip()[:4000]) + "\n\n_Opened by the Claude Worker (API push — no clone)._"
        pr_number: Optional[int] = None
        url = ""
        try:
            pr = call("POST", "/pulls", {"title": title, "head": head, "base": base_branch, "body": body})
            if isinstance(pr, dict):
                pr_number, url = pr.get("number"), pr.get("html_url", "")
            _trace(trace_file, f"api-push PR created: {url}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 422:  # a PR for head->base already exists — find + reuse it
                try:
                    items = call("GET", f"/pulls?head={owner}:{head}&base={base_branch}&state=open")
                    if isinstance(items, list) and items:
                        pr_number, url = items[0].get("number"), items[0].get("html_url", "")
                except Exception:
                    pass
            if not pr_number:
                raise RuntimeError(f"open PR failed (HTTP {exc.code}): {detail[:200]}")
        # Auto-merge the hackathon PR. Non-fatal: branch protection / conflicts just leave it
        # open. GitHub computes mergeability async, so retry a few times; try squash then merge.
        if pr_number:
            merged = False
            for attempt in range(4):
                for method in ("squash", "merge"):
                    try:
                        call("PUT", f"/pulls/{pr_number}/merge", {"merge_method": method})
                        _trace(trace_file, f"api-push PR #{pr_number} auto-merged ({method})")
                        merged = True
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code == 405:   # this merge_method isn't allowed by the repo — try the other
                            continue
                        break                  # 405/409/422 mergeability not ready → wait + retry
                if merged:
                    break
                time.sleep(2)
            if not merged:
                _trace(trace_file, f"api-push PR #{pr_number} left OPEN (auto-merge unavailable / protected)")
        return url

    def _open_pr(self, workspace: str, repo_link: str, base_branch: str, git_token: str,
                 ticket_key: str, summary: str, trace_file: Optional[str]) -> str:
        """Commit Claude's edits on a new branch, push, and open a PR. Returns the PR URL.
        Requires a write+PR-scoped token. Raises on push/API failure (caught by caller)."""
        import urllib.request
        import urllib.error

        safe_key = re.sub(r"[^A-Za-z0-9._-]+", "-", (ticket_key or "ticket")).strip("-").lower() or "ticket"
        head = f"claude/{safe_key}"
        first_line = (summary or "automated change").strip().splitlines()[0][:70] if summary else "automated change"
        title = f"{ticket_key or 'Ticket'}: {first_line}"
        body = ((summary or "").strip()[:4000]) + "\n\n_Opened automatically by the Claude Worker._"

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        def _git(*a: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", "-C", workspace, *a], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120, env=env,
                                  stdin=subprocess.DEVNULL)

        _git("checkout", "-B", head)
        # -c author identity: a shallow clone may have no configured user.
        commit = _git("-c", "user.email=claude-worker@laidon.local", "-c", "user.name=Claude Worker",
                      "commit", "-m", title)
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr).lower():
            raise RuntimeError(f"git commit failed: {(commit.stderr or commit.stdout)[:200]}")

        push_url = self._auth_url(repo_link, git_token)
        # Retry the push on the machine's flaky DNS/network (same as clone); fail fast on
        # a real rejection (auth / non-fast-forward isn't possible here since we --force).
        _TRANSIENT = ("could not resolve host", "couldn't resolve host", "getaddrinfo",
                      "network is unreachable", "connection timed out", "connection reset",
                      "failed to connect", "timed out", "tls", "ssl")
        push = None
        for attempt in range(1, 4):
            push = _git("push", "--force", push_url, f"HEAD:refs/heads/{head}")
            if push.returncode == 0:
                break
            err = (push.stderr or push.stdout or "").replace(git_token, "***")
            if any(m in err.lower() for m in _TRANSIENT) and attempt < 3:
                _trace(trace_file, f"git push attempt {attempt} transient error -> retry: {err[:120]}")
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(f"git push failed (needs a write-scoped token): {err[:300]}")
        # GitLab uses a different SCM API (Merge Requests) and nested project paths; branch here.
        if self._is_gitlab(repo_link):
            _trace(trace_file, f"pushed branch '{head}'; opening MR -> {base_branch}")
            return self._open_mr_gitlab(repo_link, head, base_branch, title, body, git_token, trace_file)

        owner_repo = self._owner_repo(repo_link)
        if not owner_repo:
            raise RuntimeError(f"cannot parse owner/repo from repo_link '{repo_link}'")
        owner, repo = owner_repo
        _trace(trace_file, f"pushed branch '{head}'; opening PR {owner}/{repo} {head} -> {base_branch}")

        payload = json.dumps({"title": title, "head": head, "base": base_branch, "body": body}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}/pulls", data=payload, method="POST",
            headers={"Authorization": f"token {git_token}", "Accept": "application/vnd.github+json",
                     "User-Agent": "claude-worker", "Content-Type": "application/json"},
        )
        # POST with retry on transient connection/DNS errors (URLError that isn't an
        # HTTPError). HTTPError (4xx/5xx) is a real API response — handle it, don't retry.
        last_url_err = ""
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    url = (json.loads(resp.read().decode("utf-8")) or {}).get("html_url", "")
                    _trace(trace_file, f"PR created: {url}")
                    return url
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                # 422 usually means a PR for head->base already exists — find + return it.
                if exc.code == 422:
                    try:
                        find = urllib.request.Request(
                            f"https://api.github.com/repos/{owner}/{repo}/pulls?head={owner}:{head}&base={base_branch}&state=open",
                            headers={"Authorization": f"token {git_token}", "Accept": "application/vnd.github+json",
                                     "User-Agent": "claude-worker"},
                        )
                        with urllib.request.urlopen(find, timeout=60) as r2:
                            items = json.loads(r2.read().decode("utf-8")) or []
                            if items:
                                url = items[0].get("html_url", "")
                                _trace(trace_file, f"PR already existed (reused): {url}")
                                return url
                    except Exception:
                        pass
                raise RuntimeError(f"open PR failed (HTTP {exc.code}): {detail[:200]}")
            except urllib.error.URLError as exc:
                last_url_err = str(exc.reason)
                _trace(trace_file, f"PR API attempt {attempt}/3 network error -> retry: {last_url_err[:120]}")
                if attempt < 3:
                    time.sleep(1.5 * attempt)
                    continue
                raise RuntimeError(f"open PR failed (network): {last_url_err[:200]}")
        raise RuntimeError(f"open PR failed (network): {last_url_err[:200]}")

    def _open_mr_gitlab(self, repo_link: str, head: str, base_branch: str, title: str,
                        body: str, git_token: str, trace_file: Optional[str]) -> str:
        """Open a GitLab Merge Request via the API (branch already pushed). GitLab differs
        from GitHub: nested group paths (``group/sub/project``) and a Merge-Request endpoint
        keyed on the URL-encoded project path, authed with ``PRIVATE-TOKEN``. Returns the
        MR ``web_url``; reuses an existing open MR for the same source branch (idempotent)."""
        import urllib.request
        import urllib.error

        parts = urlparse(repo_link)
        host = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
        path = parts.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if not path:
            raise RuntimeError(f"cannot parse GitLab project path from repo_link '{repo_link}'")
        proj = quote(path, safe="")   # nested path -> single encoded id, e.g. group%2Fsub%2Fproj
        api = f"{host}/api/v4/projects/{proj}/merge_requests"
        hdr = {"PRIVATE-TOKEN": git_token, "Content-Type": "application/json",
               "User-Agent": "claude-worker", "Accept": "application/json"}
        payload = json.dumps({
            "source_branch": head, "target_branch": base_branch,
            "title": title, "description": body, "remove_source_branch": False,
        }).encode("utf-8")

        last_url_err = ""
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(api, data=payload, method="POST", headers=hdr)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    url = (json.loads(resp.read().decode("utf-8")) or {}).get("web_url", "")
                    _trace(trace_file, f"MR created: {url}")
                    return url
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                # 409/422 or "already exists" => an open MR for head->base exists; find + reuse it.
                if exc.code in (409, 422) or "already exists" in detail.lower():
                    try:
                        find = urllib.request.Request(
                            f"{host}/api/v4/projects/{proj}/merge_requests"
                            f"?source_branch={quote(head, safe='')}&state=opened",
                            headers={"PRIVATE-TOKEN": git_token, "User-Agent": "claude-worker",
                                     "Accept": "application/json"},
                        )
                        with urllib.request.urlopen(find, timeout=60) as r2:
                            items = json.loads(r2.read().decode("utf-8")) or []
                            if items:
                                url = items[0].get("web_url", "")
                                _trace(trace_file, f"MR already existed (reused): {url}")
                                return url
                    except Exception:
                        pass
                raise RuntimeError(f"open MR failed (HTTP {exc.code}): {detail[:200]}")
            except urllib.error.URLError as exc:
                last_url_err = str(exc.reason)
                _trace(trace_file, f"MR API attempt {attempt}/3 network error -> retry: {last_url_err[:120]}")
                if attempt < 3:
                    time.sleep(1.5 * attempt)
                    continue
                raise RuntimeError(f"open MR failed (network): {last_url_err[:200]}")
        raise RuntimeError(f"open MR failed (network): {last_url_err[:200]}")

    @staticmethod
    def _terminate_tree(proc: "subprocess.Popen") -> None:
        """Kill the whole subprocess tree. On Windows the CLI runs as `cmd /C claude …`,
        so killing `proc` (cmd.exe) leaves the real `claude.exe`/node grandchildren
        orphaned (they keep running, hold the temp clone, and burn a token session).
        `taskkill /T` kills the entire tree."""
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @staticmethod
    def _safe_rmtree(path: Optional[str]) -> None:
        if not path:
            return
        # Windows marks git's .git/objects read-only, so a plain rmtree leaves them
        # behind (temp leak). Chmod-and-retry on each failure clears them.
        def _onerror(func: Any, p: str, _exc: Any) -> None:
            try:
                import stat
                os.chmod(p, stat.S_IWRITE)
                func(p)
            except Exception:
                pass
        try:
            shutil.rmtree(path, onerror=_onerror)
        except Exception:
            pass

    @staticmethod
    def _parse_cli_json(stdout: str) -> Optional[dict[str, Any]]:
        """Parse the CLI's JSON. `--output-format json` is one object; be tolerant of
        leading warning lines by falling back to the last line that parses to a
        `type == "result"` object."""
        if not stdout:
            return None
        try:
            obj = json.loads(stdout)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict) and obj.get("type") == "result":
                return obj
        return None

    # -- helpers --------------------------------------------------------------

    def _mock_result(
        self, inputs: dict[str, Any], model: str, description: str,
    ) -> dict[str, Any]:
        """Deterministic, CLI-free result for the ``mock`` engine."""
        enough = len(description) >= 60

        input_tokens = max(1, len(description) // 4)
        output_tokens = 480 if enough else 120
        base: dict[str, Any] = {
            "model_used": f"{model} (MOCK)",
            "finish_reason": "mock",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": 0,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": _estimate_cost(model, input_tokens, output_tokens),
            "num_turns": 1,
            "raw_response": "[mock engine — no CLI was called]",
        }

        if not enough:
            base.update({
                "enough": False,
                "mismatches": [],
                "questions": [
                    "What are the concrete acceptance criteria for this ticket?",
                    "Which module / endpoint should the change live in?",
                    "Is there an existing pattern or test to follow?",
                ],
                "summary": "MOCK: the ticket is too thin to implement safely — blocking with questions "
                           "(in the real pipeline these get @-tagged on Jira and the ticket moves to Blocked).",
                "patch": "",
                "files_changed": [],
                "score": 0,
            })
            return base

        patch = (
            "diff --git a/app/api/health.py b/app/api/health.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/app/api/health.py\n"
            "@@\n"
            "+from fastapi import APIRouter\n"
            "+\n"
            "+router = APIRouter()\n"
            "+\n"
            "+\n"
            "+@router.get(\"/healthz\")\n"
            "+def healthz() -> dict[str, str]:\n"
            "+    return {\"status\": \"ok\"}\n"
        )
        base.update({
            "enough": True,
            "questions": [],
            "mismatches": [],
            "summary": "MOCK: implemented the ticket. Added a /healthz route (no auth, no DB) and a unit "
                       "test asserting 200 + {status: ok}. Smallest change meeting the acceptance criteria.",
            "patch": patch,
            "files_changed": ["app/api/health.py", "tests/test_health.py"],
            "score": 90,
        })
        return base

    def _build_user_prompt(self, inputs: dict[str, Any], description: str) -> str:
        parts: list[str] = []
        fields = self._extract_ticket_fields(description)
        key = self._as_text(fields.get("key")).strip()
        parts.append(f"# Ticket {key}".rstrip())

        # Toggle-driven: the require_* toggles decide WHICH ticket fields feed the prompt.
        # We include the CONTENT of each selected, extractable field (title/description).
        # The toggles' meaning is mode-specific (verify vs. implement-from) — see
        # node_types _TOGGLE_LABELS. If nothing is selectable, send the whole ticket.
        required = self._required_list(inputs)
        selected = [(name, fields.get(name)) for name in required if fields.get(name)]
        if selected:
            mode = self._as_text(inputs.get("mode")).strip().lower() or "both"
            if mode == "check":
                parts.append("Verify each of these ticket fields is present and unambiguous:")
            elif mode == "implement":
                parts.append("Base the implementation on these ticket fields:")
            else:
                parts.append("These ticket fields must be present, and are the basis for the work:")
            for name, val in selected:
                text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
                parts.append(f"## {name.capitalize()}\n{text}")
        else:
            parts.append(f"## Ticket content\n{description}")

        rulebook = self._as_text(inputs.get("rulebook")).strip()
        if rulebook:
            parts.append(f"## Coding Guidelines\n{rulebook}")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_result(text: str) -> dict[str, Any]:
        """Extract the JSON object the model was asked to return (tolerant of fences)."""
        cleaned = (text or "").strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1).strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass
        first, last = cleaned.find("{"), cleaned.rfind("}")
        if first != -1 and last != -1 and last > first:
            try:
                return json.loads(cleaned[first:last + 1])
            except (json.JSONDecodeError, ValueError):
                pass
        return {"enough": False, "questions": [], "summary": text, "patch": "", "files_changed": [], "score": 0}

    def _precheck_required(self, inputs: dict[str, Any], description: str) -> list[str]:
        """Deterministic (no-LLM) pre-check: which toggled-required fields are missing
        from the ticket. Known fields (title/description) are checked from the parsed
        ticket; unknown field names are left to Claude (not reported here)."""
        required = self._required_list(inputs)
        if not required:
            return []
        fields = self._extract_ticket_fields(description)
        missing: list[str] = []
        for name in required:
            if name not in fields:
                continue  # can't code-check this one — Claude will judge it
            val = fields[name]
            present = bool(val) and (not isinstance(val, str) or bool(val.strip()))
            if not present:
                missing.append(name)
        return missing

    @staticmethod
    def _extract_ticket_fields(description: str) -> dict[str, Any]:
        """Pull title/description out of the ticket content. ``description`` is usually
        the full Jira issue (a JSON string from ``get_issue.output.issue``); fall back to
        treating plain text as the description."""
        obj: Any = None
        if isinstance(description, str):
            try:
                obj = json.loads(description)
            except (json.JSONDecodeError, ValueError):
                obj = None
        elif isinstance(description, dict):
            obj = description
        out: dict[str, Any] = {}
        if isinstance(obj, dict):
            f = obj.get("fields") if isinstance(obj.get("fields"), dict) else {}
            out["key"] = obj.get("key") or obj.get("issue_key") or f.get("key")
            out["title"] = obj.get("summary") or obj.get("title") or f.get("summary")
            out["description"] = obj.get("description") or f.get("description")
        else:
            out["description"] = description if isinstance(description, str) else ""
        return out

    @staticmethod
    def _required_list(inputs: dict[str, Any]) -> list[str]:
        """Required ticket fields = every ``require_<field>`` toggle that is ON, mapped
        back to ``<field>`` (e.g. require_title=True -> 'title'). New toggles added with
        the same naming are picked up automatically. Also accepts a legacy
        ``required_fields`` list/string for backward compatibility."""
        out: list[str] = []
        for key, val in inputs.items():
            if not str(key).startswith("require_"):
                continue
            truthy = val is True or val == 1 or (isinstance(val, str) and val.strip().lower() in ("true", "1", "yes"))
            if truthy:
                out.append(str(key)[len("require_"):])
        legacy = inputs.get("required_fields")
        if isinstance(legacy, list):
            out += [str(x).strip() for x in legacy if str(x).strip()]
        elif isinstance(legacy, str):
            out += [p.strip() for p in legacy.replace("\n", ",").split(",") if p.strip()]
        return list(dict.fromkeys(out))  # dedupe, keep order

    @staticmethod
    def _looks_rate_limited(text: str) -> bool:
        t = text.lower()
        return any(m in t for m in _RATE_LIMIT_MARKERS)

    @staticmethod
    def _looks_auth_failure(text: str) -> bool:
        t = text.lower()
        return any(m in t for m in _AUTH_FAIL_MARKERS)

    @staticmethod
    def _as_text(value: Any) -> str:
        """Coerce any resolved input to text. Jira nodes (e.g. get_issue.output.issue)
        resolve to a dict/list — stringify those to JSON so downstream ``.strip()`` and
        prompt-building never hit ``'dict' has no attribute 'strip'``."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
