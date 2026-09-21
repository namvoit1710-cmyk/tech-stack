from __future__ import annotations

from typing import Any, Dict, Optional, Protocol


class IWorkflowNodeGateway(Protocol):
    """Port for workflow node input-data lookup and update."""

    async def get_table_field_key(self, run_id: str, node_id: str) -> Optional[str]:
        """Return table-wrapper field key if node expects table input."""

    async def assign_file_to_node(
        self,
        run_id: str,
        node_id: str,
        file_id: str,
        target_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Assign a file to node input and return response payload."""
