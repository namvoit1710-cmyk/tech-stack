"""Port for the few application-config values layer 2 needs to read.

The concrete pydantic ``Settings`` singleton in layer4_frameworks/config
satisfies this structurally; bootstrap injects it. Keeping the dependency
as a Protocol lets layer 2 read config without importing the framework
layer (and tests can pass the same singleton or a stub).
"""

from __future__ import annotations

from typing import Protocol


class IAppConfig(Protocol):
    GENERATION_GUARD_MODE: str
    API_GATEWAY_URL: str
    ORPHAN_GC_ENABLED: bool
