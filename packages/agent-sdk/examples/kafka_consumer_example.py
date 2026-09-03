"""kafka_consumer_example.py — CONSUMER mode / SAP Event Mesh native example.

Demonstrates how to run an agent as a SAP Event Mesh consumer using the SDK's
``CONSUMER`` mode (``APP_MODE=CONSUMER``). Kafka remains the local
compatibility backend, but the native CONSUMER path is ``MESSAGING_MODE=sap``.

In CONSUMER mode, ``run_agent`` calls ``run_consumer_agent(container)``
instead of starting the HTTP server.  The agent continuously polls the
``EVENT_MESH_REQUEST_TOPIC`` topic for inbound messages when
``MESSAGING_MODE=sap`` (or ``KAFKA_REQUEST_TOPIC`` when ``MESSAGING_MODE=local``),
processes each one with
``ExecuteAgentUseCase``, and publishes the result to the reply topic
specified in the message payload. If the execution returns an
``AGENT_CALL`` interrupt, the consumer delegates it through
``AsyncAgentDelegator`` and resumes when the correlated ``agent.response``
arrives back through ``MessageReactionRouter``.

Key SDK symbols / components:
    ``run_agent``           — entrypoint; routes to CONSUMER or SERVER based
                              on ``APP_MODE`` environment variable
    ``KafkaMessageConsumer``— wraps a raw Kafka client; calls your handler
                              for each inbound message
    ``run_consumer_agent``  — top-level async loop; wires consumer → handler
                              → execute_use_case → publisher
    ``KafkaPublisher``      — publishes agent responses back to Kafka
    ``MESSAGING_MODE``      — selects the transport backend:
                              "mock" (default) → no-op / console publisher
                              "sap"            → SAP Event Mesh publisher + consumer
                              "local"          → KafkaPublisher + KafkaMessageConsumer

Native Event Mesh settings:
    EVENT_MESH_NAMESPACE     — namespace prefix for Event Mesh topics/queues
    EVENT_MESH_REQUEST_TOPIC — request topic consumed in sap mode
    EVENT_MESH_MESSAGING_URL — publish/consume REST base URL
    EVENT_MESH_MANAGEMENT_URL — queue/topic provisioning REST base URL

Environment variables required for production:
    APP_MODE=CONSUMER               — activates consumer mode in ``run_agent``
    MESSAGING_MODE=sap              — selects SAP Event Mesh (native)
    EVENT_MESH_TOKEN_URL            — OAuth 2.0 token endpoint
    EVENT_MESH_CLIENT_ID            — OAuth 2.0 client ID
    EVENT_MESH_CLIENT_SECRET        — OAuth 2.0 client secret
    EVENT_MESH_MESSAGING_URL        — Event Mesh messaging REST base URL
    EVENT_MESH_MANAGEMENT_URL       — Event Mesh management REST base URL
    EVENT_MESH_REQUEST_TOPIC        — topic the agent reads from (default: "agent.request")
    OPENAI_API_KEY                  — required if the pipeline calls an LLM

Compatibility / local development:
    MESSAGING_MODE=local            — selects Kafka transport
    KAFKA_BOOTSTRAP_SERVERS         — e.g. "kafka:9092"
    KAFKA_REQUEST_TOPIC             — topic the agent reads from (default: "agent.request")
    KAFKA_GROUP_ID                  — consumer group id (default: "agent-sdk-consumer")

Orchestrator-style inbound message format (JSON):
    {
      "correlation_id":    "correlation-uuid",          // echoed back in the response
      "reply_to":          "agent.responses",           // primary reply topic (preferred)
      "reply_topic":       "agent.responses",           // legacy alias for reply_to
      "intent": {
        "text":            "user request text"          // preferred message field
      },
      "message":           "user request text",         // legacy alias for intent.text
      "execution_context": {
        "conversation_id": "conversation-id"            // preferred conv_id source
      },
      "conv_id":           "conversation-id",           // legacy alias
      "user_id":           "user-identifier",           // optional, defaults to "anonymous"
      "tenant_id":         "tenant-identifier",         // optional, defaults to "default"
      "source":            "orchestrator",              // optional routing hint
      "parameters":        {},                          // optional extra parameters
      "context_snapshot":  {}                           // optional context snapshot
    }

Outbound response format (JSON, published to reply_to):
    {
      "correlation_id": "<echoed>",
      "success":        true | false,
      "message":        "agent reply text",
      "status":         "success" | "error",
      "error":          null,
      "agent_data":     {},
      "result": {
        "message":          "agent reply text",
        "status":           "success" | "error",
        "session_id":       null,
        "interrupted":      false,
        "interrupt_payload": null,
        "agent_data":       {},
        "error":            null,
        "error_code":       null
      }
    }

Flow diagram::

    Kafka topic (agent.request)
          ↓
    KafkaMessageConsumer.start(handler)
          ↓
    run_consumer_agent._handle(raw_message)
          ↓  maps fields → ExecuteAgentInput
          ↓  (intent.text / message, reply_to / reply_topic,
          ↓   execution_context.conversation_id / conv_id, …)
    ExecuteAgentUseCase.execute(request)
          ↓  returns ExecuteAgentOutput
    KafkaPublisher.publish(reply_to, response_envelope)
          ↓
    Kafka topic (reply_to)

NOTE: This file is a **conceptual example**.  ``main()`` cannot be run
without a live Kafka cluster.  Use ``demo_locally()`` (invoked via
``--demo``) for a self-contained dry-run that verifies the wiring using
in-memory stubs — it exits 0 without any external services.

Usage::

    # SAP Event Mesh native consumer run (requires Event Mesh env vars):
    APP_MODE=CONSUMER MESSAGING_MODE=sap \
        EVENT_MESH_TOKEN_URL=https://... \
        EVENT_MESH_CLIENT_ID=... \
        EVENT_MESH_CLIENT_SECRET=... \
        EVENT_MESH_MESSAGING_URL=https://... \
        EVENT_MESH_MANAGEMENT_URL=https://... \
        EVENT_MESH_REQUEST_TOPIC=agent.request \
        OPENAI_API_KEY=sk-... python examples/kafka_consumer_example.py

    # Compatibility / local Kafka run:
    APP_MODE=CONSUMER MESSAGING_MODE=local KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
        KAFKA_REQUEST_TOPIC=agent.request KAFKA_GROUP_ID=my-agent \
        OPENAI_API_KEY=sk-... python examples/kafka_consumer_example.py

    # In-memory demo (no dependencies):
    python examples/kafka_consumer_example.py --demo
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# In-memory stubs used by the local demo
# ---------------------------------------------------------------------------


class _StubConsumer:
    """In-memory consumer that delivers one message immediately when started.

    This mirrors the real ``KafkaMessageConsumer`` interface (start/stop)
    without requiring a live Kafka broker.  On ``start(handler)`` it calls
    the handler once with the pre-loaded inbound message and then returns.
    """

    def __init__(self, message: dict, topic: str = "agent.request") -> None:
        self._message = message
        self._topic = topic
        self._queue_name = f"default/{topic}"
        self.topic = topic
        self.started = False

    async def start(self, handler) -> None:
        self.started = True
        print(f"[StubConsumer] Started on topic '{self.topic}', delivering 1 message")
        await handler(self._message)

    async def stop(self) -> None:
        pass


class _StubPublisher:
    """In-memory publisher that records all published messages."""

    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, topic: str, message: dict, key=None) -> None:
        self.published.append({"topic": topic, "message": message})
        print(f"[StubPublisher] Published to '{topic}': {message}")

    async def close(self) -> None:
        pass


class _StubRegistry:
    def __init__(self, capabilities: list[dict]) -> None:
        self._capabilities = capabilities

    async def list_capabilities(self) -> list[dict]:
        return list(self._capabilities)


class _TrackedDelivery:
    def __init__(self, payload: dict, label: str = "Delivery") -> None:
        self.payload = payload
        self._label = label
        self.acked = False
        self.rejected = False
        self.nack_calls: list[bool] = []

    async def ack(self) -> None:
        self.acked = True
        print(f"{self._label} acknowledged")

    async def nack(self, requeue: bool = True) -> None:
        self.nack_calls.append(requeue)

    async def reject(self) -> None:
        self.rejected = True


class _CapturingResumeUseCase:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def execute(self, request) -> dict:
        self.requests.append(request)
        return {"status": "success"}


class _StubLogger:
    def info(self, msg, **kw):
        print(f"[LOG] {msg}", {k: v for k, v in kw.items()})

    def error(self, msg, **kw):
        print(f"[ERR] {msg}", {k: v for k, v in kw.items()})

    def warning(self, msg, **kw):
        pass

    def debug(self, msg, **kw):
        pass


# ---------------------------------------------------------------------------
# Local demo — no Kafka or LLM required
# ---------------------------------------------------------------------------


async def demo_locally() -> None:
    """Dry-run the consumer pipeline with in-memory stubs.

    Three scenarios are exercised:

    1. **Legacy envelope** — flat ``message`` + ``reply_topic`` fields.
       Verifies backward-compatibility with older callers.

    2. **Orchestrator-style envelope** — nested ``intent.text``,
       ``execution_context``, and ``reply_to``.  Verifies that the
       consumer correctly resolves the message from ``intent.text`` and
       the reply topic from ``reply_to``.

    3. **Queue-native delegation and resume** — an ``AGENT_CALL``
       interrupt is delegated via ``AsyncAgentDelegator`` and then
       resumed through ``MessageReactionRouter`` using a correlated
       ``agent.response`` envelope.  This mirrors the current
       CONSUMER-mode broker path.

    Both scenarios verify the outbound response envelope, including the
    nested ``result``.
    """
    from agent_sdk import ExecuteAgentInput, ExecuteAgentOutput, run_consumer_agent

    class _EchoUseCase:
        async def execute(self, req: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(
                message=f"[echo] {req.message}",
                status="success",
                correlation_id=req.correlation_id,
            )

    # ------------------------------------------------------------------
    # Scenario 1: legacy flat envelope
    # ------------------------------------------------------------------
    legacy_inbound = {
        "message": "Analyse Q1 sales data",
        "conv_id": "conv-demo-001",
        "user_id": "alice",
        "tenant_id": "acme-corp",
        "correlation_id": "corr-legacy-001",
        "reply_topic": "agent.responses",
    }

    publisher1 = _StubPublisher()
    container1 = {
        "execute_agent": _EchoUseCase(),
        "consumer": _StubConsumer(legacy_inbound, topic="agent.request"),
        "publisher": publisher1,
        "logger": _StubLogger(),
    }

    print("Scenario 1: legacy envelope …\n")
    await run_consumer_agent(container1)

    assert len(publisher1.published) == 1, "Expected exactly one published response"
    resp1 = publisher1.published[0]["message"]
    assert (
        resp1["message"] == "[echo] Analyse Q1 sales data"
    ), f"Got: {resp1['message']}"
    assert resp1["status"] == "success", f"Got: {resp1['status']}"
    assert (
        resp1["correlation_id"] == "corr-legacy-001"
    ), f"Got: {resp1['correlation_id']}"
    assert resp1["success"] is True, f"Got: {resp1['success']}"
    assert publisher1.published[0]["topic"] == "agent.responses"
    result1 = resp1["result"]
    assert result1["message"] == "[echo] Analyse Q1 sales data"
    assert result1["status"] == "success"
    assert result1["interrupted"] is False
    print("Scenario 1 passed.\n")

    # ------------------------------------------------------------------
    # Scenario 2: orchestrator-style envelope
    # ------------------------------------------------------------------
    orchestrator_inbound = {
        "correlation_id": "corr-orch-002",
        "reply_to": "orchestrator.agent.responses",
        "intent": {
            "text": "Summarise the quarterly report",
        },
        "execution_context": {
            "conversation_id": "conv-orch-002",
        },
        "user_id": "bob",
        "tenant_id": "globex",
        "source": "orchestrator",
        "context_snapshot": {"prior_summary": "Q3 was strong"},
    }

    publisher2 = _StubPublisher()
    container2 = {
        "execute_agent": _EchoUseCase(),
        "consumer": _StubConsumer(orchestrator_inbound, topic="agent.request"),
        "publisher": publisher2,
        "logger": _StubLogger(),
    }

    print("Scenario 2: orchestrator-style envelope …\n")
    await run_consumer_agent(container2)

    assert len(publisher2.published) == 1, "Expected exactly one published response"
    resp2 = publisher2.published[0]["message"]
    assert (
        resp2["message"] == "[echo] Summarise the quarterly report"
    ), f"Got: {resp2['message']}"
    assert resp2["status"] == "success", f"Got: {resp2['status']}"
    assert resp2["correlation_id"] == "corr-orch-002", f"Got: {resp2['correlation_id']}"
    assert resp2["success"] is True, f"Got: {resp2['success']}"
    assert (
        publisher2.published[0]["topic"] == "orchestrator.agent.responses"
    ), f"reply_to routing failed; got: {publisher2.published[0]['topic']}"
    result2 = resp2["result"]
    assert result2["message"] == "[echo] Summarise the quarterly report"
    assert result2["status"] == "success"
    assert result2["interrupted"] is False
    print("Scenario 2 passed.\n")

    # ------------------------------------------------------------------
    # Scenario 3: queue-native AGENT_CALL delegation
    # ------------------------------------------------------------------
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
        InterruptType,
    )
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )
    from agent_sdk.layer2_application.services.message_reaction.router import (
        MessageReactionRouter,
    )

    class _DelegatingUseCase:
        async def execute(self, req: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(
                message="Delegating to planner",
                status="interrupted",
                correlation_id=req.correlation_id,
                session_id="sess-delegate-003",
                interrupted=True,
                interrupt_payload=HitlInterruptPayload(
                    thread_id=req.conv_id,
                    interrupt_id="int-delegate-003",
                    value={
                        "type": "AGENT_CALL",
                        "agent_id": "planner-1",
                        "agent_type": "planner",
                        "input": '{"message": "plan this"}',
                    },
                    type=InterruptType.AGENT_CALL,
                    message="Call planner",
                    tenant_id=req.tenant_id,
                    user_id=req.user_id,
                    conv_id=req.conv_id,
                ),
            )

    delegated_inbound = {
        "message": "Delegate the planning work",
        "conv_id": "conv-delegate-003",
        "reply_to": "orchestrator.agent.responses",
        "correlation_id": "corr-delegate-003",
        "user_id": "bob",
        "tenant_id": "globex",
    }

    print("Scenario 3: queue-native delegation and resume\n")
    original_delivery = _TrackedDelivery(delegated_inbound, label="Original delivery")
    delegating_consumer = _StubConsumer(original_delivery, topic="agent.request")
    publisher3 = _StubPublisher()
    correlation_threads: dict[str, str] = {}
    delegator = AsyncAgentDelegator(
        publisher=publisher3,
        registry=_StubRegistry(
            [
                {
                    "agent_type": "planner",
                    "queue_metadata": {"request_topic": "planner.request"},
                }
            ]
        ),
        correlation_threads=correlation_threads,
    )
    resume_use_case = _CapturingResumeUseCase()
    router = MessageReactionRouter(
        resume_use_case=resume_use_case,
        correlation_threads=correlation_threads,
    )
    container3 = {
        "execute_agent": _DelegatingUseCase(),
        "consumer": delegating_consumer,
        "publisher": publisher3,
        "logger": _StubLogger(),
        "_dependencies": {"agent_delegator": delegator},
    }

    await run_consumer_agent(container3)

    assert len(publisher3.published) == 1, "Expected delegated request publish"
    delegated_publish = publisher3.published[0]
    delegated_message = delegated_publish["message"]
    assert delegated_publish["topic"] == "planner.request"
    assert delegated_message["type"] == "agent.request.agent"
    assert delegated_message["agent_id"] == "planner-1"
    assert delegated_message["thread_id"] == "conv-delegate-003"
    assert delegated_message["correlation_id"] == "corr-delegate-003"
    assert delegated_message["session_id"] == "sess-delegate-003"
    assert delegated_message["reply_topic"] == "agent.responses"
    assert delegated_message["reply_queue"] == "default/agent.request"
    assert isinstance(
        AgentCallRequest(
            agent_id=delegated_message["agent_id"],
            agent_type=delegated_message["agent_type"],
            input_payload=delegated_message["input"],
            interrupt_id=delegated_message["interrupt_id"],
            thread_id=delegated_message["thread_id"],
            correlation_id=delegated_message["correlation_id"],
            session_id=delegated_message["session_id"],
            reply_topic=delegated_message["reply_topic"],
            reply_queue=delegated_message["reply_queue"],
        ),
        AgentCallRequest,
    )
    assert correlation_threads == {"corr-delegate-003": "conv-delegate-003"}
    assert original_delivery.acked is True
    print(
        "Delegated request reply path: "
        f"topic={delegated_message['reply_topic']} queue={delegated_message['reply_queue']}"
    )

    response_delivery = _TrackedDelivery(
        {
            "type": "agent.response",
            "correlation_id": "corr-delegate-003",
            "result": {"message": "Planner completed the plan", "status": "success"},
        },
        label="Resume delivery",
    )
    print("Received correlated agent.response")
    await router.handle(response_delivery)

    assert len(resume_use_case.requests) == 1, "Expected a single resume request"
    resume_request = resume_use_case.requests[0]
    assert resume_request.thread_id == "conv-delegate-003"
    assert resume_request.correlation_id == "corr-delegate-003"
    assert resume_request.resume_value == {
        "message": "Planner completed the plan",
        "status": "success",
    }
    assert response_delivery.acked is True
    assert correlation_threads == {}
    print(f"Parent thread resumed: {resume_request.thread_id}")
    print("Correlation state cleaned up")
    print("Scenario 3 passed.\n")

    print(
        "Demo passed — consumer pipeline verified end-to-end, including "
        "queue-native delegation and correlated resume."
    )


# ---------------------------------------------------------------------------
# Production entry point
#
# Requires: APP_MODE=CONSUMER, Kafka env vars, OPENAI_API_KEY
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the agent in CONSUMER mode.

    ``run_agent`` reads ``APP_MODE`` from the environment.  When
    ``APP_MODE=CONSUMER`` it calls ``run_consumer_agent(container)``; when
    ``APP_MODE=SERVER`` (default) it starts the HTTP server instead.

    All Kafka connection parameters (bootstrap servers, topic, group ID)
    are read from environment variables by ``build_app_container``.
    Set ``MESSAGING_MODE=local`` (or ``sap`` for SAP Event Mesh) to select
    a real transport backend; the default ``mock`` mode uses a no-op
    publisher and no consumer.
    """
    from agent_sdk import run_agent

    run_agent()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        asyncio.run(demo_locally())
    else:
        main()
