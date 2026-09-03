from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_router_routes_execute_messages_to_execute_use_case():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock(return_value={"status": "success"})
    router = MessageReactionRouter(execute_use_case=execute_use_case)

    delivery = AsyncMock()
    delivery.payload = {"message": "hello", "reply_to": "agent.responses"}

    await router.handle(delivery)

    execute_use_case.execute.assert_awaited_once()
    delivery.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_routes_typed_agent_request_messages_with_nested_execute_payload():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock(return_value={"status": "success"})
    router = MessageReactionRouter(execute_use_case=execute_use_case)

    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.request.agent",
        "correlation_id": "corr-1",
        "thread_id": "thread-parent",
        "reply_topic": "planner.reply",
        "context_snapshot": {"outer": True},
        "input": {
            "message": "hello child",
            "parameters": {"depth": 2},
            "execution_context": {"conversation_id": "conv-1"},
            "context_snapshot": {"inner": True},
        },
    }

    await router.handle(delivery)

    request = execute_use_case.execute.await_args.args[0]
    assert request.message == "hello child"
    assert request.parameters == {"depth": 2}
    assert request.conv_id == "conv-1"
    assert request.reply_topic == "planner.reply"
    assert request.context_snapshot == {"outer": True}
    delivery.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_routes_executor_step_request_messages_to_execute_use_case():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock(return_value={"status": "success"})
    router = MessageReactionRouter(execute_use_case=execute_use_case)

    delivery = AsyncMock()
    delivery.payload = {
        "type": "executor.request.agent",
        "correlation_id": "corr-1",
        "conversation_id": "conv-1",
        "payload": {
            "from": "executor.runtime",
            "to": "agent.file",
            "type": "step",
            "step": {
                "id": "step-1",
                "execute_by": "agent.file",
                "next_step_id": None,
                "goal": "Normalize uploaded files",
                "message": "Normalize the uploaded files for downstream use",
                "input_data": {"workflow_id": "wf-1", "mode": "fast"},
                "uploaded_file_ids": ["file-1"],
                "input_schema": ["step-0.file_id"],
                "output_schema": ["step-1.file_id"],
            },
        },
    }

    await router.handle(delivery)

    request = execute_use_case.execute.await_args.args[0]
    assert request.message == "Normalize the uploaded files for downstream use"
    assert request.parameters == {"workflow_id": "wf-1", "mode": "fast"}
    assert request.execution_context["request_contract_type"] == "executor_step"
    delivery.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_routes_resume_messages_by_correlation_to_resume_use_case():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    resume_use_case = AsyncMock()
    resume_use_case.execute = AsyncMock(return_value={"status": "success"})
    router = MessageReactionRouter(resume_use_case=resume_use_case)

    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.response",
        "correlation_id": "corr-1",
        "result": {"status": "success"},
        "thread_id": "thread-1",
    }

    await router.handle(delivery)

    resume_use_case.execute.assert_awaited_once()
    delivery.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_routes_resume_messages_via_correlation_thread_store_when_thread_id_missing():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    class _CorrelationThreadStore:
        def resolve(self, correlation_id: str) -> str | None:
            if correlation_id == "corr-store":
                return "thread-from-store"
            return None

        def forget(self, correlation_id: str) -> None:
            self.last_forget = correlation_id

    resume_use_case = AsyncMock()
    resume_use_case.execute = AsyncMock(return_value={"status": "success"})
    store = _CorrelationThreadStore()
    router = MessageReactionRouter(
        resume_use_case=resume_use_case,
        correlation_thread_store=store,
    )

    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.response",
        "correlation_id": "corr-store",
        "result": {"status": "success"},
    }

    await router.handle(delivery)

    request = resume_use_case.execute.await_args.args[0]
    assert request.thread_id == "thread-from-store"
    assert store.last_forget == "corr-store"
    delivery.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_requeues_resume_messages_when_correlation_mapping_is_not_ready():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    class _CorrelationThreadStore:
        def resolve(self, correlation_id: str) -> str | None:
            return None

        def forget(self, correlation_id: str) -> None:
            raise AssertionError("forget should not be called when mapping is missing")

    resume_use_case = AsyncMock()
    resume_use_case.execute = AsyncMock(return_value={"status": "success"})
    router = MessageReactionRouter(
        resume_use_case=resume_use_case,
        correlation_thread_store=_CorrelationThreadStore(),
    )

    delivery = AsyncMock()
    delivery.payload = {
        "type": "agent.response",
        "correlation_id": "corr-missing",
        "result": {"status": "success"},
    }

    await router.handle(delivery)

    delivery.nack.assert_awaited_once_with(requeue=True)
    delivery.reject.assert_not_awaited()
    delivery.ack.assert_not_awaited()
    resume_use_case.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_rejects_unknown_typed_queue_envelope():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock()
    router = MessageReactionRouter(execute_use_case=execute_use_case)

    delivery = AsyncMock()
    delivery.payload = {"type": "workflow.unknown", "message": "hello"}

    await router.handle(delivery)

    execute_use_case.execute.assert_not_called()
    delivery.reject.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_rejects_deterministic_execute_parse_failures(monkeypatch):
    from agent_sdk.layer2_application.services.message_reaction import (
        router as router_module,
    )

    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock()
    router = router_module.MessageReactionRouter(execute_use_case=execute_use_case)

    def _raise_parse_error(raw):
        raise ValueError("bad execute payload")

    monkeypatch.setattr(router_module, "build_execute_input", _raise_parse_error)

    delivery = AsyncMock()
    delivery.payload = {"type": "agent.request.agent", "input": {"message": "hello"}}

    await router.handle(delivery)

    execute_use_case.execute.assert_not_called()
    delivery.reject.assert_awaited_once()
    delivery.nack.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_rejects_unparseable_poison_pill_message():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    router = MessageReactionRouter()
    delivery = AsyncMock()
    delivery.payload = "not-a-dict"

    await router.handle(delivery)

    delivery.reject.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_dispatches_custom_handler_for_queue_envelope_type():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    seen = {}

    async def custom_handler(payload):
        seen["payload"] = payload

    router = MessageReactionRouter(custom_handlers={"workflow.queue": custom_handler})

    delivery = AsyncMock()
    delivery.payload = {"type": "workflow.queue", "payload": {"step": 1}}

    await router.handle(delivery)

    assert seen["payload"] == delivery.payload
    delivery.ack.assert_awaited_once()


def test_build_execute_response_marks_messages_as_resumable_agent_responses():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )
    from agent_sdk.layer2_application.services.message_reaction.default_handlers import (
        build_execute_response,
    )

    response = build_execute_response(
        ExecuteAgentOutput(
            message="done",
            status="success",
            correlation_id="corr-1",
        ),
        request_correlation_id="corr-1",
    )

    assert response["type"] == "agent.response"


def test_build_execute_response_populates_executor_step_output_data():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentOutput,
    )
    from agent_sdk.layer2_application.services.message_reaction.default_handlers import (
        build_execute_response,
    )

    request = ExecuteAgentInput(
        message="Normalize the uploaded files for downstream use",
        conv_id="conv-1",
        correlation_id="corr-1",
        execution_context={
            "request_contract_type": "executor_step",
            "executor_step_contract": {
                "from": "executor.runtime",
                "to": "agent.file",
                "type": "step",
                "step": {
                    "id": "step-1",
                    "execute_by": "agent.file",
                    "message": "Normalize the uploaded files for downstream use",
                },
            },
        },
        agent_type="agent.file",
    )

    response = build_execute_response(
        ExecuteAgentOutput(
            message="[processed] Normalize the uploaded files for downstream use",
            status="success",
            correlation_id="corr-1",
        ),
        request_correlation_id="corr-1",
        request=request,
    )

    assert response["type"] == "agent.step.status"
    assert response["payload"]["from"] == "agent.file"
    assert response["payload"]["to"] == "executor.runtime"
    assert response["payload"]["step"]["status"] == "success"
    assert response["payload"]["step"]["error"] == ""
    assert response["payload"]["step"]["output_data"] == {
        "message": "[processed] Normalize the uploaded files for downstream use",
        "status": "success",
        "agent_data": {},
    }


@pytest.mark.asyncio
async def test_async_agent_delegator_records_correlation_thread_lookup():
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.return_value = []
    correlation_threads: dict[str, str] = {}

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
        correlation_threads=correlation_threads,
    )

    await delegator.delegate(
        AgentCallRequest(
            agent_id="planner-1",
            agent_type="planner",
            input_payload={"message": "plan"},
            interrupt_id="int-1",
            thread_id="thread-parent",
            correlation_id="corr-child",
        )
    )

    assert correlation_threads == {"corr-child": "thread-parent"}


@pytest.mark.asyncio
async def test_async_agent_delegator_records_correlation_thread_store_lookup():
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )

    class _CorrelationThreadStore:
        def __init__(self) -> None:
            self.remember_calls: list[tuple[str, str]] = []

        def remember(self, correlation_id: str, thread_id: str) -> None:
            self.remember_calls.append((correlation_id, thread_id))

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.return_value = []
    store = _CorrelationThreadStore()

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
        correlation_thread_store=store,
    )

    await delegator.delegate(
        AgentCallRequest(
            agent_id="planner-1",
            agent_type="planner",
            input_payload={"message": "plan"},
            interrupt_id="int-1",
            thread_id="thread-parent",
            correlation_id="corr-store",
        )
    )

    assert store.remember_calls == [("corr-store", "thread-parent")]
