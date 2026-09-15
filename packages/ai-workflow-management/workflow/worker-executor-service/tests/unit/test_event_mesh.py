import sys
import os
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer4_frameworks.messaging.event_mesh_result_publisher import EventMeshResultPublisher
from app.layer4_frameworks.messaging.event_mesh_task_queue import EventMeshTaskQueue


class MockBrokerClient:
    def __init__(self, consume_result=None):
        self.published = []
        self.acknowledged = []
        self.subscriptions = []
        self._consume_result = consume_result

    def publish_to_topic(self, topic, message, key=None):
        self.published.append({"topic": topic, "message": message, "key": key})

    def subscribe_to_topic(self, topic, callback):
        self.subscriptions.append({"topic": topic, "callback": callback})

    def consume_from_queue(self, queue_name):
        return self._consume_result

    def acknowledge(self, queue_name, message_id):
        self.acknowledged.append({"queue": queue_name, "message_id": message_id})


# --- EventMeshResultPublisher ---

def test_publish_dict_result():
    broker = MockBrokerClient()
    pub = EventMeshResultPublisher(broker)
    pub.publish_result({"task_id": "t1", "status": "COMPLETED"})
    assert len(broker.published) == 1
    assert broker.published[0]["topic"] == "workflow.results"
    assert broker.published[0]["message"]["task_id"] == "t1"


def test_publish_dataclass_result():
    @dataclass
    class SomeResult:
        task_id: str = ""
        status: str = ""

    broker = MockBrokerClient()
    pub = EventMeshResultPublisher(broker)
    pub.publish_result(SomeResult(task_id="t2", status="DONE"))
    assert broker.published[0]["message"]["task_id"] == "t2"


def test_publish_string_result():
    broker = MockBrokerClient()
    pub = EventMeshResultPublisher(broker)
    pub.publish_result("raw string")
    assert broker.published[0]["message"] == {"data": "raw string"}


# --- EventMeshTaskQueue ---

def test_consume_subscribes_to_tasks_topic():
    def cb(data):
        pass

    broker = MockBrokerClient()
    queue = EventMeshTaskQueue(broker)
    queue.consume(cb)
    assert len(broker.subscriptions) == 1
    assert broker.subscriptions[0]["topic"] == "workflow.tasks"
    assert broker.subscriptions[0]["callback"] is cb


def test_acknowledge_is_noop():
    broker = MockBrokerClient()
    queue = EventMeshTaskQueue(broker)
    queue.acknowledge({"message_id": "m1"})
    queue.acknowledge("not a dict")
    assert len(broker.acknowledged) == 0
