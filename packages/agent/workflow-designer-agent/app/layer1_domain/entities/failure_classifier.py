from __future__ import annotations

from enum import Enum


class RepairClass(str, Enum):
    """Whether a failure is mechanically auto-fixable or needs a user-supplied value."""

    A = "A"  # mechanical / inferable — the repairer can fix it without the user
    B = "B"  # value only the user knows (URL / credential / threshold) — ask instead


# Deterministic ErrorCode -> Class A table (control-plane app/layer2_application/errors.py).
_CLASS_A_ERROR_CODES: frozenset[str] = frozenset({
    "VALIDATION_ERROR",
    "INVALID_WORKFLOW",
    "INVALID_NODE_DATA",
    "INVALID_EDGE_DATA",
    "INVALID_EDGE_REFERENCE",
    "NO_HANDLER",
    "NOTHING_TO_EDIT",
})

# Deterministic ErrorCode -> Class B table.
_CLASS_B_ERROR_CODES: frozenset[str] = frozenset({
    "SERVICE_NOT_CONFIGURED",
    "GATEWAY_SPEC_FETCH_FAILED",
    "GATEWAY_SPEC_INVALID",
})

# NODE_CONFIG_INVALID is Class A UNLESS the failing value is one only the user knows.
_USER_OWNED_KEYWORDS: tuple[str, ...] = (
    "url", "credential", "password", "secret", "api_key", "api key", "token", "threshold",
)

# Keyword fallback for an un-coded free-text message (no error_code at all).
_MECHANICAL_KEYWORDS: tuple[str, ...] = (
    "missing", "required", "duplicate", "not found", "invalid type",
    "unknown node", "isolated", "edge",
)

# SA-2132 — signatures of a NESTED-WORKFLOW failure. These surface at test-run straight from
# the control-plane's own exception text (CrossWorkflowCycleError / MaxDepthExceededError, and
# a WORKFLOW node with no target) and carry NO tabled ErrorCode, so without this they'd fall to
# the generic "provide the correct value" clarify. A cycle or an over-deep nest is a design
# choice only the user can change — never mechanically repairable — so they are terminal
# Class B. Matched on the failure message, so no node metadata is hardcoded here.
_NESTED_WORKFLOW_SIGNATURES: tuple[str, ...] = (
    "cross-workflow cycle",
    "already appears in the ancestry chain",
    "self-referenc",
    "max nesting depth",
    "nesting depth",
    "recursion depth",
    "missing config.workflow_id",
)


class FailureClassifier:
    """Deterministic (no-LLM) classifier of a validate/persist/dry-run/test-run failure.

    Classifies purely from the control-plane ``ErrorCode`` (or a domain
    ``WorkflowValidationError``) via a fixed table; regex/keyword matching is only a
    fallback for un-coded free-text. Defaults to Class B (fail-safe) when unsure — never
    guesses that an unclassifiable failure is auto-fixable.
    """

    @staticmethod
    def classify(
        error_code: str = "",
        message: str = "",
        error_details: dict | None = None,
        is_workflow_validation_error: bool = False,
    ) -> RepairClass:
        if is_workflow_validation_error:
            return RepairClass.A

        # SA-2132: a nested-workflow cycle / too-deep nest / missing target is terminal and
        # user-owned — classify B before the code tables so even a VALIDATION_ERROR-coded cycle
        # (which would otherwise be Class A) is never fed to the repairer to loop on forever.
        if FailureClassifier.nested_workflow_reason(error_code, message, error_details) is not None:
            return RepairClass.B

        code = (error_code or "").strip().upper()
        if code in _CLASS_A_ERROR_CODES:
            return RepairClass.A
        if code == "NODE_CONFIG_INVALID":
            return (
                RepairClass.B
                if FailureClassifier._points_at_user_owned_value(error_details, message)
                else RepairClass.A
            )
        if code in _CLASS_B_ERROR_CODES:
            return RepairClass.B
        if code:
            return RepairClass.B  # a coded but un-tabled error — fail safe

        # No error_code at all: fall back to a keyword scan of the free-text message.
        text = (message or "").lower()
        if any(keyword in text for keyword in _USER_OWNED_KEYWORDS):
            return RepairClass.B
        if any(keyword in text for keyword in _MECHANICAL_KEYWORDS):
            return RepairClass.A
        return RepairClass.B

    @staticmethod
    def nested_workflow_reason(
        error_code: str = "", message: str = "", error_details: dict | None = None,
    ) -> str | None:
        """SA-2132 — a specific, user-actionable explanation when a failure is a nested-workflow
        problem (a cycle, too-deep nesting, or no sub-workflow selected), else ``None``. Keyed on
        the control-plane's own runtime message text, so no node type/config metadata is baked
        into the agent. Used both to force Class B (never auto-repair a cycle) and to replace the
        generic clarify with one that names the actual cause."""
        haystack = " ".join([
            message or "",
            " ".join(str(v) for v in (error_details or {}).values()),
        ]).lower()
        if not any(sig in haystack for sig in _NESTED_WORKFLOW_SIGNATURES):
            return None
        detail = (message or "").strip()
        if "cycle" in haystack or "ancestry" in haystack or "self-referenc" in haystack:
            return (
                "This would make a workflow invoke itself — nested workflows can't form a cycle"
                + (f": {detail}" if detail else "")
                + ". Point the sub-workflow step at a different workflow."
            )
        if "depth" in haystack:
            return (
                "The nested workflows are chained too deeply"
                + (f": {detail}" if detail else "")
                + ". Reduce the nesting or invoke fewer sub-workflows."
            )
        return (
            "A sub-workflow step has no target workflow selected"
            + (f": {detail}" if detail else "")
            + ". Tell me which existing workflow it should invoke."
        )

    @staticmethod
    def _points_at_user_owned_value(error_details: dict | None, message: str) -> bool:
        haystack = " ".join([
            message or "",
            " ".join(str(v) for v in (error_details or {}).values()),
        ]).lower()
        return any(keyword in haystack for keyword in _USER_OWNED_KEYWORDS)
