import pytest

from agent_sdk.layer4_frameworks.messaging.kafka_publisher import (
    KafkaMessagePublisher,
)


class Broker:
    def __init__(self):
        self.published = []
        self.flush_called = False
        self.flush_timeout = None
        self.close_called = False

    def publish_to_topic(self, topic, message, key=None):
        self.published.append(
            {
                "topic": topic,
                "message": message,
                "key": key,
            }
        )

    def flush_producer(self, timeout=10.0):
        self.flush_called = True
        self.flush_timeout = timeout

    def close(self):
        self.close_called = True


@pytest.mark.asyncio
async def test_kafka_publisher_publish_uses_broker():
    broker = Broker()
    publisher = KafkaMessagePublisher(broker_client=broker)

    await publisher.publish(
        topic="agent.request",
        message={"type": "agent.request.agent"},
        key="corr-1",
    )

    assert broker.published == [
        {
            "topic": "agent.request",
            "message": {"type": "agent.request.agent"},
            "key": "corr-1",
        }
    ]


@pytest.mark.asyncio
async def test_kafka_publisher_close_flushes_producer_without_closing_broker():
    broker = Broker()
    publisher = KafkaMessagePublisher(broker_client=broker)

    await publisher.close()

    assert broker.flush_called is True
    assert broker.flush_timeout == 10.0
    assert broker.close_called is False
