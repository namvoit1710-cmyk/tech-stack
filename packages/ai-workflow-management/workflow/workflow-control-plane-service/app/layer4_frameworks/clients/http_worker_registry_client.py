"""HTTP adapter for querying the Worker Execution service's worker registry."""
import logging
from typing import Any, Optional
from uuid import uuid4

import httpx

from app.layer2_application.interfaces.worker_registry_client_interface import IWorkerRegistryClient

logger = logging.getLogger(__name__)

_API_PREFIX = "/api/v1"


class HttpWorkerRegistryClient(IWorkerRegistryClient):
    def __init__(self, worker_execution_url: str) -> None:
        self._base_url = worker_execution_url.rstrip("/")

    def _get(self, path: str) -> Optional[Any]:
        """Shared GET helper. Returns parsed JSON or None on failure."""
        url = f"{self._base_url}{_API_PREFIX}{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                # Unwrap response envelope from ResponseWrapperMiddleware
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                return data
        except Exception as exc:
            logger.error("GET %s failed: %s", path, exc)
            return None

    def list_workers(self) -> list[dict[str, Any]]:
        """Fetch all registered workers. Returns [] on any failure."""
        data = self._get("/workers")
        if data is None:
            return []
        if isinstance(data, list):
            return data
        return data.get("workers", [])

    def get_worker(self, worker_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single worker by ID. Returns None if not found."""
        data = self._get(f"/workers/{worker_id}")
        if data is None or (isinstance(data, dict) and "error" in data):
            return None
        return data

    def get_worker_by_type(self, worker_type: str) -> Optional[dict[str, Any]]:
        """Return the first registered worker matching ``worker_type``.

        Phase 1: filter client-side via :meth:`list_workers`. Acceptable
        because the list is small (O(20)) and cached per CP instance.
        Optimize with a dedicated executor endpoint if/when needed.
        """
        if not worker_type:
            return None
        for w in self.list_workers():
            if w.get("worker_type") == worker_type:
                return w
        return None

    def get_replica_summary(self) -> Optional[list[dict[str, Any]]]:
        """GET /api/v1/workers/replicas (SA-2053/R6). None on transport failure,
        [] on a genuine empty 200. NOT list_workers' None->[] collapse: R9 must
        tell a network flap (None) apart from a real full-drain ([])."""
        data = self._get("/workers/replicas")
        if data is None:
            return None                       # unreachable — pass THROUGH, never ->[]
        if isinstance(data, list):
            return data
        # The R6 route (worker_controller.py on the executor) always returns a
        # bare list, and _get already unwrapped the {"status":"ok","data":[...]}
        # envelope before this code runs — so a non-list, non-None body here is
        # a contract violation, not some alternate "workers"-wrapped shape.
        # Fail soft (R9 must not crash on it) but say so loudly.
        logger.warning(
            "GET /workers/replicas returned a non-list body (contract "
            "violation, expected a bare list): type=%s body=%.200r",
            type(data).__name__, data,
        )
        return []

    def get_type_replicas(self, worker_type: str) -> Optional[list[dict[str, Any]]]:
        """GET /api/v1/workers/type/{worker_type}/replicas (SA-2047 R10). None on
        transport failure, [] on a genuine empty 200 (unknown/drained worker_type).
        Same None-vs-[] contract as get_replica_summary above — _get already
        unwraps the executor's {"status":"ok","data":[...]} envelope before this
        code runs."""
        data = self._get(f"/workers/type/{worker_type}/replicas")
        if data is None:
            return None                       # unreachable — pass THROUGH, never ->[]
        if isinstance(data, list):
            return data
        logger.warning(
            "GET /workers/type/%s/replicas returned a non-list body (contract "
            "violation, expected a bare list): type=%s body=%.200r",
            worker_type, type(data).__name__, data,
        )
        return []

    def get_app_summary(self) -> Optional[list[dict[str, Any]]]:
        """GET /api/v1/workers/apps (SA-2047 R14). None on transport failure OR
        a non-list body, [] on a genuine empty 200 (no worker apps registered).
        _get already unwraps the executor's {"status":"ok","data":[...]}
        envelope before this code runs.

        DELIBERATE DIVERGENCE from get_replica_summary / get_type_replicas
        above: those two fail soft to [] (with a loud warning) on a non-list
        body. This method folds a non-list body into None instead. A contract
        violation is not the same fact as "there are no workers" — Task 15
        rekeys the R9 realtime feed onto this method, where None means
        "unreachable, keep the last snapshot, publish nothing" but [] means
        "genuinely empty, tombstone every entry". Reporting a malformed body
        as [] would wipe the whole fleet from the live view because of one
        bad response; staying silent (None) is the safer failure. Whether
        []-on-non-list is right for the two older replica methods is a
        separate, out-of-scope question — they are untouched here."""
        data = self._get("/workers/apps")
        if data is None:
            return None                       # unreachable — pass THROUGH, never ->[]
        if isinstance(data, list):
            return data
        # The R14 route (worker_controller.py on the executor) always returns a
        # bare list, and _get already unwrapped the envelope before this code
        # runs — so a non-list, non-None body here is a contract violation, not
        # some alternate wrapped shape. Fail soft to None (NOT [] — see the
        # docstring above: an empty list here would misreport a bad response
        # as "genuinely zero apps" and R9 would tombstone the whole fleet on
        # it) but say so loudly.
        logger.warning(
            "GET /workers/apps returned a non-list body (contract "
            "violation, expected a bare list): type=%s body=%.200r",
            type(data).__name__, data,
        )
        return None

    def get_app_instances(self, worker_app: str) -> Optional[list[dict[str, Any]]]:
        """GET /api/v1/workers/apps/{worker_app}/instances (SA-2047 R14). None on
        transport failure OR a non-list body, [] on a genuine empty 200 (no
        instances for this app, including an unknown app name). Same tri-state
        contract as get_app_summary above.

        DELIBERATE DIVERGENCE from get_replica_summary / get_type_replicas
        (same reasoning as get_app_summary's docstring above): those two fail
        soft to [] on a non-list body; this method folds a non-list body into
        None instead, because R9 (Task 15) treats None as "unreachable, hold
        last snapshot" and [] as "genuinely empty, tombstone" — a malformed
        body reported as [] would wrongly tombstone this app's instances."""
        data = self._get(f"/workers/apps/{worker_app}/instances")
        if data is None:
            return None                       # unreachable — pass THROUGH, never ->[]
        if isinstance(data, list):
            return data
        logger.warning(
            "GET /workers/apps/%s/instances returned a non-list body (contract "
            "violation, expected a bare list): type=%s body=%.200r",
            worker_app, type(data).__name__, data,
        )
        return None

    def get_worker_schema(self, worker_id: str) -> Optional[dict[str, Any]]:
        """Fetch input/output schema for a worker. Returns None if not found."""
        data = self._get(f"/workers/{worker_id}/schema")
        if data is None or (isinstance(data, dict) and "error" in data):
            return None
        return data

    def discover_workers(self, capability: str) -> list[dict[str, Any]]:
        """Discover workers matching a capability. Returns [] on failure."""
        data = self._get(f"/workers/discover/{capability}")
        if data is None:
            return []
        if isinstance(data, list):
            return data
        return []

    def list_worker_functions(self, worker_type: str) -> list[dict[str, Any]]:
        """Fetch functions declared by a worker. Returns [] on failure."""
        data = self._get(f"/workers/type/{worker_type}/functions")
        if data is None:
            return []
        if isinstance(data, list):
            return data
        return []

    def invoke_worker_function(self, worker_type: str, function_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke a named function on a worker via the executor."""
        url = f"{self._base_url}{_API_PREFIX}/workers/type/{worker_type}/functions/{function_name}/invoke"
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json={"params": params})
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                return data
        except Exception as exc:
            logger.error("POST %s failed: %s", url, exc)
            return {"status": "error", "error": str(exc)}

    def _resolve_worker_endpoint(self, worker_type: str) -> Optional[str]:
        """Look up a worker's endpoint URL from the executor registry by worker_type."""
        workers = self.list_workers()
        for w in workers:
            if w.get("worker_type") == worker_type and w.get("endpoint_url"):
                return w["endpoint_url"].rstrip("/")
        return None

    def execute_task(
        self,
        worker_type: str,
        input_data: dict[str, Any],
        node_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a worker synchronously by calling its /api/v1/execute endpoint directly.

        Resolves the worker's endpoint URL via the executor registry, then
        POSTs to the worker's sync execute endpoint and waits for the result.
        """
        endpoint = self._resolve_worker_endpoint(worker_type)
        if not endpoint:
            return {"status": "FAILED", "error": f"No worker found for type '{worker_type}'"}

        url = f"{endpoint}/api/v1/execute"
        payload = {
            "task_id": f"test-{uuid4().hex[:8]}",
            "action": "execute",
            "inputs": input_data,
            "parameters": node_config or {},
        }
        try:
            logger.info("execution payload: %s",payload)
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                return data
        except Exception as exc:
            logger.error("execute_task failed (worker_type=%s, url=%s): %s", worker_type, url, exc)
            return {"status": "FAILED", "error": str(exc)}
