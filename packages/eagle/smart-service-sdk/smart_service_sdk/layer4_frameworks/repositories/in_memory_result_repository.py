from typing import Any

from smart_service_sdk.layer2_application.repositories.result_repository_interface import IResultRepository


class InMemoryResultRepository(IResultRepository):
    def __init__(self):
        self._results: dict[str, Any] = {}

    async def save_result(self, result: Any) -> Any:
        result_id = getattr(result, "id", None)
        if result_id is None:
            raise ValueError("result.id is required")
        self._results[result_id] = result
        return result
