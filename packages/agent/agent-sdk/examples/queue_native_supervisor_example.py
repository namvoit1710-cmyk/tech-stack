"""queue_native_supervisor_example.py — queue-first supervisor walkthrough.

Demonstrates the queue-native counterpart to
``examples/supervisor_agent_example.py``.

Instead of relying on ``AgentCallCoordinator`` and HTTP round-trips, this
example shows how a supervisor can run in ``CONSUMER`` mode and delegate
sub-agent work through ``AsyncAgentDelegator`` while resuming the paused
parent thread through ``MessageReactionRouter``.

The ``--demo`` flow is intentionally in-memory and proves six steps:

1. the supervisor receives a broker delivery
2. supervisor execution returns an ``AGENT_CALL`` interrupt
3. registry ``QueueMetadata`` resolves the downstream request / reply route
4. ``AsyncAgentDelegator`` publishes an ``agent.request.agent`` envelope and
   records ``correlation_threads``
5. a simulated downstream ``agent.response`` is consumed by
   ``MessageReactionRouter``
6. the paused supervisor thread resumes and correlation state is cleaned up

Usage::

    python examples/queue_native_supervisor_example.py --demo
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _StubConsumer:
    def __init__(self, message, topic: str = "supervisor.request") -> None:
        self._message = message
        self._topic = topic
        self._queue_name = f"default/{topic}"
        self.topic = topic

    async def start(self, handler) -> None:
        print(
            "[StubConsumer] Started on topic "
            f"'{self.topic}', delivering 1 supervisor request"
        )
        await handler(self._message)

    async def stop(self) -> None:
        pass


class _StubPublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, topic: str, message: dict, key=None) -> None:
        self.published.append({"topic": topic, "message": message, "key": key})
        print(f"[StubPublisher] Published to '{topic}': {message}")


class _StubRegistry:
    async def list_capabilities(self) -> list[dict]:
        return [
            {
                "agent_type": "planner-agent",
                "queue_metadata": {
                    "request_topic": "planner.request.topic",
                    "reply_topic": "planner.reply.topic",
                },
            }
        ]


class _TrackedDelivery:
    def __init__(self, payload: dict, label: str) -> None:
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


async def demo_locally() -> None:
    from agent_sdk import ExecuteAgentInput, ExecuteAgentOutput, run_consumer_agent
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

    class _SupervisorExecuteUseCase:
        async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
            return ExecuteAgentOutput(
                message="Delegating planner work",
                status="interrupted",
                correlation_id=request.correlation_id,
                session_id="sess-supervisor-001",
                interrupted=True,
                interrupt_payload=HitlInterruptPayload(
                    thread_id=request.conv_id,
                    interrupt_id="int-supervisor-001",
                    value={
                        "type": "AGENT_CALL",
                        "agent_id": "planner-uuid-001",
                        "agent_type": "planner-agent",
                        "input": {
                            "message": "Draft a three-step execution plan",
                            "parameters": {"team": "finance"},
                        },
                    },
                    type=InterruptType.AGENT_CALL,
                    message="Call planner-agent",
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    conv_id=request.conv_id,
                ),
            )

    print("Queue-first supervisor demo\n")

    inbound = {
        "message": "Plan the finance rollout",
        "conv_id": "supervisor-thread-001",
        "correlation_id": "corr-supervisor-001",
        "reply_to": "supervisor.final.responses",
        "user_id": "alice",
        "tenant_id": "globex",
    }
    original_delivery = _TrackedDelivery(inbound, label="Supervisor request delivery")
    publisher = _StubPublisher()
    correlation_threads: dict[str, str] = {}
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_StubRegistry(),
        correlation_threads=correlation_threads,
        compatibility_request_topic="agent.request.agent",
    )
    resume_use_case = _CapturingResumeUseCase()
    router = MessageReactionRouter(
        resume_use_case=resume_use_case,
        correlation_threads=correlation_threads,
    )

    container = {
        "execute_agent": _SupervisorExecuteUseCase(),
        "consumer": _StubConsumer(original_delivery),
        "publisher": publisher,
        "logger": _StubLogger(),
        "_dependencies": {"agent_delegator": delegator},
    }

    await run_consumer_agent(container)

    assert len(publisher.published) == 1, "Expected delegated queue publish"
    delegated_publish = publisher.published[0]
    delegated_message = delegated_publish["message"]
    assert delegated_publish["topic"] == "planner.request.topic"
    assert delegated_message["type"] == "agent.request.agent"
    assert delegated_message["reply_topic"] == "agent.responses"
    assert delegated_message["reply_queue"] == "default/supervisor.request"
    assert delegated_message["delegation"] == {
        "parent_thread_id": "supervisor-thread-001"
    }
    assert original_delivery.acked is True
    assert correlation_threads == {"corr-supervisor-001": "supervisor-thread-001"}

    print(
        "Resolved queue route from registry: "
        "request_topic=planner.request.topic reply_topic=supervisor.request"
    )
    print("Published agent.request.agent")

    response_delivery = _TrackedDelivery(
        {
            "type": "agent.response",
            "correlation_id": "corr-supervisor-001",
            "result": {
                "message": "Planner completed the finance rollout plan",
                "status": "success",
            },
        },
        label="Supervisor reply delivery",
    )
    print("Received agent.response")
    await router.handle(response_delivery)

    assert len(resume_use_case.requests) == 1, "Expected one supervisor resume request"
    resume_request = resume_use_case.requests[0]
    assert resume_request.thread_id == "supervisor-thread-001"
    assert resume_request.correlation_id == "corr-supervisor-001"
    assert resume_request.resume_value == {
        "message": "Planner completed the finance rollout plan",
        "status": "success",
    }
    assert response_delivery.acked is True
    assert correlation_threads == {}
    print(f"Supervisor thread resumed: {resume_request.thread_id}")
    print("Correlation state cleaned up")
    print("Demo passed — queue-first supervisor flow verified end-to-end.")


def main() -> None:
    asyncio.run(demo_locally())


if __name__ == "__main__":
    if "--demo" in sys.argv:
        main()
    else:
        print(
            "This example is intentionally demo-first. Run with --demo to see "
            "the queue-native supervisor flow without external services."
        )
