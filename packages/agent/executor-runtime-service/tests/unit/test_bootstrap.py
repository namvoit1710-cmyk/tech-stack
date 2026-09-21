from __future__ import annotations

import pytest

from app.layer1_domain.entities.batch_request import StepBatchRequest


class _CapturedPublisher:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, object]] = []

    async def publish(self, topic: str, message: dict, key: str | None = None) -> None:
        return None


class _CapturedQueueHandler:
    def __init__(self, *args, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.requests: list[StepBatchRequest] = []

    async def handle_step_batch(self, request: StepBatchRequest) -> None:
        self.requests.append(request)


class _CapturedDelegator:
    def __init__(self, *args, **kwargs) -> None:
        self.init_kwargs = kwargs

    async def delegate(self, request):
        return {
            "request_message_type": getattr(request, "request_message_type", None),
            "response_message_type": getattr(request, "response_message_type", None),
        }


def test_build_app_container_wires_executor_handler_and_message_reaction_handlers(
    bootstrap_module,
    monkeypatch,
):
    sentinel_publisher = _CapturedPublisher()
    sentinel_router = type("_Router", (), {"_custom_handlers": {}})()
    sentinel_container = {
        "publisher": sentinel_publisher,
        "message_reaction_router": sentinel_router,
    }

    def _fake_sdk_build_app_container(*, extra_dependencies, local_tools):
        sentinel_container["_dependencies"] = {
            **extra_dependencies,
            "agent_registry": object(),
            "correlation_threads": {},
        }
        sentinel_container["logger"] = extra_dependencies["logger"]
        sentinel_container["dispatch_step_use_case"] = extra_dependencies[
            "dispatch_step_use_case"
        ]
        return sentinel_container

    monkeypatch.setattr(
        bootstrap_module,
        "_sdk_build_app_container",
        _fake_sdk_build_app_container,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "ExecutorQueueHandler",
        _CapturedQueueHandler,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "AsyncAgentDelegator",
        _CapturedDelegator,
    )

    container = bootstrap_module.build_app_container()

    message_handler = container["message_reaction_handlers"]["agent.request.executor"]
    payload = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_001",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "batch_id": "batch_001",
            "steps": [
                {
                    "id": "step_1",
                    "execute_by": "tool.11111111-1111-4111-8111-111111111111",
                    "goal": "Echo the input",
                    "message": "",
                    "input_data": {"input_text": "hello"},
                    "output_data": {},
                }
            ],
        },
    }

    import asyncio

    asyncio.run(message_handler(payload, {}))

    handler = container["_dependencies"]["executor_queue_handler"]
    status_publisher = container["_dependencies"]["status_publisher"]

    assert handler.init_kwargs["dispatcher"] is container["dispatch_step_use_case"]
    assert handler.init_kwargs["status_publisher"] is status_publisher
    assert handler.init_kwargs["logger"] is container["logger"]
    assert status_publisher._publisher is sentinel_publisher
    assert status_publisher._topic == bootstrap_module.settings.STATUS_PUBLISH_TOPIC
    assert container["agent_delegator"].init_kwargs["default_request_message_type"] == (
        "executor.request.agent"
    )
    assert container["agent_delegator"].init_kwargs[
        "default_response_message_type"
    ] == ("agent.step.status")
    assert len(handler.requests) == 1
    assert handler.requests[0] == payload
    assert (
        container["message_reaction_handlers"]
        is container["_dependencies"]["message_reaction_handlers"]
    )
    assert (
        sentinel_router._custom_handlers["agent.step.status"]
        is container["message_reaction_handlers"]["agent.step.status"]
    )


def test_build_app_container_raises_when_sdk_container_missing_publisher(
    bootstrap_module,
    monkeypatch,
):
    sentinel_container = {
        "message_reaction_router": type("_Router", (), {"_custom_handlers": {}})(),
    }

    def _fake_sdk_build_app_container(*, extra_dependencies, local_tools):
        sentinel_container["_dependencies"] = extra_dependencies
        return sentinel_container

    monkeypatch.setattr(
        bootstrap_module,
        "_sdk_build_app_container",
        _fake_sdk_build_app_container,
    )

    with pytest.raises(RuntimeError, match="SDK publisher is required"):
        bootstrap_module.build_app_container()
