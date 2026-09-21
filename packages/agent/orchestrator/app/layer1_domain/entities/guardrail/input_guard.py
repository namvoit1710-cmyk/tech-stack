"""
Input Guard — validates and sanitizes user messages before orchestration.

Checks:
  1. Message length validation
  2. Prompt injection detection (pattern-based)
  3. PII masking (email, phone, SSN, credit card)
  4. Content policy enforcement
"""

import re
from dataclasses import dataclass, field


@dataclass
class InputGuardResult:
    """Result of input guard validation."""
    passed: bool
    sanitized_message: str
    warnings: list[str] = field(default_factory=list)
    rejection_reason: str | None = None


# Prompt injection patterns — common LLM manipulation attempts
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a|an|if)\s+", re.IGNORECASE),
    re.compile(r"new\s+instruction[s]?\s*:", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>", re.IGNORECASE),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.IGNORECASE),
    re.compile(r"override\s+(system|safety|content)\s+(prompt|policy|filter)", re.IGNORECASE),
    re.compile(r"reveal\s+(your|the|system)\s+(prompt|instructions?|rules?)", re.IGNORECASE),
]

# PII patterns for masking
_PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
}

# Content policy — blocked content categories
_BLOCKED_CONTENT_PATTERNS = [
    re.compile(r"\b(hack|exploit|attack|breach)\s+(the|this|a)\s+(system|server|network|database)", re.IGNORECASE),
    re.compile(r"(sql\s+injection|xss\s+attack|buffer\s+overflow)", re.IGNORECASE),
    re.compile(r"(generate|create|write)\s+(malware|virus|ransomware|trojan)", re.IGNORECASE),
]


class InputGuard:
    """Validates and sanitizes user input before orchestration processing."""

    def __init__(self, config: dict, logger):
        self._config = config
        self._logger = logger
        self._max_length = config.get("max_message_length", 10000)
        self._injection_enabled = config.get("injection_detection_enabled", True)
        self._pii_enabled = config.get("pii_masking_enabled", True)

    def check(self, message: str, user_id: str = "") -> InputGuardResult:
        """
        Run all input guard checks on the message.

        Returns InputGuardResult with passed=True if message is safe,
        or passed=False with rejection_reason if blocked.
        """
        warnings: list[str] = []

        # 1. Length validation
        if not message or not message.strip():
            return InputGuardResult(
                passed=False,
                sanitized_message="",
                rejection_reason="Empty message",
            )

        if len(message) > self._max_length:
            return InputGuardResult(
                passed=False,
                sanitized_message="",
                rejection_reason=f"Message exceeds maximum length of {self._max_length} characters",
            )

        # 2. Prompt injection detection
        if self._injection_enabled:
            injection_match = self._detect_injection(message)
            if injection_match:
                self._logger.warning(
                    "Input guard: prompt injection detected",
                    user_id=user_id,
                    pattern=injection_match,
                )
                return InputGuardResult(
                    passed=False,
                    sanitized_message="",
                    rejection_reason="Prompt injection detected",
                )

        # 3. Content policy check
        policy_violation = self._check_content_policy(message)
        if policy_violation:
            self._logger.warning(
                "Input guard: content policy violation",
                user_id=user_id,
                violation=policy_violation,
            )
            return InputGuardResult(
                passed=False,
                sanitized_message="",
                rejection_reason="Content policy violation",
            )

        # 4. PII masking (sanitize, don't reject)
        sanitized = message
        if self._pii_enabled:
            sanitized, pii_warnings = self._mask_pii(message)
            warnings.extend(pii_warnings)

        return InputGuardResult(
            passed=True,
            sanitized_message=sanitized,
            warnings=warnings,
        )

    def _detect_injection(self, message: str) -> str | None:
        """Check message against known prompt injection patterns."""
        for pattern in _INJECTION_PATTERNS:
            match = pattern.search(message)
            if match:
                return match.group()
        return None

    def _mask_pii(self, text: str) -> tuple[str, list[str]]:
        """Mask PII in text and return (sanitized_text, warnings)."""
        warnings = []
        sanitized = text

        for pii_type, pattern in _PII_PATTERNS.items():
            matches = pattern.findall(sanitized)
            if matches:
                warnings.append(f"PII detected and masked: {pii_type} ({len(matches)} instance(s))")
                sanitized = pattern.sub(f"[{pii_type.upper()}_MASKED]", sanitized)

        return sanitized, warnings

    def _check_content_policy(self, message: str) -> str | None:
        """Check message against content policy rules."""
        for pattern in _BLOCKED_CONTENT_PATTERNS:
            match = pattern.search(message)
            if match:
                return match.group()
        return None
