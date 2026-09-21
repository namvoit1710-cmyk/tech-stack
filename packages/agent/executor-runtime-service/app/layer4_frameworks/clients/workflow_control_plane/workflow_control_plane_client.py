from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import ValidationError

from app.layer4_frameworks.clients.workflow_control_plane.models import (
    GetDataRequest,
    GetDataResponse,
    PatchDataRequest,
    PatchDataResponse,
    StartRunRequest,
    StartRunResponse,
    StopRunRequest,
    StopRunResponse,
    SubmitRequest,
    SubmitResponse,
    WorkflowApiErrorEnvelope,
    WorkflowApiSuccessEnvelope,
)

logger = logging.getLogger(__name__)


class WorkflowControlPlaneError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message or "Workflow control-plane error"
        self.details = details or {}

        super().__init__(
            f"workflow control-plane error {status_code}: "
            f"{self.code or 'UNKNOWN_ERROR'} - {self.message}"
        )


class WorkflowControlPlaneClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/api/v1"
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def start_run(
        self,
        request: StartRunRequest,
    ) -> StartRunResponse:

        response = await self._request(
            "POST",
            "/runs",
            json=request.to_payload(),
        )

        payload = self._unwrap(response)

        return StartRunResponse.model_validate(payload)

    async def get_data(
        self,
        request: GetDataRequest,
    ) -> GetDataResponse:

        response = await self._request(
            "GET",
            f"/runs/{request.run_id}/nodes/{request.node_id}/data",
            params=request.to_params(),
        )

        data = self._unwrap(response)
        etag = response.headers.get("ETag")

        return GetDataResponse(data=data, etag=etag)

    async def patch_data(
        self,
        request: PatchDataRequest,
    ) -> PatchDataResponse:

        response = await self._request(
            "PATCH",
            f"/runs/{request.run_id}/nodes/{request.node_id}/data",
            json=request.to_payload(),
            params=request.to_params(),
            headers=request.to_headers(),
        )

        return PatchDataResponse(data=self._unwrap(response))

    async def submit(
        self,
        request: SubmitRequest,
    ) -> SubmitResponse:

        response = await self._request(
            "POST",
            f"/runs/{request.run_id}/nodes/{request.node_id}/submit",
            params=request.to_params(),
        )

        return SubmitResponse(data=self._unwrap(response))

    async def stop_run(
        self,
        request: StopRunRequest,
    ) -> StopRunResponse:

        response = await self._request(
            "POST",
            f"/runs/{request.run_id}/cancel",
        )

        return StopRunResponse.model_validate(self._unwrap(response))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        return await self._client.request(
            method,
            f"{self._base_url}{path}",
            **kwargs,
        )

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 200:
            payload = _read_json_object(response)
            try:
                envelope = WorkflowApiSuccessEnvelope.model_validate(payload)
            except ValidationError as exc:
                raise WorkflowControlPlaneError(
                    502,
                    code="MALFORMED_WORKFLOW_SUCCESS_RESPONSE",
                    message="Workflow API success response is malformed",
                    details={"payload": payload},
                ) from exc

            return envelope.data

        if response.status_code == 409:
            payload = _read_json_object(response)
            try:
                envelope = WorkflowApiErrorEnvelope.model_validate(payload)
            except ValidationError as exc:
                raise WorkflowControlPlaneError(
                    409,
                    code="MALFORMED_WORKFLOW_ERROR_RESPONSE",
                    message="Workflow API error response is malformed",
                    details={"payload": payload},
                ) from exc

            raise WorkflowControlPlaneError(
                409,
                code=envelope.error.code,
                message=envelope.error.message,
                details=envelope.error.details,
            )

        payload = _read_json_object(response)

        raise WorkflowControlPlaneError(
            response.status_code,
            code="HTTP_ERROR",
            message=f"Workflow control-plane returned HTTP {response.status_code}",
            details={"payload": payload},
        )


def _read_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        body = response.text

        if response.status_code >= 400:
            raise WorkflowControlPlaneError(
                response.status_code,
                code="NON_JSON_HTTP_ERROR",
                message=f"Workflow control-plane returned HTTP {response.status_code}",
                details={"body": body},
            ) from exc

        raise WorkflowControlPlaneError(
            502,
            code="NON_JSON_WORKFLOW_RESPONSE",
            message="Workflow control-plane response is not valid JSON",
            details={"body": body},
        ) from exc

    if not isinstance(payload, dict):
        raise WorkflowControlPlaneError(
            502,
            code="MALFORMED_WORKFLOW_RESPONSE",
            message="Workflow control-plane response must be a JSON object",
            details={"payload": payload},
        )

    return payload
