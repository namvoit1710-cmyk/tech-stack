from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_router_resumes_paused_thread_when_response_correlation_matches():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    resume_use_case = AsyncMock()
    resume_use_case.execute = AsyncMock(return_value={"status": "success"})
    router = MessageReactionRouter(
        resume_use_case=resume_use_case,
        correlation_threads={"corr-child": "thread-parent"},
    )

    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.response",
        "correlation_id": "corr-child",
        "result": {"message": "done", "status": "success"},
    }

    await router.handle(delivery)

    request = resume_use_case.execute.await_args.args[0]
    assert request.thread_id == "thread-parent"
    assert request.correlation_id == "corr-child"
    assert request.resume_value == {"message": "done", "status": "success"}


@pytest.mark.asyncio
async def test_router_removes_correlation_lookup_after_successful_resume():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    resume_use_case = AsyncMock()
    resume_use_case.execute = AsyncMock(return_value={"status": "success"})
    correlation_threads = {"corr-child": "thread-parent"}
    router = MessageReactionRouter(
        resume_use_case=resume_use_case,
        correlation_threads=correlation_threads,
    )

    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.response",
        "correlation_id": "corr-child",
        "result": {"message": "done", "status": "success"},
    }

    await router.handle(delivery)

    assert correlation_threads == {}
    delivery.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_rejects_response_when_correlation_is_unknown():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    router = MessageReactionRouter(correlation_threads={})
    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.response",
        "correlation_id": "missing",
        "result": {"status": "success"},
    }

    await router.handle(delivery)

    delivery.nack.assert_awaited_once_with(requeue=True)
    delivery.reject.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_resumes_response_using_embedded_parent_thread_id_without_lookup_state():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    resume_use_case = AsyncMock()
    resume_use_case.execute = AsyncMock(return_value={"status": "success"})
    router = MessageReactionRouter(
        resume_use_case=resume_use_case, correlation_threads={}
    )

    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.response",
        "correlation_id": "corr-child",
        "result": {"status": "success"},
        "delegation": {"parent_thread_id": "thread-parent"},
    }

    await router.handle(delivery)

    request = resume_use_case.execute.await_args.args[0]
    assert request.thread_id == "thread-parent"
    delivery.ack.assert_awaited_once()


def _configure_bootstrap_settings(mock_settings: MagicMock) -> None:
    mock_settings.DEFAULT_TENANT_ID = "default"
    mock_settings.OPENAI_API_KEY = ""
    mock_settings.APP_MODE = "SERVER"
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []
    mock_settings.LLM_MODEL = ""
    mock_settings.HANA_HOST = ""
    mock_settings.KAFKA_REQUEST_TOPIC = "agent.request.compat"


@patch("agent_sdk.bootstrap.settings")
@pytest.mark.asyncio
async def test_default_container_delegation_response_resumes_parent_thread(
    mock_settings,
):
    from agent_sdk.bootstrap import build_app_container
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )
    from agent_sdk.layer2_application.services.message_reaction.default_handlers import (
        build_execute_response,
    )

    _configure_bootstrap_settings(mock_settings)

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.return_value = [
        {
            "agent_type": "planner",
            "queue_metadata": {
                "request_topic": "planner.request",
                "reply_topic": "planner.reply",
            },
        }
    ]
    runtime = AsyncMock()
    runtime.resume = AsyncMock(
        return_value=MagicMock(
            interrupted=False, output={"message": "done", "status": "success"}
        )
    )

    container = build_app_container(
        agent_graph=object(),
        extra_dependencies={
            "publisher": publisher,
            "agent_registry": registry,
            "agent_runtime": runtime,
        },
    )

    delegator = container["_dependencies"]["agent_delegator"]
    router = container["message_reaction_router"]

    await delegator.delegate(
        AgentCallRequest(
            agent_id="planner-1",
            agent_type="planner",
            input_payload={
                "message": "plan this",
                "parameters": {"priority": "high"},
                "execution_context": {"conversation_id": "conv-123"},
            },
            interrupt_id="int-1",
            thread_id="thread-parent",
            correlation_id="corr-child",
        )
    )

    publish_message = publisher.publish.await_args.kwargs["message"]
    assert publish_message["input"] == {
        "message": "plan this",
        "parameters": {"priority": "high"},
        "execution_context": {"conversation_id": "conv-123"},
    }
    assert publish_message["delegation"] == {"parent_thread_id": "thread-parent"}

    response = build_execute_response(
        ExecuteAgentOutput(
            message="done",
            status="success",
            correlation_id="corr-child",
        ),
        request_correlation_id="corr-child",
        parent_thread_id="thread-parent",
    )
    delivery = AsyncMock()
    delivery.payload = response

    await router.handle(delivery)

    assert runtime.resume.await_args.args[0] == response["result"]
    assert runtime.resume.await_args.kwargs["interrupt_id"] is None
    config = runtime.resume.await_args.args[1]
    assert config["configurable"]["thread_id"] == "thread-parent"
    assert config["configurable"]["correlation_id"] == "corr-child"
    delivery.ack.assert_awaited_once()
