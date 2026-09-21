from typing import Any, Protocol

from agent_sdk.layer1_domain.entities.shared_state import SharedStateRecord


class ISharedStateRepository(Protocol):
    def load(self, key: str) -> SharedStateRecord | None: ...

    def get(self, key: str) -> SharedStateRecord | None: ...

    def delete(self, key: str) -> bool: ...

    def save(self, record: SharedStateRecord) -> SharedStateRecord: ...

    def compare_and_set(
        self,
        key: str,
        state: dict[str, Any],
        expected_version: int,
    ) -> SharedStateRecord | None: ...

    def acquire_lock(
        self,
        key: str,
        owner: str,
        ttl_seconds: int,
    ) -> SharedStateRecord | None: ...

    def release_lock(self, key: str, owner: str) -> bool: ...
