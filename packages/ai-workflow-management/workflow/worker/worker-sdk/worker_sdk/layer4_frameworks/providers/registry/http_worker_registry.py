import logging

import httpx

from worker_sdk.layer4_frameworks.config import instance_identity
from worker_sdk.layer4_frameworks.config.app_config import settings
from worker_sdk.layer1_domain.entities.worker_registration import WorkerRegistration
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.value_objects.worker_status import WorkerStatus
from worker_sdk.layer2_application.interfaces.worker_registry_interface import IWorkerRegistry

_log = logging.getLogger("WorkerSDK")


class HttpWorkerRegistry(IWorkerRegistry):
    """Registers and heartbeats with the worker registry over HTTP."""

    def __init__(self) -> None:
        self.base_url = settings.REGISTRY_URL.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)
        _log.debug("HttpWorkerRegistry initialised, base_url=%s", self.base_url)

    async def register(self, registration: WorkerRegistration) -> str:
        url = f"{self.base_url}/api/v1/workers/register"
        payload = {
            "worker_type": registration.worker_type,
            "version": registration.version,
            "spec_version": getattr(registration, "spec_version", "1.0.0") or "1.0.0",
            "endpoint": registration.endpoint,
            "sdk_version": registration.sdk_version,
            "input_schema": registration.input_schema,
            "output_schema": registration.output_schema,
            "name": registration.name,
            "description": registration.description,
            "node_class": registration.node_class,
            # ``.value`` extracts the plain wire string ("read") for the JSON
            # payload — json.dumps already renders a ``str, Enum`` member as
            # its value rather than its repr, but being explicit removes any
            # dependence on that implicit behaviour (SA-1734 blocker fix).
            "kind": NodeKind(getattr(registration, "kind", None) or NodeKind.ACTION).value,
            # SA-2026: additive; "push" default keeps the identical push registration.
            "delivery_mode": getattr(registration, "delivery_mode", "push") or "push",
            "icon": registration.icon,
            "color": registration.color,
            "tags": registration.tags,
            "capabilities": registration.capabilities,
            "ports": registration.ports,
            "functions": [
                {
                    "name": f.name,
                    "description": f.description,
                    "input_schema": f.input_schema,
                    "output_schema": f.output_schema,
                }
                for f in (registration.functions or [])
            ],
            # --- R8 (SA-2055) §5: process identity ---
            # The executor's DTO already accepts all four (worker_controller.py:78,
            # passthrough :176) and derives the registration id from
            # instance_id + worker_type (keystone §4). The SDK sends facts about
            # the PROCESS and nothing more.
            # Entity value wins if set (embedder/test pins it); otherwise ask the
            # process. STARTED_AT is a module constant, so a 22-register-call
            # process reports ONE start time — read it through the module, never
            # copy it into a local.
            "instance_id": registration.instance_id or instance_identity.instance_id(),
            "host": registration.host or instance_identity.host(),
            "pid": registration.pid or instance_identity.pid(),
            "started_at": registration.started_at or instance_identity.STARTED_AT,
            # R14 (SA-2047): the deployable's name. Same precedence as the four
            # fields above — entity value wins if pinned, else ask the process.
            # The executor (Task 11) falls back to deriving this from the
            # endpoint host when blank, so sending it here just makes the
            # honest value available instead of the derived approximation.
            "worker_app": registration.worker_app or instance_identity.worker_app(),
        }
        _log.debug("Registering worker at %s", url)
        _log.debug("Registration payload: %s", payload)
        response = await self._client.post(url, json=payload)
        _log.debug("Register response: status=%s", response.status_code)
        response.raise_for_status()
        data = response.json()
        # Unwrap response envelope from ResponseWrapperMiddleware
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        worker_id = data["worker_id"]
        _log.debug("Registration successful, worker_id=%s", worker_id)
        return worker_id

    async def heartbeat(self, worker_id: str, status: WorkerStatus) -> dict:
        url = f"{self.base_url}/api/v1/workers/{worker_id}/heartbeat"
        _log.debug("Sending heartbeat to %s, status=%s", url, status.value)
        response = await self._client.post(url, json={"status": status.value})
        _log.debug("Heartbeat response: status=%s", response.status_code)
        response.raise_for_status()
        data = response.json()
        # Unwrap response envelope from ResponseWrapperMiddleware
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data

    async def deregister(self, worker_id: str) -> None:
        url = f"{self.base_url}/api/v1/workers/{worker_id}/deregister"
        _log.debug("Deregistering worker at %s", url)
        response = await self._client.post(url)
        _log.debug("Deregister response: status=%s", response.status_code)
        response.raise_for_status()

    async def close(self) -> None:
        _log.debug("Closing HTTP client")
        await self._client.aclose()
