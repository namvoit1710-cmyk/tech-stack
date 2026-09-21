import asyncio
import time

import httpx
from typing import Any, Optional

from app.layer1_domain.entities.agent.scored_agent import ScoredAgent, AgentKind, AgentStatus
from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Dict, Any

from app.layer2_application.interfaces.agent_registry_client import IAgentRegistryClient

class ScoredAgentDTOResponse(BaseModel):
    agents: list[ScoredAgent] = Field(default_factory=list)
    total: int = 0


class AgentRegistryClient(IAgentRegistryClient):
    """HTTP adapter calling L3 Agent Registry REST API.

    Connection management:
        - Timeout: 10s per request
        - Retry: 3x with exponential backoff (1s, 2s, 4s)
        - Circuit breaker: 3 consecutive failures -> open for 30s
    """

    def __init__(self, base_url: str, logger, timeout: float = 10):
        self._base_url = base_url.rstrip("/")
        self._logger = logger
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            limits=httpx.Limits(max_connections=20),
        )
        self._failure_count = 0
        self._circuit_open_until: float = 0.0
        self._circuit_threshold = 3
        self._circuit_reset_sec = 30.0
        # SA-892: positive cache of tool name -> id. Tool ids are stable per
        # environment, so a resolved id is cached forever; a miss is NOT cached so a
        # transient registry outage simply retries on the next stop.
        self._tool_id_by_name: dict[str, str] = {}

    async def filter_agents(
        self,
        *,
        kind: AgentKind | None = None,
        status: AgentStatus | None = None,
        is_published: bool | None = None,
    ) -> list[ScoredAgent]:
        params = {
            key: value
            for key, value in {
                "kind": kind.value if kind is not None else None,
                "status": status.value if status is not None else None,
                "is_published": is_published,
            }.items()
            if value is not None
        }
        data = await self._get("/api/v1/agents/filter", params=params)
        items = data.get("agents", []) if isinstance(data, dict) else []
        return [self._to_scored_agent(item) for item in items]

    async def _get(self, path: str, params: dict = {}) -> Optional[Any]:
        return await self._request("GET", path, params=params)

    async def _request(self, method: str, path: str, **kwargs) -> Optional[Any]:
        if self._circuit_open_until > time.monotonic():
            self._logger.warning("Circuit breaker OPEN for Agent Registry")
            return None

        for attempt in range(3):
            try:
                response = await self._client.request(method, path, **kwargs)
                response.raise_for_status()
                self._failure_count = 0
                return response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                self._logger.warning(
                    "Registry HTTP error",
                    status=e.response.status_code,
                    path=path,
                    attempt=f"{attempt + 1}/3",
                )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                self._logger.warning(
                    "Registry connection error",
                    path=path,
                    attempt=f"{attempt + 1}/3",
                    error=str(e),
                )

            if attempt < 2:
                await asyncio.sleep(2 ** attempt)

        self._failure_count += 1
        if self._failure_count >= self._circuit_threshold:
            self._circuit_open_until = time.monotonic() + self._circuit_reset_sec
            self._logger.error("Circuit breaker OPENED for Agent Registry (30s)")

        return None

    @staticmethod
    def _to_scored_agent(item: dict):
        return ScoredAgent(
            id=item.get("id", ""),
            name=item.get("name", ""),
            kind=AgentKind(item.get("kind", "")),
            status=item.get("status", ""),
            is_published=item.get("is_published", False),
            description=item.get("description", ""),
            healthcheck_endpoint=item.get("healthcheck_endpoint", ""),
            invoke_endpoint=item.get("invoke_endpoint", ""),
            is_alive=item.get("is_alive", False),
            last_heartbeat=item.get("last_heartbeat"),
            version=item.get("version", ""),
            config_type=item.get("config_type", ""),
            capabilities=item.get("capabilities", []),
            agents=item.get("agents", []),
            workflows=item.get("workflows", []),
            tools=item.get("tools", []),
            metadata=item.get("metadata") or {},
            business=item.get("business", ""),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
        )

    async def get_tool_id_by_name(self, name: str) -> str | None:
        """SA-892: resolve a registered tool's id by its BARE tool name.

        The match is on ``metadata.tool_name`` — the SAME value the executor's
        ``HttpCapabilityResolver`` maps to the local tool. ``GET /tools/all`` returns
        only SUMMARY entries (slug ``name``, no ``metadata.tool_name``), so we take
        the ACTIVE ids and fetch their FULL records via ``POST /tools/by_ids``, then
        match ``metadata.tool_name == name`` (preferring an ``executor-runtime``
        owner when several match). We do NOT parse the slug ``name`` — the tool name
        is authoritative only in ``metadata.tool_name``.

        Uses its OWN short-lived ``httpx.AsyncClient`` (bound to the calling loop) so
        it never trips the shared client's event-loop binding. Returns the bare id,
        or ``None`` on miss / registry unreachable (never raises)."""
        if not name:
            return None
        cached = self._tool_id_by_name.get(name)
        if cached is not None:
            return cached
        if not self._base_url:
            return None

        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=10.0) as client:
                summary = await client.get("/api/v1/tools/all")
                summary.raise_for_status()
                tools = (summary.json() or {}).get("tools", []) or []

                active_ids = [
                    tool["id"]
                    for tool in tools
                    if isinstance(tool, dict)
                    and tool.get("id")
                    and str(tool.get("status", "")).upper() == "ACTIVE"
                ]
                if not active_ids:
                    return None

                full_resp = await client.post(
                    "/api/v1/tools/by_ids", json=active_ids
                )
                full_resp.raise_for_status()
                full_tools = (full_resp.json() or {}).get("tools", []) or []
                # authoritative match on metadata.tool_name; prefer executor-runtime.
                full_tools.sort(
                    key=lambda t: 0
                    if "__executor-runtime__" in str(t.get("name") or "")
                    else 1
                )
                resolved: str | None = None
                for tool in full_tools:
                    md = tool.get("metadata") or {}
                    if md.get("tool_name") == name and tool.get("id"):
                        resolved = tool["id"]
                        break
                if resolved is None:
                    return None
        except Exception as exc:  # never break the stop path
            self._logger.warning(
                "get_tool_id_by_name failed", tool_name=name, error=str(exc)
            )
            return None

        self._tool_id_by_name[name] = resolved
        return resolved

    async def close(self):
        await self._client.aclose()

    async def get_available_agents(self) -> list[ScoredAgent]:
        raw = await self._get("/api/v1/agents/available")

        if raw is None:
            return []

        data = ScoredAgentDTOResponse.model_validate(raw)
        return data.agents