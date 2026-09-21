from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_REPO_AGENT_SDK_DIR = Path(__file__).resolve().parents[2] / "agent-sdk"
if _REPO_AGENT_SDK_DIR.exists():
    sys.path.insert(0, str(_REPO_AGENT_SDK_DIR))


_EXECUTOR_MODULES_TO_RELOAD = (
    "bootstrap",
    "main",
    "app.layer4_frameworks.config.app_config",
    "app.layer4_frameworks.dependency.ioc_container",
    "app.layer4_frameworks.tools.tool_registry",
    "app.layer4_frameworks.tools.local_tool_executor",
    "app.layer2_application.features.executor.use_cases.validate_step_batch_use_case",
    "app.layer2_application.queues.executor_queue_handler",
)


def _reload_executor_modules() -> None:
    for module_name in _EXECUTOR_MODULES_TO_RELOAD:
        sys.modules.pop(module_name, None)


@pytest.fixture
def executor_env(monkeypatch):
    monkeypatch.setenv("APP_MODE", "CONSUMER")
    monkeypatch.setenv("MESSAGING_MODE", "mock")
    monkeypatch.setenv("AGENT_TYPE", "executor-runtime")
    monkeypatch.setenv("AGENT_DOMAIN", "execution")
    monkeypatch.setenv("QUEUE_REQUEST_TOPIC", "agent.request.executor")
    monkeypatch.setenv("QUEUE_REPLY_TOPIC", "executor.step.status")
    monkeypatch.setenv("STATUS_PUBLISH_TOPIC", "executor.step.status")
    monkeypatch.setenv("KAFKA_REQUEST_TOPIC", "agent.request.executor")
    monkeypatch.setenv("EVENT_MESH_REQUEST_TOPIC", "agent.request.executor")
    monkeypatch.setenv("EVENT_MESH_REPLY_TOPIC", "executor.step.status")
    monkeypatch.setenv("REGISTRY_URL", "")
    monkeypatch.setenv("WORKFLOW_CONTROL_PLANE_URL", "")
    monkeypatch.setenv("WORKFLOW_CONTROL_PLANE_TIMEOUT_SECONDS", "30")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _reload_executor_modules()
    return monkeypatch


@pytest.fixture
def bootstrap_module(executor_env):
    return importlib.import_module("bootstrap")


@dataclass
class CapturedPublisher:
    calls: list[dict] = field(default_factory=list)

    async def publish(self, topic: str, message: dict, key: str | None = None) -> None:
        self.calls.append({"topic": topic, "message": message, "key": key})


@dataclass
class CapturedDelivery:
    payload: dict
    ack: object
    nack: object
    reject: object


class CapturedConsumer:
    def __init__(self, delivery) -> None:
        self._delivery = delivery
        self.started = False
        self.stopped = False
        self._topic = "agent.request.executor"
        self._queue_name = "default/agent.request.executor"

    async def start(self, handler):
        self.started = True
        await handler(self._delivery)

    async def stop(self):
        self.stopped = True


@pytest.fixture
def executor_publisher():
    return CapturedPublisher()


@pytest.fixture
def executor_step_batch_payload():
    return {
        "type": "agent.request.executor",
        "message_id": "msg_exec_001",
        "correlation_id": "corr_exec_001",
        "conv_id": "conv_exec_001",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "batch_id": "batch_exec_001",
            "steps": [
                {
                    "id": "step_1",
                    "execute_by": "tool.b85063e7-32c8-46c9-90e2-0c836b1d6fa8",
                    "goal": "Echo the input",
                    "message": "",
                    "input_data": {"input_text": "Hello from integration"},
                    "output_data": {},
                }
            ],
        },
    }


@pytest.fixture
def executor_delivery(executor_step_batch_payload):
    from unittest.mock import AsyncMock

    return CapturedDelivery(
        payload=executor_step_batch_payload,
        ack=AsyncMock(),
        nack=AsyncMock(),
        reject=AsyncMock(),
    )


@pytest.fixture
def executor_consumer(executor_delivery):
    return CapturedConsumer(executor_delivery)
