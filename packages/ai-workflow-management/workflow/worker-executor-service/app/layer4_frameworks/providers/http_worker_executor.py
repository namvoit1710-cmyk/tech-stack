"""HTTP-based worker executor — dispatches tasks to remote worker services."""

from typing import Any

import httpx

from app.layer2_application.interfaces.worker_executor_interface import IWorkerExecutor


class HttpWorkerExecutor(IWorkerExecutor):
    def __init__(self, worker_url: str) -> None:
        self._worker_url = worker_url.rstrip("/")

    def execute(self, task_data: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{self._worker_url}/api/v1/execute",
                json={
                    "task_id": task_data.get("task_id", ""),
                    "action": task_data.get("action", "execute"),
                    "inputs": task_data.get("inputs", {}),
                    "parameters": task_data.get("parameters", {}),
                    "correlation_id": task_data.get("correlation_id"),
                    "worker_type": task_data.get("worker_type", ""),
                },
            )
            response.raise_for_status()
            data = response.json()
            # Unwrap response envelope from ResponseWrapperMiddleware
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            return data

    def dispatch(self, task_data: dict[str, Any]) -> dict[str, Any]:
        """Fire-and-forget POST to /execute-async. Short timeout (10s) — just enough for acceptance."""
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{self._worker_url}/api/v1/execute-async",
                json={
                    "task_id": task_data.get("task_id", ""),
                    "action": task_data.get("action", "execute"),
                    "inputs": task_data.get("inputs", {}),
                    "parameters": task_data.get("parameters", {}),
                    "correlation_id": task_data.get("correlation_id"),
                    "callback_url": task_data.get("callback_url"),
                    "worker_type": task_data.get("worker_type", ""),
                },
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            return data
