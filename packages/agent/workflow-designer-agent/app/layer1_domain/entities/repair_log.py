from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepairAttempt:
    """Evidence for one validate -> classify -> repair iteration."""

    attempt: int
    error_code: str
    error_message: str
    repair_class: str  # RepairClass.value ("A" or "B")
    fixed: bool = False


@dataclass
class RepairLog:
    """Append-only, per-run log of every repair iteration (AC3 / AC4 evidence)."""

    attempts: list[RepairAttempt] = field(default_factory=list)

    def record(self, attempt: RepairAttempt) -> None:
        self.attempts.append(attempt)

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def last_error(self) -> str:
        return self.attempts[-1].error_message if self.attempts else ""

    def to_document(self) -> list[dict]:
        return [
            {
                "attempt": a.attempt,
                "error_code": a.error_code,
                "error_message": a.error_message,
                "repair_class": a.repair_class,
                "fixed": a.fixed,
            }
            for a in self.attempts
        ]
