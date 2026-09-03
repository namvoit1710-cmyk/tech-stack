import asyncio
import time
from typing import Any, Optional

from langchain_core.tools import BaseTool
from langgraph.types import interrupt
from pydantic import PrivateAttr

from agent_sdk.layer2_application.interfaces.agent_registry import IAgentRegistry


class RemoteAgentTool(BaseTool):
    remote_agent_type: str
    resolve_ttl_seconds: float = 0.0  # 0 = cache forever (backward compat)
    _registry: Any = PrivateAttr()
    _resolved_id: Optional[str] = PrivateAttr(default=None)
    _resolved_at: float = PrivateAttr(default=0.0)
    _resolve_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    def __init__(
        self,
        remote_agent_type: str,
        registry: IAgentRegistry,
        name: str,
        description: str,
        resolve_ttl_seconds: float = 0.0,
        **kwargs: Any,
    ):
        super().__init__(
            remote_agent_type=remote_agent_type,
            resolve_ttl_seconds=resolve_ttl_seconds,
            name=name,
            description=description,
            **kwargs,
        )
        self._registry = registry

    def invalidate_cached_resolution(self) -> None:
        self._resolved_id = None
        self._resolved_at = 0.0

    async def _resolve(self) -> str:
        async with self._resolve_lock:
            now = time.monotonic()
            cache_valid = self._resolved_id is not None and (
                self.resolve_ttl_seconds <= 0
                or (now - self._resolved_at) < self.resolve_ttl_seconds
            )
            if not cache_valid:
                resolved = await self._registry.resolve_agent_id(self.remote_agent_type)
                if not resolved:
                    raise ValueError(
                        f"Could not resolve agent_id for type: {self.remote_agent_type}"
                    )
                self._resolved_id = resolved
                self._resolved_at = now
        assert self._resolved_id is not None
        return self._resolved_id

    def _run(self, input: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "RemoteAgentTool requires async execution (_arun) for agent_type resolution."
        )

    async def _arun(self, input: Any, **kwargs: Any) -> Any:
        agent_id = await self._resolve()
        payload = {
            "type": "AGENT_CALL",
            "agent_id": agent_id,
            "agent_type": self.remote_agent_type,
            "input": input,
            "message": f"Calling agent {self.remote_agent_type} ({agent_id})",
        }
        for key in (
            "correlation_id",
            "reply_queue",
            "reply_topic",
            "session_id",
            "context_snapshot",
            "metadata",
        ):
            if key in kwargs and kwargs[key] is not None:
                payload[key] = kwargs[key]
        return interrupt(payload)
