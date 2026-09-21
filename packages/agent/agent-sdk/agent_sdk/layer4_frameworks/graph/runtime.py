from __future__ import annotations

from typing import Any

from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from agent_sdk.layer2_application.interfaces.agent_runtime import (
    AgentRuntimeInterrupt,
    AgentRuntimeResult,
)

__all__ = [
    "AgentRuntimeInterrupt",
    "AgentRuntimeResult",
    "LangGraphRuntime",
]


def _normalize_graph_interrupt(exc: BaseException) -> AgentRuntimeResult | None:
    interrupt_exc = None
    if isinstance(exc, GraphInterrupt):
        interrupt_exc = exc
    elif isinstance(exc, BaseExceptionGroup):
        matched, _ = exc.split(GraphInterrupt)
        if matched:
            interrupt_exc = matched.exceptions[0]
    if interrupt_exc is None:
        return None
    return AgentRuntimeResult(
        output=None,
        interrupted=True,
        interrupt_payload=AgentRuntimeInterrupt(payload=interrupt_exc),
    )


class LangGraphRuntime:
    def __init__(self, compiled_graph: Any) -> None:
        self._graph = compiled_graph

    async def invoke(
        self, state: dict, config: dict | None = None
    ) -> AgentRuntimeResult:
        try:
            result = await self._graph.ainvoke(state, config or {})
            return AgentRuntimeResult(output=result)
        except BaseException as exc:
            normalized = _normalize_graph_interrupt(exc)
            if normalized is not None:
                return normalized
            raise

    async def resume(
        self,
        resume_value: Any,
        config: dict | None = None,
        interrupt_id: str | None = None,
    ) -> AgentRuntimeResult:
        if interrupt_id:
            command = Command(resume={interrupt_id: resume_value})
        else:
            command = Command(resume=resume_value)
        try:
            result = await self._graph.ainvoke(command, config or {})
            return AgentRuntimeResult(output=result)
        except BaseException as exc:
            normalized = _normalize_graph_interrupt(exc)
            if normalized is not None:
                return normalized
            raise

    async def stream(self, state: dict, config: dict | None = None):
        async for chunk in self._graph.astream(state, config or {}):
            yield chunk
