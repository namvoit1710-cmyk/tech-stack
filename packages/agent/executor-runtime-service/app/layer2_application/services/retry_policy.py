"""
RetryPolicy — bounded retry with exponential backoff + full jitter for step execution.

Operates purely on StepResult: it retries an attempt only while the returned result is an
ERROR flagged `retryable` and the attempt budget is not exhausted. Business failures and
config errors (retryable=False) return immediately. `sleep` and `rand` are injectable so the
backoff schedule is fully deterministic under test (no real waiting).

MAX_ATTEMPTS = 1 disables retry entirely (reproduces the pre-SA-1716 behavior).
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable

from app.layer1_domain.entities.step import StepResult, StepStatus
from app.layer2_application.interfaces.observability import ILogger


class RetryPolicy:
    def __init__(
        self,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_cap_seconds: float = 30.0,
        jitter: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        logger: ILogger | None = None,
    ) -> None:
        self._max_attempts = max(1, int(max_attempts))
        self._base = backoff_base_seconds
        self._cap = backoff_cap_seconds
        self._jitter = jitter
        self._sleep = sleep
        self._rand = rand
        self._logger = logger

    def _backoff(self, attempt: int) -> float:
        """Delay before the (attempt+1)-th try. Exponential, capped, full-jitter."""
        delay = min(self._base * (2 ** (attempt - 1)), self._cap)
        if self._jitter:
            delay = self._rand() * delay  # full jitter: uniform in [0, delay)
        return delay

    async def run(
        self, attempt: Callable[[], Awaitable[StepResult]], *, step_id: str = ""
    ) -> StepResult:
        last: StepResult | None = None
        for n in range(1, self._max_attempts + 1):
            result = await attempt()
            if result.status != StepStatus.ERROR or not result.retryable:
                return result  # success or terminal failure — done
            last = result
            if n >= self._max_attempts:
                break
            delay = self._backoff(n)
            if self._logger is not None:
                self._logger.warning(
                    "Retrying step after transient failure",
                    step_id=step_id or result.step_id,
                    attempt=n,
                    max_attempts=self._max_attempts,
                    backoff_seconds=round(delay, 3),
                    error=result.error,
                )
            await self._sleep(delay)

        assert last is not None  # loop runs >=1 time; only reached on retryable ERROR
        if self._max_attempts == 1:
            return last  # retry disabled — pass the single result through unchanged
        return StepResult(
            step_id=last.step_id,
            status=StepStatus.ERROR,
            error=(
                f"Retry budget exhausted after {self._max_attempts} attempt(s): {last.error}"
            ),
            retryable=False,
        )
