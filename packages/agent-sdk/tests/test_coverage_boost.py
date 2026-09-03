"""Coverage boost for agent-sdk — targets messaging, http, registry, runner,
flow_graph_builder, middleware nodes, tool_agent_builder, agent_server, vcap_util, config."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# vcap_util.py
# ---------------------------------------------------------------------------
from agent_sdk.layer4_frameworks.config.vcap_util import (
    _get_vcap_services,
    get_hana_credentials,
)


def test_vcap_get_services_empty():
    os.environ.pop("VCAP_SERVICES", None)
    assert _get_vcap_services() == {}


def test_vcap_get_services_valid_json():
    vcap = {"hana": [{"credentials": {"host": "h"}}]}
    with patch.dict(os.environ, {"VCAP_SERVICES": json.dumps(vcap)}):
        result = _get_vcap_services()
    assert "hana" in result


def test_vcap_get_services_invalid_json():
    with patch.dict(os.environ, {"VCAP_SERVICES": "not-json"}):
        result = _get_vcap_services()
    assert result == {}


def test_get_hana_credentials_no_vcap():
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value={},
    ):
        assert get_hana_credentials() is None


def test_get_hana_credentials_no_service():
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value={"other": []},
    ):
        assert get_hana_credentials() is None


def test_get_hana_credentials_incomplete():
    vcap = {"hana": [{"credentials": {"host": "", "user": "", "password": ""}}]}
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value=vcap,
    ):
        assert get_hana_credentials() is None


def test_get_hana_credentials_full():
    vcap = {
        "hana": [
            {
                "credentials": {
                    "host": "myhost",
                    "port": 443,
                    "user": "u",
                    "password": "p",
                    "schema": "S",
                    "encrypt": True,
                    "certificate": "CERT",
                    "hdi_user": "hdi",
                    "hdi_password": "hdip",
                }
            }
        ]
    }
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value=vcap,
    ):
        result = get_hana_credentials()
    assert result is not None
    assert result.host == "myhost"
    assert result.hdi_user == "hdi"


def test_get_hana_credentials_encrypt_string_true():
    vcap = {
        "hana-cloud": [
            {
                "credentials": {
                    "host": "h",
                    "port": "443",
                    "user": "u",
                    "password": "p",
                    "encrypt": "true",
                }
            }
        ]
    }
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value=vcap,
    ):
        result = get_hana_credentials()
    assert result.encrypt is True


def test_get_hana_credentials_encrypt_string_false():
    vcap = {
        "hanatrial": [
            {
                "credentials": {
                    "host": "h",
                    "port": 443,
                    "user": "u",
                    "password": "p",
                    "encrypt": "false",
                }
            }
        ]
    }
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value=vcap,
    ):
        result = get_hana_credentials()
    assert result.encrypt is False


def test_get_hana_credentials_hdi_user_logs():
    vcap = {
        "hana": [
            {
                "credentials": {
                    "host": "h",
                    "port": 443,
                    "user": "u",
                    "password": "p",
                    "hdi_user": "hdi",
                    "hdi_password": "hdip",
                }
            }
        ]
    }
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value=vcap,
    ):
        result = get_hana_credentials()
    assert result.hdi_user == "hdi"


def test_get_hana_credentials_rejects_invalid_vcap_port():
    vcap = {
        "hana": [
            {
                "credentials": {
                    "host": "h",
                    "port": "not-a-port",
                    "user": "u",
                    "password": "p",
                }
            }
        ]
    }
    with patch(
        "agent_sdk.layer4_frameworks.config.vcap_util._get_vcap_services",
        return_value=vcap,
    ):
        with pytest.raises(
            ValueError,
            match="Invalid HANA port value for VCAP_SERVICES.hana.credentials.port",
        ):
            get_hana_credentials()


# ---------------------------------------------------------------------------
# app_config.py
# ---------------------------------------------------------------------------


def test_app_config_import():
    from agent_sdk.layer4_frameworks.config.app_config import settings

    assert settings is not None
    assert isinstance(settings.APP_NAME, str)
    assert hasattr(settings, "AGENT_TYPE")
    assert hasattr(settings, "HANA_HOST")


def test_detect_cf_uri_empty():
    from agent_sdk.layer4_frameworks.config.app_config import _detect_cf_uri

    os.environ.pop("VCAP_APPLICATION", None)
    assert _detect_cf_uri() == ""


def test_detect_cf_uri_with_uris():
    from agent_sdk.layer4_frameworks.config.app_config import _detect_cf_uri

    vcap = {"application_uris": ["my-app.cfapps.io"]}
    with patch.dict(os.environ, {"VCAP_APPLICATION": json.dumps(vcap)}):
        result = _detect_cf_uri()
    assert result == "my-app.cfapps.io"


def test_detect_cf_uri_with_uris_key():
    from agent_sdk.layer4_frameworks.config.app_config import _detect_cf_uri

    vcap = {"uris": ["app.cfapps.io"]}
    with patch.dict(os.environ, {"VCAP_APPLICATION": json.dumps(vcap)}):
        result = _detect_cf_uri()
    assert result == "app.cfapps.io"


def test_detect_cf_uri_invalid_json():
    from agent_sdk.layer4_frameworks.config.app_config import _detect_cf_uri

    with patch.dict(os.environ, {"VCAP_APPLICATION": "not-json"}):
        result = _detect_cf_uri()
    assert result == ""


def test_detect_cf_uri_empty_uris():
    from agent_sdk.layer4_frameworks.config.app_config import _detect_cf_uri

    vcap = {"application_uris": []}
    with patch.dict(os.environ, {"VCAP_APPLICATION": json.dumps(vcap)}):
        result = _detect_cf_uri()
    assert result == ""


def test_settings_with_vcap_hana():
    """Covers the _hana_creds branch in app_config.py by passing HANA_HOST directly."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    with patch.dict(os.environ, {"HANA_HOST": "vcap-host"}):
        s = Settings()
    assert s.HANA_HOST == "vcap-host"


def test_settings_cf_auto_detect():
    """Covers _auto_detect_cf_host when AGENT_ADVERTISED_HOST is localhost."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    vcap = {"application_uris": ["cf-app.example.com"]}
    with patch.dict(os.environ, {"VCAP_APPLICATION": json.dumps(vcap)}):
        s = Settings(AGENT_ADVERTISED_HOST="localhost")
    assert s.AGENT_ADVERTISED_HOST == "cf-app.example.com"


# ---------------------------------------------------------------------------
# messaging/__init__, console_publisher, kafka_consumer, kafka_publisher
# ---------------------------------------------------------------------------


def test_messaging_init_imports():
    from agent_sdk.layer4_frameworks.messaging import (
        ConsoleMessagePublisher,
        KafkaMessageConsumer,
        KafkaMessagePublisher,
    )

    assert KafkaMessageConsumer is not None
    assert KafkaMessagePublisher is not None
    assert ConsoleMessagePublisher is not None


@pytest.mark.asyncio
async def test_console_publisher_publish():
    from agent_sdk.layer4_frameworks.messaging.console_publisher import (
        ConsoleMessagePublisher,
    )

    pub = ConsoleMessagePublisher()
    await pub.publish("test.topic", {"msg": "hello"}, key="k1")


@pytest.mark.asyncio
async def test_console_publisher_close():
    from agent_sdk.layer4_frameworks.messaging.console_publisher import (
        ConsoleMessagePublisher,
    )

    pub = ConsoleMessagePublisher()
    await pub.close()


@pytest.mark.asyncio
async def test_kafka_publisher_publish():
    from agent_sdk.layer4_frameworks.messaging.kafka_publisher import (
        KafkaMessagePublisher,
    )

    broker = MagicMock()
    broker.publish_to_topic = MagicMock()
    log = MagicMock()
    log.info = MagicMock()
    pub = KafkaMessagePublisher(broker_client=broker, logger=log)
    await pub.publish("agent.response", {"data": 1}, key="corr-123")
    broker.publish_to_topic.assert_called_once_with(
        "agent.response", {"data": 1}, key="corr-123"
    )


@pytest.mark.asyncio
async def test_kafka_publisher_close():
    from agent_sdk.layer4_frameworks.messaging.kafka_publisher import (
        KafkaMessagePublisher,
    )

    pub = KafkaMessagePublisher(broker_client=MagicMock(), logger=MagicMock())
    await pub.close()


@pytest.mark.asyncio
async def test_kafka_consumer_start():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    broker = MagicMock()
    broker.subscribe_to_topic = MagicMock()
    log = MagicMock()
    log.info = MagicMock()
    log.error = MagicMock()
    consumer = KafkaMessageConsumer(
        broker_client=broker, topic="agent.requests", logger=log
    )
    handler = AsyncMock()
    await consumer.start(handler)
    broker.subscribe_to_topic.assert_called_once()
    assert consumer._running is True


@pytest.mark.asyncio
async def test_kafka_consumer_start_dispatches_message():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    broker = MagicMock()
    captured = {}

    def _subscribe(topic, callback):
        captured["callback"] = callback

    broker.subscribe_to_topic = _subscribe
    log = MagicMock()
    log.info = MagicMock()
    log.error = MagicMock()
    consumer = KafkaMessageConsumer(broker_client=broker, topic="req", logger=log)
    handler = AsyncMock()
    await consumer.start(handler)

    # Trigger the captured _dispatch callback
    await captured["callback"]({"correlation_id": "c1", "message": "test"})
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_kafka_consumer_dispatch_handler_exception():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    broker = MagicMock()
    captured = {}

    def _subscribe(topic, callback):
        captured["callback"] = callback

    broker.subscribe_to_topic = _subscribe
    log = MagicMock()
    log.info = MagicMock()
    log.error = MagicMock()
    consumer = KafkaMessageConsumer(broker_client=broker, topic="req", logger=log)
    handler = AsyncMock(side_effect=RuntimeError("handler failed"))
    await consumer.start(handler)
    await captured["callback"]({"correlation_id": "c2"})
    log.error.assert_called()


@pytest.mark.asyncio
async def test_kafka_consumer_stop():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    log = MagicMock()
    log.info = MagicMock()
    broker = MagicMock()
    consumer = KafkaMessageConsumer(broker_client=broker, topic="req", logger=log)
    await consumer.stop()
    assert consumer._running is False
    broker.close.assert_called_once()


# ---------------------------------------------------------------------------
# registry/http_agent_registry.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_registry_register():
    from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json = MagicMock(return_value={"agents": []})
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"agent_id": "reg-001"})
    agent_unpublished_response = MagicMock()
    agent_unpublished_response.raise_for_status = MagicMock()
    agent_unpublished_response.json = MagicMock(
        return_value={
            "id": "reg-001",
            "status": "inactive",
            "is_published": False,
        }
    )
    agent_published_response = MagicMock()
    agent_published_response.raise_for_status = MagicMock()
    agent_published_response.json = MagicMock(
        return_value={
            "id": "reg-001",
            "status": "inactive",
            "is_published": True,
        }
    )
    agent_active_response = MagicMock()
    agent_active_response.raise_for_status = MagicMock()
    agent_active_response.json = MagicMock(
        return_value={
            "id": "reg-001",
            "status": "active",
            "is_published": True,
        }
    )
    mock_client.get = AsyncMock(
        side_effect=[
            list_response,
            agent_unpublished_response,
            agent_published_response,
            agent_active_response,
        ]
    )
    mock_client.post = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    reg = AgentRegistration(
        agent_type="test-agent",
        version="1.0",
        sdk_version="1.0",
        domain="chat",
        endpoint_url="http://agent:8000",
        capabilities=[{"domain": "chat", "action": "ask"}],
        metadata={"description": "Test agent"},
    )
    result = await registry.register(reg)
    assert result == "reg-001"


@pytest.mark.asyncio
async def test_http_registry_register_no_description():
    from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json = MagicMock(return_value={"agents": []})
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"agent_id": "reg-002"})
    agent_unpublished_response = MagicMock()
    agent_unpublished_response.raise_for_status = MagicMock()
    agent_unpublished_response.json = MagicMock(
        return_value={
            "id": "reg-002",
            "status": "inactive",
            "is_published": False,
        }
    )
    agent_published_response = MagicMock()
    agent_published_response.raise_for_status = MagicMock()
    agent_published_response.json = MagicMock(
        return_value={
            "id": "reg-002",
            "status": "inactive",
            "is_published": True,
        }
    )
    agent_active_response = MagicMock()
    agent_active_response.raise_for_status = MagicMock()
    agent_active_response.json = MagicMock(
        return_value={
            "id": "reg-002",
            "status": "active",
            "is_published": True,
        }
    )
    mock_client.get = AsyncMock(
        side_effect=[
            list_response,
            agent_unpublished_response,
            agent_published_response,
            agent_active_response,
        ]
    )
    mock_client.post = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    reg = AgentRegistration(
        agent_type="test-agent",
        version="1.0",
        sdk_version="1.0",
        domain="chat",
        endpoint_url="http://agent:8000",
    )
    result = await registry.register(reg)
    assert result == "reg-002"


@pytest.mark.asyncio
async def test_http_registry_heartbeat():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    await registry.heartbeat("agent-1", "HEALTHY")
    mock_client.get.assert_called_once()
    assert mock_client.get.call_args.args[0].endswith("/api/v1/agents/agent-1")


@pytest.mark.asyncio
async def test_http_registry_deregister():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    await registry.deregister("agent-1")

    mock_client.post.assert_awaited_once()
    assert mock_client.post.call_args.args[0].endswith(
        "/api/v1/agents/agent-1/deactivate"
    )


@pytest.mark.asyncio
async def test_http_registry_close():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_client.aclose = AsyncMock()
    registry._client = mock_client

    await registry.close()
    mock_client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# runner.py
# ---------------------------------------------------------------------------


def test_runner_server_mode():
    from agent_sdk import runner

    container = {"_dependencies": {}}
    graph_factory = MagicMock(name="graph_factory")
    local_tool = MagicMock(name="local_tool")
    with (
        patch(
            "agent_sdk.runner.build_app_container", return_value=container
        ) as mock_build,
        patch("agent_sdk.runner.create_agent_app", return_value=MagicMock()),
        patch("agent_sdk.runner.settings") as mock_settings,
        patch("agent_sdk.runner.uvicorn.run") as mock_run,
    ):
        mock_settings.APP_MODE = "SERVER"
        mock_settings.SERVER_HOST = "0.0.0.0"
        mock_settings.SERVER_PORT = 8000
        runner.run_agent(agent_graph_factory=graph_factory, local_tools=[local_tool])
        mock_build.assert_called_once_with(
            features_path=None,
            base_module=None,
            extra_dependencies=None,
            agent_graph=None,
            agent_graph_factory=graph_factory,
            local_tools=[local_tool],
        )
        mock_run.assert_called_once()


def test_runner_consumer_mode():
    from agent_sdk import runner

    container = {"_dependencies": {}}
    graph_factory = MagicMock(name="graph_factory")
    local_tool = MagicMock(name="local_tool")

    def _consume_coroutine(coro):
        coro.close()

    with (
        patch(
            "agent_sdk.runner.build_app_container", return_value=container
        ) as mock_build,
        patch("agent_sdk.runner.run_consumer_agent", new=AsyncMock()),
        patch("agent_sdk.runner.settings") as mock_settings,
        patch("asyncio.run", side_effect=_consume_coroutine) as mock_asyncio_run,
    ):
        mock_settings.APP_MODE = "CONSUMER"
        runner.run_agent(agent_graph_factory=graph_factory, local_tools=[local_tool])
        mock_build.assert_called_once_with(
            features_path=None,
            base_module=None,
            extra_dependencies=None,
            agent_graph=None,
            agent_graph_factory=graph_factory,
            local_tools=[local_tool],
        )
        mock_asyncio_run.assert_called_once()


def test_runner_unknown_mode():
    from agent_sdk import runner

    container = {"_dependencies": {}}
    with (
        patch("agent_sdk.runner.build_app_container", return_value=container),
        patch("agent_sdk.runner.settings") as mock_settings,
    ):
        mock_settings.APP_MODE = "INVALID"
        with pytest.raises(ValueError, match="Unknown APP_MODE"):
            runner.run_agent()


# ---------------------------------------------------------------------------
# flow_graph_builder.py
# ---------------------------------------------------------------------------

from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
from agent_sdk.layer2_application.services.node_type_registry import NodeTypeRegistry
from agent_sdk.layer4_frameworks.graph.flow_graph_builder import FlowGraphBuilder


def _make_registry(step_types=None):
    registry = NodeTypeRegistry()
    types = step_types or ["llm_call", "condition", "router"]
    for t in types:

        def _fn(state, step_config, deps, _t=t):
            return {"result": _t}

        registry.register(t, _fn)
    return registry


def _make_flow(agent_type="test", flow_type="main", steps=None):
    config = FlowConfig(agent_type=agent_type, flow_type=flow_type)
    if steps:
        config.steps = steps
    return config


def test_flow_graph_builder_empty_steps():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    config = _make_flow(steps=[])
    with pytest.raises(ValueError, match="no steps"):
        builder.build(config)


def test_flow_graph_builder_unknown_type():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [StepConfig(id="s1", type="unknown_type")]
    config = _make_flow(steps=steps)
    with pytest.raises(ValueError, match="Unknown step type"):
        builder.build(config)


def test_flow_graph_builder_single_step():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [StepConfig(id="s1", type="llm_call")]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_sequential_steps():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [
        StepConfig(id="s1", type="llm_call"),
        StepConfig(id="s2", type="llm_call"),
    ]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_next_step_explicit():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [
        StepConfig(id="s1", type="llm_call", next_step="s2"),
        StepConfig(id="s2", type="llm_call"),
    ]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_condition_step_with_branches():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [
        StepConfig(id="check", type="condition", true_branch="yes", false_branch="no"),
        StepConfig(id="yes", type="llm_call"),
        StepConfig(id="no", type="llm_call"),
    ]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_condition_no_branches():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [
        StepConfig(id="check", type="condition"),
        StepConfig(id="next", type="llm_call"),
    ]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_router_with_valid_routes():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [
        StepConfig(
            id="route",
            type="router",
            routes={"default": "handler", "special": "handler2"},
        ),
        StepConfig(id="handler", type="llm_call"),
        StepConfig(id="handler2", type="llm_call"),
    ]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_router_no_valid_routes():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [
        StepConfig(id="route", type="router", routes={"unknown": "nonexistent"}),
        StepConfig(id="next", type="llm_call"),
    ]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_router_no_routes():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)
    steps = [
        StepConfig(id="route", type="router"),
        StepConfig(id="next", type="llm_call"),
    ]
    config = _make_flow(steps=steps)
    graph = builder.build(config)
    assert graph is not None


def test_flow_graph_builder_wrap_step_sync():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)

    def sync_fn(state, step_config, deps):
        return {"result": "sync"}

    step = StepConfig(id="s1", type="llm_call")
    wrapped = builder._wrap_step(sync_fn, step, {})
    result = wrapped({"msg": "hi"})
    assert result == {"result": "sync"}


def test_flow_graph_builder_wrap_step_async():
    reg = _make_registry()
    builder = FlowGraphBuilder(registry=reg)

    async def async_fn(state, step_config, deps):
        return {"result": "async"}

    step = StepConfig(id="s1", type="llm_call")
    wrapped = builder._wrap_step(async_fn, step, {})

    assert asyncio.iscoroutinefunction(wrapped)


# ---------------------------------------------------------------------------
# middleware nodes
# ---------------------------------------------------------------------------

from agent_sdk.layer2_application.services.middleware.input_guard_node import (
    input_guard_node,
)
from agent_sdk.layer2_application.services.middleware.output_guard_node import (
    output_guard_node,
)


def test_input_guard_node_no_guard():
    result = input_guard_node({"message": "hello"}, {})
    assert result == {}


def test_input_guard_node_passes():
    guard = MagicMock()
    guard.validate = MagicMock(return_value={"passed": True, "warnings": []})
    state = {"message": "hi", "user_id": "u1"}
    result = input_guard_node(state, {"input_guard": guard})
    assert result["input_guard_result"]["passed"] is True


def test_input_guard_node_with_sanitized():
    guard = MagicMock()
    guard.validate = MagicMock(
        return_value={"passed": True, "sanitized_message": "clean msg"}
    )
    state = {"message": "hi", "user_id": "u1"}
    result = input_guard_node(state, {"input_guard": guard})
    assert result["message"] == "clean msg"


def test_input_guard_node_fails():
    guard = MagicMock()
    guard.validate = MagicMock(
        return_value={"passed": False, "rejection_reason": "bad input"}
    )
    state = {"message": "bad", "user_id": "u1"}
    result = input_guard_node(state, {"input_guard": guard})
    assert result["error_code"] == "SDK_INPUT_001"
    assert result["input_guard_result"]["passed"] is False


def test_output_guard_node_no_guard():
    result = output_guard_node({"formatted_response": {}}, {})
    assert result == {}


def test_output_guard_node_passes():
    guard = MagicMock()
    guard.validate = MagicMock(return_value={"passed": True, "warnings": []})
    state = {"formatted_response": {"content": "reply"}}
    result = output_guard_node(state, {"output_guard": guard})
    assert result["output_guard_result"]["passed"] is True


def test_output_guard_node_with_sanitized():
    guard = MagicMock()
    guard.validate = MagicMock(
        return_value={"passed": True, "sanitized_response": {"content": "safe"}}
    )
    state = {"formatted_response": {"content": "raw"}}
    result = output_guard_node(state, {"output_guard": guard})
    assert result["formatted_response"] == {"content": "safe"}


def test_output_guard_node_fails():
    guard = MagicMock()
    guard.validate = MagicMock(
        return_value={"passed": False, "rejection_reason": "bad output"}
    )
    state = {"formatted_response": {"content": "bad"}}
    result = output_guard_node(state, {"output_guard": guard})
    assert result["error_code"] == "SDK_OUTPUT_001"


# ---------------------------------------------------------------------------
# tool_agent_builder.py
# ---------------------------------------------------------------------------


def test_tool_agent_builder_compile_no_pre_post():
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=MagicMock())

    tools = []
    builder = ToolAgentBuilder(llm=llm, tools=tools, system_prompt="You are helpful.")
    graph = builder.compile()
    assert graph is not None


def test_tool_agent_builder_compile_with_pre_post():
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=MagicMock())

    def pre_node(state):
        return {}

    def post_node(state):
        return {}

    builder = ToolAgentBuilder(
        llm=llm,
        tools=[],
        pre_nodes=[("pre", pre_node)],
        post_nodes=[("post", post_node)],
    )
    graph = builder.compile()
    assert graph is not None


def test_tool_agent_builder_compile_no_system_prompt():
    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    llm = MagicMock()
    llm.bind_tools = MagicMock(return_value=MagicMock())
    builder = ToolAgentBuilder(llm=llm, tools=[], system_prompt="")
    graph = builder.compile()
    assert graph is not None


# ---------------------------------------------------------------------------
# agent_consumer (run_consumer_agent + _NoopLogger)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_consumer_agent_success():
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    output = MagicMock()
    output.message = "reply"
    output.status = "SUCCESS"
    output.correlation_id = "c1"
    output.error = None
    output.error_code = None
    output.data = {}

    execute_uc = AsyncMock()
    execute_uc.execute = AsyncMock(return_value=output)

    consumer = AsyncMock()
    consumer.start = AsyncMock()
    consumer.requires_shutdown_wait = False

    publisher = AsyncMock()
    publisher.publish = AsyncMock()

    logger = MagicMock()
    logger.info = MagicMock()

    container = {
        "execute_agent": execute_uc,
        "consumer": consumer,
        "publisher": publisher,
        "logger": logger,
    }
    await run_consumer_agent(container)
    consumer.start.assert_called_once()


@pytest.mark.asyncio
async def test_run_consumer_agent_no_logger():
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    output = MagicMock()
    output.message = "ok"
    output.status = "SUCCESS"
    output.correlation_id = ""
    output.error = None
    output.error_code = None
    output.data = {}

    execute_uc = AsyncMock()
    execute_uc.execute = AsyncMock(return_value=output)
    consumer = AsyncMock()
    consumer.requires_shutdown_wait = False
    publisher = AsyncMock()

    container = {
        "execute_agent": execute_uc,
        "consumer": consumer,
        "publisher": publisher,
    }
    await run_consumer_agent(container)


@pytest.mark.asyncio
async def test_consumer_handle_message():
    """Test that the _handle callback actually processes a message."""
    from agent_sdk.layer3_adapters.presenters.agent_consumer import run_consumer_agent

    output = MagicMock()
    output.message = "done"
    output.status = "SUCCESS"
    output.correlation_id = "corr-1"
    output.error = None
    output.error_code = None
    output.data = {"key": "val"}

    execute_uc = AsyncMock()
    execute_uc.execute = AsyncMock(return_value=output)

    publisher = AsyncMock()
    publisher.publish = AsyncMock()

    log = MagicMock()
    log.info = MagicMock()

    captured_handler = {}

    async def _mock_start(handler):
        captured_handler["fn"] = handler

    consumer = AsyncMock()
    consumer.requires_shutdown_wait = False
    consumer.start = _mock_start

    container = {
        "execute_agent": execute_uc,
        "consumer": consumer,
        "publisher": publisher,
        "logger": log,
    }
    await run_consumer_agent(container)

    # Simulate a message arriving
    await captured_handler["fn"](
        {
            "correlation_id": "corr-1",
            "reply_topic": "resp.topic",
            "message": "hello",
            "conversation_id": "c1",
        }
    )
    publisher.publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# agent_server/__init__.py — _build_endpoint_url and _lifespan
# ---------------------------------------------------------------------------


def test_build_endpoint_url_local():
    from agent_sdk.layer3_adapters.presenters.agent_server import _build_endpoint_url

    mock_settings = MagicMock()
    mock_settings.AGENT_ADVERTISED_URL = ""
    mock_settings.AGENT_ADVERTISE_URL = ""
    mock_settings.AGENT_ADVERTISED_SCHEME = ""
    mock_settings.AGENT_ADVERTISE_SCHEME = ""
    mock_settings.AGENT_ADVERTISED_PORT = ""
    mock_settings.AGENT_ADVERTISE_PORT = ""
    mock_settings.AGENT_ADVERTISED_HOST = "localhost"
    mock_settings.SERVER_PORT = 8000
    os.environ.pop("VCAP_APPLICATION", None)
    url = _build_endpoint_url(mock_settings)
    assert url == "http://localhost:8000"


def test_build_endpoint_url_cf():
    from agent_sdk.layer3_adapters.presenters.agent_server import _build_endpoint_url

    mock_settings = MagicMock()
    mock_settings.AGENT_ADVERTISED_URL = ""
    mock_settings.AGENT_ADVERTISE_URL = ""
    mock_settings.AGENT_ADVERTISED_SCHEME = ""
    mock_settings.AGENT_ADVERTISE_SCHEME = ""
    mock_settings.AGENT_ADVERTISED_PORT = ""
    mock_settings.AGENT_ADVERTISE_PORT = ""
    mock_settings.AGENT_ADVERTISED_HOST = "myapp.cfapps.io"
    mock_settings.SERVER_PORT = 8000
    with patch.dict(os.environ, {"VCAP_APPLICATION": '{"uris": ["myapp.cfapps.io"]}'}):
        url = _build_endpoint_url(mock_settings)
    assert url == "https://myapp.cfapps.io"


def test_create_agent_app_structure():
    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    uc = MagicMock()
    container = {"_dependencies": {"execute_agent": uc}}
    app = create_agent_app(container)
    assert app is not None

    # Check health and ready routes exist. app.routes can include mounts/router
    # wrappers without a .path attribute, so inspect defensively.
    routes = {
        path
        for route in app.routes
        for path in [getattr(route, "path", None)]
        if isinstance(path, str)
    }
    assert "/health" in routes
    assert "/ready" in routes


@pytest.mark.asyncio
async def test_lifespan_no_registry():
    from fastapi.testclient import TestClient

    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    container = {"_dependencies": {}}
    app = create_agent_app(container)

    # Use TestClient context to trigger lifespan
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lifespan_with_registry():
    from fastapi.testclient import TestClient

    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    registry = AsyncMock()
    registry.register = AsyncMock(return_value="agent-xyz")
    registry.heartbeat = AsyncMock()
    registry.deregister = AsyncMock()
    registry.close = AsyncMock()

    mock_settings = MagicMock()
    mock_settings.AGENT_ADVERTISED_URL = ""
    mock_settings.AGENT_ADVERTISE_URL = ""
    mock_settings.AGENT_ADVERTISED_SCHEME = ""
    mock_settings.AGENT_ADVERTISE_SCHEME = ""
    mock_settings.AGENT_ADVERTISED_PORT = ""
    mock_settings.AGENT_ADVERTISE_PORT = ""
    mock_settings.CAPABILITIES = ["search"]
    mock_settings.AGENT_DOMAIN = "chat"
    mock_settings.AGENT_TYPE = "test-agent"
    mock_settings.AGENT_VERSION = "1.0"
    mock_settings.SDK_VERSION = "1.0"
    mock_settings.DESCRIPTION = "Test"
    mock_settings.HEARTBEAT_INTERVAL_SECONDS = 3600  # long enough to not fire

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VCAP_APPLICATION", None)

    mock_settings.AGENT_ADVERTISED_HOST = "localhost"
    mock_settings.SERVER_PORT = 8000

    container = {
        "_dependencies": {
            "agent_registry": registry,
            "settings": mock_settings,
        }
    }
    app = create_agent_app(container)

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200

    registry.deregister.assert_called_once_with("agent-xyz")


@pytest.mark.asyncio
async def test_lifespan_registration_fails():
    from fastapi.testclient import TestClient

    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    registry = AsyncMock()
    registry.register = AsyncMock(side_effect=RuntimeError("registry down"))
    registry.close = AsyncMock()

    mock_settings = MagicMock()
    mock_settings.AGENT_ADVERTISED_URL = ""
    mock_settings.AGENT_ADVERTISE_URL = ""
    mock_settings.AGENT_ADVERTISED_SCHEME = ""
    mock_settings.AGENT_ADVERTISE_SCHEME = ""
    mock_settings.AGENT_ADVERTISED_PORT = ""
    mock_settings.AGENT_ADVERTISE_PORT = ""
    mock_settings.CAPABILITIES = []
    mock_settings.AGENT_DOMAIN = "chat"
    mock_settings.AGENT_TYPE = "test-agent"
    mock_settings.AGENT_VERSION = "1.0"
    mock_settings.SDK_VERSION = "1.0"
    mock_settings.DESCRIPTION = "Test"
    mock_settings.HEARTBEAT_INTERVAL_SECONDS = 3600
    mock_settings.AGENT_ADVERTISED_HOST = "localhost"
    mock_settings.SERVER_PORT = 8000

    container = {
        "_dependencies": {
            "agent_registry": registry,
            "settings": mock_settings,
        }
    }
    app = create_agent_app(container)
    # Should not raise even if registration fails
    with TestClient(app) as client:
        resp = client.get("/ready")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# bootstrap.py — scan_and_load_features
# ---------------------------------------------------------------------------


def test_scan_and_load_features():
    from agent_sdk.bootstrap import scan_and_load_features

    features = scan_and_load_features()
    assert isinstance(features, dict)


def test_scan_and_load_features_custom_path():
    import tempfile

    from agent_sdk.bootstrap import scan_and_load_features

    with tempfile.TemporaryDirectory() as tmpdir:
        features = scan_and_load_features(
            features_path=tmpdir, base_module="agent_sdk.test"
        )
    assert isinstance(features, dict)


# ---------------------------------------------------------------------------
# http_agent_registry.py — resolve_agent_id coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_resolve_agent_id_success():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={"agents": [{"id": "boost-id", "name": "boost-agent"}]}
    )
    mock_client.get = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    result = await registry.resolve_agent_id("boost-agent")
    assert result == "boost-id"


@pytest.mark.asyncio
async def test_coverage_resolve_agent_id_returns_none_on_empty():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"agents": []})
    mock_client.get = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    result = await registry.resolve_agent_id("no-agent")
    assert result is None


@pytest.mark.asyncio
async def test_coverage_resolve_agent_id_returns_none_on_exception():
    import httpx

    from agent_sdk.layer1_domain.exceptions import RegistrationError
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("down"))
    registry._client = mock_client

    with pytest.raises(RegistrationError, match="down"):
        await registry.resolve_agent_id("unreachable")
