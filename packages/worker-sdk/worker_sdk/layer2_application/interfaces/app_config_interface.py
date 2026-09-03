"""Port for the worker-identity config values layer 2 reads.

The concrete pydantic ``Settings`` singleton in layer4_frameworks/config
satisfies this structurally; bootstrap injects it. Keeps layer 2 free of a
direct framework import.
"""

from __future__ import annotations

from typing import Protocol


class IAppConfig(Protocol):
    WORKER_TYPE: str
    WORKER_VERSION: str
    SDK_VERSION: str
    WORKER_NAME: str
    WORKER_DESCRIPTION: str
    WORKER_NODE_CLASS: str
    WORKER_ICON: str
    WORKER_COLOR: str

    def get_tags_list(self) -> list[str]: ...
