import asyncio
from unittest.mock import AsyncMock, Mock


def test_nodes_exports_openai_node_hitl_node_get_current_time():
    from app.layer2_application.echo_nodes import (
        get_current_time,
        hitl_node,
        openai_node,
    )

    assert callable(openai_node)
    assert callable(hitl_node)
    assert get_current_time is not None


def test_get_current_time_returns_formatted_string():
    from app.layer2_application.echo_nodes import (
        get_current_time,
    )

    result = get_current_time.invoke({})
    assert isinstance(result, str)
    assert len(result) == 19  # "YYYY-MM-DD HH:MM:SS"


def test_hitl_node_returns_processing_when_confirmed():
    from app.layer2_application.echo_nodes import hitl_node

    state = {"confirmed": True}
    result = hitl_node(state, {})
    assert result == {"transport_state": "PROCESSING"}


def test_openai_node_echoes_without_service():
    from app.layer2_application.echo_nodes import openai_node

    state = {"message": "hello world"}
    result = asyncio.run(openai_node(state, {}))
    assert result == {"agent_result": {"content": "[echo] hello world"}}


def test_openai_node_uses_openai_service_wrapper_when_available():
    from app.layer2_application.echo_nodes import openai_node

    openai_service = Mock()
    openai_service.get_chat_completion = AsyncMock(return_value="professional hello")

    state = {"message": "hello world"}
    result = asyncio.run(openai_node(state, {"openai_service": openai_service}))

    openai_service.get_chat_completion.assert_awaited_once_with(
        system_prompt=(
            "You are an echo assistant. Just repeat what the user says but make it sound professional. "
            "If the user asks for the time, you can mention that you have a tool for that."
        ),
        user_prompt="hello world",
        json_mode=False,
    )
    assert result == {"agent_result": {"content": "professional hello"}}
