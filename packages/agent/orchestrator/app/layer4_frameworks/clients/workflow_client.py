from typing import Any, Dict, List, Optional, Tuple
import httpx
import logging

logger = logging.getLogger(__name__)


class WorkflowClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/") + "/api/v1"
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None


    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._c().request(method, f"{self._base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            logger.exception("control-plane request failed: %s %s", method, path)
            raise ValueError(
                502,
                {
                    "status": "error",
                    "message": "Failed to reach control plane",
                    "detail": str(exc),
                    "path": path,
                },
            ) from exc

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = resp.text
            raise ValueError(resp.status_code, body)
        payload = resp.json()
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def start_run(
        self,
        workflow_id: str,
        predefined_run_id: Optional[str] = None,
        input_data: Optional[Dict[str, Any]] = None,
        execution_mode: str = "auto",
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"workflow_id": workflow_id, "input_data": input_data or {}, "execution_mode": execution_mode}
        if predefined_run_id:
            body["predefined_run_id"] = predefined_run_id
        resp = await self._request("POST", "/runs", json=body)
        return self._unwrap(resp)

    async def get_schema(self, run_id: str, node_id: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        params = {"task_id": task_id} if task_id else None
        resp = await self._request("GET", f"/runs/{run_id}/nodes/{node_id}/schema", params=params)
        return self._unwrap(resp)

    async def get_data(
        self, run_id: str, node_id: str, side: str = "input", task_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        params: Dict[str, Any] = {"side": side}
        if task_id:
            params["task_id"] = task_id
        resp = await self._request("GET", f"/runs/{run_id}/nodes/{node_id}/data", params=params)
        data = self._unwrap(resp)
        etag = resp.headers.get("ETag")
        return data, etag

    async def patch_data(
        self,
        run_id: str,
        node_id: str,
        artifacts: List[Dict[str, Any]],
        if_match: str,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params = {"task_id": task_id} if task_id else None
        headers = {"If-Match": if_match} if if_match else {}
        resp = await self._request(
            "PATCH",
            f"/runs/{run_id}/nodes/{node_id}/data",
            json={"artifacts": artifacts},
            params=params,
            headers=headers,
        )
        print(f"PATCH data response: status={resp.status_code} body={resp.text}")
        return self._unwrap(resp)

    async def submit(self, run_id: str, node_id: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        params = {"task_id": task_id} if task_id else None
        resp = await self._request("POST", f"/runs/{run_id}/nodes/{node_id}/submit", params=params)
        print(f"Submit response: status={resp.status_code} body={resp.text}")
        return self._unwrap(resp)
    
    async def submit_user_action(self, run_id: str, node_id: str, task_id: Optional[str] = None, artifacts: Optional[list[dict[str, Any]]] = None, params: Optional[dict[str, str]] = None) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if artifacts:
                get_params = {"side": "input"}
                if task_id:
                    get_params["task_id"] = task_id

                response = await client.get(
                    f"{self._base}/api/v1/runs/{run_id}/nodes/{node_id}/data",
                    params=get_params,
                )
                response.raise_for_status()
                etag = response.headers.get("ETag", "")

                patch_response = await client.patch(
                    f"{self._base}/api/v1/runs/{run_id}/nodes/{node_id}/data",
                    json={"artifacts": artifacts},
                    params=params,
                    headers={"If-Match": etag} if etag else None,
                )
                patch_response.raise_for_status()

            submit_response = await client.post(
                f"{self._base}/api/v1/runs/{run_id}/nodes/{node_id}/submit",
                params=params,
            )
            submit_response.raise_for_status()

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
