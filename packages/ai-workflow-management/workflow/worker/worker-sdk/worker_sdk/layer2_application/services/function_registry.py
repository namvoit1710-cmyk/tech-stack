"""In-process registry for worker functions."""

from __future__ import annotations

from typing import Any

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction, WorkerFunctionDefinition


class FunctionRegistry:
    """Stores WorkerFunction instances keyed by name."""

    def __init__(self) -> None:
        self._functions: dict[str, WorkerFunction] = {}

    def register(self, func: WorkerFunction) -> None:
        self._functions[func.name] = func

    def get(self, name: str) -> WorkerFunction | None:
        return self._functions.get(name)

    def list_names(self) -> list[str]:
        return list(self._functions.keys())

    def list_definitions(self) -> list[WorkerFunctionDefinition]:
        return [f.to_definition() for f in self._functions.values()]

    async def invoke(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        func = self._functions.get(name)
        if func is None:
            raise KeyError(f"Unknown function: {name}")
        if func.handler is None:
            raise ValueError(f"Function '{name}' has no handler")
        return await func.handler(params)
