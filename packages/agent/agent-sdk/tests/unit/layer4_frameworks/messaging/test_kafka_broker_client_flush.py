import pytest

from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient


class Producer:
    def __init__(self, remaining=0):
        self.remaining = remaining
        self.timeout = None

    def flush(self, timeout=10.0):
        self.timeout = timeout
        return self.remaining


def make_client_without_init(producer):
    client = object.__new__(KafkaBrokerClient)
    client._producer = producer
    return client


def test_flush_producer_flushes_underlying_kafka_producer():
    producer = Producer(remaining=0)
    client = make_client_without_init(producer)

    client.flush_producer(timeout=3.0)

    assert producer.timeout == 3.0


def test_flush_producer_raises_when_messages_remain_buffered():
    producer = Producer(remaining=2)
    client = make_client_without_init(producer)

    with pytest.raises(RuntimeError, match="2 message"):
        client.flush_producer(timeout=3.0)
