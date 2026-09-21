from __future__ import annotations

import logging
from typing import Any

import httpx

from app.layer2_application.interfaces.workflow_run_reader_interface import (
    IWorkflowRunReader,
)

logger = logging.getLogger(__name__)


def _unwrap(payload: Any) -> dict[str, Any]:
    """Control plane wraps success as {"status": "ok", "data": {...}} — return the inner data."""
    if isinstance(payload, dict) and "data" in payload and "status" in payload:
        return payload.get("data") or {}
    return payload if isinstance(payload, dict) else {}


class HttpWorkflowRunReader(IWorkflowRunReader):
    """Read-only client over the Workflow Control Plane, for diagnose-and-repair on the
    designer side. Same endpoints as the troubleshooting-agent's WorkflowClient
    (`/api/v1/runs`, `/api/v1/runs/{id}/execution-log`). Degrades to empty on any error —
    never raises into the design flow.
    """

    def __init__(self, base_url: str = "", client: httpx.AsyncClient | None = None,
                 timeout: float = 30.0) -> None:
        self._base = (base_url or "").rstrip("/")
        self._client = client
        self._timeout = timeout

    def _acquire(self) -> tuple[Any, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=self._timeout), True

    async def _get(self, client: Any, path: str, params: dict) -> dict[str, Any]:
        try:
            resp = await client.get(f"{self._base}{path}", params=params)
            resp.raise_for_status()
            return _unwrap(resp.json())
        except Exception as exc:  # read-only: degrade, never raise into the flow
            logger.warning("run-reader GET %s failed: %s", path, exc)
            return {}

    async def resolve_run_id(self, workflow_id: str = "", workflow_name: str = "") -> str:
        if not self._base or not (workflow_id or workflow_name):
            return ""
        client, owns = self._acquire()
        try:
            base_params: dict[str, Any] = {"$top": 100, "$orderby": "started_at desc"}
            if workflow_id:
                base_params["workflow_id"] = workflow_id
            for status in ("FAILED", None):  # prefer a failed run, fall back to any
                params = dict(base_params)
                if status:
                    params["status"] = status
                items = (await self._get(client, "/api/v1/runs", params)).get("items") or []
                if workflow_name:
                    items = [
                        r for r in items
                        if str(r.get("workflow_name", "")).lower() == workflow_name.lower()
                    ]
                if items:
                    items.sort(key=lambda r: str(r.get("started_at", "")), reverse=True)
                    return items[0].get("id") or items[0].get("run_id") or ""
        finally:
            if owns:
                await client.aclose()
        return ""

    async def get_execution_log(
        self, run_id: str, offset: int = 0, include_children: bool = True
    ) -> dict[str, Any]:
        if not self._base:
            return {}
        client, owns = self._acquire()
        try:
            log = await self._get(
                client, f"/api/v1/runs/{run_id}/execution-log",
                {"offset": offset, "include_children": str(include_children).lower()},
            )
            run = await self._get(client, f"/api/v1/runs/{run_id}", {})
            status = run.get("status")
            if status:
                log["run_status"] = status
                log.setdefault("summary", {}).setdefault("status", status)
            if run.get("error"):
                log["run_error"] = run["error"]
            if run.get("workflow_id"):
                log.setdefault("workflow_id", run["workflow_id"])
            log.setdefault("run_id", run_id)
            return log
        finally:
            if owns:
                await client.aclose()
