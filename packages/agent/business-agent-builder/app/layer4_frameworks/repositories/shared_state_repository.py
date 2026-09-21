from typing import Any, Optional

from app.layer2_application.interfaces.shared_state_repository_interface import (
    ISharedStateRepository,
)


class SharedStateRepository(ISharedStateRepository):
    def save_state(self, workflow_id: str, state: dict[str, Any]) -> dict[str, Any]:
        # TODO: Implement logic of saving state to database
        return state

    def get_state(self, workflow_id: str) -> Optional[dict[str, Any]]:
        # TODO: Implement logic of getting state from database
        return None
