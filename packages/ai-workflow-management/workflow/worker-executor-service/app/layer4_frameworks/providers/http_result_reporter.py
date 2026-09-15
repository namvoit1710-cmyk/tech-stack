"""HTTP result reporter — calls back to WCP to complete/fail tasks."""

import logging
from typing import Any

import httpx

from app.layer2_application.interfaces.result_reporter_interface import IResultReporter

logger = logging.getLogger(__name__)


class HttpResultReporter(IResultReporter):
    def __init__(self, wcp_base_url: str) -> None:
        base = wcp_base_url.rstrip("/")
        # Ensure /api/v1 prefix is present for WCP routes
        if not base.endswith("/api/v1"):
            base = f"{base}/api/v1"
        self._wcp_base_url = base

    def report_completed(
        self,
        run_id: str,
        task_id: str,
        output_reference: str = "",
        output_metadata: dict[str, Any] | None = None,
        run_generation: str = "",
        idempotency_key: str = "",
    ) -> dict:
        url = f"{self._wcp_base_url}/runs/{run_id}/tasks/{task_id}/complete"
        body = {
            "output_reference": output_reference,
            "output_metadata": output_metadata or {},
            "status": "COMPLETED",
        }
        # SA-2032: stable fence on the sync path (only when supplied → push unchanged).
        if run_generation:
            body["run_generation"] = run_generation
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        # SA-1941 — never log the full body: ``output_metadata`` carries the
        # worker's (possibly large) inline output, so logging it duplicates the
        # whole payload in RAM as a string AND floods the logs. Log the ref +
        # a top-level field count only (byte size isn't free here without an
        # extra serialize; the broker clients WARN with real bytes).
        logger.info(
            "POST %s complete: ref=%s output_metadata_fields=%d",
            url, output_reference or "-", len(body["output_metadata"]),
        )
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=body)
            logger.info("POST %s -> %s", url, response.status_code)
            response.raise_for_status()
            data = response.json()
            # Unwrap response envelope from ResponseWrapperMiddleware
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            return data

    def report_failed(
        self,
        run_id: str,
        task_id: str,
        error: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        run_generation: str = "",
        idempotency_key: str = "",
    ) -> dict:
        url = f"{self._wcp_base_url}/runs/{run_id}/tasks/{task_id}/fail"
        body: dict[str, Any] = {"error": error or {}}
        if outputs:
            body["outputs"] = outputs
        if run_generation:
            body["run_generation"] = run_generation
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        # SA-1941 — summary only, never the (possibly large) outputs body.
        logger.info(
            "POST %s fail: error_code=%s outputs_fields=%d",
            url, (error or {}).get("code", "-"), len(outputs or {}),
        )
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            return data
