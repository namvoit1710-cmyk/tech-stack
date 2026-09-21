from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict


class SharedStateDocument(TypedDict, total=False):
    key: str
    state: dict[str, Any]
    version: int
    updated_at: str
    lock_owner: NotRequired[str | None]
    lock_acquired_at: NotRequired[str | None]
    lock_expires_at: NotRequired[str | None]


@dataclass(frozen=True)
class SharedStateLockInfo:
    owner: str
    acquired_at: str
    expires_at: str


@dataclass(frozen=True)
class SharedStateRecord:
    key: str
    state: dict[str, Any]
    version: int = 0
    updated_at: str = ""
    lock: SharedStateLockInfo | None = None
