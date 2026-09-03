import pytest

from agent_sdk.layer2_application.services.message_reaction.router import (
    MessageReactionRouter,
)


class Delivery:
    def __init__(self, payload):
        self.payload = payload
        self.acked = False
        self.nacked = False
        self.rejected = False
        self.requeue = None

    async def ack(self):
        self.acked = True

    async def nack(self, requeue=True):
        self.nacked = True
        self.requeue = requeue

    async def reject(self):
        self.rejected = True


class Publisher:
    def __init__(self):
        self.published = []

    async def publish(self, topic, message, key=None):
        self.published.append(
            {
                "topic": topic,
                "message": message,
                "key": key,
            }
        )


@pytest.mark.asyncio
async def test_custom_handler_payload_only_still_works():
    seen = {}

    async def handler(payload):
        seen["payload"] = payload

    router = MessageReactionRouter(
        custom_handlers={
            "echo.queue.demo": handler,
        }
    )

    delivery = Delivery(
        {
            "type": "echo.queue.demo",
            "correlation_id": "corr-1",
            "payload": {"text": "hello"},
        }
    )

    await router.handle(delivery)

    assert delivery.acked is True
    assert delivery.nacked is False
    assert delivery.rejected is False
    assert seen["payload"] == delivery.payload


@pytest.mark.asyncio
async def test_custom_handler_can_receive_dependencies_positionally():
    publisher = Publisher()

    async def handler(payload, deps):
        await deps["publisher"].publish(
            topic="echo.response",
            message={
                "type": "echo.response",
                "correlation_id": payload["correlation_id"],
                "payload": payload["payload"],
            },
            key=payload["correlation_id"],
        )

    router = MessageReactionRouter(
        custom_handlers={
            "echo.queue.demo": handler,
        },
        handler_dependencies={
            "publisher": publisher,
        },
    )

    delivery = Delivery(
        {
            "type": "echo.queue.demo",
            "correlation_id": "corr-2",
            "payload": {"text": "hello"},
        }
    )

    await router.handle(delivery)

    assert delivery.acked is True
    assert delivery.nacked is False
    assert delivery.rejected is False
    assert publisher.published == [
        {
            "topic": "echo.response",
            "message": {
                "type": "echo.response",
                "correlation_id": "corr-2",
                "payload": {"text": "hello"},
            },
            "key": "corr-2",
        }
    ]


@pytest.mark.asyncio
async def test_custom_handler_can_receive_dependencies_by_keyword():
    seen = {}

    async def handler(payload, *, deps):
        seen["settings"] = deps["settings"]
        seen["payload"] = payload

    router = MessageReactionRouter(
        custom_handlers={
            "echo.queue.demo": handler,
        },
        handler_dependencies={
            "settings": {"APP_MODE": "CONSUMER"},
        },
    )

    delivery = Delivery(
        {
            "type": "echo.queue.demo",
            "correlation_id": "corr-3",
            "payload": {"text": "hello"},
        }
    )

    await router.handle(delivery)

    assert delivery.acked is True
    assert seen["settings"] == {"APP_MODE": "CONSUMER"}
    assert seen["payload"] == delivery.payload


@pytest.mark.asyncio
async def test_custom_handler_failure_nacks_for_retry():
    async def handler(payload, deps):
        raise RuntimeError("boom")

    router = MessageReactionRouter(
        custom_handlers={
            "echo.queue.demo": handler,
        },
        handler_dependencies={},
    )

    delivery = Delivery(
        {
            "type": "echo.queue.demo",
            "correlation_id": "corr-4",
            "payload": {"text": "hello"},
        }
    )

    await router.handle(delivery)

    assert delivery.acked is False
    assert delivery.nacked is True
    assert delivery.requeue is True
    assert delivery.rejected is False
