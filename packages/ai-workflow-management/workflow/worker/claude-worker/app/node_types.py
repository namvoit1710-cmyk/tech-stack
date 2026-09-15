"""Multi-type node registry for the claude-worker (jira-worker / SA-1078 pattern).

One worker process exposes THREE node types that all share the same Claude engine
(the ``ExecuteTaskUseCase`` in ``execute_task/use_cases``). Behaviour is fixed per
node type via an injected ``mode`` — so there is NO ``mode`` field on the form:

    claude_check      -> mode=check      (verify required fields in code, then Claude
                                          judges ambiguity / conflict / mismatch)
    claude_implement  -> mode=implement  ([GitHub] clone repo@branch, Claude CLI edits it,
                                          return the real git diff; assumes approved)
    claude_code       -> mode=both       (single node: check + implement)

Handler contract (worker-sdk multi-type): ``(inputs, parameters) -> dict`` and RAISE
on failure. We reuse the engine's own logic and map a non-SUCCESS result to a raise.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from worker_sdk import NodeKind
from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition
from worker_sdk.layer4_frameworks.logger.standard_logger import StandardLogger
from worker_sdk.layer4_frameworks.providers.monitoring.prometheus_monitor import PrometheusMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus

import copy

from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    AGENTIC_CODER_INPUT_SCHEMA,
    AGENTIC_CODER_OUTPUT_SCHEMA,
    _CHECK_PROMPT,
    _IMPLEMENT_PROMPT,
    _BOTH_PROMPT,
)
from app.layer4_frameworks.config import settings

# Base form = the engine's schema WITHOUT the ``mode`` field (mode is fixed per type).
_BASE_INPUT_SCHEMA = [f for f in AGENTIC_CODER_INPUT_SCHEMA if f.get("key") != "mode"]
_OUTPUT_SCHEMA = AGENTIC_CODER_OUTPUT_SCHEMA


# Per-mode labels for the require_* toggles. The toggles pick WHICH ticket fields are
# fed into the prompt to Claude; the MEANING differs per node, so relabel for clarity:
#   check     -> the fields Claude must verify (present? unambiguous?)
#   implement -> the fields the implementation is based on
#   both      -> used for the check AND as the implementation basis
_TOGGLE_LABELS = {
    "check": {
        "require_title": "Verify field: Title",
        "require_description": "Verify field: Description",
    },
    "implement": {
        "require_title": "Implement from: Title",
        "require_description": "Implement from: Description",
    },
    "both": {
        "require_title": "Use field: Title",
        "require_description": "Use field: Description",
    },
}


# UI-only toggle (not read by the engine). Only on the CHECK node type — that's what the
# "Find Root Cause" node uses — so it doesn't appear on implement / check+implement.
_BTP_LOG_FIELD = {
    "key": "btp_log", "outputType": "boolean",
    "fieldConfig": {"fieldWrapper": "FormItemWrapper", "wrapperProps": {"label": "BTP log"},
                    "fieldControl": "SwitchControl"},
}


def _schema_for(default_prompt: str, mode: str) -> list:
    """Per-type input schema: same fields, but (a) the ``System Prompt`` field pre-filled
    with the mode-appropriate default, (b) the ``require_*`` toggles relabeled for this
    node's role (see ``_TOGGLE_LABELS``), and (c) the UI-only ``BTP log`` toggle added
    to the CHECK type only (used by Find Root Cause)."""
    labels = _TOGGLE_LABELS.get(mode, {})
    sch = copy.deepcopy(_BASE_INPUT_SCHEMA)
    for f in sch:
        key = f.get("key")
        if key == "system_prompt":
            f.setdefault("fieldConfig", {}).setdefault("controlProps", {})["value"] = default_prompt
        elif key in labels:
            f.setdefault("fieldConfig", {}).setdefault("wrapperProps", {})["label"] = labels[key]
    if mode == "check":
        # Place BTP log right BELOW the description toggle (require_description).
        idx = next((i for i, f in enumerate(sch) if f.get("key") == "require_description"), None)
        btp = copy.deepcopy(_BTP_LOG_FIELD)
        if idx is None:
            sch.append(btp)
        else:
            sch.insert(idx + 1, btp)
    return sch

# One shared engine instance for all three handlers.
_ENGINE = ExecuteTaskUseCase(logger=StandardLogger(), monitor=PrometheusMonitor())
_TAGS = ["ai", "agent", "coder", "claude", "cli", "local"]


def _error_output(err: str) -> Dict[str, Any]:
    """A graceful, schema-complete output for a failed run. Returned as SUCCESS (not
    raised) so the workflow keeps flowing to its comment node and the error lands on
    the Jira ticket instead of the run dying silently at this node. The failure is
    unmistakable in the payload: enough=false, error text in summary/questions/pr_error."""
    msg = (err or "unknown error").strip()
    return {
        "status": "FAILED",
        "enough": False,
        "questions": [f"ERROR: {msg}"],
        "mismatches": [],
        "summary": f"❌ Claude node failed: {msg}",
        "patch": "",
        "files_changed": [],
        "pr_url": "",
        "pr_error": msg,
        "score": 0,
        "model_used": "error",
        "finish_reason": "error",
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "total_tokens": 0, "cost_usd": 0.0, "num_turns": 0, "elapsed_ms": 0,
    }


def _run(mode: str, inputs: Optional[Dict[str, Any]], parameters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Run the shared engine with a fixed ``mode``. On ANY failure (exception or
    non-SUCCESS status) return a graceful error payload (as SUCCESS) so the workflow
    routes on to its Jira-comment node and the error is recorded on the ticket —
    rather than hard-failing the node with no trace on the ticket."""
    merged: Dict[str, Any] = {**(parameters or {}), **(inputs or {})}
    merged["mode"] = mode
    cmd = ExecuteTaskCommand(
        task_id=str(merged.get("__task_id") or "adhoc"),
        action="execute",
        inputs=merged,
        parameters={},
    )
    try:
        result = _ENGINE.execute(cmd)
    except Exception as exc:  # noqa: BLE001 — surface to Jira, don't crash the node
        return _error_output(str(exc))
    if result.status != TaskStatus.SUCCESS:
        return _error_output(result.error or "claude-worker execution failed")
    return result.outputs


def _check_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    return _run("check", inputs, parameters)


def _implement_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    return _run("implement", inputs, parameters)


def _code_handler(inputs: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    return _run("both", inputs, parameters)


def _nt(worker_type: str, name: str, description: str, icon: str, handler: Any,
        default_prompt: str, mode: str) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        worker_type=worker_type,
        name=name,
        description=description,
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon=icon,
        color=settings.WORKER_COLOR,
        tags=_TAGS,
        input_schema=_schema_for(default_prompt, mode),
        output_schema=_OUTPUT_SCHEMA,
        functions=[],
        handler=handler,
        kind=NodeKind.ACTION,
    )


# NOTE: the SMDG log-fetcher node types (request_action_log / submit_event_log) now
# live in their own standalone worker (../log-worker) — kept out of the claude-worker.
# The reqID extractor DOES live here (below) so it reuses the engine's token-rotating
# _call_claude_cli; the log-worker's copy was removed.

from app.extract_reqid import (
    make_extract_handler as _mk_extract,
    INPUT_SCHEMA as _EXTRACT_INPUT,
    OUTPUT_SCHEMA as _EXTRACT_OUTPUT,
)
from app.summarize_log import (
    make_summary_handler as _mk_summary,
    INPUT_SCHEMA as _SUMMARY_INPUT,
    OUTPUT_SCHEMA as _SUMMARY_OUTPUT,
)
from app.classify_message import (
    make_classify_handler as _mk_classify,
    INPUT_SCHEMA as _CLASSIFY_INPUT,
    OUTPUT_SCHEMA as _CLASSIFY_OUTPUT,
)
from app.gitlab_analyze import (
    make_gitlab_analyze_handler as _mk_gl_analyze,
    INPUT_SCHEMA as _GL_ANALYZE_INPUT,
    OUTPUT_SCHEMA as _GL_ANALYZE_OUTPUT,
)
from app.repro import (
    make_repro_handler as _mk_repro,
    INPUT_SCHEMA as _REPRO_INPUT,
    OUTPUT_SCHEMA as _REPRO_OUTPUT,
)

_SUMMARY_NT = NodeTypeDefinition(
    worker_type="summarize_log", name="SMDG: Summarize Log (Claude)",
    description="Reads the collected process logs and writes a BA-friendly summary: per "
                "failed validation step -> plain-language cause, owning team (NodeJS/ABAP) + "
                "service, and a next-step suggestion. Non-agentic; token-rotating CLI.",
    version=settings.WORKER_VERSION, node_class="TECHNICAL", icon="Sparkles",
    color=settings.WORKER_COLOR, tags=["smdg", "claude", "summary", "triage"],
    input_schema=_SUMMARY_INPUT, output_schema=_SUMMARY_OUTPUT,
    functions=[], handler=_mk_summary(_ENGINE), kind=NodeKind.ACTION,
)

_EXTRACT_NT = NodeTypeDefinition(
    worker_type="extract_reqid", name="SMDG: Extract reqID (Claude)",
    description="Reads a ticket's title + description via the Claude CLI and returns ONLY "
                "the reqID mentioned (e.g. CR0000018045). Non-agentic, no repo clone; reuses "
                "the engine's token-rotating CLI call; regex fallback.",
    version=settings.WORKER_VERSION, node_class="TECHNICAL", icon="Sparkles",
    color=settings.WORKER_COLOR, tags=["smdg", "claude", "extract", "reqid"],
    input_schema=_EXTRACT_INPUT, output_schema=_EXTRACT_OUTPUT,
    functions=[], handler=_mk_extract(_ENGINE), kind=NodeKind.ACTION,
)


_CLASSIFY_NT = NodeTypeDefinition(
    worker_type="classify_message",
    name="Chat: Classify Message (Claude)",
    description="Reads a chat message and returns ONE route key from a list configured on "
                "the form — the fuzzy part a SWITCH cannot do. Feed 'route' into a SWITCH "
                "and keep a default branch for 'unknown'. Non-agentic; token-rotating CLI.",
    version=settings.WORKER_VERSION, node_class="TECHNICAL", icon="Split",
    color=settings.WORKER_COLOR, tags=["chat", "claude", "classify", "intent", "router"],
    input_schema=_CLASSIFY_INPUT, output_schema=_CLASSIFY_OUTPUT,
    functions=[], handler=_mk_classify(_ENGINE), kind=NodeKind.ACTION,
)


_GL_ANALYZE_NT = NodeTypeDefinition(
    worker_type="gitlab_analyze",
    name="[GitLab] Claude Analyze (no clone)",
    description="Root-causes a ticket by READING the repo over the GitLab API at a given "
                "ref - plan search terms, fetch the matching files, then analyse against "
                "the FS rule. No clone: needs read_api only, and sidesteps the Windows "
                "'Filename too long' checkout that leaves a cloned tree empty. Returns a "
                "layer verdict (frontend/backend/data/config/not-a-bug) + the implicated "
                "files. Read-only; never writes.",
    version=settings.WORKER_VERSION, node_class="TECHNICAL", icon="Search",
    color=settings.WORKER_COLOR, tags=["smdg", "claude", "gitlab", "analyze", "rootcause"],
    input_schema=_GL_ANALYZE_INPUT, output_schema=_GL_ANALYZE_OUTPUT,
    functions=[], handler=_mk_gl_analyze(_ENGINE), kind=NodeKind.ACTION,
)


_REPRO_NT = NodeTypeDefinition(
    worker_type="claude_repro",
    name="[Live] Claude Reproduce (browser)",
    description="Reproduces a ticket on the RUNNING app: plans an action script from the "
                "ticket's steps, drives a real browser (login handled in code, password "
                "never in a prompt), probes the DOM for the thing Expected is about, and "
                "returns reproduced / inconclusive with the observation. Clicks that "
                "submit, save or activate are refused unless allow_mutating is on. This is "
                "the PROVE step a code-read root cause cannot replace.",
    version=settings.WORKER_VERSION, node_class="TECHNICAL", icon="MonitorPlay",
    color=settings.WORKER_COLOR, tags=["smdg", "claude", "browser", "reproduce", "verify"],
    input_schema=_REPRO_INPUT, output_schema=_REPRO_OUTPUT,
    functions=[], handler=_mk_repro(_ENGINE), kind=NodeKind.ACTION,
)


NODE_TYPES = [
    _CLASSIFY_NT,
    _EXTRACT_NT,
    _SUMMARY_NT,
    _GL_ANALYZE_NT,
    _REPRO_NT,
    _nt(
        "claude_check", "Claude: Check Ticket",
        "Checks a ticket: verifies required fields in code first, then uses the Claude "
        "CLI to judge ambiguity / conflicts / mismatches. No code changes.",
        "ClipboardCheck", _check_handler, _CHECK_PROMPT, "check",
    ),
    _nt(
        "claude_implement", "[GitHub] Claude Implement",
        "Clones the configured GitHub repo @ branch, implements the ticket via the local "
        "Claude CLI (cwd=hub for context), then returns the real git diff, files changed, "
        "self-score and usage. Assumes the ticket was already approved.",
        "Github", _implement_handler, _IMPLEMENT_PROMPT, "implement",
    ),
    _nt(
        "claude_code", "Claude: Check + Implement",
        "Single node — checks the ticket then implements it via the local Claude CLI.",
        "SquareTerminal", _code_handler, _BOTH_PROMPT, "both",
    ),
]
