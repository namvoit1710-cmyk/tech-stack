from typing import Any, Optional, Protocol


class ISharedStateRepository(Protocol):
    def save_state(self, workflow_id: str, state: dict[str, Any]) -> dict[str, Any]:
        ...

    def get_state(self, workflow_id: str) -> Optional[dict[str, Any]]:
        ...
