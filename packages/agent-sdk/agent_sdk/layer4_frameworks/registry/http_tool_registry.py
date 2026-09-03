from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any

import httpx

from agent_sdk.layer1_domain.entities.tool_registration import ToolRegistration
from agent_sdk.layer1_domain.exceptions import RegistrationError
from agent_sdk.layer2_application.interfaces.tool_registry import IToolRegistry
from agent_sdk.layer4_frameworks.config.app_config import settings

_DEFAULT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
_DEFINITION_HASH_ALGORITHM = "sha256:v1"


class HttpToolRegistry(IToolRegistry):
    def __init__(self) -> None:
        self.base_url = settings.REGISTRY_URL.rstrip("/")
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=10.0,
        )

    async def sync_tools(
        self,
        registrations: list[ToolRegistration],
    ) -> dict[str, str]:
        try:
            existing_tools = await self._list_existing_tools()

            synced_ids: dict[str, str] = {}
            for registration in registrations:
                selected_registration, selected_tool = self._select_existing_tool(
                    registration,
                    existing_tools,
                )

                if selected_tool is not None:
                    tool_id = selected_tool["id"]
                    if selected_tool.get("status") != "active":
                        await self._activate_tool(tool_id)
                        selected_tool["status"] = "active"
                    await self._deactivate_stale_versions(
                        selected_tool_id=tool_id,
                        registration=selected_registration,
                        existing_tools=existing_tools,
                    )
                    synced_ids[registration.tool_name] = tool_id
                    continue

                create_response = await self._client.post(
                    f"{self.base_url}/api/v1/tools/register",
                    json=self._build_create_payload(selected_registration),
                    timeout=10.0,
                )

                try:
                    create_response.raise_for_status()
                except httpx.HTTPStatusError:
                    recovered_id = await self._recover_existing_id(
                        selected_registration.registry_name
                    )
                    if recovered_id:
                        await self._activate_tool(recovered_id)
                        existing_tools[selected_registration.registry_name] = {
                            "id": recovered_id,
                            "name": selected_registration.registry_name,
                            "version": selected_registration.version,
                            "status": "active",
                            "description": selected_registration.description,
                            "metadata": dict(selected_registration.metadata),
                            "input_data": selected_registration.parameters_schema
                            or dict(_DEFAULT_SCHEMA),
                            "output_data": selected_registration.response_schema
                            or dict(_DEFAULT_SCHEMA),
                        }
                        await self._deactivate_stale_versions(
                            selected_tool_id=recovered_id,
                            registration=selected_registration,
                            existing_tools=existing_tools,
                        )
                        synced_ids[registration.tool_name] = recovered_id
                        continue
                    raise

                tool_id = self._extract_created_id(create_response)
                if not tool_id:
                    tool_id = await self._recover_existing_id(
                        selected_registration.registry_name
                    )

                if not tool_id:
                    raise RegistrationError(
                        "Registry tool creation did not return an id"
                    )

                existing_tools[selected_registration.registry_name] = {
                    "id": tool_id,
                    "name": selected_registration.registry_name,
                    "version": selected_registration.version,
                    "status": "active",
                    "description": selected_registration.description,
                    "metadata": dict(selected_registration.metadata),
                    "input_data": selected_registration.parameters_schema
                    or dict(_DEFAULT_SCHEMA),
                    "output_data": selected_registration.response_schema
                    or dict(_DEFAULT_SCHEMA),
                }
                await self._deactivate_stale_versions(
                    selected_tool_id=tool_id,
                    registration=selected_registration,
                    existing_tools=existing_tools,
                )
                synced_ids[registration.tool_name] = tool_id

            return synced_ids

        except httpx.HTTPStatusError as exc:
            raise RegistrationError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise RegistrationError(str(exc)) from exc

    async def activate_tools(self, tool_ids: list[str]) -> None:
        for tool_id in self._unique_tool_ids(tool_ids):
            await self._activate_tool(tool_id)

    async def deactivate_tools(self, tool_ids: list[str]) -> None:
        for tool_id in self._unique_tool_ids(tool_ids):
            await self._deactivate_tool(tool_id)

    def _select_existing_tool(
        self,
        registration: ToolRegistration,
        existing_tools: dict[str, dict[str, Any]],
    ) -> tuple[ToolRegistration, dict[str, Any] | None]:
        candidates = self._find_logical_candidates(registration, existing_tools)
        current_hash = self._registration_definition_hash(registration)

        matching = [
            tool
            for tool in candidates
            if self._tool_definition_hash(tool) == current_hash
        ]
        if matching:
            selected_tool = self._latest_tool(matching)
            selected_registration = replace(
                registration,
                registry_name=self._tool_name(selected_tool)
                or registration.registry_name,
                version=self._tool_version(selected_tool) or registration.version,
            )
            return selected_registration, selected_tool

        if not candidates:
            return registration, None

        bumped_version = self._next_version(
            existing_versions=[
                self._tool_version(tool)
                for tool in candidates
                if self._tool_version(tool)
            ],
            base_version=registration.version,
        )
        logical_registry_name = self._registration_logical_name(registration)
        bumped_registry_name = self._build_versioned_registry_name(
            logical_registry_name,
            bumped_version,
        )
        bumped_metadata = dict(registration.metadata or {})
        bumped_metadata.update(
            {
                "owner_version": bumped_version,
                "registry_version_bumped_from": registration.version,
                "registry_version_bump_reason": "definition_hash_changed",
            }
        )
        bumped_registration = replace(
            registration,
            registry_name=bumped_registry_name,
            version=bumped_version,
            metadata=bumped_metadata,
        )
        existing_same_name = existing_tools.get(bumped_registry_name)
        return bumped_registration, existing_same_name

    def _build_create_payload(
        self, registration: ToolRegistration
    ) -> dict[str, object]:
        metadata = dict(registration.metadata or {})
        metadata.setdefault(
            "definition_hash", self._registration_definition_hash(registration)
        )
        metadata.setdefault("definition_hash_algorithm", _DEFINITION_HASH_ALGORITHM)
        metadata.setdefault(
            "logical_registry_name", self._registration_logical_name(registration)
        )

        return {
            "name": registration.registry_name,
            "description": registration.description,
            "protocol": "mcp",
            "endpoint": "inline",
            "version": registration.version,
            "status": "active",
            "input_data": registration.parameters_schema or dict(_DEFAULT_SCHEMA),
            "output_data": registration.response_schema or dict(_DEFAULT_SCHEMA),
            "auth_config": {},
            "metadata": metadata,
        }

    def _extract_created_id(self, response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        tool_id = payload.get("id")
        return tool_id if isinstance(tool_id, str) and tool_id else None

    async def _recover_existing_id(self, registry_name: str) -> str | None:
        try:
            existing_ids = await self._list_existing_ids()
        except httpx.HTTPError:
            return None

        return existing_ids.get(registry_name)

    async def _list_existing_ids(self) -> dict[str, str]:
        return {
            name: tool["id"]
            for name, tool in (await self._list_existing_tools()).items()
        }

    async def _list_existing_tools(self) -> dict[str, dict[str, Any]]:
        response = await self._client.get(
            f"{self.base_url}/api/v1/tools/all",
            timeout=10.0,
        )
        response.raise_for_status()

        payload = response.json()
        if isinstance(payload, list):
            tools = payload
        elif isinstance(payload, dict):
            tools = payload.get("tools") or payload.get("results") or []
        else:
            tools = []

        summaries: list[dict[str, Any]] = [
            tool for tool in tools if isinstance(tool, dict)
        ]
        ids: list[str] = [
            str(tool.get("id")) for tool in summaries if isinstance(tool.get("id"), str)
        ]
        full_by_id = await self._get_tools_by_ids(ids) if ids else {}

        existing: dict[str, dict[str, Any]] = {}
        for summary in summaries:
            tool_id = summary.get("id")
            name = summary.get("name")
            if not isinstance(name, str) or not isinstance(tool_id, str):
                continue

            full = full_by_id.get(tool_id, {})
            merged: dict[str, Any] = {**summary, **full}
            status = merged.get("status")
            merged["id"] = tool_id
            merged["name"] = name
            merged["status"] = status if isinstance(status, str) else ""
            existing[name] = merged
        return existing

    async def _get_tools_by_ids(self, tool_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique_ids = self._unique_tool_ids(tool_ids)
        if not unique_ids:
            return {}

        response = await self._client.post(
            f"{self.base_url}/api/v1/tools/by_ids",
            json=unique_ids,
            timeout=10.0,
        )
        if response.status_code == 404:
            return {}
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError:
            return {}

        if isinstance(payload, list):
            tools = payload
        elif isinstance(payload, dict):
            tools = payload.get("tools") or payload.get("results") or []
        else:
            tools = []

        by_id: dict[str, dict[str, Any]] = {}
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_id = tool.get("id")
            if isinstance(tool_id, str) and tool_id:
                by_id[tool_id] = tool
        return by_id

    def _find_logical_candidates(
        self,
        registration: ToolRegistration,
        existing_tools: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        logical_registry_name = self._registration_logical_name(registration)
        prefix = f"{logical_registry_name}__"
        for name, tool in existing_tools.items():
            if name == registration.registry_name or name.startswith(prefix):
                candidates.append(tool)
                continue

            metadata = tool.get("metadata")
            if (
                isinstance(metadata, dict)
                and metadata.get("logical_registry_name") == logical_registry_name
            ):
                candidates.append(tool)
        return candidates

    async def _deactivate_stale_versions(
        self,
        *,
        selected_tool_id: str,
        registration: ToolRegistration,
        existing_tools: dict[str, dict[str, Any]],
    ) -> None:
        for tool in self._find_logical_candidates(registration, existing_tools):
            tool_id = tool.get("id")
            if not isinstance(tool_id, str) or tool_id == selected_tool_id:
                continue
            if tool.get("status") == "inactive":
                continue
            await self._deactivate_tool(tool_id)
            tool["status"] = "inactive"

    async def _activate_tool(self, tool_id: str) -> None:
        await self._set_tool_status(tool_id, "activate")

    async def _deactivate_tool(self, tool_id: str) -> None:
        await self._set_tool_status(tool_id, "deactivate")

    async def _set_tool_status(self, tool_id: str, action: str) -> None:
        response = await self._client.post(
            f"{self.base_url}/api/v1/tools/{tool_id}/{action}",
            timeout=10.0,
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    @staticmethod
    def _registration_logical_name(registration: ToolRegistration) -> str:
        if registration.logical_registry_name:
            return registration.logical_registry_name
        metadata = registration.metadata or {}
        logical_name = metadata.get("logical_registry_name")
        if isinstance(logical_name, str) and logical_name:
            return logical_name
        if "__" in registration.registry_name:
            return registration.registry_name.rsplit("__", 1)[0]
        return registration.registry_name

    @staticmethod
    def _registration_definition_hash(registration: ToolRegistration) -> str:
        metadata = registration.metadata or {}
        existing_hash = metadata.get("definition_hash")
        if isinstance(existing_hash, str) and existing_hash:
            return existing_hash

        return HttpToolRegistry._hash_definition_payload(
            {
                "tool_name": registration.tool_name,
                "description": registration.description,
                "parameters_schema": registration.parameters_schema
                or dict(_DEFAULT_SCHEMA),
                "response_schema": registration.response_schema
                or dict(_DEFAULT_SCHEMA),
            }
        )

    @staticmethod
    def _tool_definition_hash(tool: dict[str, Any]) -> str:
        metadata = tool.get("metadata")
        if isinstance(metadata, dict):
            existing_hash = metadata.get("definition_hash")
            if isinstance(existing_hash, str) and existing_hash:
                return existing_hash

        return HttpToolRegistry._hash_definition_payload(
            {
                "tool_name": HttpToolRegistry._logical_tool_name(tool),
                "description": tool.get("description") or "",
                "parameters_schema": tool.get("input_data")
                or tool.get("parameters_schema")
                or dict(_DEFAULT_SCHEMA),
                "response_schema": tool.get("output_data")
                or tool.get("response_schema")
                or dict(_DEFAULT_SCHEMA),
            }
        )

    @staticmethod
    def _hash_definition_payload(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _logical_tool_name(tool: dict[str, Any]) -> str:
        metadata = tool.get("metadata")
        if isinstance(metadata, dict):
            tool_name = metadata.get("tool_name")
            if isinstance(tool_name, str) and tool_name:
                return tool_name

        name = tool.get("name")
        if not isinstance(name, str):
            return ""
        parts = name.split("__")
        return parts[-2] if len(parts) >= 2 else name

    @staticmethod
    def _latest_tool(tools: list[dict[str, Any]]) -> dict[str, Any]:
        return max(
            tools,
            key=lambda tool: HttpToolRegistry._version_sort_key(
                HttpToolRegistry._tool_version(tool)
            ),
        )

    @staticmethod
    def _tool_name(tool: dict[str, Any]) -> str:
        name = tool.get("name")
        return name if isinstance(name, str) else ""

    @staticmethod
    def _tool_version(tool: dict[str, Any]) -> str:
        version = tool.get("version")
        if isinstance(version, str) and version:
            return version

        name = tool.get("name")
        if isinstance(name, str) and "__" in name:
            return name.rsplit("__", 1)[-1]
        return ""

    @staticmethod
    def _next_version(existing_versions: list[str], base_version: str) -> str:
        versions = [version for version in existing_versions if version]
        if base_version:
            versions.append(base_version)
        latest = (
            max(versions, key=HttpToolRegistry._version_sort_key)
            if versions
            else "0.10"
        )
        return HttpToolRegistry._bump_version(latest)

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
    def _unique_tool_ids(tool_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for tool_id in tool_ids:
            if not tool_id or tool_id in seen:
                continue
            seen.add(tool_id)
            ordered.append(tool_id)
        return ordered

    async def close(self) -> None:
        await self._client.aclose()
