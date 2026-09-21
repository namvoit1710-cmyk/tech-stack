from typing import Optional, Protocol
from typing import Any, Dict, List, Optional, Tuple

class IWorkflowClient(Protocol):
    async def start_run(
        self,
        workflow_id: str,
        predefined_run_id: Optional[str] = None,
        input_data: Optional[Dict[str, Any]] = None,
        execution_mode: str = "auto",
    ) -> Dict[str, Any]:
        ...

    async def get_schema(self, run_id: str, node_id: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        ...

    async def get_data(
        self, run_id: str, node_id: str, side: str = "input", task_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        ...

    async def patch_data(
        self,
        run_id: str,
        node_id: str,
        artifacts: List[Dict[str, Any]],
        if_match: str,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...

    async def submit(self, run_id: str, node_id: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        ...

    async def submit_user_action(self, run_id: str, node_id: str, task_id: Optional[str] = None, artifacts: Optional[list[dict[str, Any]]] = None, params: Optional[dict[str, str]] = None) -> None:
        ...

    async def close(self) -> None:
        ...