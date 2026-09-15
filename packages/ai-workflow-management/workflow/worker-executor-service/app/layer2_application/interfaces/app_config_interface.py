"""Port for the application-config values layer 2 reads.

The concrete pydantic ``Settings`` singleton in layer4_frameworks/config
satisfies this structurally; bootstrap injects it. Keeps layer 2 free of a
direct framework import.
"""

from __future__ import annotations

from typing import Protocol


class IAppConfig(Protocol):
    CALLBACK_BASE_URL: str
    SERVICE_PORT: int
    TASK_TIMEOUT_SECONDS: int
    # Read by ExecuteTaskUseCase._task_timeout_for. Declared here so the port
    # names every value layer 2 actually reads; both are optional at runtime
    # (getattr with a default) so an older injected config still works.
    TASK_CALLBACK_GRACE_SECONDS: int
    TASK_TIMEOUT_MAX_SECONDS: int
