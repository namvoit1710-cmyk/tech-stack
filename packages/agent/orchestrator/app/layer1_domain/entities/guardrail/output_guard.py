"""
Output Guard — validates and sanitizes agent responses before delivery.

Checks:
  1. Schema validation (required fields: message, status)
  2. PII masking
  3. Content policy enforcement
  4. Grounding check (Phase 6 — disabled initially)
"""

import re
from dataclasses import dataclass, field


@dataclass
class OutputGuardResult:
    """Result of output guard validation."""
    passed: bool
    sanitized_response: dict
    confidence_score: float = 1.0
    warnings: list[str] = field(default_factory=list)
    rejection_reason: str | None = None


# PII patterns (same as input guard)
_PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
}

# Content policy patterns for output
_OUTPUT_POLICY_PATTERNS = [
    re.compile(r"(password|secret|token)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"(api[_-]?key|secret[_-]?key)\s*[:=]\s*['\"]?\w{20,}", re.IGNORECASE),
]

# Agents that produce LLM-generated content (subject to grounding checks)
_LLM_BOUND_AGENTS = frozenset({
    "node_advisor",
    "worker_assistant",
    "workflow_explainer",
    "workflow_selector",
})


class OutputGuard:
    """Validates agent responses before delivery to the user."""

    def __init__(self, response_validator, config: dict, logger):
        self._response_validator = response_validator
        self._config = config
        self._logger = logger
        self._pii_enabled = config.get("pii_masking_enabled", True)
        self._content_policy_enabled = config.get("content_policy_enabled", True)
        self._grounding_enabled = config.get("grounding_enabled", False)

    def validate(self, agent_result: dict, intent: object = None, agent_id: str = "") -> OutputGuardResult:
        """
        Run all output guard checks on the agent response.

        Returns OutputGuardResult with passed=True if response is safe.
        """
        warnings: list[str] = []

        # 1. Schema validation — required fields
        if not self._validate_schema(agent_result):
            return OutputGuardResult(
                passed=False,
                sanitized_response=agent_result,
                rejection_reason="Response missing required fields (message, status)",
            )

        sanitized = dict(agent_result)

        # 2. PII masking on response message
        if self._pii_enabled:
            message = sanitized.get("message", "")
            masked_message, pii_warnings = self._mask_pii(message)
            sanitized["message"] = masked_message
            warnings.extend(pii_warnings)

        # 3. Content policy check
        if self._content_policy_enabled:
            if not self._check_content_policy(sanitized.get("message", "")):
                self._logger.warning(
                    "Output guard: content policy violation in agent response",
                    agent_id=agent_id,
                )
                return OutputGuardResult(
                    passed=False,
                    sanitized_response=sanitized,
                    rejection_reason="Response contains policy-violating content",
                )

        # 4. Grounding check (Phase 6 — only for LLM-bound agents)
        confidence_score = 1.0
        if self._grounding_enabled and agent_id in _LLM_BOUND_AGENTS:
            confidence_score, grounding_warnings = self._grounding_check(
                agent_result, agent_id,
            )
            warnings.extend(grounding_warnings)
            if confidence_score < 0.5:
                return OutputGuardResult(
                    passed=False,
                    sanitized_response=sanitized,
                    confidence_score=confidence_score,
                    warnings=warnings,
                    rejection_reason=f"Grounding confidence too low: {confidence_score:.2f}",
                )

        return OutputGuardResult(
            passed=True,
            sanitized_response=sanitized,
            confidence_score=confidence_score,
            warnings=warnings,
        )

    def _validate_schema(self, result: dict) -> bool:
        """Check that response contains required fields."""
        return "message" in result and "status" in result

    def _mask_pii(self, text: str) -> tuple[str, list[str]]:
        """Mask PII in text and return (sanitized_text, warnings)."""
        warnings = []
        sanitized = text

        for pii_type, pattern in _PII_PATTERNS.items():
            matches = pattern.findall(sanitized)
            if matches:
                warnings.append(f"PII masked in output: {pii_type} ({len(matches)} instance(s))")
                sanitized = pattern.sub(f"[{pii_type.upper()}_REDACTED]", sanitized)

        return sanitized, warnings

    def _check_content_policy(self, text: str) -> bool:
        """Check response against content policy. Returns True if clean."""
        for pattern in _OUTPUT_POLICY_PATTERNS:
            if pattern.search(text):
                return False
        return True

    def _grounding_check(self, result: dict, agent_id: str) -> tuple[float, list[str]]:
        """Run grounding validation for LLM-bound agents."""
        if self._response_validator is None:
            return 1.0, []
        return 1.0, []
