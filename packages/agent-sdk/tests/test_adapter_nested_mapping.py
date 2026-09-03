"""
Tests for update API Adapters & Nested Mapping.
"""

from dataclasses import fields


def test_execute_agent_input_pydantic_has_context_field():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    assert "context" in ExecuteAgentInputPydantic.model_fields


def test_execute_agent_input_pydantic_context_defaults_none():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    p = ExecuteAgentInputPydantic(message="hello")
    assert p.context is None


def test_execute_agent_input_pydantic_context_can_be_set():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    p = ExecuteAgentInputPydantic(
        message="hello",
        context={"agent": "my-agent", "source": "api", "history": []},
    )
    assert p.context is not None
    assert p.context["agent"] == "my-agent"


def test_to_dataclass_maps_context_to_request_context():
    from agent_sdk.layer1_domain.entities.agent_request import RequestContext
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    p = ExecuteAgentInputPydantic(
        message="hello",
        context={"agent": "test-agent", "source": "api", "history": ["msg1"]},
    )
    domain_input = p.to_dataclass(ExecuteAgentInput)
    assert isinstance(domain_input, ExecuteAgentInput)
    assert isinstance(domain_input.context, RequestContext)
    assert domain_input.context.agent == "test-agent"
    assert domain_input.context.source == "api"
    assert domain_input.context.history == ["msg1"]


def test_to_dataclass_with_none_context_passes_none():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    p = ExecuteAgentInputPydantic(message="hello")
    domain_input = p.to_dataclass(ExecuteAgentInput)
    assert isinstance(domain_input, ExecuteAgentInput)
    assert domain_input.context is None


def test_execute_agent_input_dataclass_has_context_field():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )

    field_names = {f.name for f in fields(ExecuteAgentInput)}
    assert "context" in field_names


def test_execute_agent_input_context_defaults_none():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )

    inp = ExecuteAgentInput(message="hello")
    assert inp.context is None


def test_consumer_reads_conv_id_from_message():
    """Consumer builds ExecuteAgentInput with conv_id from message dict."""
    import asyncio

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentOutput,
    )
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    captured_requests = []

    class _CapturingUseCase:
        async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
            captured_requests.append(request)
            return ExecuteAgentOutput(message="ok", status="success")

    class _MockConsumer:
        def __init__(self, message: dict):
            self._message = message

        async def start(self, handler):
            await handler(self._message)

    class _MockPublisher:
        async def publish(self, topic: str, message: dict, key=None):
            pass

    inbound = {
        "message": "hello",
        "conv_id": "conv-xyz",
        "user_id": "u1",
        "tenant_id": "t1",
        "reply_topic": "responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _CapturingUseCase(),
        "consumer": consumer,
        "publisher": publisher,
    }
    asyncio.run(run_consumer_agent(container))
    assert len(captured_requests) == 1
    assert captured_requests[0].conv_id == "conv-xyz"


def test_consumer_response_uses_agent_data():
    """Consumer publishes response with agent_data key (not data)."""
    import asyncio

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentOutput,
    )
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    class _StubUseCase:
        async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(
                message="done",
                status="success",
                agent_data={"key": "value"},
            )

    class _MockConsumer:
        def __init__(self, message: dict):
            self._message = message

        async def start(self, handler):
            await handler(self._message)

    class _MockPublisher:
        def __init__(self):
            self.published = []

        async def publish(self, topic: str, message: dict, key=None):
            self.published.append(message)

    inbound = {
        "message": "test",
        "conv_id": "c1",
        "reply_topic": "responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _StubUseCase(),
        "consumer": consumer,
        "publisher": publisher,
    }
    asyncio.run(run_consumer_agent(container))
    assert len(publisher.published) == 1
    msg = publisher.published[0]
    assert "agent_data" in msg
    assert msg["agent_data"] == {"key": "value"}
    assert "data" not in msg
