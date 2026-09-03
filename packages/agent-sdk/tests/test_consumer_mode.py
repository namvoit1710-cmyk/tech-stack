import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_sdk.layer1_domain.entities.agent_info import AgentInfo
from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
    HitlInterruptPayload,
    InterruptType,
)
from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
)
from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent
from tests.helpers.testing import StubLogger as _StubLogger


class _StubExecuteUseCase:
    """Async stub matching the now-async ExecuteAgentUseCase contract."""

    async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        return ExecuteAgentOutput(
            message=f"[processed] {request.message}",
            status="success",
            correlation_id=request.correlation_id,
        )


class _MockConsumer:
    def __init__(self, message: dict):
        self._message = message
        self.started = False
        self.stopped = False
        self._topic = "agent.request"
        self._queue_name = "default/agent.request"

    async def start(self, handler):
        self.started = True
        await handler(self._message)

    async def stop(self):
        self.stopped = True


class _MockPublisher:
    def __init__(self):
        self.published: list[dict] = []

    async def publish(self, topic: str, message: dict, key: str | None = None) -> None:
        self.published.append({"topic": topic, "message": message, "key": key})

    async def close(self) -> None:
        pass


class _TrackedDelivery:
    def __init__(self, payload: dict):
        self.payload = payload
        self.ack = AsyncMock()
        self.nack = AsyncMock()
        self.reject = AsyncMock()


class _InMemoryInboxRepository:
    def __init__(self) -> None:
        self._store: dict[str, InboxRecord] = {}

    def find_by_message_id(self, message_id: str) -> InboxRecord | None:
        return self._store.get(message_id)

    def save(self, record: InboxRecord) -> InboxRecord:
        self._store[record.message_id] = record
        return record

    def update_status(
        self,
        message_id: str,
        status: str,
        *,
        error: str = "",
        processed_at: str = "",
    ) -> bool:
        existing = self._store.get(message_id)
        if existing is None:
            return False
        self._store[message_id] = InboxRecord(
            message_id=existing.message_id,
            idempotency_key=existing.idempotency_key,
            payload=existing.payload,
            status=status,
            received_at=existing.received_at,
            processed_at=processed_at or existing.processed_at,
            error=error or existing.error,
        )
        return True


class _BackgroundConsumer:
    requires_shutdown_wait = True

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.handler = None
        self.started_event = asyncio.Event()

    async def start(self, handler):
        self.started = True
        self.handler = handler
        self.started_event.set()

    async def stop(self):
        self.stopped = True


class _StubGetAgentInfoUseCase:
    def execute(self) -> AgentInfo:
        return AgentInfo(
            agent_type="consumer-agent",
            version="1.0.0",
            sdk_version="1.0.0",
            domain="test",
            capabilities=[{"domain": "test", "action": "run"}],
            metadata={"tenant_aware": True},
        )


def test_consumer_agent_processes_message_and_publishes_response():
    inbound = {
        "message": "Hello from Kafka",
        "conversation_id": "conv-42",
        "user_id": "user-7",
        "tenant_id": "acme",
        "correlation_id": "corr-001",
        "reply_topic": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _StubExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    asyncio.run(run_consumer_agent(container))
    assert consumer.started is True
    assert len(publisher.published) == 1
    pub = publisher.published[0]
    assert pub["topic"] == "agent.responses"
    assert pub["message"]["message"] == "[processed] Hello from Kafka"
    assert pub["message"]["correlation_id"] == "corr-001"
    assert pub["message"]["status"] == "success"


def test_consumer_agent_processes_executor_step_request_and_publishes_step_status():
    inbound = {
        "type": "executor.request.agent",
        "message_id": "msg-1",
        "conversation_id": "conv-42",
        "correlation_id": "corr-001",
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
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    settings = SimpleNamespace(
        EXECUTOR_STATUS_TOPIC="executor.statuses",
        QUEUE_REPLY_TOPIC="",
        KAFKA_RESPONSE_TOPIC="agent.responses",
    )
    container = {
        "execute_agent": _StubExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
        "_dependencies": {"settings": settings},
    }
    asyncio.run(run_consumer_agent(container))

    assert consumer.started is True
    assert len(publisher.published) == 1
    pub = publisher.published[0]
    assert pub["topic"] == "executor.statuses"
    assert pub["message"]["type"] == "agent.step.status"
    assert pub["message"]["correlation_id"] == "corr-001"
    assert pub["message"]["conversation_id"] == "conv-42"
    assert pub["message"]["payload"]["from"] == "agent.file"
    assert pub["message"]["payload"]["to"] == "executor.runtime"
    assert pub["message"]["payload"]["step"]["id"] == "step-1"
    assert pub["message"]["payload"]["step"]["status"] == "success"
    assert pub["message"]["payload"]["step"]["error"] == ""
    assert pub["message"]["payload"]["step"]["output_data"] == {
        "message": "[processed] Normalize the uploaded files for downstream use",
        "status": "success",
        "agent_data": {},
    }


def test_create_consumer_ops_app_exposes_ops_routes_without_execute_endpoint():
    from agent_sdk.layer3_adapters.presenters.agent_consumer import (
        create_consumer_ops_app,
    )

    settings = SimpleNamespace(
        ALLOW_ORIGINS=["*"],
        SERVER_HOST="127.0.0.1",
        SERVER_PORT=36000,
        HEARTBEAT_INTERVAL_SECONDS=30,
    )
    container = {
        "get_agent_info": _StubGetAgentInfoUseCase(),
        "_dependencies": {"settings": settings},
    }

    app = create_consumer_ops_app(container)

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in paths
    assert "/ready" in paths
    assert "/api/v1/info" in paths
    assert "/api/v1/execute" not in paths

    info_route = next(
        route for route in app.routes if getattr(route, "path", None) == "/api/v1/info"
    )
    info = info_route.endpoint()
    assert info["agent_type"] == "consumer-agent"
    assert info["domain"] == "test"
    assert info["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_consumer_agent_waits_for_shutdown_when_consumer_requires_it():
    shutdown_event = asyncio.Event()
    consumer = _BackgroundConsumer()
    publisher = _MockPublisher()
    container = {
        "execute_agent": _StubExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
        "_dependencies": {"shutdown_event": shutdown_event},
    }

    task = asyncio.create_task(run_consumer_agent(container))
    await consumer.started_event.wait()

    assert consumer.started is True
    assert task.done() is False

    shutdown_event.set()
    await task

    assert consumer.stopped is True


@pytest.mark.asyncio
async def test_consumer_agent_can_run_with_bootstrap_built_container():
    from unittest.mock import patch

    from agent_sdk.bootstrap import build_app_container

    settings = SimpleNamespace(
        DEFAULT_TENANT_ID="default",
        APP_MODE="CONSUMER",
        OPENAI_API_KEY="",
        LLM_MODEL="",
        MCP_SERVERS=[],
        MCP_TOOL_FILTER=[],
        REGISTRY_URL="",
        HANA_HOST="",
        AGENT_TYPE="test-agent",
        AGENT_VERSION="0.0.0",
    )
    shutdown_event = asyncio.Event()
    consumer = _BackgroundConsumer()
    publisher = _MockPublisher()

    with patch("agent_sdk.bootstrap.settings", settings):
        container = build_app_container(
            extra_dependencies={
                "consumer": consumer,
                "publisher": publisher,
                "shutdown_event": shutdown_event,
                "logger": _StubLogger(),
                "agent_registry": None,
            }
        )

    task = asyncio.create_task(run_consumer_agent(container))
    await consumer.started_event.wait()

    shutdown_event.set()
    await task

    assert consumer.started is True
    assert consumer.stopped is True


@pytest.mark.asyncio
async def test_consumer_agent_starts_and_stops_ops_server_when_enabled(monkeypatch):
    import agent_sdk.layer3_adapters.presenters.agent_consumer as consumer_module

    shutdown_event = asyncio.Event()
    consumer = _BackgroundConsumer()
    publisher = _MockPublisher()
    ops_started = asyncio.Event()
    ops_stopped = asyncio.Event()

    async def _serve_consumer_ops_app(container, shutdown_event):
        ops_started.set()
        await shutdown_event.wait()
        ops_stopped.set()

    monkeypatch.setattr(
        consumer_module,
        "_serve_consumer_ops_app",
        _serve_consumer_ops_app,
        raising=False,
    )

    settings = SimpleNamespace(
        CONSUMER_OPS_ENABLED=True,
        SERVER_HOST="127.0.0.1",
        SERVER_PORT=36000,
        ALLOW_ORIGINS=["*"],
        HEARTBEAT_INTERVAL_SECONDS=30,
    )
    container = {
        "execute_agent": _StubExecuteUseCase(),
        "get_agent_info": _StubGetAgentInfoUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
        "_dependencies": {"shutdown_event": shutdown_event, "settings": settings},
    }

    task = asyncio.create_task(run_consumer_agent(container))
    await consumer.started_event.wait()
    await asyncio.wait_for(ops_started.wait(), timeout=1)

    shutdown_event.set()
    await task

    assert ops_stopped.is_set()


@pytest.mark.asyncio
async def test_handle_awaits_async_execute_use_case():
    """_handle must await execute_use_case.execute(); verifies the async contract."""
    inbound = {
        "message": "async test",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "tenant_id": "default",
        "correlation_id": "corr-async",
        "reply_topic": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _StubExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert len(publisher.published) == 1
    assert publisher.published[0]["message"]["message"] == "[processed] async test"
    assert publisher.published[0]["message"]["status"] == "success"


@pytest.mark.asyncio
async def test_execute_use_case_execute_is_called_once_with_async_mock():
    """execute_use_case.execute is awaited exactly once per message."""
    inbound = {
        "message": "mock test",
        "conversation_id": "conv-2",
        "user_id": "user-2",
        "tenant_id": "default",
        "correlation_id": "corr-mock",
        "reply_topic": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    async_execute_mock = AsyncMock(
        return_value=ExecuteAgentOutput(
            message="[mock] mock test",
            status="success",
            correlation_id="corr-mock",
        )
    )
    stub_use_case = _StubExecuteUseCase()
    stub_use_case.execute = async_execute_mock  # type: ignore[method-assign]
    container = {
        "execute_agent": stub_use_case,
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    async_execute_mock.assert_awaited_once()
    assert publisher.published[0]["message"]["message"] == "[mock] mock test"


# ─── Consumer error handling ─────────────────────────────────────────────


class _FailingExecuteUseCase:
    """Stub that always raises to simulate an unhandled exception."""

    async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        raise RuntimeError("simulated execute failure")


@pytest.mark.asyncio
async def test_consumer_publishes_error_response_when_execute_raises():
    """exception in execute must publish an error response instead of crashing."""
    inbound = {
        "message": "test",
        "correlation_id": "corr-err-1",
        "reply_to": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _FailingExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert len(publisher.published) == 1
    msg = publisher.published[0]["message"]
    assert msg["success"] is False
    assert msg["status"] == "error"
    assert msg["correlation_id"] == "corr-err-1"
    assert msg["error"] == "Internal agent error"
    assert msg["result"]["error"] == "Internal agent error"
    assert "simulated execute failure" not in msg["error"]


@pytest.mark.asyncio
async def test_consumer_error_response_has_full_shape():
    """error response must have all required top-level keys matching success shape."""
    inbound = {
        "message": "test",
        "correlation_id": "corr-err-2",
        "reply_to": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _FailingExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    msg = publisher.published[0]["message"]
    for key in (
        "correlation_id",
        "success",
        "message",
        "result",
        "error",
        "agent_data",
        "status",
    ):
        assert key in msg, f"Missing top-level key: {key}"
    result = msg["result"]
    for key in (
        "message",
        "status",
        "session_id",
        "interrupted",
        "interrupt_payload",
        "agent_data",
        "error",
        "error_code",
        "related_step_id",
        "is_critical",
        "error_context",
    ):
        assert key in result, f"Missing result key: {key}"
    assert result["interrupted"] is False
    assert result["interrupt_payload"] is None
    assert result["error_code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_consumer_no_publish_when_execute_raises_and_no_reply_to():
    """when reply_to is empty, publish the error response to the safe default topic."""
    inbound = {
        "message": "test",
        "correlation_id": "corr-err-3",
        "reply_to": "",
        "reply_topic": "",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _FailingExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert len(publisher.published) == 1
    published = publisher.published[0]
    assert published["topic"] == "agent.responses"
    assert published["key"] == "corr-err-3"
    assert published["message"]["status"] == "error"
    assert published["message"]["error"] == "Internal agent error"


@pytest.mark.asyncio
async def test_consumer_logs_error_when_execute_raises():
    """exception must be logged via logger.error."""

    class _TrackingLogger(_StubLogger):
        def __init__(self):
            self.errors = []

        def error(self, message: str, **kwargs):
            self.errors.append(message)

    inbound = {
        "message": "test",
        "correlation_id": "corr-err-4",
        "reply_to": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    logger = _TrackingLogger()
    container = {
        "execute_agent": _FailingExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": logger,
    }
    await run_consumer_agent(container)
    assert len(logger.errors) >= 1


# ─── Interrupt payload shape consistency ─────────────────────────────────


class _InterruptedExecuteUseCase:
    """Stub that returns an interrupted output with a full HitlInterruptPayload."""

    async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        payload = HitlInterruptPayload(
            thread_id="thread-42",
            interrupt_id="int-42",
            value={"question": "Approve?"},
            type=InterruptType.CONFIRMATION,
            message="Please confirm",
            tenant_id="tenant-1",
            user_id="user-1",
            conv_id="conv-42",
        )
        return ExecuteAgentOutput(
            message="interrupted",
            status="interrupted",
            correlation_id=request.correlation_id,
            interrupted=True,
            interrupt_payload=payload,
        )


@pytest.mark.asyncio
async def test_consumer_interrupt_payload_preserves_full_shape():
    """interrupt_payload in response.result must contain all domain fields, not just 3."""
    inbound = {
        "message": "test",
        "correlation_id": "corr-int-1",
        "reply_to": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _InterruptedExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    msg = publisher.published[0]["message"]
    result = msg["result"]
    interrupt = result["interrupt_payload"]
    assert interrupt is not None
    assert (
        interrupt.get("type") is not None
    ), "type field missing — was overwritten with 3-key shape"
    assert (
        interrupt.get("message") is not None
    ), "message field missing — was overwritten with 3-key shape"
    assert (
        interrupt.get("tenant_id") is not None
    ), "tenant_id field missing — was overwritten with 3-key shape"


@pytest.mark.asyncio
async def test_consumer_interrupt_payload_not_overwritten_at_top_level():
    """top-level response must NOT have 'interrupt_payload' with truncated shape."""
    inbound = {
        "message": "test",
        "correlation_id": "corr-int-2",
        "reply_to": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    container = {
        "execute_agent": _InterruptedExecuteUseCase(),
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    msg = publisher.published[0]["message"]
    assert "interrupt_payload" not in msg, (
        "interrupt_payload should not be at top level of response — "
        "it belongs in result sub-dict only"
    )


@pytest.mark.asyncio
async def test_consumer_delegates_agent_call_interrupt_via_queue_and_acks_delivery():
    """AGENT_CALL interrupts must be delegated instead of published back immediately."""
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest

    delivery = _TrackedDelivery(
        {
            "message": "delegate this",
            "conversation_id": "conv-99",
            "correlation_id": "corr-delegate-1",
            "reply_to": "orchestrator.reply.topic",
        }
    )
    consumer = _MockConsumer(delivery)
    publisher = _MockPublisher()
    delegator = AsyncMock()
    delegator.delegate = AsyncMock(return_value={"type": "AGENT_CALL"})
    payload = HitlInterruptPayload(
        thread_id="conv-99",
        interrupt_id="int-99",
        value={
            "type": "AGENT_CALL",
            "agent_id": "planner-1",
            "agent_type": "planner",
            "input": '{"message": "plan this"}',
        },
        type=InterruptType.AGENT_CALL,
        message="Call planner",
        tenant_id="tenant-1",
        user_id="user-1",
        conv_id="conv-99",
    )
    execute_output = ExecuteAgentOutput(
        message="interrupted",
        status="interrupted",
        correlation_id="corr-delegate-1",
        session_id="sess-99",
        interrupted=True,
        interrupt_payload=payload,
    )
    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock(return_value=execute_output)
    container = {
        "execute_agent": execute_use_case,
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
        "_dependencies": {"agent_delegator": delegator},
    }

    await run_consumer_agent(container)

    delegator.delegate.assert_awaited_once()
    delegated_request = delegator.delegate.await_args.args[0]
    assert isinstance(delegated_request, AgentCallRequest)
    assert delegated_request.agent_id == "planner-1"
    assert delegated_request.input_payload == {"message": "plan this"}
    assert delegated_request.thread_id == "conv-99"
    assert delegated_request.correlation_id == "corr-delegate-1"
    assert delegated_request.session_id == "sess-99"
    assert delegated_request.reply_topic == "agent.responses"
    assert delegated_request.reply_queue == "default/agent.request"
    delivery.ack.assert_awaited_once()
    assert publisher.published == []


@pytest.mark.asyncio
async def test_consumer_routes_typed_execute_envelopes_without_losing_agent_call_delegation():
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    delivery = _TrackedDelivery(
        {
            "type": "agent.request.agent",
            "correlation_id": "corr-delegate-router-1",
            "thread_id": "conv-101",
            "reply_to": "orchestrator.reply.topic",
            "input": {"message": "delegate this"},
        }
    )
    consumer = _MockConsumer(delivery)
    publisher = _MockPublisher()
    delegator = AsyncMock()
    delegator.delegate = AsyncMock(return_value={"type": "AGENT_CALL"})
    payload = HitlInterruptPayload(
        thread_id="conv-101",
        interrupt_id="int-101",
        value={
            "type": "AGENT_CALL",
            "agent_id": "planner-1",
            "agent_type": "planner",
            "input": '{"message": "plan this"}',
        },
        type=InterruptType.AGENT_CALL,
        message="Call planner",
        tenant_id="tenant-1",
        user_id="user-1",
        conv_id="conv-101",
    )
    execute_output = ExecuteAgentOutput(
        message="interrupted",
        status="interrupted",
        correlation_id="corr-delegate-router-1",
        session_id="sess-101",
        interrupted=True,
        interrupt_payload=payload,
    )
    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock(return_value=execute_output)
    router = MessageReactionRouter(execute_use_case=execute_use_case)
    container = {
        "execute_agent": execute_use_case,
        "consumer": consumer,
        "publisher": publisher,
        "message_reaction_router": router,
        "logger": _StubLogger(),
        "_dependencies": {"agent_delegator": delegator},
    }

    await run_consumer_agent(container)

    delegator.delegate.assert_awaited_once()
    delegated_request = delegator.delegate.await_args.args[0]
    assert isinstance(delegated_request, AgentCallRequest)
    assert delegated_request.thread_id == "conv-101"
    assert delegated_request.correlation_id == "corr-delegate-router-1"
    delivery.ack.assert_awaited_once()
    assert publisher.published == []


@pytest.mark.asyncio
async def test_consumer_prefers_queue_reply_topic_for_delegated_agent_calls():
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest

    delivery = _TrackedDelivery(
        {
            "message": "delegate this",
            "correlation_id": "corr-delegate-2",
            "session_id": "sess-100",
        }
    )
    consumer = _MockConsumer(delivery)
    publisher = _MockPublisher()
    delegator = AsyncMock()
    payload = HitlInterruptPayload(
        thread_id="conv-100",
        interrupt_id="interrupt-100",
        value={
            "type": "AGENT_CALL",
            "agent_id": "planner-1",
            "agent_type": "planner",
            "input": '{"message": "plan this"}',
        },
        type=InterruptType.AGENT_CALL,
        message="Call planner",
        tenant_id="tenant-1",
        user_id="user-1",
        conv_id="conv-100",
    )
    execute_output = ExecuteAgentOutput(
        message="interrupted",
        status="interrupted",
        correlation_id="corr-delegate-2",
        session_id="sess-100",
        interrupted=True,
        interrupt_payload=payload,
    )
    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock(return_value=execute_output)

    class _Settings:
        QUEUE_REPLY_TOPIC = "planner.reply"
        KAFKA_RESPONSE_TOPIC = "agent.responses"

    container = {
        "execute_agent": execute_use_case,
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
        "_dependencies": {
            "agent_delegator": delegator,
            "settings": _Settings(),
        },
    }

    await run_consumer_agent(container)

    delegated_request = delegator.delegate.await_args.args[0]
    assert isinstance(delegated_request, AgentCallRequest)
    assert delegated_request.reply_topic == "planner.reply"
    assert delegated_request.reply_queue == "default/agent.request"


class _CapturingExecuteUseCase:
    """Stub that captures the request passed to execute."""

    def __init__(self):
        self.captured_request: ExecuteAgentInput | None = None

    async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        self.captured_request = request
        return ExecuteAgentOutput(
            message="ok",
            status="success",
            session_id=request.session_id,
            correlation_id=request.correlation_id,
        )


@pytest.mark.asyncio
async def test_consumer_passes_session_id_from_message_to_execute_agent_input():
    """session_id from Kafka message must be present in ExecuteAgentInput passed to execute."""
    inbound = {
        "message": "hello",
        "session_id": "sess-abc-123",
        "correlation_id": "corr-sess-1",
        "reply_to": "agent.responses",
    }
    consumer = _MockConsumer(inbound)
    publisher = _MockPublisher()
    use_case = _CapturingExecuteUseCase()
    container = {
        "execute_agent": use_case,
        "consumer": consumer,
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert use_case.captured_request is not None
    assert use_case.captured_request.session_id == "sess-abc-123"


@pytest.mark.asyncio
async def test_consumer_rejects_top_level_agent_call_typed_envelope():
    """Top-level AGENT_CALL queue envelopes must be rejected, not executed or acked."""
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    delivery = _TrackedDelivery(
        {
            "type": "AGENT_CALL",
            "agent_id": "child-agent",
            "input": {"message": "hello"},
        }
    )
    consumer = _MockConsumer(delivery)
    publisher = _MockPublisher()
    execute_use_case = AsyncMock()
    execute_use_case.execute = AsyncMock(
        return_value=ExecuteAgentOutput(
            message="should not run",
            status="success",
            correlation_id="corr-agent-call",
        )
    )
    router = MessageReactionRouter(execute_use_case=execute_use_case)
    container = {
        "execute_agent": execute_use_case,
        "consumer": consumer,
        "publisher": publisher,
        "message_reaction_router": router,
        "logger": _StubLogger(),
    }

    await run_consumer_agent(container)

    execute_use_case.execute.assert_not_awaited()
    delivery.reject.assert_awaited_once()
    delivery.ack.assert_not_awaited()
    assert publisher.published == []


@pytest.mark.asyncio
async def test_consumer_marks_router_handled_custom_message_ids_as_seen():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    class _DuplicateDeliveryConsumer:
        def __init__(self, deliveries: list[_TrackedDelivery]) -> None:
            self._deliveries = deliveries
            self._topic = "agent.request"
            self._queue_name = "default/agent.request"

        async def start(self, handler):
            for delivery in self._deliveries:
                await handler(delivery)

        async def stop(self):
            pass

    payload = {
        "type": "workflow.queue",
        "message_id": "msg-workflow-1",
        "correlation_id": "corr-workflow-1",
        "payload": {"step": 1},
    }
    first_delivery = _TrackedDelivery(dict(payload))
    second_delivery = _TrackedDelivery(dict(payload))
    seen = {}

    async def custom_handler(payload):
        seen["payload"] = payload

    router = MessageReactionRouter(custom_handlers={"workflow.queue": custom_handler})
    container = {
        "execute_agent": AsyncMock(),
        "consumer": _DuplicateDeliveryConsumer([first_delivery, second_delivery]),
        "publisher": _MockPublisher(),
        "message_reaction_router": router,
        "logger": _StubLogger(),
    }

    await run_consumer_agent(container)

    assert seen["payload"] == first_delivery.payload
    first_delivery.ack.assert_awaited_once()
    second_delivery.ack.assert_awaited_once()
    second_delivery.reject.assert_not_awaited()
    second_delivery.nack.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_marks_router_rejected_messages_as_failed_in_durable_inbox():
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    delivery = _TrackedDelivery(
        {
            "type": "AGENT_CALL",
            "message_id": "msg-router-reject-1",
            "agent_id": "child-agent",
            "input": {"message": "hello"},
        }
    )
    inbox_repository = _InMemoryInboxRepository()
    execute_use_case = AsyncMock()
    router = MessageReactionRouter(execute_use_case=execute_use_case)
    container = {
        "execute_agent": execute_use_case,
        "consumer": _MockConsumer(delivery),
        "publisher": _MockPublisher(),
        "message_reaction_router": router,
        "logger": _StubLogger(),
        "inbox_repository": inbox_repository,
    }

    await run_consumer_agent(container)

    record = inbox_repository.find_by_message_id("msg-router-reject-1")
    assert record is not None
    assert record.status == "FAILED"
    execute_use_case.execute.assert_not_awaited()
    delivery.reject.assert_awaited_once()
    delivery.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_treats_router_ack_then_raise_as_completed():
    class _AckThenRaiseRouter:
        async def handle(self, delivery):
            await delivery.ack()
            raise RuntimeError("router raised after ack")

    delivery = _TrackedDelivery(
        {
            "type": "workflow.queue",
            "message_id": "msg-router-ack-raise-1",
            "correlation_id": "corr-router-ack-raise-1",
            "payload": {"step": 1},
        }
    )
    inbox_repository = _InMemoryInboxRepository()
    container = {
        "execute_agent": AsyncMock(),
        "consumer": _MockConsumer(delivery),
        "publisher": _MockPublisher(),
        "message_reaction_router": _AckThenRaiseRouter(),
        "logger": _StubLogger(),
        "inbox_repository": inbox_repository,
    }

    await run_consumer_agent(container)

    record = inbox_repository.find_by_message_id("msg-router-ack-raise-1")
    assert record is not None
    assert record.status == "COMPLETED"
    delivery.ack.assert_awaited_once()
    delivery.nack.assert_not_awaited()
    delivery.reject.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_does_not_treat_failed_router_ack_as_completed():
    class _AckFailureRouter:
        async def handle(self, delivery):
            await delivery.ack()

    delivery = _TrackedDelivery(
        {
            "type": "workflow.queue",
            "message_id": "msg-router-ack-fail-1",
            "correlation_id": "corr-router-ack-fail-1",
            "payload": {"step": 1},
        }
    )
    delivery.ack.side_effect = RuntimeError("broker ack failed")
    inbox_repository = _InMemoryInboxRepository()
    container = {
        "execute_agent": AsyncMock(),
        "consumer": _MockConsumer(delivery),
        "publisher": _MockPublisher(),
        "message_reaction_router": _AckFailureRouter(),
        "logger": _StubLogger(),
        "inbox_repository": inbox_repository,
    }

    with pytest.raises(RuntimeError, match="broker ack failed"):
        await run_consumer_agent(container)

    record = inbox_repository.find_by_message_id("msg-router-ack-fail-1")
    assert record is not None
    assert record.status == "FAILED"
    delivery.ack.assert_awaited_once()
    delivery.nack.assert_awaited_once_with(requeue=True)
    delivery.reject.assert_not_awaited()
