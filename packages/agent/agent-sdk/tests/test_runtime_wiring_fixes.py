"""
Tests for runtime wiring gap.
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import ModuleType

import pytest

from tests.helpers.testing import StubLogger as _StubLogger

# ---------------------------------------------------------------------------
# build_app_container auto-injects WorkflowEventEmitter
# ---------------------------------------------------------------------------


def test_build_app_container_auto_injects_workflow_event_emitter():
    """build_app_container must auto-create a WorkflowEventEmitter and inject it."""
    from agent_sdk.bootstrap import build_app_container
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    container = build_app_container()
    deps = container["_dependencies"]
    assert (
        "workflow_event_emitter" in deps
    ), "workflow_event_emitter must be auto-wired into _dependencies"
    assert isinstance(deps["workflow_event_emitter"], WorkflowEventEmitter)


def test_execute_agent_use_case_receives_workflow_event_emitter():
    """ExecuteAgentUseCase must have _event_emitter set after build_app_container."""
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    execute_uc = container["execute_agent"]
    assert (
        execute_uc._event_emitter is not None
    ), "ExecuteAgentUseCase._event_emitter must be set when workflow_event_emitter is auto-wired"


def test_resume_agent_use_case_receives_workflow_event_emitter():
    """ResumeAgentUseCase must have _event_emitter set when agent_graph is provided."""
    from agent_sdk.bootstrap import build_app_container

    class _MockGraph:
        async def ainvoke(self, state, config=None):
            return {"message": "ok"}

    container = build_app_container(agent_graph=_MockGraph())
    resume_uc = container.get("resume_agent")
    assert (
        resume_uc is not None
    ), "resume_agent must be present when agent_graph provided"
    assert (
        resume_uc._event_emitter is not None
    ), "ResumeAgentUseCase._event_emitter must be set when workflow_event_emitter is auto-wired"


def test_container_respects_injected_workflow_event_emitter():
    """extra_dependencies['workflow_event_emitter'] must take precedence over auto-created one."""
    from agent_sdk.bootstrap import build_app_container

    class _StubEmitter:
        async def emit(self, event, **kwargs):
            pass

    stub = _StubEmitter()
    container = build_app_container(extra_dependencies={"workflow_event_emitter": stub})
    deps = container["_dependencies"]
    assert deps["workflow_event_emitter"] is stub


# ---------------------------------------------------------------------------
# build_app_container must pass broker to create_messaging for local/sap
# ---------------------------------------------------------------------------


def test_build_app_container_kafka_mode_passes_broker_to_messaging(monkeypatch):
    """In MESSAGING_MODE=local, build_app_container must create KafkaBrokerClient
    and pass it to create_messaging, so the result is Kafka (not mock) backend."""
    import unittest.mock as mock

    from agent_sdk.bootstrap import _build_broker
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    class _FakeKafkaBroker:
        pass

    fake_broker = _FakeKafkaBroker()

    class _LocalSettings(Settings):
        MESSAGING_MODE: str = "local"
        KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
        KAFKA_GROUP_ID: str = "agent-sdk-consumer"

    with mock.patch(
        "agent_sdk.layer4_frameworks.messaging.kafka_broker_client.KafkaBrokerClient",
        return_value=fake_broker,
    ):
        result = _build_broker(_LocalSettings(MESSAGING_MODE="local"))

    assert (
        result is fake_broker
    ), "_build_broker must return a KafkaBrokerClient for MESSAGING_MODE=local"


def test_build_app_container_sap_mode_passes_event_mesh_broker(monkeypatch):
    """In MESSAGING_MODE=sap, build_app_container must create EventMeshBrokerClient
    and pass it to create_messaging."""
    import unittest.mock as mock

    from agent_sdk.bootstrap import _build_broker
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    class _FakeEventMeshBroker:
        pass

    fake_broker = _FakeEventMeshBroker()

    class _SapSettings(Settings):
        MESSAGING_MODE: str = "sap"
        EVENT_MESH_TOKEN_URL: str = "https://token.example.com"
        EVENT_MESH_MESSAGING_URL: str = "https://messaging.example.com"
        EVENT_MESH_BROKER_URL: str = "https://broker.example.com"
        EVENT_MESH_MANAGEMENT_URL: str = "https://management.example.com"
        EVENT_MESH_CLIENT_ID: str = "test-client"
        EVENT_MESH_CLIENT_SECRET: str = "test-secret"
        EVENT_MESH_NAMESPACE: str = "event-mesh-ns"

    with mock.patch(
        "agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client.EventMeshBrokerClient",
        return_value=fake_broker,
    ) as broker_cls:
        result = _build_broker(_SapSettings(MESSAGING_MODE="sap"))

    assert (
        result is fake_broker
    ), "_build_broker must return an EventMeshBrokerClient for MESSAGING_MODE=sap"
    broker_cls.assert_called_once_with(
        token_url="https://token.example.com",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-client",
        client_secret="test-secret",
        namespace="event-mesh-ns",
        verify_ssl=True,
        request_timeout_seconds=30.0,
        poll_interval_seconds=1.0,
        max_in_flight_messages=4,
        shutdown_grace_seconds=30,
    )


def test_build_app_container_sap_mode_falls_back_to_event_mesh_broker_url(monkeypatch):
    """When dedicated Event Mesh URLs are blank, bootstrap must fall back to EVENT_MESH_BROKER_URL."""
    import unittest.mock as mock

    from agent_sdk.bootstrap import _build_broker
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    class _FakeEventMeshBroker:
        pass

    fake_broker = _FakeEventMeshBroker()

    class _SapSettings(Settings):
        MESSAGING_MODE: str = "sap"
        EVENT_MESH_TOKEN_URL: str = "https://token.example.com"
        EVENT_MESH_MESSAGING_URL: str = ""
        EVENT_MESH_BROKER_URL: str = "https://broker.example.com"
        EVENT_MESH_MANAGEMENT_URL: str = ""
        EVENT_MESH_CLIENT_ID: str = "test-client"
        EVENT_MESH_CLIENT_SECRET: str = "test-secret"

    with mock.patch(
        "agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client.EventMeshBrokerClient",
        return_value=fake_broker,
    ) as broker_cls:
        result = _build_broker(_SapSettings(MESSAGING_MODE="sap"))

    assert (
        result is fake_broker
    ), "_build_broker must return an EventMeshBrokerClient for MESSAGING_MODE=sap"
    broker_cls.assert_called_once_with(
        token_url="https://token.example.com",
        messaging_url="https://broker.example.com",
        management_url="https://broker.example.com",
        client_id="test-client",
        client_secret="test-secret",
        namespace="default",
        verify_ssl=True,
        request_timeout_seconds=30.0,
        poll_interval_seconds=1.0,
        max_in_flight_messages=4,
        shutdown_grace_seconds=30,
    )


def test_build_app_container_kafka_mode_passes_reject_topic_setting():
    import unittest.mock as mock

    from agent_sdk.bootstrap import _build_broker
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    class _LocalSettings(Settings):
        MESSAGING_MODE: str = "local"
        KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
        KAFKA_GROUP_ID: str = "agent-sdk-consumer"
        KAFKA_REJECT_TOPIC: str = "agent.request.dead-letter"

    with mock.patch(
        "agent_sdk.layer4_frameworks.messaging.kafka_broker_client.KafkaBrokerClient"
    ) as broker_cls:
        _build_broker(_LocalSettings(MESSAGING_MODE="local"))

    broker_cls.assert_called_once_with(
        bootstrap_servers="localhost:9092",
        group_id="agent-sdk-consumer",
        kafka_reject_topic="agent.request.dead-letter",
        max_in_flight_messages=4,
        shutdown_grace_seconds=30,
    )


def test_build_broker_filters_sdk_only_kafka_kwargs(monkeypatch):
    from agent_sdk.bootstrap import _build_broker
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    captured: dict[str, dict[str, object]] = {}

    class _LocalSettings(Settings):
        MESSAGING_MODE: str = "local"
        KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
        KAFKA_GROUP_ID: str = "agent-sdk-consumer"
        KAFKA_REJECT_TOPIC: str = "agent.request.dead-letter"

    class _Producer:
        def __init__(self, conf):
            captured["producer"] = dict(conf)

        def poll(self, timeout):
            return None

        def produce(self, *args, **kwargs):
            return None

        def flush(self, timeout=None):
            return 0

    confluent_kafka = ModuleType("confluent_kafka")
    confluent_kafka.Producer = _Producer
    monkeypatch.setitem(sys.modules, "confluent_kafka", confluent_kafka)

    broker = _build_broker(_LocalSettings(MESSAGING_MODE="local"))

    assert broker._kafka_reject_topic == "agent.request.dead-letter"
    assert "kafka_reject_topic" not in captured["producer"]
    assert "kafka_reject_topic" not in broker._consumer_conf


def test_build_app_container_raises_when_kafka_broker_init_fails():
    import unittest.mock as mock

    from agent_sdk.bootstrap import _build_broker
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    class _LocalSettings(Settings):
        MESSAGING_MODE: str = "local"
        KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
        KAFKA_GROUP_ID: str = "agent-sdk-consumer"

    with mock.patch(
        "agent_sdk.layer4_frameworks.messaging.kafka_broker_client.KafkaBrokerClient",
        side_effect=RuntimeError("connection refused"),
    ):
        with pytest.raises(
            RuntimeError,
            match="Failed to initialize KafkaBrokerClient for messaging mode 'local'",
        ):
            _build_broker(_LocalSettings(MESSAGING_MODE="local"))


def test_build_app_container_raises_when_event_mesh_broker_init_fails():
    import unittest.mock as mock

    from agent_sdk.bootstrap import _build_broker
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    class _SapSettings(Settings):
        MESSAGING_MODE: str = "sap"
        EVENT_MESH_TOKEN_URL: str = "https://token.example.com"
        EVENT_MESH_MESSAGING_URL: str = "https://messaging.example.com"
        EVENT_MESH_MANAGEMENT_URL: str = "https://management.example.com"
        EVENT_MESH_CLIENT_ID: str = "test-client"
        EVENT_MESH_CLIENT_SECRET: str = "test-secret"

    with mock.patch(
        "agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client.EventMeshBrokerClient",
        side_effect=RuntimeError("connection refused"),
    ):
        with pytest.raises(
            RuntimeError,
            match="Failed to initialize Event Mesh broker client for messaging mode 'sap'",
        ):
            _build_broker(_SapSettings(MESSAGING_MODE="sap"))


def test_build_app_container_mock_mode_uses_no_broker():
    """In MESSAGING_MODE=mock, broker should be None (no external connection)."""
    import unittest.mock as mock

    captured_broker = {}

    def _fake_create_messaging(settings, logger, *, broker=None):
        captured_broker["broker"] = broker
        from agent_sdk.layer4_frameworks.messaging.factory import _build_mock_messaging

        return _build_mock_messaging(logger)

    with mock.patch(
        "agent_sdk.layer4_frameworks.messaging.factory.create_messaging",
        side_effect=_fake_create_messaging,
    ):
        os.environ.pop("MESSAGING_MODE", None)
        from agent_sdk.bootstrap import build_app_container
        from agent_sdk.layer4_frameworks.config.app_config import Settings

        mock_settings = Settings()
        mock_settings.MESSAGING_MODE = "mock"
        build_app_container()

    assert (
        captured_broker.get("broker") is None
    ), "broker must be None for MESSAGING_MODE=mock"


# ---------------------------------------------------------------------------
# EVENT_MESH_REQUEST_TOPIC setting and factory usage
# ---------------------------------------------------------------------------


def test_settings_has_event_mesh_request_topic():
    """Settings must expose EVENT_MESH_REQUEST_TOPIC with a default value."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    s = Settings()
    assert hasattr(
        s, "EVENT_MESH_REQUEST_TOPIC"
    ), "Settings must have EVENT_MESH_REQUEST_TOPIC attribute"
    assert isinstance(s.EVENT_MESH_REQUEST_TOPIC, str)
    assert (
        s.EVENT_MESH_REQUEST_TOPIC != ""
    ), "EVENT_MESH_REQUEST_TOPIC must have a non-empty default"


def test_settings_has_event_mesh_messaging_url():
    """Settings must expose EVENT_MESH_MESSAGING_URL with a default value."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    s = Settings()
    assert hasattr(
        s, "EVENT_MESH_MESSAGING_URL"
    ), "Settings must have EVENT_MESH_MESSAGING_URL attribute"
    assert isinstance(s.EVENT_MESH_MESSAGING_URL, str)
    assert (
        s.EVENT_MESH_MESSAGING_URL != ""
    ), "EVENT_MESH_MESSAGING_URL must have a non-empty default"


def test_settings_has_event_mesh_management_url():
    """Settings must expose EVENT_MESH_MANAGEMENT_URL with a default value."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    s = Settings()
    assert hasattr(
        s, "EVENT_MESH_MANAGEMENT_URL"
    ), "Settings must have EVENT_MESH_MANAGEMENT_URL attribute"
    assert isinstance(s.EVENT_MESH_MANAGEMENT_URL, str)
    assert (
        s.EVENT_MESH_MANAGEMENT_URL != ""
    ), "EVENT_MESH_MANAGEMENT_URL must have a non-empty default"


def test_factory_sap_mode_uses_event_mesh_request_topic():
    """create_messaging in sap mode must use EVENT_MESH_REQUEST_TOPIC, not KAFKA_REQUEST_TOPIC."""
    from agent_sdk.layer4_frameworks.messaging.factory import create_messaging

    subscribed_topics = []

    class _FakeBroker:
        def publish_to_topic(self, topic, message, key=None):
            pass

        def subscribe_to_topic(self, topic, callback):
            subscribed_topics.append(topic)

    class _Settings:
        MESSAGING_MODE = "sap"
        INFRA_MODE = "mock"
        KAFKA_REQUEST_TOPIC = "kafka.request"
        EVENT_MESH_REQUEST_TOPIC = "event-mesh.request"

    fake_broker = _FakeBroker()
    result = create_messaging(_Settings(), _StubLogger(), broker=fake_broker)

    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    assert isinstance(result.consumer, EventMeshMessageConsumer)

    asyncio.run(result.consumer.start(lambda msg: None))
    assert len(subscribed_topics) == 1
    assert (
        subscribed_topics[0] == "event-mesh.request"
    ), f"Event Mesh consumer must subscribe to EVENT_MESH_REQUEST_TOPIC, got: {subscribed_topics}"


# ---------------------------------------------------------------------------
# Sync node wrappers must not auto-emit lifecycle events
# ---------------------------------------------------------------------------


def test_sync_node_emission_does_not_block_with_done_wait():
    """AgentGraphBuilder source must not contain legacy blocking done.wait()."""
    import inspect

    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    builder_source = inspect.getsource(AgentGraphBuilder)
    assert (
        "done.wait" not in builder_source
    ), "graph_builder must not use blocking done.wait() in sync node wrappers"


def test_flow_sync_node_emission_does_not_block_with_done_wait():
    """FlowGraphBuilder source must not contain legacy blocking done.wait()."""
    import inspect

    from agent_sdk.layer4_frameworks.graph.flow_graph_builder import (
        FlowGraphBuilder,
    )

    builder_source = inspect.getsource(FlowGraphBuilder)
    assert (
        "done.wait" not in builder_source
    ), "flow_graph_builder must not use blocking done.wait() in sync node wrappers"


def test_sync_node_wrapper_uses_safe_fire_and_forget():
    """AgentGraphBuilder must not retain sync fire-and-forget event scheduling code."""
    import inspect

    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder

    builder_source = inspect.getsource(AgentGraphBuilder)
    assert "ensure_future" not in builder_source
    assert "call_soon_threadsafe" not in builder_source
    assert "create_task" not in builder_source


# ---------------------------------------------------------------------------
# push_gateway_notifier wiring in build_app_container
# ---------------------------------------------------------------------------


def test_build_app_container_seeds_push_gateway_notifier():
    """build_app_container() must auto-create and seed push_gateway_notifier into _dependencies."""
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    deps = container["_dependencies"]
    assert (
        "push_gateway_notifier" in deps
    ), "push_gateway_notifier must be auto-wired into _dependencies"


def test_build_app_container_injected_push_gateway_notifier_takes_precedence():
    """extra_dependencies['push_gateway_notifier'] must override the auto-created notifier."""
    from agent_sdk.bootstrap import build_app_container

    class _StubNotifier:
        async def send_notification(self, key, data, *, is_final=True):
            pass

    stub = _StubNotifier()
    container = build_app_container(extra_dependencies={"push_gateway_notifier": stub})
    deps = container["_dependencies"]
    assert deps["push_gateway_notifier"] is stub


def test_build_app_container_workflow_event_emitter_receives_push_notifier():
    """The auto-created WorkflowEventEmitter must have the push_gateway_notifier wired in."""
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    deps = container["_dependencies"]
    emitter = deps["workflow_event_emitter"]
    notifier = deps["push_gateway_notifier"]
    assert emitter._push_gateway_notifier is notifier, (
        "WorkflowEventEmitter._push_gateway_notifier must be the same object as "
        "_dependencies['push_gateway_notifier']"
    )


def test_build_app_container_preserves_injected_notifier_on_emitter():
    """Injected notifier must flow unchanged into _dependencies and WorkflowEventEmitter."""
    from agent_sdk.bootstrap import build_app_container

    class _StubNotifier:
        async def send_notification(self, key, data, *, is_final=True):
            pass

        async def close(self):
            pass

    stub = _StubNotifier()
    container = build_app_container(extra_dependencies={"push_gateway_notifier": stub})
    deps = container["_dependencies"]

    assert deps["push_gateway_notifier"] is stub
    assert deps["workflow_event_emitter"]._push_gateway_notifier is stub
