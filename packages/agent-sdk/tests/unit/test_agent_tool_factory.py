from unittest.mock import MagicMock

from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability


def _make_capability(**overrides) -> AgentCapability:
    defaults = dict(
        agent_type="test-agent",
        name="Test Agent",
        description="Handles test tasks",
        input_schema={"type": "object", "properties": {}},
        output_schema={},
    )
    defaults.update(overrides)
    return AgentCapability(**defaults)


def test_factory_creates_tool_with_correct_name():
    from agent_sdk.layer4_frameworks.ai.agent_tool_factory import default_tool_factory

    cap = _make_capability(agent_type="file-processor")
    tool = default_tool_factory(cap, MagicMock())
    assert tool.name == "call_file_processor"


def test_factory_appends_negative_examples_to_description():
    from agent_sdk.layer4_frameworks.ai.agent_tool_factory import default_tool_factory

    cap = _make_capability(
        description="Process files",
        negative_examples=["general questions", "conversation summaries"],
    )
    tool = default_tool_factory(cap, MagicMock())
    assert "Do NOT call this agent when" in tool.description
    assert "general questions" in tool.description
    assert "conversation summaries" in tool.description


def test_factory_no_negative_examples_plain_description():
    from agent_sdk.layer4_frameworks.ai.agent_tool_factory import default_tool_factory

    cap = _make_capability(description="Process files", negative_examples=[])
    tool = default_tool_factory(cap, MagicMock())
    assert tool.description == "Process files"
    assert "Do NOT" not in tool.description
