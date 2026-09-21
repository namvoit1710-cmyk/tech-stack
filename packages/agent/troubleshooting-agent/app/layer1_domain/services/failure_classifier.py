"""Pure, deterministic classifier for Workflow Control Plane run failures.

Software 5.0 / deterministic-first: a rule engine over the execution-log reaches a
verdict with no LLM. Input is the `GET /api/v1/runs/{run_id}/execution-log` shape
(verified against the control-plane e2e test):

    { "summary": {"node_counts": {"failed": N, ...}},
      "events": [ {"event_type": "NodeFailed",
                   "payload": {"error_code": ..., "error_message": ...,
                               "node_id": ..., "run_id": ...}},
                  {"event_type": "NodeOutputProduced", ...},
                  {"event_type": "RunStatusChanged", "payload": {"status": "started"}} ] }
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.layer1_domain.entities.diagnostic_report import DiagnosticReport
from app.layer1_domain.value_objects.failure_category import (
    EXPLANATION,
    RECOMMENDED_FIX,
    FailureCategory,
)

# Signal → category. Order matters: first match wins (most specific first).
# Each entry: (category, tuple-of-substrings matched against the lowercased blob).
_SIGNALS: list[tuple[FailureCategory, tuple[str, ...]]] = [
    (FailureCategory.TIMEOUT_RETRY,
     ("timeout", "timed out", "deadline", "retries exhausted", "retry limit",
      "max retries", "etimedout", "did not callback in time", "callback in time",
      "did not respond", "no callback", "deadline exceeded")),
    (FailureCategory.AGENT_SERVICE_COMMUNICATION,
     ("http_5", "http 5", "status_code=5", "connection", "refused", "econnrefused",
      "unreachable", "unavailable", "grpc", "bad gateway", "service error", "network",
      "getaddrinfo", "name resolution", "name or service not known", "dns",
      "econnreset", "connection reset", "socket", "ssl", "certificate", "bad request to")),
    (FailureCategory.CREATION_CONFIGURATION,
     ("not configured", "misconfig", "invalid definition", "invalid config",
      "createmeta", "missing config", "missing required", "validationerror",
      "invalid workflow", "schema", "no credential", "credential found",
      "auth_type", "auth_token", "auth_username", "runtime_credentials",
      "unauthorized", "missing credential")),
    (FailureCategory.STATE_MANAGEMENT,
     ("stale", "aggregate", "checkpoint", "inconsistent state", "state mismatch",
      "state corrupt", "out of order")),
    (FailureCategory.STEP_EXECUTION,
     ("expression", "handler", "step execution", "node execution", "script",
      "keyerror", "typeerror", "valueerror", "nullpointer", "is undefined",
      "syntaxerror", "syntax error", "invalid character", "indexerror",
      "attributeerror", "importerror", "nameerror", "indentationerror",
      "zerodivision", "traceback")),
    (FailureCategory.TRIGGERING,
     ("trigger", "dispatch", "failed to start", "could not start", "webhook")),
]

_SEVERITY: dict[FailureCategory, str] = {
    FailureCategory.CREATION_CONFIGURATION: "medium",
    FailureCategory.TRIGGERING: "high",
    FailureCategory.STEP_EXECUTION: "high",
    FailureCategory.AGENT_SERVICE_COMMUNICATION: "high",
    FailureCategory.TIMEOUT_RETRY: "medium",
    FailureCategory.STATE_MANAGEMENT: "high",
    FailureCategory.UNKNOWN: "medium",
    FailureCategory.NO_FAILURE: "none",
}

_STARTED_STATES = {"started", "running", "in_progress"}
# Terminal-but-not-clean statuses engines emit; any of these means the run did not succeed.
_FAILED_STATES = {"failed", "error", "aborted", "timed_out", "timeout",
                  "canceled", "cancelled", "terminated", "killed"}

# Normalize away run-specific tokens so "the same issue" hashes to the same signature.
# ponytail: heuristic — masks uuids / long hex / numbers; upgrade to a template extractor
# if distinct errors ever collide.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.I)
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def error_signature(text: str) -> str:
    """Stable signature of a failure's error text, so identical issues → identical verdicts
    (cache key). Same input always yields the same hash — the basis for A==B consistency."""
    norm = (text or "").lower()
    norm = _UUID_RE.sub("#", norm)
    norm = _HEX_RE.sub("#", norm)
    norm = _NUM_RE.sub("#", norm)
    norm = _WS_RE.sub(" ", norm).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


# Mask secret-shaped substrings before run text leaves the tenant boundary (e.g. to an LLM).
# ponytail: heuristic set — labeled secrets, URL creds, known token prefixes, JWTs; extend if a
# new secret shape turns up in control-plane errors.
_URL_CRED_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s:/@]+@", re.I)
_LABELED_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret|credential|authorization)\b\s*[:=]\s*[\"']?[^\s\"',}]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{8,}|gh[oprsu]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|xox[baprs]-[A-Za-z0-9-]{8,})"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def redact_secrets(text: str) -> str:
    """Best-effort masking of secret-shaped substrings so run error text can be sent to an
    external LLM without leaking credentials. Preserves the error wording (the classification
    signal) — only the sensitive values become ***."""
    if not text:
        return text
    out = _URL_CRED_RE.sub(r"\g<scheme>***:***@", text)
    out = _LABELED_SECRET_RE.sub(lambda m: f"{m.group(1)}=***", out)
    out = _BEARER_RE.sub("bearer ***", out)
    out = _TOKEN_PREFIX_RE.sub("***", out)
    out = _JWT_RE.sub("***", out)
    return out


def _events(execution_log: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    out = []
    for e in execution_log.get("events") or []:
        if e.get("event_type") == event_type:
            out.append(e.get("payload") or {})
    return out


def _run_failed(execution_log: dict[str, Any]) -> bool:
    summary = execution_log.get("summary") or {}
    if (summary.get("node_counts") or {}).get("failed", 0):
        return True
    # The run record's free-text error is the authoritative failure signal (per SA-1438 design):
    # a terminal status like TIMED_OUT/CANCELED may carry no NodeFailed event.
    if str(execution_log.get("run_error", "")).strip():
        return True
    statuses = {str(p.get("status", "")).lower() for p in _events(execution_log, "RunStatusChanged")}
    # Live run record carries the verdict directly (events may not be persisted).
    statuses |= {str(execution_log.get("run_status", "")).lower(),
                 str(summary.get("status", "")).lower()}
    return bool(statuses & _FAILED_STATES)


def _run_started(execution_log: dict[str, Any]) -> bool:
    statuses = {str(p.get("status", "")).lower() for p in _events(execution_log, "RunStatusChanged")}
    return bool(statuses & _STARTED_STATES)


def classify(execution_log: dict[str, Any], run_id: str = "") -> DiagnosticReport:
    execution_log = execution_log or {}
    run_id = run_id or execution_log.get("run_id", "")
    failures = _events(execution_log, "NodeFailed")

    if not _run_failed(execution_log) and not failures:
        return DiagnosticReport(
            run_id=run_id,
            category=FailureCategory.NO_FAILURE,
            severity="none",
            root_cause="No failure detected: no failed nodes and no failed run status.",
            recommended_fix="",
            explanation=EXPLANATION[FailureCategory.NO_FAILURE],
            confidence=1.0,
        )

    run_error = str(execution_log.get("run_error", "")).strip()
    node_ids = [str(p.get("node_id")) for p in failures if p.get("node_id")]
    # Redact secrets on everything that becomes root_cause/evidence — these are logged and
    # returned in the API response, so masking must not be limited to the LLM path.
    evidence = [
        redact_secrets(
            f"NodeFailed[{p.get('node_id', '?')}]: {p.get('error_code', '')} — {p.get('error_message', '')}".strip()
        )
        for p in failures
    ]
    if run_error and not evidence:
        evidence = [redact_secrets(f"run error: {run_error}")]
    # Matching runs on the raw text (never egressed/logged) for maximum signal.
    blob = " ".join(
        [run_error] + [f"{p.get('error_code', '')} {p.get('error_message', '')}" for p in failures]
    ).lower()

    matched: FailureCategory | None = None
    for category, needles in _SIGNALS:
        if any(n in blob for n in needles):
            matched = category
            break

    # No node-level failure signal and no error text but the run failed → never triggered/started.
    if matched is None and not failures and not run_error and not _run_started(execution_log):
        matched = FailureCategory.TRIGGERING

    if matched is None:
        primary = redact_secrets(failures[0].get("error_message") if failures else (run_error or "run failed"))
        return DiagnosticReport(
            run_id=run_id,
            category=FailureCategory.UNKNOWN,
            severity=_SEVERITY[FailureCategory.UNKNOWN],
            root_cause=f"Run failed but no known signal matched (first error: {primary}).",
            recommended_fix=RECOMMENDED_FIX[FailureCategory.UNKNOWN],
            explanation=EXPLANATION[FailureCategory.UNKNOWN],
            confidence=0.3,
            failed_node_ids=node_ids,
            evidence=evidence or ["run failed with no NodeFailed event"],
        )

    primary_msg = redact_secrets(failures[0].get("error_message") if failures else (run_error or "run did not start"))
    return DiagnosticReport(
        run_id=run_id,
        category=matched,
        severity=_SEVERITY[matched],
        root_cause=f"{matched.value}: {primary_msg}",
        recommended_fix=RECOMMENDED_FIX[matched],
        explanation=EXPLANATION[matched],
        confidence=0.85,
        failed_node_ids=node_ids,
        evidence=evidence or ["run failed before any node ran"],
    )
