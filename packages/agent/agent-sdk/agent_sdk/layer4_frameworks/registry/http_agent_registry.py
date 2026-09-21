from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass, replace
from enum import Enum
from typing import Any

import httpx

from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer1_domain.exceptions import RegistrationError
from agent_sdk.layer2_application.interfaces.agent_registry import IAgentRegistry
from agent_sdk.layer4_frameworks.config.app_config import settings

_MAX_REGISTRY_DESCRIPTION_LENGTH = 256
_DEFINITION_HASH_ALGORITHM = "sha256:v1"
_DEFAULT_UPDATE_METADATA_KEYS = {
    "agent_type",
    "logical_registry_name",
    "definition_hash",
    "definition_hash_algorithm",
    "registry_version_bumped_from",
    "registry_version_bump_reason",
}
_REGISTRY_ACTIVATION_MAX_ATTEMPTS = 3
_REGISTRY_ACTIVATION_INITIAL_BACKOFF_SECONDS = 0.25
_REGISTRY_AGENT_DETAIL_FALLBACK_CONCURRENCY = 8

logger = logging.getLogger(__name__)


class HttpAgentRegistry(IAgentRegistry):
    def __init__(self) -> None:
        self.base_url = settings.REGISTRY_URL.rstrip("/")
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=10.0,
        )

    async def register(self, registration: AgentRegistration) -> str:
        if "." in registration.agent_type:
            raise ValueError("agent_type must not contain dots ('.')")

        try:
            existing_agents = await self._list_existing_agents(
                self._registration_logical_name(registration)
            )
            selected_registration, selected_agent = self._select_existing_agent(
                registration,
                existing_agents,
            )

            description = self._safe_description(
                self._build_description(selected_registration)
            )
            business = self._build_business(selected_registration, description)

            if selected_agent is None:
                agent_id = await self._create_agent(
                    selected_registration,
                    description=description,
                    business=business,
                )
                existing_agents[selected_registration.agent_type] = {
                    "id": agent_id,
                    "name": selected_registration.agent_type,
                    "version": selected_registration.version,
                    "status": "inactive",
                    "is_published": False,
                    "metadata": self._build_metadata(selected_registration),
                }
            else:
                agent_id = selected_agent["id"]
                await self._update_agent(
                    agent_id=agent_id,
                    registration=selected_registration,
                    description=description,
                    business=business,
                )

            active_agent = await self._ensure_agent_published_and_active(agent_id)
            existing_agents[selected_registration.agent_type] = {
                **existing_agents.get(selected_registration.agent_type, {}),
                **active_agent,
                "id": agent_id,
                "name": self._agent_name(active_agent)
                or selected_registration.agent_type,
                "status": self._agent_status(active_agent),
                "is_published": self._agent_is_published(active_agent),
            }

            await self._deactivate_stale_versions(
                selected_agent_id=agent_id,
                registration=selected_registration,
                existing_agents=existing_agents,
            )
            return agent_id

        except httpx.HTTPStatusError as exc:
            raise RegistrationError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise RegistrationError(str(exc)) from exc

    async def resolve_agent_id(self, agent_type: str) -> str | None:
        """Resolve the active/current id for a logical agent type.

        Supports both legacy registry rows named exactly ``agent_type`` and the
        SDK versioned rows named ``agent_type__<version>``. This keeps remote
        agent calls stable even after a definition-hash version bump.
        """
        try:
            active_agents = await self.list_active_agents()
            active_candidates = self._find_logical_candidates_by_name(
                agent_type,
                active_agents,
            )
            if active_candidates:
                return self._agent_id(self._latest_agent(active_candidates))

            existing_agents = await self._list_existing_agents(agent_type)
            candidates = self._find_logical_candidates_by_name(
                agent_type,
                list(existing_agents.values()),
            )
            if not candidates:
                return None
            return self._agent_id(self._latest_agent(candidates))
        except httpx.HTTPStatusError as exc:
            raise RegistrationError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise RegistrationError(str(exc)) from exc

    async def list_active_agents(self, domain: str | None = None) -> list[dict]:
        try:
            response = await self._client.get(
                f"{self.base_url}/api/v1/agents/available",
                timeout=10.0,
            )
            response.raise_for_status()
            payload = response.json()
            agents = (
                payload
                if isinstance(payload, list)
                else payload.get("agents") or payload.get("results", [])
            )
            agents = [agent for agent in agents if isinstance(agent, dict)]
            if domain is None:
                return agents

            filtered = []
            for agent in agents:
                agent_domain = agent.get("domain")
                if agent_domain is None:
                    metadata = agent.get("metadata") or {}
                    if isinstance(metadata, dict):
                        agent_domain = metadata.get("agent_domain")
                if agent_domain == domain:
                    filtered.append(agent)
            return filtered
        except httpx.HTTPStatusError as exc:
            raise RegistrationError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise RegistrationError(str(exc)) from exc

    async def list_capabilities(self) -> list[dict]:
        capabilities: list[dict] = []
        for agent in await self.list_active_agents():
            capability = self._build_capability(agent)
            if capability is not None:
                capabilities.append(capability)
        return capabilities

    async def get_queue_metadata(self, queue_name: str) -> dict:
        for capability in await self.list_capabilities():
            queue_metadata = capability.get("queue_metadata")
            if not isinstance(queue_metadata, dict):
                continue
            if queue_metadata.get("queue_name") == queue_name:
                return queue_metadata
        return {}

    async def heartbeat(self, agent_id: str, status: str) -> None:
        response = await self._client.get(
            f"{self.base_url}/api/v1/agents/{agent_id}",
            timeout=10.0,
        )
        response.raise_for_status()

    async def deregister(self, agent_id: str) -> None:
        response = await self._client.post(
            f"{self.base_url}/api/v1/agents/{agent_id}/deactivate",
            timeout=5.0,
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()

    async def _ensure_agent_published_and_active(
        self,
        agent_id: str,
    ) -> dict[str, Any]:
        delay_seconds = _REGISTRY_ACTIVATION_INITIAL_BACKOFF_SECONDS
        last_error: Exception | None = None

        for attempt in range(1, _REGISTRY_ACTIVATION_MAX_ATTEMPTS + 1):
            try:
                agent = await self._get_agent(agent_id)

                if not self._agent_is_published(agent):
                    await self._publish_agent(agent_id)
                    agent = await self._get_agent(agent_id)

                if not self._agent_is_active(agent):
                    await self._activate_agent(agent_id)
                    agent = await self._get_agent(agent_id)

                if self._agent_is_published(agent) and self._agent_is_active(agent):
                    return agent

                last_error = RegistrationError(
                    f"Registry agent {agent_id!r} was not confirmed active "
                    "after publish/activate"
                )
            except (
                httpx.HTTPStatusError,
                httpx.TransportError,
                RegistrationError,
            ) as exc:
                last_error = exc

            if attempt == _REGISTRY_ACTIVATION_MAX_ATTEMPTS:
                break
            await asyncio.sleep(delay_seconds)
            delay_seconds *= 2

        raise RegistrationError(
            f"Registry agent {agent_id!r} could not be published and activated"
        ) from last_error

    async def _get_agent(self, agent_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"{self.base_url}/api/v1/agents/{agent_id}",
            timeout=10.0,
        )
        response.raise_for_status()
        agent = self._agent_from_response(response, agent_id)
        if not agent:
            raise RegistrationError(f"Registry agent {agent_id!r} response was empty")
        return agent

    @classmethod
    def _agent_from_response(
        cls,
        response: httpx.Response,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}

        if isinstance(payload, dict):
            direct_agent_id = cls._agent_id(payload)
            if direct_agent_id and (agent_id is None or direct_agent_id == agent_id):
                return payload
            data = payload.get("data")
            if isinstance(data, dict):
                data_agent_id = cls._agent_id(data)
                if data_agent_id and (agent_id is None or data_agent_id == agent_id):
                    return data

        by_id = cls._agents_by_id_from_payload(payload)
        if agent_id is not None and agent_id in by_id:
            return by_id[agent_id]
        if by_id:
            return next(iter(by_id.values()))
        return {}

    @staticmethod
    def _agent_status(agent: dict[str, Any]) -> str:
        status = agent.get("status")
        return status.lower() if isinstance(status, str) else ""

    @staticmethod
    def _agent_is_active(agent: dict[str, Any]) -> bool:
        return HttpAgentRegistry._agent_status(agent) == "active"

    @staticmethod
    def _agent_is_published(agent: dict[str, Any]) -> bool:
        is_published = agent.get("is_published")
        if isinstance(is_published, bool):
            return is_published
        if isinstance(is_published, str):
            return is_published.strip().lower() in {"1", "true", "yes"}
        return False

    @staticmethod
    def _build_description(registration: AgentRegistration) -> str:
        description = (
            registration.metadata.get("description") if registration.metadata else None
        )
        if isinstance(description, str) and description:
            return description
        logical_name = HttpAgentRegistry._registration_logical_name(registration)
        return (
            f"Agent SDK agent: {logical_name} "
            f"v{registration.version} (SDK {registration.sdk_version})"
        )

    @staticmethod
    def _build_business(registration: AgentRegistration, description: str) -> str:
        runtime_config = registration.agent_runtime_config
        if runtime_config is not None and runtime_config.system_prompt:
            return runtime_config.system_prompt
        return description

    async def _create_agent(
        self,
        registration: AgentRegistration,
        *,
        description: str,
        business: str,
    ) -> str:
        create_payload: dict[str, object] = {
            "name": registration.agent_type,
            "description": description,
            "kind": registration.kind,
            "status": "inactive",
            "config_type": "custom",
            "business": business,
            "tools": registration.tool_ids,
            "agents": registration.attached_agent_ids,
            "is_published": False,
            "version": registration.version,
            "healthcheck_endpoint": f"{registration.endpoint_url}/health",
            "invoke_endpoint": f"{registration.endpoint_url}/api/v1/execute",
            "metadata": self._build_metadata(registration),
        }
        create_payload.update(self._build_runtime_create_payload(registration))

        response = await self._client.post(
            f"{self.base_url}/api/v1/agents/register",
            json=create_payload,
            timeout=10.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            recovered_id = await self._recover_existing_id(
                registration.agent_type, registration.version
            )
            if recovered_id:
                return recovered_id
            raise

        agent_id = self._extract_created_id(response)
        if not agent_id:
            agent_id = await self._recover_existing_id(
                registration.agent_type, registration.version
            )
        if not agent_id:
            raise RegistrationError("Registry agent creation did not return an id")
        return agent_id

    async def _update_agent(
        self,
        agent_id: str,
        registration: AgentRegistration,
        description: str,
        business: str,
    ) -> httpx.Response:
        payload = self._build_update_payload(
            registration=registration,
            description=description,
            business=business,
        )
        response = await self._client.put(
            f"{self.base_url}/api/v1/agents/{agent_id}",
            json=payload,
            timeout=10.0,
        )
        if response.status_code == 422:
            compatible_payload = self._registry_update_compat_payload(payload)
            if compatible_payload != payload:
                response = await self._client.put(
                    f"{self.base_url}/api/v1/agents/{agent_id}",
                    json=compatible_payload,
                    timeout=10.0,
                )
        response.raise_for_status()
        return response

    @staticmethod
    def _build_update_payload(
        registration: AgentRegistration,
        description: str,
        business: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": registration.agent_type,
            "description": HttpAgentRegistry._safe_description(description),
            "kind": registration.kind,
            "tools": registration.tool_ids,
            "agents": registration.attached_agent_ids,
            "config_type": "custom",
            "business": business,
            "version": registration.version,
            "healthcheck_endpoint": f"{registration.endpoint_url}/health",
            "invoke_endpoint": f"{registration.endpoint_url}/api/v1/execute",
        }

        payload.update(HttpAgentRegistry._build_runtime_update_payload(registration))
        return HttpAgentRegistry._registry_update_compat_payload(payload)

    @staticmethod
    def _registry_update_compat_payload(
        payload: dict[str, object],
    ) -> dict[str, object]:
        allowed_keys = {
            "name",
            "description",
            "kind",
            "is_published",
            "config_type",
            "business",
            "tools",
            "workflows",
            "agents",
            "knowledge_base",
            "version",
            "healthcheck_endpoint",
            "invoke_endpoint",
            "model",
            "provider",
            "temperature",
        }
        return {key: value for key, value in payload.items() if key in allowed_keys}

    def _select_existing_agent(
        self,
        registration: AgentRegistration,
        existing_agents: dict[str, dict[str, Any]],
    ) -> tuple[AgentRegistration, dict[str, Any] | None]:
        candidates = self._find_logical_candidates(registration, existing_agents)
        current_hash = self._registration_definition_hash(registration)

        exact_same_version = [
            agent
            for agent in candidates
            if self._agent_version(agent) == registration.version
            and self._logical_name_matches(
                self._agent_name(agent),
                self._registration_logical_name(registration),
            )
        ]
        if exact_same_version:
            selected_agent = self._latest_agent(exact_same_version)
            selected_registration = replace(
                registration,
                agent_type=self._agent_name(selected_agent) or registration.agent_type,
                version=self._agent_version(selected_agent) or registration.version,
                metadata=self._merge_selected_metadata(
                    registration,
                    selected_agent,
                    current_hash=current_hash,
                ),
            )
            return selected_registration, selected_agent

        matching = [
            agent
            for agent in candidates
            if self._agent_definition_hash(agent) == current_hash
        ]
        if matching:
            selected_agent = self._latest_agent(matching)
            selected_registration = replace(
                registration,
                agent_type=self._agent_name(selected_agent) or registration.agent_type,
                version=self._agent_version(selected_agent) or registration.version,
                metadata=self._merge_selected_metadata(
                    registration,
                    selected_agent,
                    current_hash=current_hash,
                ),
            )
            return selected_registration, selected_agent

        if not candidates:
            metadata = self._metadata_with_definition(registration, current_hash)
            return replace(registration, metadata=metadata), None

        bumped_version = self._next_version(
            existing_versions=[
                self._agent_version(agent)
                for agent in candidates
                if self._agent_version(agent)
            ],
            base_version=registration.version,
        )
        logical_registry_name = self._registration_logical_name(registration)
        bumped_registry_name = self._build_versioned_registry_name(
            logical_registry_name,
            bumped_version,
        )
        bumped_metadata = self._metadata_with_definition(registration, current_hash)
        bumped_metadata.update(
            {
                "registry_version_bumped_from": registration.version,
                "registry_version_bump_reason": "definition_hash_changed",
            }
        )
        bumped_registration = replace(
            registration,
            agent_type=bumped_registry_name,
            version=bumped_version,
            metadata=bumped_metadata,
        )
        existing_same_name = existing_agents.get(bumped_registry_name)
        return bumped_registration, existing_same_name

    @staticmethod
    def _merge_selected_metadata(
        registration: AgentRegistration,
        selected_agent: dict[str, Any],
        *,
        current_hash: str,
    ) -> dict[str, Any]:
        metadata = dict(registration.metadata or {})
        existing_metadata = selected_agent.get("metadata")
        if isinstance(existing_metadata, dict):
            for key in _DEFAULT_UPDATE_METADATA_KEYS:
                if key in existing_metadata and key not in metadata:
                    metadata[key] = existing_metadata[key]
        metadata.setdefault("definition_hash", current_hash)
        metadata.setdefault("definition_hash_algorithm", _DEFINITION_HASH_ALGORITHM)
        metadata.setdefault(
            "logical_registry_name",
            HttpAgentRegistry._registration_logical_name(registration),
        )
        metadata.setdefault(
            "agent_type", HttpAgentRegistry._registration_logical_name(registration)
        )
        return metadata

    @staticmethod
    def _metadata_with_definition(
        registration: AgentRegistration,
        definition_hash: str,
    ) -> dict[str, Any]:
        metadata = dict(registration.metadata or {})
        logical_registry_name = HttpAgentRegistry._registration_logical_name(
            registration
        )
        metadata["logical_registry_name"] = logical_registry_name
        metadata["agent_type"] = logical_registry_name
        metadata["definition_hash"] = definition_hash
        metadata["definition_hash_algorithm"] = _DEFINITION_HASH_ALGORITHM
        return metadata

    @staticmethod
    def _build_metadata(registration: AgentRegistration) -> dict[str, Any]:
        return HttpAgentRegistry._metadata_with_definition(
            registration,
            HttpAgentRegistry._registration_definition_hash(registration),
        )

    def _find_logical_candidates(
        self,
        registration: AgentRegistration,
        existing_agents: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self._find_logical_candidates_by_name(
            self._registration_logical_name(registration),
            list(existing_agents.values()),
        )

    @staticmethod
    def _find_logical_candidates_by_name(
        logical_name: str,
        agents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        prefix = f"{logical_name}__"
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            name = HttpAgentRegistry._agent_name(agent)
            if name == logical_name or name.startswith(prefix):
                candidates.append(agent)
                continue

            metadata = agent.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if metadata.get("logical_registry_name") == logical_name:
                candidates.append(agent)
                continue
            if metadata.get("agent_type") == logical_name:
                candidates.append(agent)
        return candidates

    async def _deactivate_stale_versions(
        self,
        *,
        selected_agent_id: str,
        registration: AgentRegistration,
        existing_agents: dict[str, dict[str, Any]],
    ) -> None:
        for agent in self._find_logical_candidates(registration, existing_agents):
            agent_id = self._agent_id(agent)
            if not agent_id or agent_id == selected_agent_id:
                continue
            if not self._agent_is_active(agent):
                continue
            await self._deactivate_agent(agent_id)
            agent["status"] = "inactive"

    async def _list_existing_agents(
        self,
        logical_name: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        response = await self._client.get(
            f"{self.base_url}/api/v1/agents/all",
            timeout=10.0,
        )
        response.raise_for_status()

        payload = response.json()
        if isinstance(payload, list):
            agents = payload
        elif isinstance(payload, dict):
            agents = payload.get("agents") or payload.get("results") or []
        else:
            agents = []

        summaries: list[dict[str, Any]] = [
            agent for agent in agents if isinstance(agent, dict)
        ]
        if logical_name:
            summaries = [
                agent
                for agent in summaries
                if self._summary_matches_logical_name(agent, logical_name)
            ]

        ids: list[str] = [
            str(agent.get("id") or agent.get("agent_id"))
            for agent in summaries
            if isinstance(agent.get("id") or agent.get("agent_id"), str)
        ]
        full_by_id = await self._get_agents_by_ids(ids) if ids else {}

        existing: dict[str, dict[str, Any]] = {}
        for summary in summaries:
            agent_id = summary.get("id") or summary.get("agent_id")
            name = summary.get("name")
            if not isinstance(name, str) or not isinstance(agent_id, str):
                continue

            full = full_by_id.get(agent_id, {})
            merged: dict[str, Any] = {**summary, **full}
            if logical_name and not self._summary_matches_logical_name(
                merged,
                logical_name,
            ):
                continue
            status = merged.get("status")
            merged["id"] = agent_id
            merged["name"] = name
            merged["status"] = status if isinstance(status, str) else ""
            existing[name] = merged
        return existing

    @classmethod
    def _summary_matches_logical_name(
        cls,
        agent: dict[str, Any],
        logical_name: str,
    ) -> bool:
        normalized_logical_name = (logical_name or "").strip()
        if not normalized_logical_name:
            return True

        metadata = agent.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        configuration = agent.get("configuration")
        if not isinstance(configuration, dict):
            configuration = {}

        candidates = (
            agent.get("name"),
            agent.get("agent_type"),
            metadata.get("logical_registry_name"),
            metadata.get("agent_type"),
            configuration.get("agent_type"),
        )
        return any(
            cls._logical_name_matches(candidate, normalized_logical_name)
            for candidate in candidates
        )

    @staticmethod
    def _logical_name_matches(candidate: Any, logical_name: str) -> bool:
        if not isinstance(candidate, str):
            return False
        value = candidate.strip()
        return value == logical_name or value.startswith(f"{logical_name}__")

    async def _get_agents_by_ids(
        self, agent_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        unique_ids = self._unique_agent_ids(agent_ids)
        if not unique_ids:
            return {}

        response = await self._client.post(
            f"{self.base_url}/api/v1/agents/by_ids",
            json=unique_ids,
            timeout=10.0,
        )
        if response.status_code in {404, 422}:
            return await self._get_agents_by_ids_individually(unique_ids)
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError:
            return {}

        return self._agents_by_id_from_payload(payload)

    async def _get_agents_by_ids_individually(
        self,
        agent_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not agent_ids:
            return {}

        logger.warning(
            "Registry bulk agent lookup unavailable; falling back to per-id GETs "
            "for %d agent(s)",
            len(agent_ids),
        )
        semaphore = asyncio.Semaphore(_REGISTRY_AGENT_DETAIL_FALLBACK_CONCURRENCY)

        async def fetch(agent_id: str) -> dict[str, dict[str, Any]]:
            async with semaphore:
                response = await self._client.get(
                    f"{self.base_url}/api/v1/agents/{agent_id}",
                    timeout=10.0,
                )
                if response.status_code == 404:
                    return {}
                response.raise_for_status()

                try:
                    payload = response.json()
                except ValueError:
                    return {}
                return self._agents_by_id_from_payload(payload)

        by_id: dict[str, dict[str, Any]] = {}
        for result in await asyncio.gather(
            *(fetch(agent_id) for agent_id in agent_ids)
        ):
            by_id.update(result)
        return by_id

    @staticmethod
    def _agents_by_id_from_payload(payload: Any) -> dict[str, dict[str, Any]]:
        if isinstance(payload, list):
            agents = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("data"), dict):
                agents = [payload["data"]]
            elif isinstance(payload.get("agent"), dict):
                agents = [payload["agent"]]
            elif payload.get("id") or payload.get("agent_id"):
                agents = [payload]
            else:
                agents = payload.get("agents") or payload.get("results") or []
        else:
            agents = []

        by_id: dict[str, dict[str, Any]] = {}
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            agent_id = agent.get("id") or agent.get("agent_id")
            if isinstance(agent_id, str) and agent_id:
                by_id[agent_id] = agent
        return by_id

    async def _recover_existing_id(
        self,
        registry_name: str,
        version: str | None = None,
    ) -> str | None:
        logical_name = (
            registry_name.rsplit("__", 1)[0] if "__" in registry_name else registry_name
        )
        try:
            existing_agents = await self._list_existing_agents(logical_name)
        except httpx.HTTPError:
            try:
                existing_agents = await self._list_existing_agents()
            except httpx.HTTPError:
                return None

        candidates = list(existing_agents.values())

        exact_name_and_version = [
            agent
            for agent in candidates
            if self._agent_name(agent) == registry_name
            and (not version or self._agent_version(agent) == version)
        ]
        if exact_name_and_version:
            return self._agent_id(self._latest_agent(exact_name_and_version))

        if version:
            exact_logical_version = [
                agent
                for agent in candidates
                if self._agent_version(agent) == version
                and self._logical_name_matches(self._agent_name(agent), logical_name)
            ]
            if exact_logical_version:
                return self._agent_id(self._latest_agent(exact_logical_version))

        same_name = [
            agent for agent in candidates if self._agent_name(agent) == registry_name
        ]
        if same_name:
            return self._agent_id(self._latest_agent(same_name))

        logical_candidates = self._find_logical_candidates_by_name(
            logical_name,
            candidates,
        )
        if logical_candidates:
            return self._agent_id(self._latest_agent(logical_candidates))
        return None

    async def _publish_agent(self, agent_id: str) -> None:
        response = await self._client.post(
            f"{self.base_url}/api/v1/agents/{agent_id}/publish",
            timeout=10.0,
        )
        response.raise_for_status()

    async def _activate_agent(self, agent_id: str) -> None:
        response = await self._client.post(
            f"{self.base_url}/api/v1/agents/{agent_id}/activate",
            timeout=10.0,
        )
        response.raise_for_status()

    async def _deactivate_agent(self, agent_id: str) -> None:
        response = await self._client.post(
            f"{self.base_url}/api/v1/agents/{agent_id}/deactivate",
            timeout=10.0,
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    @classmethod
    def _build_capability(cls, agent: dict) -> dict | None:
        metadata = agent.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        configuration = agent.get("configuration")
        if not isinstance(configuration, dict):
            configuration = {}

        routing = agent.get("routing")
        if not isinstance(routing, dict):
            routing = metadata.get("routing")
        if not isinstance(routing, dict):
            routing = configuration.get("routing")
        if not isinstance(routing, dict):
            routing = {}

        queue_metadata = cls._get_dict(
            agent.get("queue_metadata"),
            metadata.get("queue_metadata"),
            configuration.get("queue_metadata"),
        )

        agent_type = (
            metadata.get("agent_type")
            or metadata.get("logical_registry_name")
            or agent.get("agent_type")
            or configuration.get("agent_type")
            or agent.get("name")
        )
        if not isinstance(agent_type, str) or not agent_type:
            return None

        semantic_intents = cls._get_list(
            agent.get("semantic_intents"),
            metadata.get("semantic_intents"),
        )

        return {
            "agent_type": agent_type,
            "name": agent.get("name") or agent_type,
            "description": agent.get("description") or f"Agent: {agent_type}",
            "input_schema": cls._get_dict(
                agent.get("input_schema"), routing.get("input_schema")
            )
            or {"type": "object", "properties": {}},
            "output_schema": cls._get_dict(
                agent.get("output_schema"), routing.get("output_schema")
            ),
            "required_parameters": cls._get_list(
                agent.get("required_parameters"), routing.get("required_parameters")
            ),
            "negative_examples": cls._get_list(
                agent.get("negative_examples"), routing.get("negative_examples")
            ),
            "timeout_seconds": agent.get("timeout_seconds")
            or routing.get("timeout_seconds")
            or 300.0,
            "required_confirmation": agent.get("required_confirmation")
            if isinstance(agent.get("required_confirmation"), bool)
            else routing.get("required_confirmation", True),
            "semantic_intents": semantic_intents,
            "queue_metadata": queue_metadata,
        }

    @staticmethod
    def _get_dict(*values: object) -> dict:
        for value in values:
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _get_list(*values: object) -> list:
        for value in values:
            if isinstance(value, list):
                return value
        return []

    @staticmethod
    def _safe_description(description: str) -> str:
        if len(description) <= _MAX_REGISTRY_DESCRIPTION_LENGTH:
            return description
        return description[: _MAX_REGISTRY_DESCRIPTION_LENGTH - 3] + "..."

    @staticmethod
    def _build_runtime_create_payload(
        registration: AgentRegistration,
    ) -> dict[str, object]:
        runtime_config = registration.agent_runtime_config
        if runtime_config is None:
            return {}

        payload: dict[str, object] = {}

        if runtime_config.llm_model:
            payload["model"] = runtime_config.llm_model
        if runtime_config.llm_provider:
            payload["provider"] = runtime_config.llm_provider
        if runtime_config.llm_temperature is not None:
            payload["temperature"] = runtime_config.llm_temperature
        if runtime_config.llm_max_tokens is not None:
            payload["max_tokens"] = runtime_config.llm_max_tokens
        if runtime_config.llm_timeout is not None:
            payload["timeout_ms"] = int(runtime_config.llm_timeout * 1000)
        if runtime_config.max_concurrency is not None:
            payload["max_concurrency"] = runtime_config.max_concurrency
        if runtime_config.llm_max_retries is not None:
            payload["retry_count"] = runtime_config.llm_max_retries

        return payload

    @staticmethod
    def _build_runtime_update_payload(
        registration: AgentRegistration,
    ) -> dict[str, object]:
        runtime_config = registration.agent_runtime_config
        if runtime_config is None:
            return {}

        payload: dict[str, object] = {}

        if runtime_config.llm_model:
            payload["model"] = runtime_config.llm_model
        if runtime_config.llm_provider:
            payload["provider"] = runtime_config.llm_provider
        if runtime_config.llm_temperature is not None:
            payload["temperature"] = runtime_config.llm_temperature

        return payload

    @staticmethod
    def _registration_logical_name(registration: AgentRegistration) -> str:
        metadata = registration.metadata or {}
        logical_name = metadata.get("logical_registry_name")
        if isinstance(logical_name, str) and logical_name:
            return logical_name
        agent_type = metadata.get("agent_type")
        if isinstance(agent_type, str) and agent_type:
            return agent_type
        if "__" in registration.agent_type:
            return registration.agent_type.rsplit("__", 1)[0]
        return registration.agent_type

    @staticmethod
    def _registration_definition_hash(registration: AgentRegistration) -> str:
        metadata = registration.metadata or {}
        existing_hash = metadata.get("definition_hash")
        if isinstance(existing_hash, str) and existing_hash:
            return existing_hash

        return HttpAgentRegistry._hash_definition_payload(
            HttpAgentRegistry._registration_definition_payload(registration)
        )

    @staticmethod
    def _agent_definition_hash(agent: dict[str, Any]) -> str:
        metadata = agent.get("metadata")
        if isinstance(metadata, dict):
            existing_hash = metadata.get("definition_hash")
            if isinstance(existing_hash, str) and existing_hash:
                return existing_hash

        return HttpAgentRegistry._hash_definition_payload(
            HttpAgentRegistry._agent_definition_payload(agent)
        )

    @staticmethod
    def _registration_definition_payload(
        registration: AgentRegistration,
    ) -> dict[str, Any]:
        metadata = dict(registration.metadata or {})
        functional_metadata = {
            key: metadata[key]
            for key in (
                "routing",
                "agent_runtime_config",
                "execution_policy",
                "queue_metadata",
            )
            if key in metadata
        }
        return {
            "agent_type": HttpAgentRegistry._registration_logical_name(registration),
            "description": HttpAgentRegistry._build_description(registration),
            "kind": registration.kind,
            "domain": registration.domain,
            "capabilities": registration.capabilities,
            "tool_ids": registration.tool_ids,
            "attached_agent_ids": registration.attached_agent_ids,
            "runtime_config": registration.agent_runtime_config,
            "execution_policy": registration.execution_policy,
            "queue_metadata": registration.queue_metadata,
            "metadata": functional_metadata,
        }

    @staticmethod
    def _agent_definition_payload(agent: dict[str, Any]) -> dict[str, Any]:
        metadata = agent.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        logical_name = (
            metadata.get("agent_type")
            or metadata.get("logical_registry_name")
            or agent.get("agent_type")
            or agent.get("name")
            or ""
        )
        functional_metadata = {
            key: metadata[key]
            for key in (
                "routing",
                "agent_runtime_config",
                "execution_policy",
                "queue_metadata",
            )
            if key in metadata
        }
        return {
            "agent_type": logical_name,
            "description": agent.get("description")
            or metadata.get("description")
            or "",
            "kind": agent.get("kind") or "",
            "domain": metadata.get("agent_domain") or agent.get("domain") or "",
            "capabilities": agent.get("capabilities") or [],
            "tool_ids": agent.get("tools") or agent.get("tool_ids") or [],
            "attached_agent_ids": agent.get("agents")
            or agent.get("attached_agent_ids")
            or [],
            "runtime_config": metadata.get("agent_runtime_config"),
            "execution_policy": metadata.get("execution_policy"),
            "queue_metadata": metadata.get("queue_metadata"),
            "metadata": functional_metadata,
        }

    @staticmethod
    def _hash_definition_payload(payload: dict[str, Any]) -> str:
        normalized = HttpAgentRegistry._normalize_definition_value(payload)
        canonical = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_definition_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, Enum):
            return HttpAgentRegistry._normalize_definition_value(value.value)

        if is_dataclass(value) and not isinstance(value, type):
            return HttpAgentRegistry._normalize_definition_value(asdict(value))

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return HttpAgentRegistry._normalize_definition_value(
                    model_dump(mode="json", exclude_none=False)
                )
            except TypeError:
                try:
                    return HttpAgentRegistry._normalize_definition_value(model_dump())
                except Exception:
                    pass
            except Exception:
                pass

        if isinstance(value, Mapping):
            return {
                str(key): HttpAgentRegistry._normalize_definition_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }

        if isinstance(value, (list, tuple)):
            return [
                HttpAgentRegistry._normalize_definition_value(item) for item in value
            ]

        if isinstance(value, (set, frozenset)):
            normalized_items = [
                HttpAgentRegistry._normalize_definition_value(item) for item in value
            ]
            return sorted(
                normalized_items,
                key=lambda item: json.dumps(
                    item,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            )

        try:
            public_attrs = {
                key: item
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
        except TypeError:
            public_attrs = {}
        if public_attrs:
            value_type = type(value)
            return {
                "__type__": f"{value_type.__module__}.{value_type.__qualname__}",
                "attributes": HttpAgentRegistry._normalize_definition_value(
                    public_attrs
                ),
            }

        rendered = str(value)
        return re.sub(r" at 0x[0-9A-Fa-f]+", " at 0x<addr>", rendered)

    @staticmethod
    def _latest_agent(agents: list[dict[str, Any]]) -> dict[str, Any]:
        return max(
            agents,
            key=lambda agent: HttpAgentRegistry._version_sort_key(
                HttpAgentRegistry._agent_version(agent)
            ),
        )

    @staticmethod
    def _agent_id(agent: dict[str, Any] | None) -> str | None:
        if not isinstance(agent, dict):
            return None
        agent_id = agent.get("id") or agent.get("agent_id")
        return agent_id if isinstance(agent_id, str) and agent_id else None

    @staticmethod
    def _agent_name(agent: dict[str, Any]) -> str:
        name = agent.get("name")
        return name if isinstance(name, str) else ""

    @staticmethod
    def _agent_version(agent: dict[str, Any]) -> str:
        version = agent.get("version")
        if isinstance(version, str) and version:
            return version

        name = agent.get("name")
        if isinstance(name, str) and "__" in name:
            return name.rsplit("__", 1)[-1]
        return ""

    @staticmethod
    def _next_version(existing_versions: list[str], base_version: str) -> str:
        versions = [version for version in existing_versions if version]
        if base_version:
            versions.append(base_version)
        latest = (
            max(versions, key=HttpAgentRegistry._version_sort_key)
            if versions
            else "0.10"
        )
        return HttpAgentRegistry._bump_version(latest)

    @staticmethod
    def _bump_version(version: str) -> str:
        stripped = version.strip()
        two_part = re.fullmatch(r"(\d+)\.(\d+)", stripped)
        if two_part:
            major = int(two_part.group(1))
            minor_text = two_part.group(2)
            minor_width = max(len(minor_text), 2)
            minor = int(minor_text) + 10
            return f"{major}.{minor:0{minor_width}d}"

        semver = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(.*)", stripped)
        if semver:
            major, minor, patch = semver.group(1), semver.group(2), semver.group(3)
            return f"{major}.{minor}.{int(patch) + 1}"

        integer = re.fullmatch(r"(\d+)", stripped)
        if integer:
            return str(int(integer.group(1)) + 1)

        return f"{stripped}.1" if stripped else "0.10"

    @staticmethod
    def _version_sort_key(version: str) -> tuple[int, tuple[int, ...], str]:
        numeric_parts = tuple(int(part) for part in re.findall(r"\d+", version or ""))
        return (1 if numeric_parts else 0, numeric_parts, version or "")

    @staticmethod
    def _build_versioned_registry_name(logical_registry_name: str, version: str) -> str:
        return f"{logical_registry_name}__{version}"

    @staticmethod
    def _unique_agent_ids(agent_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for agent_id in agent_ids:
            if not agent_id or agent_id in seen:
                continue
            seen.add(agent_id)
            ordered.append(agent_id)
        return ordered

    @staticmethod
    def _extract_created_id(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        agent_id = payload.get("id") or payload.get("agent_id")
        return agent_id if isinstance(agent_id, str) and agent_id else None
