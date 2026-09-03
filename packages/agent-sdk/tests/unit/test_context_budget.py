from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_sdk.layer1_domain.entities.context_config import (
    CompactionStrategy,
    ContextBudgetConfig,
    PayloadType,
)
from agent_sdk.layer4_frameworks.ai.context_budget import ContextBudgetManager

# ─── Existing tests (updated for async compact) ───


@pytest.mark.asyncio
async def test_budget_enforces_token_limit():
    """Total tokens after compaction must not exceed max_total_tokens."""
    config = ContextBudgetConfig(max_total_tokens=100, compaction_threshold=0.5)
    manager = ContextBudgetManager(config)
    messages = [{"role": "system", "content": "x" * 50}] + [
        {"role": "user", "content": f"msg {i} " + "y" * 20} for i in range(10)
    ]
    compacted, warnings = await manager.compact(messages)
    assert manager.count_tokens(compacted) <= config.max_total_tokens
    assert len(warnings) > 0


@pytest.mark.asyncio
async def test_selective_compaction_preserves_business_data():
    """Messages tagged as BUSINESS_DATA must never be summarized/truncated."""
    config = ContextBudgetConfig(max_total_tokens=100, compaction_threshold=0.5)
    manager = ContextBudgetManager(config)
    business_content = '{"file_id": "abc", "content": "col1,col2\\na,b"}'
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "tool", "content": "long narrative explanation " * 50},
        {"role": "tool", "content": business_content},
    ]
    payload_types = {1: PayloadType.NARRATIVE, 2: PayloadType.BUSINESS_DATA}
    compacted, _ = await manager.compact(messages, payload_types=payload_types)
    assert any(m["content"] == business_content for m in compacted)


@pytest.mark.asyncio
async def test_head_tail_keeps_system_and_recent():
    """System prompt and last N messages always preserved."""
    config = ContextBudgetConfig(
        max_total_tokens=50,
        max_message_history=3,
        compaction_threshold=0.1,
        compaction_strategy=CompactionStrategy.HEAD_TAIL,
    )
    manager = ContextBudgetManager(config)
    messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"msg{i} " + "z" * 5} for i in range(8)
    ]
    compacted, warnings = await manager.compact(messages)
    assert compacted[0]["role"] == "system"
    assert compacted[0]["content"] == "sys"
    tail_contents = [m["content"] for m in compacted[1:]]
    for i in range(5, 8):
        assert f"msg{i}" in " ".join(tail_contents)


@pytest.mark.asyncio
async def test_tool_result_clearing_replaces_old_results():
    """Old tool results cleared to placeholder, recent ones kept."""
    config = ContextBudgetConfig(
        max_total_tokens=50,
        max_message_history=2,
        compaction_threshold=0.1,
        compaction_strategy=CompactionStrategy.TOOL_RESULT_CLEAR,
    )
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "tool", "content": "old result 1 " + "a" * 20},
        {"role": "tool", "content": "old result 2 " + "b" * 20},
        {"role": "tool", "content": "recent result 1"},
        {"role": "tool", "content": "recent result 2"},
    ]
    compacted, warnings = await manager.compact(messages)
    assert compacted[0]["content"] == "[cleared]"
    assert compacted[1]["content"] == "[cleared]"
    assert compacted[2]["content"] == "recent result 1"
    assert compacted[3]["content"] == "recent result 2"


@pytest.mark.asyncio
async def test_no_compaction_when_under_threshold():
    """When total tokens < threshold, messages returned unchanged."""
    config = ContextBudgetConfig(max_total_tokens=1000, compaction_threshold=0.8)
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "system", "content": "short system"},
        {"role": "user", "content": "hi"},
    ]
    compacted, warnings = await manager.compact(messages)
    assert compacted == messages
    assert len(warnings) == 0


def test_count_tokens_returns_int():
    """count_tokens must return an integer."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    messages = [{"role": "user", "content": "hello world"}]
    result = manager.count_tokens(messages)
    assert isinstance(result, int)
    assert result > 0


def test_context_budget_manager_accepts_model_name():
    """ContextBudgetManager.__init__ must accept an optional model_name parameter."""
    import inspect

    sig = inspect.signature(ContextBudgetManager.__init__)
    assert (
        "model_name" in sig.parameters
    ), "ContextBudgetManager.__init__ must have a model_name parameter"
    param = sig.parameters["model_name"]
    assert (
        param.default is not inspect.Parameter.empty
    ), "model_name must have a default value (optional)"


def test_context_budget_manager_uses_provided_model_name():
    """ContextBudgetManager must use the provided model_name for tiktoken encoding."""
    from unittest.mock import MagicMock

    config = ContextBudgetConfig()
    mock_encoder = MagicMock()
    mock_encoder.encode.return_value = [1, 2, 3]

    with patch("tiktoken.encoding_for_model", return_value=mock_encoder) as mock_enc:
        manager = ContextBudgetManager(config, model_name="gpt-4")
        mock_enc.assert_called_once_with("gpt-4")

    messages = [{"role": "user", "content": "hello"}]
    manager.count_tokens(messages)
    mock_encoder.encode.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_selective_clears_narrative_tool_results_in_middle():
    """Selective compaction clears narrative tool results from middle messages."""
    config = ContextBudgetConfig(
        max_total_tokens=60,
        max_message_history=2,
        compaction_threshold=0.1,
        compaction_strategy=CompactionStrategy.SELECTIVE,
    )
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "content": "narrative " + "n" * 30},
        {"role": "user", "content": "recent 1"},
        {"role": "user", "content": "recent 2"},
    ]
    compacted, _ = await manager.compact(messages)
    cleared = [
        m for m in compacted if m.get("content") == "[cleared — old tool result]"
    ]
    assert len(cleared) >= 1


def test_tool_agent_builder_accepts_context_budget_param():
    """ToolAgentBuilder.__init__ must accept optional context_budget parameter."""
    import inspect

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    sig = inspect.signature(ToolAgentBuilder.__init__)
    assert (
        "context_budget" in sig.parameters
    ), "ToolAgentBuilder.__init__ must have a context_budget parameter"
    param = sig.parameters["context_budget"]
    assert param.default is None, "context_budget must default to None (optional)"


@pytest.mark.asyncio
async def test_tool_agent_builder_applies_context_budget_before_llm():
    """When context_budget is provided, compact() is called before LLM invocation."""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool as langchain_tool

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    @langchain_tool
    def noop_tool(x: int) -> str:
        """Do nothing."""
        return str(x)

    class MockLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="ok")

    mock_budget = MagicMock(spec=ContextBudgetManager)
    mock_budget.compact = AsyncMock(return_value=([], []))

    builder = ToolAgentBuilder(
        llm=MockLLM(), tools=[noop_tool], context_budget=mock_budget
    )
    graph = builder.compile()
    await graph.ainvoke({"message": "test"})
    mock_budget.compact.assert_called()


# ─── #1: Fix max_tool_result_tokens ───


@pytest.mark.asyncio
async def test_truncate_tool_results_oversized():
    """Tool results exceeding max_tool_result_tokens are truncated with marker."""
    config = ContextBudgetConfig(
        max_total_tokens=10000,
        max_tool_result_tokens=10,
    )
    manager = ContextBudgetManager(config)
    long_content = "word " * 200  # ~200 tokens
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": long_content},
    ]
    compacted, _ = await manager.compact(messages)
    tool_msg = compacted[1]
    assert "[truncated —" in tool_msg["content"]
    assert "→ 10 tokens]" in tool_msg["content"]


@pytest.mark.asyncio
async def test_truncate_tool_results_under_limit_unchanged():
    """Tool results under max_tool_result_tokens are not modified."""
    config = ContextBudgetConfig(
        max_total_tokens=10000,
        max_tool_result_tokens=1000,
    )
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "short result"},
    ]
    compacted, _ = await manager.compact(messages)
    assert compacted[1]["content"] == "short result"


@pytest.mark.asyncio
async def test_truncate_tool_results_non_tool_messages_unchanged():
    """Non-tool messages are never truncated by _truncate_tool_results."""
    config = ContextBudgetConfig(
        max_total_tokens=10000,
        max_tool_result_tokens=5,
    )
    manager = ContextBudgetManager(config)
    long_user_msg = "word " * 200
    messages = [
        {"role": "user", "content": long_user_msg},
    ]
    compacted, _ = await manager.compact(messages)
    assert compacted[0]["content"] == long_user_msg


@pytest.mark.asyncio
async def test_truncate_tool_results_with_dict_content():
    """Tool results with dict content are serialized then truncated."""
    config = ContextBudgetConfig(
        max_total_tokens=10000,
        max_tool_result_tokens=5,
    )
    manager = ContextBudgetManager(config)
    dict_content = {"data": "x" * 500, "key": "value"}
    messages = [
        {"role": "tool", "content": dict_content},
    ]
    compacted, _ = await manager.compact(messages)
    assert "[truncated —" in compacted[0]["content"]


# ─── #2: Fix preserve_business_payloads in HEAD_TAIL ───


@pytest.mark.asyncio
async def test_head_tail_preserves_business_data_when_enabled():
    """HEAD_TAIL preserves BUSINESS_DATA messages from the middle when preserve_business_payloads=True."""
    config = ContextBudgetConfig(
        max_total_tokens=200,
        max_message_history=2,
        compaction_threshold=0.1,
        compaction_strategy=CompactionStrategy.HEAD_TAIL,
        preserve_business_payloads=True,
    )
    manager = ContextBudgetManager(config)
    business_content = '{"critical_data": "keep_me"}'
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "msg1"},
        {"role": "tool", "content": business_content},
        {"role": "user", "content": "msg3"},
        {"role": "user", "content": "msg4 " + "z" * 20},
        {"role": "user", "content": "msg5 recent"},
        {"role": "user", "content": "msg6 recent"},
    ]
    payload_types = {2: PayloadType.BUSINESS_DATA}
    compacted, _ = await manager.compact(messages, payload_types=payload_types)
    # Business data must be preserved
    assert any(m["content"] == business_content for m in compacted)
    # System prompt preserved
    assert compacted[0]["content"] == "sys"


@pytest.mark.asyncio
async def test_head_tail_drops_business_data_when_disabled():
    """HEAD_TAIL drops BUSINESS_DATA messages from the middle when preserve_business_payloads=False."""
    config = ContextBudgetConfig(
        max_total_tokens=200,
        max_message_history=2,
        compaction_threshold=0.1,
        compaction_strategy=CompactionStrategy.HEAD_TAIL,
        preserve_business_payloads=False,
    )
    manager = ContextBudgetManager(config)
    business_content = '{"critical_data": "drop_me"}'
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "msg1"},
        {"role": "tool", "content": business_content},
        {"role": "user", "content": "msg3"},
        {"role": "user", "content": "recent1"},
        {"role": "user", "content": "recent2"},
    ]
    payload_types = {2: PayloadType.BUSINESS_DATA}
    compacted, _ = await manager.compact(messages, payload_types=payload_types)
    # Business data should NOT be preserved
    assert not any(m["content"] == business_content for m in compacted)


# ─── #3: Fix token counting for structured content ───


def test_count_tokens_handles_dict_content():
    """count_tokens must count tokens in dict content (serialized to JSON)."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "user", "content": {"key": "value", "nested": [1, 2, 3]}},
    ]
    result = manager.count_tokens(messages)
    assert result > 4  # More than just the per-message overhead


def test_count_tokens_handles_list_content():
    """count_tokens must count tokens in list content (serialized to JSON)."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image_url", "url": "http://example.com"},
            ],
        },
    ]
    result = manager.count_tokens(messages)
    assert result > 4  # More than just the per-message overhead


def test_count_tokens_string_still_works():
    """count_tokens must still work correctly with string content."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    messages = [{"role": "user", "content": "hello world"}]
    result = manager.count_tokens(messages)
    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_empty_content():
    """count_tokens handles empty/missing content gracefully."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    messages = [{"role": "user"}, {"role": "user", "content": ""}]
    result = manager.count_tokens(messages)
    # Should be just the per-message overhead (4 per message)
    assert result == 8


# ─── #4: Lost-in-the-middle system_reminder ───


def test_tool_agent_builder_accepts_system_reminder_param():
    """ToolAgentBuilder.__init__ must accept optional system_reminder parameter."""
    import inspect

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder

    sig = inspect.signature(ToolAgentBuilder.__init__)
    assert "system_reminder" in sig.parameters
    assert sig.parameters["system_reminder"].default is None
    assert "system_reminder_threshold" in sig.parameters
    assert sig.parameters["system_reminder_threshold"].default == 6


@pytest.mark.asyncio
async def test_system_reminder_injected_when_messages_exceed_threshold():
    """system_reminder is appended as SystemMessage when messages >= threshold."""
    from langchain_core.messages import AIMessage, SystemMessage
    from langchain_core.tools import tool as langchain_tool

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder

    @langchain_tool
    def noop_tool(x: int) -> str:
        """Do nothing."""
        return str(x)

    captured_messages = []

    class MockLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            captured_messages.extend(messages)
            return AIMessage(content="ok")

    builder = ToolAgentBuilder(
        llm=MockLLM(),
        tools=[noop_tool],
        system_prompt="You are a helpful assistant.",
        system_reminder="Remember: stay focused on the task.",
        system_reminder_threshold=2,
    )
    graph = builder.compile()
    await graph.ainvoke({"message": "test"})
    # prepare_messages creates [SystemMessage(system_prompt), HumanMessage("test")] = 2 messages
    # threshold=2, so reminder should be appended
    reminder_msgs = [
        m
        for m in captured_messages
        if isinstance(m, SystemMessage) and "stay focused" in getattr(m, "content", "")
    ]
    assert len(reminder_msgs) >= 1


@pytest.mark.asyncio
async def test_system_reminder_not_injected_below_threshold():
    """system_reminder is NOT injected when messages < threshold."""
    from langchain_core.messages import AIMessage, SystemMessage
    from langchain_core.tools import tool as langchain_tool

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder

    @langchain_tool
    def noop_tool(x: int) -> str:
        """Do nothing."""
        return str(x)

    captured_messages = []

    class MockLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            captured_messages.extend(messages)
            return AIMessage(content="ok")

    builder = ToolAgentBuilder(
        llm=MockLLM(),
        tools=[noop_tool],
        system_reminder="Remember: stay focused.",
        system_reminder_threshold=100,  # Very high threshold
    )
    graph = builder.compile()
    await graph.ainvoke({"message": "test"})
    reminder_msgs = [
        m
        for m in captured_messages
        if isinstance(m, SystemMessage) and "stay focused" in getattr(m, "content", "")
    ]
    assert len(reminder_msgs) == 0


@pytest.mark.asyncio
async def test_no_system_reminder_when_none():
    """No reminder injected when system_reminder is None."""
    from langchain_core.messages import AIMessage, SystemMessage
    from langchain_core.tools import tool as langchain_tool

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder

    @langchain_tool
    def noop_tool(x: int) -> str:
        """Do nothing."""
        return str(x)

    captured_messages = []

    class MockLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            captured_messages.extend(messages)
            return AIMessage(content="ok")

    builder = ToolAgentBuilder(
        llm=MockLLM(),
        tools=[noop_tool],
        system_reminder=None,
        system_reminder_threshold=1,
    )
    graph = builder.compile()
    await graph.ainvoke({"message": "test"})
    # Only the HumanMessage should be there, no reminder SystemMessage
    sys_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
    assert len(sys_msgs) == 0


# ─── #5: Tiered compaction ───


def test_compaction_strategy_tiered_exists():
    """CompactionStrategy.TIERED must exist."""
    assert hasattr(CompactionStrategy, "TIERED")
    assert CompactionStrategy.TIERED.value == "tiered"


def test_context_budget_config_has_tiered_thresholds():
    """ContextBudgetConfig must have aggressive_threshold and danger_threshold."""
    config = ContextBudgetConfig()
    assert hasattr(config, "aggressive_threshold")
    assert hasattr(config, "danger_threshold")
    assert config.aggressive_threshold == 0.85
    assert config.danger_threshold == 0.95


@pytest.mark.asyncio
async def test_tiered_light_pressure_clears_tool_results():
    """At light pressure (threshold < ratio < aggressive), TIERED applies TOOL_RESULT_CLEAR."""
    # Target: ~65% pressure => above 0.5 threshold, below 0.85 aggressive
    # max_total_tokens=100 => threshold at 50, aggressive at 85
    # Need ~65 tokens total
    config = ContextBudgetConfig(
        max_total_tokens=100,
        max_message_history=2,
        compaction_threshold=0.5,
        aggressive_threshold=0.85,
        danger_threshold=0.95,
        compaction_strategy=CompactionStrategy.TIERED,
    )
    manager = ContextBudgetManager(config)
    # ~11 + 4 = 15, ~21 + 4 = 25, ~11 + 4 = 15, ~2 + 4 = 6 => ~61 tokens
    messages = [
        {"role": "system", "content": "word " * 10},
        {"role": "tool", "content": "word " * 20},
        {"role": "user", "content": "word " * 10},
        {"role": "user", "content": "recent"},
    ]
    total = manager.count_tokens(messages)
    assert total > 50, f"Need >50 tokens, got {total}"
    assert total < 85, f"Need <85 tokens, got {total}"
    compacted, warnings = await manager.compact(messages)
    assert len(warnings) > 0
    # Old tool result should be cleared (TOOL_RESULT_CLEAR behavior)
    cleared = [m for m in compacted if m.get("content") == "[cleared]"]
    assert len(cleared) >= 1


@pytest.mark.asyncio
async def test_tiered_medium_pressure_applies_selective():
    """At medium pressure (aggressive <= ratio < danger) without LLM, TIERED applies SELECTIVE."""
    # Target: above aggressive (0.80), below danger (0.95)
    # max_total_tokens=100 => aggressive at 80, danger at 95
    config = ContextBudgetConfig(
        max_total_tokens=100,
        max_message_history=2,
        compaction_threshold=0.3,
        aggressive_threshold=0.80,
        danger_threshold=0.95,
        compaction_strategy=CompactionStrategy.TIERED,
    )
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "system", "content": "word " * 14},
        {"role": "tool", "content": "word " * 30},
        {"role": "user", "content": "word " * 10},
        {"role": "user", "content": "recent1"},
        {"role": "user", "content": "recent2"},
    ]
    total = manager.count_tokens(messages)
    assert total > 80, f"Need >80 tokens, got {total}"
    assert total < 95, f"Need <95 tokens, got {total}"
    compacted, warnings = await manager.compact(messages)
    assert len(warnings) > 0
    # SELECTIVE behavior: narrative tool results in middle are cleared
    cleared = [m for m in compacted if "[cleared" in m.get("content", "")]
    assert len(cleared) >= 1


@pytest.mark.asyncio
async def test_tiered_danger_pressure_applies_head_tail():
    """At danger pressure (ratio >= danger), TIERED applies HEAD_TAIL."""
    config = ContextBudgetConfig(
        max_total_tokens=100,
        max_message_history=2,
        compaction_threshold=0.1,
        aggressive_threshold=0.5,
        danger_threshold=0.6,  # danger at 60 tokens
        compaction_strategy=CompactionStrategy.TIERED,
    )
    manager = ContextBudgetManager(config)
    # Create messages totaling ~80+ tokens (80% = danger)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "msg1 " + "a" * 20},
        {"role": "user", "content": "msg2 " + "b" * 20},
        {"role": "user", "content": "msg3 " + "c" * 20},
        {"role": "user", "content": "recent1"},
        {"role": "user", "content": "recent2"},
    ]
    compacted, warnings = await manager.compact(messages)
    assert len(warnings) > 0
    # HEAD_TAIL: system + last 2
    assert compacted[0]["content"] == "sys"
    assert len(compacted) <= 4  # system + at most 2-3 tail msgs


# ─── #6: LLM-based summarization within TIERED ───


@pytest.mark.asyncio
async def test_context_budget_manager_accepts_llm_service():
    """ContextBudgetManager accepts optional llm_service parameter."""
    import inspect

    sig = inspect.signature(ContextBudgetManager.__init__)
    assert "llm_service" in sig.parameters
    assert sig.parameters["llm_service"].default is None


@pytest.mark.asyncio
async def test_tiered_with_llm_service_summarizes_at_medium_pressure():
    """At medium pressure with llm_service, TIERED summarizes middle messages."""
    mock_chat_client = AsyncMock()
    mock_chat_client.ainvoke = AsyncMock(
        return_value=MagicMock(content="Summary: user asked about X, tool returned Y.")
    )
    mock_llm = MagicMock()
    mock_llm.get_chat_client.return_value = mock_chat_client

    # Target: above aggressive (0.80), below danger (0.95)
    config = ContextBudgetConfig(
        max_total_tokens=100,
        max_message_history=2,
        compaction_threshold=0.3,
        aggressive_threshold=0.80,
        danger_threshold=0.95,
        compaction_strategy=CompactionStrategy.TIERED,
    )
    manager = ContextBudgetManager(config, llm_service=mock_llm)
    messages = [
        {"role": "system", "content": "word " * 10},
        {"role": "user", "content": "word " * 15},
        {"role": "tool", "content": "word " * 15},
        {"role": "user", "content": "word " * 10},
        {"role": "user", "content": "recent1"},
        {"role": "user", "content": "recent2"},
    ]
    total = manager.count_tokens(messages)
    assert total > 80, f"Need >80 tokens, got {total}"
    assert total < 95, f"Need <95 tokens, got {total}"
    compacted, warnings = await manager.compact(messages)
    assert len(warnings) > 0
    # LLM should have been called for summarization
    mock_llm.get_chat_client.assert_called_once()
    mock_chat_client.ainvoke.assert_called_once()
    # Summary message should be present
    summary_msgs = [
        m for m in compacted if "[Conversation summary]" in m.get("content", "")
    ]
    assert len(summary_msgs) == 1


@pytest.mark.asyncio
async def test_tiered_with_llm_preserves_business_data_during_summarization():
    """LLM summarization preserves BUSINESS_DATA messages, only summarizes the rest."""
    mock_chat_client = AsyncMock()
    mock_chat_client.ainvoke = AsyncMock(
        return_value=MagicMock(content="Summarized content.")
    )
    mock_llm = MagicMock()
    mock_llm.get_chat_client.return_value = mock_chat_client

    # Target: above aggressive (0.70), below danger (0.95)
    config = ContextBudgetConfig(
        max_total_tokens=100,
        max_message_history=2,
        compaction_threshold=0.3,
        aggressive_threshold=0.70,
        danger_threshold=0.95,
        compaction_strategy=CompactionStrategy.TIERED,
    )
    manager = ContextBudgetManager(config, llm_service=mock_llm)
    business_content = '{"important": "data"}'
    messages = [
        {"role": "system", "content": "word " * 10},
        {"role": "user", "content": "word " * 15},
        {"role": "tool", "content": business_content},
        {"role": "user", "content": "word " * 15},
        {"role": "user", "content": "recent1"},
        {"role": "user", "content": "recent2"},
    ]
    total = manager.count_tokens(messages)
    assert total > 70, f"Need >70 tokens, got {total}"
    assert total < 95, f"Need <95 tokens, got {total}"
    payload_types = {2: PayloadType.BUSINESS_DATA}
    compacted, _ = await manager.compact(messages, payload_types=payload_types)
    # Business data preserved
    assert any(m["content"] == business_content for m in compacted)
    # Summary present
    assert any("[Conversation summary]" in m.get("content", "") for m in compacted)


@pytest.mark.asyncio
async def test_tiered_llm_failure_falls_back_to_selective():
    """If LLM summarization fails, TIERED falls back to SELECTIVE."""
    mock_chat_client = AsyncMock()
    mock_chat_client.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    mock_llm = MagicMock()
    mock_llm.get_chat_client.return_value = mock_chat_client

    # Target: above aggressive (0.70), below danger (0.95)
    config = ContextBudgetConfig(
        max_total_tokens=100,
        max_message_history=2,
        compaction_threshold=0.3,
        aggressive_threshold=0.70,
        danger_threshold=0.95,
        compaction_strategy=CompactionStrategy.TIERED,
    )
    manager = ContextBudgetManager(config, llm_service=mock_llm)
    messages = [
        {"role": "system", "content": "word " * 10},
        {"role": "tool", "content": "word " * 30},
        {"role": "user", "content": "word " * 10},
        {"role": "user", "content": "recent1"},
        {"role": "user", "content": "recent2"},
    ]
    total = manager.count_tokens(messages)
    assert total > 70, f"Need >70 tokens, got {total}"
    assert total < 95, f"Need <95 tokens, got {total}"
    # Should not raise, should fall back gracefully
    compacted, warnings = await manager.compact(messages)
    assert len(warnings) > 0
    # Should still produce compacted output (SELECTIVE fallback)
    assert len(compacted) > 0


@pytest.mark.asyncio
async def test_compact_is_async():
    """compact() must be an async method (returns coroutine)."""
    import inspect

    assert inspect.iscoroutinefunction(ContextBudgetManager.compact)


# ─── LangChain BaseMessage object support ───


def test_get_field_helper_on_dict():
    """_get_field returns dict value by key."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    msg = {"role": "user", "content": "hello"}
    assert manager._get_field(msg, "content", "") == "hello"
    assert manager._get_field(msg, "missing", "default") == "default"


def test_get_field_helper_on_langchain_message():
    """_get_field returns attribute value from LangChain BaseMessage objects."""
    from langchain_core.messages import HumanMessage

    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    msg = HumanMessage(content="hello")
    assert manager._get_field(msg, "content", "") == "hello"
    assert manager._get_field(msg, "missing_attr", "default") == "default"


def test_get_role_helper_on_dict():
    """_get_role returns role from dict messages."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    assert manager._get_role({"role": "user", "content": "x"}) == "user"
    assert manager._get_role({"role": "tool", "content": "x"}) == "tool"
    assert manager._get_role({"content": "x"}) == "unknown"


def test_get_role_helper_on_langchain_message():
    """_get_role returns .type from LangChain BaseMessage objects."""
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    assert manager._get_role(HumanMessage(content="hi")) == "human"
    assert manager._get_role(AIMessage(content="hi")) == "ai"
    assert manager._get_role(SystemMessage(content="sys")) == "system"
    tool_msg = ToolMessage(content="result", tool_call_id="call_123")
    assert manager._get_role(tool_msg) == "tool"


def test_count_tokens_with_langchain_messages():
    """count_tokens must work with LangChain BaseMessage objects."""
    from langchain_core.messages import HumanMessage, SystemMessage

    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    messages = [
        SystemMessage(content="system prompt"),
        HumanMessage(content="hello world"),
    ]
    result = manager.count_tokens(messages)
    assert isinstance(result, int)
    assert result > 0


@pytest.mark.asyncio
async def test_truncate_tool_results_with_langchain_tool_message():
    """_truncate_tool_results handles LangChain ToolMessage objects."""
    from langchain_core.messages import ToolMessage

    config = ContextBudgetConfig(
        max_total_tokens=10000,
        max_tool_result_tokens=10,
    )
    manager = ContextBudgetManager(config)
    long_content = "word " * 200
    messages = [
        ToolMessage(content=long_content, tool_call_id="call_1"),
    ]
    compacted, _ = await manager.compact(messages)
    assert "[truncated —" in compacted[0].content


@pytest.mark.asyncio
async def test_compact_works_with_mixed_langchain_and_dict_messages():
    """compact() handles a mix of LangChain BaseMessage objects and dicts."""
    from langchain_core.messages import HumanMessage, SystemMessage

    config = ContextBudgetConfig(max_total_tokens=1000, compaction_threshold=0.8)
    manager = ContextBudgetManager(config)
    messages = [
        SystemMessage(content="system prompt"),
        HumanMessage(content="hello"),
        {"role": "assistant", "content": "hi there"},
    ]
    compacted, warnings = await manager.compact(messages)
    assert len(compacted) == 3
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_selective_compaction_with_langchain_messages():
    """_apply_selective handles LangChain message objects without AttributeError."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    config = ContextBudgetConfig(
        max_total_tokens=60,
        max_message_history=2,
        compaction_threshold=0.1,
        compaction_strategy=CompactionStrategy.SELECTIVE,
    )
    manager = ContextBudgetManager(config)
    messages = [
        SystemMessage(content="sys"),
        ToolMessage(content="narrative " + "n" * 30, tool_call_id="call_1"),
        HumanMessage(content="recent 1"),
        HumanMessage(content="recent 2"),
    ]
    compacted, _ = await manager.compact(messages)
    assert len(compacted) > 0


@pytest.mark.asyncio
async def test_enforce_hard_limit_with_langchain_messages():
    """_enforce_hard_limit handles LangChain message objects without AttributeError."""
    from langchain_core.messages import HumanMessage, SystemMessage

    config = ContextBudgetConfig(
        max_total_tokens=30,
        compaction_threshold=0.1,
        compaction_strategy=CompactionStrategy.HEAD_TAIL,
    )
    manager = ContextBudgetManager(config)
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="word " * 10),
        HumanMessage(content="word " * 10),
        HumanMessage(content="word " * 10),
    ]
    compacted, _ = await manager.compact(messages)
    assert manager.count_tokens(compacted) <= config.max_total_tokens


# ─── O(N) _enforce_hard_limit ───


def test_count_message_tokens_helper_exists():
    """_count_message_tokens must exist and return int for a single message."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    msg = {"role": "user", "content": "hello world"}
    result = manager._count_message_tokens(msg)
    assert isinstance(result, int)
    assert result > 0


def test_count_message_tokens_with_list_content():
    """_count_message_tokens handles list content (multimodal messages)."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    msg = {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    result = manager._count_message_tokens(msg)
    assert isinstance(result, int)
    assert result > 0


def test_count_message_tokens_consistent_with_count_tokens():
    """Sum of _count_message_tokens for each message equals count_tokens total."""
    config = ContextBudgetConfig()
    manager = ContextBudgetManager(config)
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "hi"},
    ]
    total_via_helper = sum(manager._count_message_tokens(m) for m in messages)
    total_via_count = manager.count_tokens(messages)
    assert total_via_helper == total_via_count


def test_enforce_hard_limit_uses_running_total_not_recounting():
    """_enforce_hard_limit must use running total (O(N)) not recount on every iteration."""

    config = ContextBudgetConfig(max_total_tokens=50)
    manager = ContextBudgetManager(config)
    encode_call_count = [0]
    original_encode = manager._encoder.encode

    def counting_encode(text):
        encode_call_count[0] += 1
        return original_encode(text)

    manager._encoder.encode = counting_encode

    messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"msg {i} " + "x" * 5} for i in range(20)
    ]
    encode_call_count[0] = 0
    manager._enforce_hard_limit(messages)
    calls_with_running_total = encode_call_count[0]

    encode_call_count[0] = 0
    manager._enforce_hard_limit(messages)
    assert encode_call_count[0] == calls_with_running_total

    total_messages = len(messages)
    assert (
        calls_with_running_total < total_messages * total_messages
    ), f"Expected O(N) behavior, got {calls_with_running_total} encode calls for {total_messages} messages"
