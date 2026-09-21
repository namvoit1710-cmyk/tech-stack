from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass
class RepairDecision:
    should_repair: bool
    next_attempt: int = 0


class RepairPolicy:
    """Bounds the auto-repair loop by a max attempt count.

    Mirrors the shape of worker-executor-service's ``RetryPolicy`` (max-attempts +
    deterministic ``evaluate``) as a separate instance for the designer agent — no
    cross-service import. No backoff: a repair round is a one-shot LLM fix, not a
    network retry, so there is nothing to delay.
    """

    DEFAULT_MAX_ATTEMPTS: ClassVar[int] = 3

    def __init__(self, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        self._max_attempts = max_attempts

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def evaluate(self, attempts_used: int) -> RepairDecision:
        """Decide whether another repair attempt fits within the budget."""
        if attempts_used >= self._max_attempts:
            return RepairDecision(should_repair=False)
        return RepairDecision(should_repair=True, next_attempt=attempts_used + 1)
