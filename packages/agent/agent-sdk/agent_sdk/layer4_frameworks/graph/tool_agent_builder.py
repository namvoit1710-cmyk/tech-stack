import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from agent_sdk.layer1_domain.value_objects.transport_state import TransportState
from agent_sdk.layer2_application.interfaces.llm_service import ILLMService
from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder
from agent_sdk.layer4_frameworks.graph.tool_agent_state import ToolAgentState

if TYPE_CHECKING:
    from agent_sdk.layer4_frameworks.ai.context_budget import ContextBudgetManager

logger = logging.getLogger(__name__)


def _callable_accepts_kwarg(fn: Any, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    if name in params:
        return True
    return any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())


async def _ainvoke_with_optional_config(
    llm: Any,
    messages: Any,
    config: dict[str, Any],
) -> Any:
    ainvoke = getattr(llm, "ainvoke")
    if _callable_accepts_kwarg(ainvoke, "config"):
        return await ainvoke(messages, config=config)
    return await ainvoke(messages)


class ToolAgentBuilder:
    @staticmethod
    def _build_payload_types(messages) -> dict:
        from agent_sdk.layer1_domain.entities.context_config import PayloadType

        payload_types = {}
        for i, msg in enumerate(messages):
            if hasattr(msg, "type") and msg.type == "tool":
                name = getattr(msg, "name", "") or ""
            elif isinstance(msg, dict):
                name = msg.get("name", "") or ""
            else:
                name = ""
            if name.startswith("call_"):
                payload_types[i] = PayloadType.BUSINESS_DATA
        return payload_types

    def __init__(
        self,
        tools: list[BaseTool],
        llm: Any = None,
        llm_service: Optional[ILLMService] = None,
        system_prompt: str = "",
        pre_nodes: list[tuple[str, Callable]] | None = None,
        post_nodes: list[tuple[str, Callable]] | None = None,
        context_budget: Optional["ContextBudgetManager"] = None,
        system_reminder: Optional[str] = None,
        system_reminder_threshold: int = 6,
    ):
        if llm is not None:
            resolved_llm = llm
        elif llm_service is not None:
            resolved_llm = llm_service.get_chat_client()
        else:
            raise ValueError(
                "ToolAgentBuilder requires either 'llm' or 'llm_service'. "
                "Provide an explicit LangChain chat model via 'llm', or an ILLMService "
                "implementation via 'llm_service'."
            )
        self._llm = resolved_llm
        self._tools = tools
        self._system_prompt = system_prompt
        self._pre_nodes = pre_nodes or []
        self._post_nodes = post_nodes or []
        self._context_budget = context_budget
        self._system_reminder = system_reminder
        self._system_reminder_threshold = system_reminder_threshold

    @staticmethod
    def _build_llm_usage_config(
        state: ToolAgentState, config: Any | None
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(config or {}) if isinstance(config, dict) else {}
        metadata = dict(merged.get("metadata") or {})
        configurable = dict(merged.get("configurable") or {})

        conv_id = state.get("conv_id") or configurable.get("conv_id")
        if conv_id:
            metadata.setdefault("conversation_id", conv_id)
            metadata.setdefault("thread_id", conv_id)
            configurable.setdefault("conv_id", conv_id)
            configurable.setdefault("thread_id", conv_id)
        correlation_id = state.get("correlation_id") or configurable.get(
            "correlation_id"
        )
        if correlation_id:
            metadata.setdefault("correlation_id", correlation_id)
            configurable.setdefault("correlation_id", correlation_id)
        agent_type = state.get("agent_type") or configurable.get("agent_type")
        if agent_type:
            metadata.setdefault("agent_type", agent_type)
            configurable.setdefault("agent_type", agent_type)

        merged["metadata"] = metadata
        merged["configurable"] = configurable
        return merged

    def compile(
        self,
        checkpointer: Any | None = None,
        interrupt_before: list[str] | str | None = None,
        interrupt_after: list[str] | str | None = None,
    ) -> Any:
        llm_with_tools = self._llm.bind_tools(self._tools)
        system_prompt = self._system_prompt
        context_budget = self._context_budget
        system_reminder = self._system_reminder
        system_reminder_threshold = self._system_reminder_threshold

        def prepare_messages(state: ToolAgentState) -> dict:
            messages = state.get("messages", [])
            if not messages:
                msgs = []
                if system_prompt:
                    msgs.append(SystemMessage(content=system_prompt))
                user_msg = state.get("message", "")
                msgs.append(HumanMessage(content=user_msg))
                return {
                    "messages": msgs,
                    "transport_state": TransportState.PROCESSING.value,
                }
            return {"transport_state": TransportState.PROCESSING.value}

        async def call_llm(state: ToolAgentState, config: Any | None = None) -> dict:
            messages = state.get("messages", [])

            if context_budget is not None:
                payload_types = ToolAgentBuilder._build_payload_types(messages)
                pre_compaction_count = len(messages)
                messages, warnings = await context_budget.compact(
                    messages, payload_types=payload_types
                )
                payload_types = ToolAgentBuilder._build_payload_types(messages)
                if warnings:
                    logger.info("Context budget warnings: %s", warnings)
            else:
                pre_compaction_count = len(messages)

            if system_reminder and pre_compaction_count >= system_reminder_threshold:
                messages = list(messages) + [SystemMessage(content=system_reminder)]

            response = await _ainvoke_with_optional_config(
                llm_with_tools,
                messages,
                ToolAgentBuilder._build_llm_usage_config(state, config),
            )
            if response.tool_calls:
                from agent_sdk.layer1_domain.entities.workflow_event import (
                    ToolSelectedEvent,
                )
                from agent_sdk.layer2_application.services.workflow_event_runtime import (
                    emit_workflow_event,
                )

                for tc in response.tool_calls:
                    tool_name = (
                        tc.get("name", "")
                        if isinstance(tc, dict)
                        else getattr(tc, "name", "")
                    )
                    await emit_workflow_event(
                        ToolSelectedEvent(data={"tool": tool_name})
                    )
            return {"messages": [response]}

        def format_final_response(state: ToolAgentState) -> dict:
            messages = state.get("messages", [])
            last_msg = messages[-1] if messages else None
            content = getattr(last_msg, "content", str(last_msg)) if last_msg else ""
            tool_results = []
            for msg in messages:
                if hasattr(msg, "type") and msg.type == "tool":
                    tool_results.append(
                        {"tool": getattr(msg, "name", ""), "result": msg.content}
                    )
            return {
                "formatted_response": {
                    "content": content,
                    "tool_results": tool_results,
                },
                "tool_results": tool_results,
                "transport_state": TransportState.COMPLETED.value,
            }

        builder = AgentGraphBuilder(state_schema=ToolAgentState)
        builder.add_tools(self._tools)
        for name, fn in self._pre_nodes:
            builder.add_node(name, fn)
        builder.add_node("prepare_messages", prepare_messages)
        builder.add_node("call_llm", call_llm)
        builder.add_tool_node("tools")
        builder.add_node("format_response", format_final_response)
        for name, fn in self._post_nodes:
            builder.add_node(name, fn)
        if self._pre_nodes:
            builder.set_entry_point(self._pre_nodes[0][0])
            for i in range(len(self._pre_nodes) - 1):
                builder.add_edge(self._pre_nodes[i][0], self._pre_nodes[i + 1][0])
            builder.add_edge(self._pre_nodes[-1][0], "prepare_messages")
        else:
            builder.set_entry_point("prepare_messages")
        builder.add_edge("prepare_messages", "call_llm")
        builder.add_tools_condition(
            "call_llm", tools_node="tools", next_node="format_response"
        )
        builder.add_edge("tools", "call_llm")
        if self._post_nodes:
            builder.add_edge("format_response", self._post_nodes[0][0])
            for i in range(len(self._post_nodes) - 1):
                builder.add_edge(self._post_nodes[i][0], self._post_nodes[i + 1][0])
            from langgraph.graph import END

            builder.add_edge(self._post_nodes[-1][0], END)
        else:
            from langgraph.graph import END

            builder.add_edge("format_response", END)
        return builder.compile(
            checkpointer=checkpointer,
            interrupt_before=interrupt_before,
            interrupt_after=interrupt_after,
        )
