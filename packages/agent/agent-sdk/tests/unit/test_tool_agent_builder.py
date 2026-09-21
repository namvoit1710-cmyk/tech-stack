import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool as langchain_tool

from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder


@langchain_tool
def mock_add(a: int, b: int) -> str:
    """Add two integers and return the result as a string."""
    return str(a + b)


class MockLLM:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(content="Hello! I can help you with tools.")


class MockLLMWithToolCall:
    def __init__(self):
        self._call_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self._call_count += 1
        if self._call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "mock_add", "args": {"a": 1, "b": 2}, "id": "call_1"}
                ],
            )
        return AIMessage(content="The result is 3.")


def test_compile_creates_runnable_graph():
    builder = ToolAgentBuilder(
        llm=MockLLM(), tools=[mock_add], system_prompt="You are a helper."
    )
    graph = builder.compile()
    assert graph is not None


@pytest.mark.asyncio
async def test_graph_no_tool_call_returns_response():
    builder = ToolAgentBuilder(llm=MockLLM(), tools=[mock_add])
    graph = builder.compile()
    result = await graph.ainvoke({"message": "Hi there"})
    assert result["transport_state"] == "COMPLETED"
    assert "Hello" in result["formatted_response"]["content"]


@pytest.mark.asyncio
async def test_graph_with_tool_call_loops_and_returns():
    builder = ToolAgentBuilder(llm=MockLLMWithToolCall(), tools=[mock_add])
    graph = builder.compile()
    result = await graph.ainvoke({"message": "What is 1+2?"})
    assert result["transport_state"] == "COMPLETED"
    assert "3" in result["formatted_response"]["content"]
    assert len(result["tool_results"]) == 1
    assert result["tool_results"][0]["tool"] == "mock_add"


@pytest.mark.asyncio
async def test_system_prompt_injected():
    builder = ToolAgentBuilder(
        llm=MockLLM(), tools=[mock_add], system_prompt="Be helpful."
    )
    graph = builder.compile()
    result = await graph.ainvoke({"message": "Hi"})
    messages = result.get("messages", [])
    assert any((hasattr(m, "type") and m.type == "system" for m in messages))


def test_call_llm_node_is_async():
    """call_llm node function inside compile() must be an async def coroutine function."""
    builder = ToolAgentBuilder(llm=MockLLM(), tools=[mock_add])
    graph = builder.compile()
    # Access the underlying PregelNode for call_llm
    call_llm_node = graph.nodes.get("call_llm")
    assert call_llm_node is not None, "call_llm node must exist in compiled graph"
    # LangGraph wraps async functions in RunnableCallable with afunc attribute
    bound = getattr(call_llm_node, "bound", None)
    assert bound is not None, "call_llm node must have a bound RunnableCallable"
    afunc = getattr(bound, "afunc", None)
    assert (
        afunc is not None
    ), "call_llm RunnableCallable must have afunc (async function)"
    assert inspect.iscoroutinefunction(
        afunc
    ), f"call_llm afunc must be a coroutine function, got: {afunc}"


@pytest.mark.asyncio
async def test_call_llm_uses_ainvoke():
    """call_llm must call ainvoke (not invoke) on the LLM."""
    llm_mock = MagicMock()
    llm_mock.bind_tools.return_value = llm_mock
    llm_mock.ainvoke = AsyncMock(return_value=AIMessage(content="async response"))
    # Ensure sync invoke raises so we catch any accidental sync usage
    llm_mock.invoke = MagicMock(
        side_effect=RuntimeError("invoke must not be called; use ainvoke")
    )

    builder = ToolAgentBuilder(llm=llm_mock, tools=[mock_add])
    graph = builder.compile()
    result = await graph.ainvoke({"message": "Hi async"})

    llm_mock.ainvoke.assert_called()
    assert result["transport_state"] == "COMPLETED"
    assert "async response" in result["formatted_response"]["content"]


@pytest.mark.asyncio
async def test_graph_ainvoke_no_tool_call():
    """The compiled graph must work end-to-end via ainvoke."""
    builder = ToolAgentBuilder(llm=MockLLM(), tools=[mock_add])
    graph = builder.compile()
    result = await graph.ainvoke({"message": "Hi async"})
    assert result["transport_state"] == "COMPLETED"
    assert "Hello" in result["formatted_response"]["content"]


# ---------------------------------------------------------------------------
# LLM seam tests — ToolAgentBuilder accepting ILLMService or SDK-built LLM
# ---------------------------------------------------------------------------


class MockLLMService:
    """Minimal ILLMService-compliant stub that returns a static chat client."""

    def __init__(self, chat_client):
        self._client = chat_client

    def get_chat_client(self):
        return self._client

    async def get_chat_completion(
        self, system_prompt: str, user_prompt: str, json_mode: bool = True
    ):
        return user_prompt


def test_tool_agent_builder_accepts_llm_service_via_llm_service_param():
    """ToolAgentBuilder must accept an ILLMService instance via llm_service kwarg and resolve its chat client."""
    chat_client = MockLLM()
    service = MockLLMService(chat_client)
    builder = ToolAgentBuilder(llm_service=service, tools=[mock_add])
    graph = builder.compile()
    assert graph is not None, "compile() must succeed when llm_service is supplied"


@pytest.mark.asyncio
async def test_tool_agent_builder_llm_service_resolves_chat_client():
    """When llm_service is provided, the compiled graph must use the chat client from llm_service.get_chat_client()."""
    chat_client = MockLLM()
    service = MockLLMService(chat_client)
    builder = ToolAgentBuilder(llm_service=service, tools=[mock_add])
    graph = builder.compile()
    result = await graph.ainvoke({"message": "Hello from seam"})
    assert result["transport_state"] == "COMPLETED"
    assert "Hello" in result["formatted_response"]["content"]


def test_tool_agent_builder_explicit_llm_still_works():
    """Explicit llm= kwarg must still compile successfully (backward compat for advanced users)."""
    builder = ToolAgentBuilder(llm=MockLLM(), tools=[mock_add])
    graph = builder.compile()
    assert graph is not None


def test_tool_agent_builder_raises_when_neither_llm_nor_llm_service():
    """ToolAgentBuilder must raise ValueError when neither llm nor llm_service is supplied."""
    with pytest.raises((ValueError, TypeError)):
        ToolAgentBuilder(tools=[mock_add])


@pytest.mark.asyncio
async def test_prepare_messages_correct_structure():
    """prepare_messages must build: 1 SystemMessage + 1 HumanMessage when system_prompt is provided."""
    builder = ToolAgentBuilder(
        llm=MockLLM(), tools=[mock_add], system_prompt="Be helpful."
    )
    graph = builder.compile()
    result = await graph.ainvoke({"message": "Hi"})
    system_msgs = [m for m in result["messages"] if m.type == "system"]
    human_msgs = [m for m in result["messages"] if m.type == "human"]
    assert len(system_msgs) == 1
    assert len(human_msgs) == 1
    assert all(m.content == "Hi" for m in human_msgs)


def test_compile_has_type_annotations():
    sig = inspect.signature(ToolAgentBuilder.compile)
    params = sig.parameters
    assert (
        params["checkpointer"].annotation != inspect.Parameter.empty
    ), "checkpointer must have a type annotation"
    assert (
        params["interrupt_before"].annotation != inspect.Parameter.empty
    ), "interrupt_before must have a type annotation"
    assert (
        params["interrupt_after"].annotation != inspect.Parameter.empty
    ), "interrupt_after must have a type annotation"
    assert (
        sig.return_annotation != inspect.Parameter.empty
    ), "compile must have a return type annotation"


# ---------------------------------------------------------------------------
#  payload_types indices stale after compaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_payload_types_static_helper_exists():
    """ToolAgentBuilder must expose a static _build_payload_types helper."""
    assert hasattr(
        ToolAgentBuilder, "_build_payload_types"
    ), "_build_payload_types static method must exist on ToolAgentBuilder"


@pytest.mark.asyncio
async def test_build_payload_types_indexes_tool_messages():
    """_build_payload_types must map positions of tool-type messages whose name starts with 'call_' to BUSINESS_DATA."""
    from langchain_core.messages import ToolMessage

    from agent_sdk.layer1_domain.entities.context_config import PayloadType

    tool_msg = ToolMessage(
        content="result", tool_call_id="call_abc", name="call_my_func"
    )
    human_msg = HumanMessage(content="hi")
    ai_msg = AIMessage(content="ok")

    messages = [human_msg, tool_msg, ai_msg]
    result = ToolAgentBuilder._build_payload_types(messages)

    assert 1 in result, "Index 1 (tool message) must be in payload_types"
    assert result[1] == PayloadType.BUSINESS_DATA
    assert 0 not in result
    assert 2 not in result


@pytest.mark.asyncio
async def test_payload_types_rebuilt_after_compaction():
    """After compact() removes messages, payload_types must reflect the NEW indices (H5 fix)."""
    from langchain_core.messages import ToolMessage

    from agent_sdk.layer1_domain.entities.context_config import PayloadType

    # Build a mock context_budget that drops the first message and tracks
    # the payload_types it receives after compaction.

    class FakeContextBudget:
        async def compact(self, messages, payload_types=None):
            # Simulate compaction: drop the first message (index 0)
            compacted = list(messages)[1:]
            return compacted, []

    tool_msg = ToolMessage(
        content="result", tool_call_id="call_xyz", name="call_do_work"
    )
    HumanMessage(content="hello")

    # After compact drops index 0 (human_msg), tool_msg becomes index 0.
    # _build_payload_types on the compacted list must map index 0 → BUSINESS_DATA.
    compacted = [tool_msg]
    result_after = ToolAgentBuilder._build_payload_types(compacted)

    assert (
        0 in result_after
    ), "After compaction shifts tool_msg to index 0, payload_types must map index 0"
    assert result_after[0] == PayloadType.BUSINESS_DATA


# ---------------------------------------------------------------------------
#  system_reminder threshold must use pre-compaction count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_reminder_uses_pre_compaction_count():
    """system_reminder must be appended based on message count BEFORE compact(), not after (M1 fix)."""
    from langchain_core.messages import ToolMessage

    appended_reminders = []

    class TrackingLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, msgs):
            reminder_msgs = [
                m
                for m in msgs
                if hasattr(m, "type")
                and m.type == "system"
                and getattr(m, "content", "") == "REMINDER"
            ]
            appended_reminders.extend(reminder_msgs)
            return AIMessage(content="done")

    class SheddingContextBudget:
        """Simulates compaction that reduces messages below the threshold."""

        async def compact(self, messages, payload_types=None):
            # Drop all but 2 messages (below a threshold of 6)
            return list(messages)[:2], []

    # Provide 6 messages (at/above threshold=6) — compact will reduce to 2
    tool_msg = ToolMessage(content="r", tool_call_id="call_1", name="call_f")
    pre_compact_messages = [HumanMessage(content=f"msg{i}") for i in range(5)] + [
        tool_msg
    ]
    assert len(pre_compact_messages) == 6

    builder = ToolAgentBuilder(
        llm=TrackingLLM(),
        tools=[mock_add],
        system_reminder="REMINDER",
        system_reminder_threshold=6,
        context_budget=SheddingContextBudget(),
    )
    graph = builder.compile()
    # Inject 6 messages directly into state (bypassing prepare_messages)
    await graph.ainvoke({"messages": pre_compact_messages})

    assert (
        len(appended_reminders) >= 1
    ), "system_reminder must be appended because pre-compaction count (6) >= threshold (6)"
