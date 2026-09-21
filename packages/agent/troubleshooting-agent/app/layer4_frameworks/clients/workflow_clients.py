import logging
from typing import Any

import httpx

from app.layer2_application.interfaces.workflow_client_interface import IWorkflowClient

logger = logging.getLogger(__name__)

# Offline/mock execution-log: a realistic failed run (worker HTTP 500 → comms failure),
# mirroring the control-plane e2e fixture, so the agent is demoable with no live service.
_MOCK_FAILED_RUN: dict[str, Any] = {
    "summary": {"node_counts": {"failed": 1, "succeeded": 0, "running": 0}},
    "events": [
        {"event_type": "RunStatusChanged",
         "payload": {"run_id": "mock-run", "status": "started"}},
        {"event_type": "NodeFailed",
         "payload": {"node_id": "task-1", "task_id": "t-1",
                     "error_code": "HTTP_500", "error_message": "HTTP 500"}},
        {"event_type": "NodeOutputProduced",
         "payload": {"state_updates": {"task-1": {"status_code": 500}}}},
    ],
}


def _unwrap(payload: Any) -> dict[str, Any]:
    """Control plane wraps success responses as ``{"status": "ok", "data": {...}}``.
    Return the inner ``data`` (or the payload itself if it isn't wrapped)."""
    if isinstance(payload, dict) and "data" in payload and "status" in payload:
        return payload.get("data") or {}
    return payload if isinstance(payload, dict) else {}


class WorkflowClient(IWorkflowClient):
    """Read-only client over the Workflow Control Plane. Diagnose-only: it never calls
    a mutating endpoint. With no base URL it returns an empty log (honest degrade) unless
    ``use_mock`` is set, which serves a canned failed-run for offline demos/tests."""

    def __init__(self, base_url: str = "", use_mock: bool = False):
        self._base_url = base_url.rstrip("/")
        self._use_mock = use_mock

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> dict[str, Any]:
        try:
            response = await client.get(f"{self._base_url}{path}", params=params)
            response.raise_for_status()
            return _unwrap(response.json())
        except Exception as exc:  # read-only: degrade gracefully, never raise into the flow
            logger.warning("GET %s failed: %s", path, exc)
            return {}

    async def resolve_run_id(self, workflow_id: str = "", workflow_name: str = "") -> str:
        """Find a run to diagnose when the caller only knows the workflow (id or name).
        Prefers the most recent FAILED run, falling back to the most recent run.
        ponytail: scans the latest 100 runs; widen the page if a target can be older."""
        if not self._base_url or not (workflow_id or workflow_name):
            return ""

        async with httpx.AsyncClient(timeout=30.0) as client:
            base_params: dict[str, Any] = {"$top": 100, "$orderby": "started_at desc"}
            if workflow_id:
                base_params["workflow_id"] = workflow_id

            # Prefer failed runs; fall back to any status if none failed.
            for status in ("FAILED", None):
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
                    # Newest first — trust $orderby, but sort defensively on started_at.
                    items.sort(key=lambda r: str(r.get("started_at", "")), reverse=True)
                    run_id = items[0].get("id") or items[0].get("run_id") or ""
                    logger.info("Resolved run %s for workflow_id=%r name=%r (status=%s)",
                                run_id, workflow_id, workflow_name, status or "any")
                    return run_id
        logger.warning("No run found for workflow_id=%r name=%r", workflow_id, workflow_name)
        return ""

    async def get_execution_log(
        self, run_id: str, offset: int = 0, include_children: bool = True
    ) -> dict[str, Any]:
        if not self._base_url:
            # No control plane configured: return fake data only when explicitly opted in,
            # otherwise degrade to an empty log so a misconfigured deploy fails loudly.
            return {**_MOCK_FAILED_RUN, "run_id": run_id} if self._use_mock else {}

        async with httpx.AsyncClient(timeout=30.0) as client:
            log = await self._get(
                client,
                f"/api/v1/runs/{run_id}/execution-log",
                {"offset": offset, "include_children": str(include_children).lower()},
            )
            # This WCP instance doesn't always persist granular NodeFailed events; the
            # authoritative failure signal is the free-text `error` on the run record.
            # Enrich the log with run status + error so the diagnoser has something to read.
            run = await self._get(client, f"/api/v1/runs/{run_id}", {})
            status = run.get("status")
            if status:
                log["run_status"] = status
                log.setdefault("summary", {}).setdefault("status", status)
            if run.get("error"):
                log["run_error"] = run["error"]
            log.setdefault("run_id", run_id)
            return log
