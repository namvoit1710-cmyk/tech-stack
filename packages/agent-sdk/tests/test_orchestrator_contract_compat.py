"""
Tests for accept orchestrator/executor request envelopes
and publish compatible progress/final responses.
"""

import pytest

from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentInput,
    ResumeAgentOutput,
)
from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent
from tests.helpers.testing import StubLogger as _StubLogger


class _CapturingUseCase:
    def __init__(self, output: ExecuteAgentOutput | None = None):
        self.received: list[ExecuteAgentInput] = []
        self._output = output or ExecuteAgentOutput(
            message="ok",
            status="success",
            correlation_id="corr-1",
            session_id="sess-1",
        )

    async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        self.received.append(request)
        return self._output


class _MockConsumer:
    def __init__(self, message: dict):
        self._message = message

    async def start(self, handler):
        await handler(self._message)


class _MockPublisher:
    def __init__(self):
        self.published: list[dict] = []

    async def publish(self, topic: str, message: dict, key: str | None = None) -> None:
        self.published.append({"topic": topic, "message": message, "key": key})


# ---------------------------------------------------------------------------
# Full orchestrator inbound envelope
# ---------------------------------------------------------------------------

ORCHESTRATOR_INBOUND = {
    "agent_type": "sdk-agent",
    "action": "run",
    "correlation_id": "corr-1",
    "reply_to": "orchestrator.orch-001.response",
    "intent": {"text": "hello"},
    "parameters": {"x": 1},
    "execution_context": {"conversation_id": "conv-1"},
    "context_snapshot": {"history": []},
}


# ---------------------------------------------------------------------------
# Test: reply_to is preferred over reply_topic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_uses_reply_to_as_reply_topic():
    """reply_to is preferred over reply_topic for the response destination."""
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(ORCHESTRATOR_INBOUND),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert len(publisher.published) == 1
    assert publisher.published[0]["topic"] == "orchestrator.orch-001.response"


@pytest.mark.asyncio
async def test_consumer_falls_back_to_reply_topic_when_no_reply_to():
    """reply_topic is used when reply_to is absent."""
    inbound = {
        "message": "hello",
        "correlation_id": "corr-2",
        "reply_topic": "agent.responses",
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert publisher.published[0]["topic"] == "agent.responses"


# ---------------------------------------------------------------------------
# Test: message extraction from intent.text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_extracts_message_from_intent_text():
    """When 'message' key is missing, intent.text is used as the message."""
    inbound = {
        "correlation_id": "corr-3",
        "reply_to": "somewhere",
        "intent": {"text": "hello from intent"},
        "execution_context": {"conversation_id": "conv-3"},
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert use_case.received[0].message == "hello from intent"


@pytest.mark.asyncio
async def test_consumer_prefers_message_over_intent_text():
    """Explicit 'message' key takes priority over intent.text."""
    inbound = {
        "message": "direct message",
        "correlation_id": "corr-4",
        "reply_to": "somewhere",
        "intent": {"text": "intent text"},
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert use_case.received[0].message == "direct message"


# ---------------------------------------------------------------------------
# Test: conv_id extraction from execution_context.conversation_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_extracts_conv_id_from_execution_context():
    """conv_id falls back to execution_context.conversation_id."""
    inbound = {
        "correlation_id": "corr-5",
        "reply_to": "somewhere",
        "intent": {"text": "hi"},
        "execution_context": {"conversation_id": "conv-from-ctx"},
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert use_case.received[0].conv_id == "conv-from-ctx"


@pytest.mark.asyncio
async def test_consumer_prefers_conv_id_over_conversation_id():
    """Explicit conv_id takes priority over conversation_id and execution_context."""
    inbound = {
        "conv_id": "conv-explicit",
        "conversation_id": "conv-alt",
        "correlation_id": "corr-6",
        "reply_to": "somewhere",
        "intent": {"text": "hi"},
        "execution_context": {"conversation_id": "conv-from-ctx"},
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert use_case.received[0].conv_id == "conv-explicit"


@pytest.mark.asyncio
async def test_consumer_prefers_conversation_id_over_execution_context():
    """conversation_id takes priority over execution_context.conversation_id."""
    inbound = {
        "conversation_id": "conv-flat",
        "correlation_id": "corr-7",
        "reply_to": "somewhere",
        "intent": {"text": "hi"},
        "execution_context": {"conversation_id": "conv-from-ctx"},
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert use_case.received[0].conv_id == "conv-flat"


# ---------------------------------------------------------------------------
# Test: ExecuteAgentInput receives orchestrator fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_maps_orchestrator_fields_to_execute_input():
    """Full orchestrator envelope maps correctly to ExecuteAgentInput."""
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(ORCHESTRATOR_INBOUND),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    req = use_case.received[0]
    assert req.correlation_id == "corr-1"
    assert req.message == "hello"
    assert req.conv_id == "conv-1"
    assert req.reply_to == "orchestrator.orch-001.response"
    assert req.parameters == {"x": 1}


# ---------------------------------------------------------------------------
# Test: Final response envelope mirrors executor contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_response_has_executor_compatible_envelope():
    """Response top-level envelope mirrors the executor reply shape."""
    output = ExecuteAgentOutput(
        message="done",
        status="success",
        correlation_id="corr-1",
        session_id="sess-42",
        agent_data={"k": "v"},
        error=None,
        error_code=None,
        interrupted=False,
        interrupt_payload=None,
    )
    use_case = _CapturingUseCase(output)
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(ORCHESTRATOR_INBOUND),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    resp = publisher.published[0]["message"]

    assert resp["correlation_id"] == "corr-1"
    assert resp["success"] is True
    assert resp["message"] == "done"
    assert resp["error"] is None
    assert resp["agent_data"] == {"k": "v"}
    assert resp["status"] == "success"


@pytest.mark.asyncio
async def test_consumer_response_has_result_sub_object():
    """Response contains a 'result' sub-object with full SDK fields."""
    output = ExecuteAgentOutput(
        message="done",
        status="success",
        correlation_id="corr-1",
        session_id="sess-42",
        agent_data={"k": "v"},
        error=None,
        error_code=None,
        interrupted=False,
        interrupt_payload=None,
    )
    use_case = _CapturingUseCase(output)
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(ORCHESTRATOR_INBOUND),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    result = publisher.published[0]["message"]["result"]

    assert result["message"] == "done"
    assert result["status"] == "success"
    assert result["session_id"] == "sess-42"
    assert result["interrupted"] is False
    assert result["interrupt_payload"] is None
    assert result["agent_data"] == {"k": "v"}
    assert result["error"] is None
    assert result["error_code"] is None


@pytest.mark.asyncio
async def test_consumer_response_success_false_on_error_status():
    """'success' field is False when output.status is 'error'."""
    output = ExecuteAgentOutput(
        message="",
        status="error",
        correlation_id="corr-1",
        error="Something went wrong",
        error_code="ERR_001",
    )
    use_case = _CapturingUseCase(output)
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(ORCHESTRATOR_INBOUND),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    resp = publisher.published[0]["message"]
    assert resp["success"] is False
    assert resp["error"] == "Something went wrong"
    assert resp["result"]["error_code"] == "ERR_001"


# ---------------------------------------------------------------------------
# Test: correlation_id passed to ExecuteAgentInput (request scope)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_passes_correlation_id_to_use_case():
    """correlation_id from inbound envelope is forwarded to the use case."""
    inbound = {
        "correlation_id": "corr-req-1",
        "reply_to": "some.topic",
        "message": "hi",
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert use_case.received[0].correlation_id == "corr-req-1"


# ---------------------------------------------------------------------------
# Test: HTTP route accepts same additive fields
# ---------------------------------------------------------------------------


def test_http_execute_route_accepts_orchestrator_fields():
    """SERVER mode /execute endpoint accepts full orchestrator envelope fields."""
    from fastapi.testclient import TestClient

    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    class _StubExecUseCase:
        async def execute(self, req: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(
                message="ok",
                status="success",
                correlation_id=req.correlation_id,
            )

    container = {"execute_agent": _StubExecUseCase()}
    app = create_agent_app(container)
    client = TestClient(app)
    payload = {
        "agent_type": "sdk-agent",
        "action": "run",
        "correlation_id": "corr-http-1",
        "reply_to": "orchestrator.orch-001.response",
        "message": "hello",
        "intent": {"text": "hello"},
        "parameters": {"x": 1},
        "execution_context": {"conversation_id": "conv-1"},
        "context_snapshot": {"history": []},
    }
    response = client.post("/api/v1/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["correlation_id"] == "corr-http-1"


def test_http_execute_response_does_not_embed_event_stream():
    """SERVER mode HTTP response body does not contain an 'events' key."""
    from fastapi.testclient import TestClient

    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    class _StubExecUseCase:
        async def execute(self, req: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(message="ok", status="success")

    container = {"execute_agent": _StubExecUseCase()}
    app = create_agent_app(container)
    client = TestClient(app)
    response = client.post(
        "/api/v1/execute",
        json={"message": "hello"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "events" not in data


# ---------------------------------------------------------------------------
# Test: legacy consumer payloads still work (no regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_consumer_payload_still_works():
    """Old-style inbound with reply_topic and message still routes correctly."""
    inbound = {
        "message": "legacy msg",
        "correlation_id": "corr-legacy",
        "reply_topic": "agent.responses",
        "conv_id": "conv-legacy",
        "user_id": "u1",
        "tenant_id": "t1",
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert publisher.published[0]["topic"] == "agent.responses"
    assert use_case.received[0].message == "legacy msg"
    assert use_case.received[0].conv_id == "conv-legacy"


# ---------------------------------------------------------------------------
# Test: identity fields fall back to execution_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_extracts_identity_fields_from_execution_context():
    """session_id, user_id, tenant_id fallback to execution_context when absent at top level."""
    inbound = {
        "correlation_id": "corr-ctx",
        "reply_to": "somewhere",
        "intent": {"text": "hi"},
        "execution_context": {
            "conversation_id": "conv-ctx",
            "session_id": "sess-ctx",
            "user_id": "user-ctx",
            "tenant_id": "tenant-ctx",
        },
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    req = use_case.received[0]
    assert req.session_id == "sess-ctx"
    assert req.user_id == "user-ctx"
    assert req.tenant_id == "tenant-ctx"
    assert req.conv_id == "conv-ctx"


@pytest.mark.asyncio
async def test_top_level_identity_fields_override_execution_context():
    """Top-level session_id/user_id/tenant_id take priority over execution_context."""
    inbound = {
        "correlation_id": "corr-top",
        "reply_to": "somewhere",
        "message": "hi",
        "session_id": "sess-top",
        "user_id": "user-top",
        "tenant_id": "tenant-top",
        "execution_context": {
            "session_id": "sess-ctx",
            "user_id": "user-ctx",
            "tenant_id": "tenant-ctx",
        },
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    req = use_case.received[0]
    assert req.session_id == "sess-top"
    assert req.user_id == "user-top"
    assert req.tenant_id == "tenant-top"


# ---------------------------------------------------------------------------
# Helper: capturing resume use case for thread-ID tests
# ---------------------------------------------------------------------------


class _CapturingResumeUseCase:
    def __init__(self):
        self.received: list[ResumeAgentInput] = []

    async def execute(self, request: ResumeAgentInput) -> ResumeAgentOutput:
        self.received.append(request)
        return ResumeAgentOutput(message="resumed", status="success")


# ---------------------------------------------------------------------------
# Test: thread-ID resolved from delegation.parent_thread_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_resolves_thread_id_from_delegation_parent():
    """agent.response with delegation.parent_thread_id resolves thread_id correctly."""
    inbound = {
        "type": "agent.response",
        "delegation": {"parent_thread_id": "thread-from-delegation"},
        "result": {"answer": "42"},
        "correlation_id": "corr-resume",
    }
    execute_uc = _CapturingUseCase()
    resume_uc = _CapturingResumeUseCase()
    publisher = _MockPublisher()
    container = {
        "execute_agent": execute_uc,
        "resume_agent": resume_uc,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": _StubLogger(),
    }
    await run_consumer_agent(container)
    assert len(resume_uc.received) == 1
    assert resume_uc.received[0].thread_id == "thread-from-delegation"
    assert len(execute_uc.received) == 0


# ---------------------------------------------------------------------------
# Helper: recording logger for warning tests
# ---------------------------------------------------------------------------


class _RecordingLogger(_StubLogger):
    def __init__(self):
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, message: str, *args, **kwargs) -> None:
        self.warnings.append((message, kwargs))


# ---------------------------------------------------------------------------
# Test: typeless message emits warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_warns_on_typeless_message():
    """A message without a 'type' field triggers a logger.warning call."""
    inbound = {
        "message": "no type here",
        "correlation_id": "corr-notype",
        "reply_to": "somewhere",
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    logger = _RecordingLogger()
    container = {
        "execute_agent": use_case,
        "consumer": _MockConsumer(inbound),
        "publisher": publisher,
        "logger": logger,
    }
    await run_consumer_agent(container)
    # Should still process as execute request
    assert len(use_case.received) == 1
    # And should have emitted a warning
    assert len(logger.warnings) == 1
    assert "no 'type' field" in logger.warnings[0][0]


# ---------------------------------------------------------------------------
# Tests: MessageDeduplicator unit tests
# ---------------------------------------------------------------------------

from agent_sdk.layer2_application.services.message_deduplicator import (
    MessageDeduplicator,
)


def test_dedup_unseen_message_is_not_duplicate():
    dedup = MessageDeduplicator()
    assert dedup.is_duplicate("msg-1") is False


def test_dedup_seen_message_is_duplicate():
    dedup = MessageDeduplicator()
    dedup.mark_seen("msg-1")
    assert dedup.is_duplicate("msg-1") is True


def test_dedup_evicts_oldest_when_over_capacity():
    dedup = MessageDeduplicator(max_size=2)
    dedup.mark_seen("a")
    dedup.mark_seen("b")
    dedup.mark_seen("c")  # evicts "a"
    assert dedup.is_duplicate("a") is False
    assert dedup.is_duplicate("b") is True
    assert dedup.is_duplicate("c") is True


def test_dedup_expired_entry_is_not_duplicate(monkeypatch):
    import time as _time

    calls = [0.0]

    def fake_monotonic():
        return calls[0]

    monkeypatch.setattr(_time, "monotonic", fake_monotonic)
    from agent_sdk.layer2_application.services import message_deduplicator as _mod

    monkeypatch.setattr(_mod.time, "monotonic", fake_monotonic)

    dedup = MessageDeduplicator(ttl_seconds=10.0)
    dedup.mark_seen("msg-1")
    assert dedup.is_duplicate("msg-1") is True

    calls[0] = 11.0  # advance past TTL
    assert dedup.is_duplicate("msg-1") is False


# ---------------------------------------------------------------------------
# Test: consumer dedup integration — duplicate message is skipped
# ---------------------------------------------------------------------------


class _MultiMessageConsumer:
    """Feeds multiple messages, one per handler call."""

    def __init__(self, messages: list[dict]):
        self._messages = messages

    async def start(self, handler):
        for msg in self._messages:
            await handler(msg)


@pytest.mark.asyncio
async def test_consumer_skips_duplicate_messages():
    """Second message with the same message_id is skipped."""
    msg = {
        "message": "hello",
        "message_id": "dup-1",
        "correlation_id": "corr-1",
        "reply_to": "somewhere",
    }
    use_case = _CapturingUseCase()
    publisher = _MockPublisher()
    logger = _RecordingLogger()
    container = {
        "execute_agent": use_case,
        "consumer": _MultiMessageConsumer([msg, msg]),
        "publisher": publisher,
        "logger": logger,
    }
    await run_consumer_agent(container)
    # Only processed once
    assert len(use_case.received) == 1
    # Duplicate warning emitted
    dup_warnings = [w for w in logger.warnings if "Duplicate" in w[0]]
    assert len(dup_warnings) == 1
