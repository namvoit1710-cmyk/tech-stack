from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_lifespan_builds_routing_metadata_from_settings():
    """_build_routing_metadata should build routing metadata dict from Settings fields."""
    from agent_sdk.layer3_adapters.presenters.agent_server import (
        _build_routing_metadata,
    )

    mock_settings = MagicMock()
    mock_settings.INPUT_SCHEMA = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }
    mock_settings.OUTPUT_SCHEMA = {"type": "object"}
    mock_settings.REQUIRED_PARAMETERS = ["x"]
    mock_settings.NEGATIVE_EXAMPLES = ["don't call for Y"]
    mock_settings.ROUTING_TIMEOUT_SECONDS = 60.0

    routing = _build_routing_metadata(mock_settings)

    assert routing["input_schema"]["properties"]["x"]["type"] == "string"
    assert routing["output_schema"] == {"type": "object"}
    assert routing["required_parameters"] == ["x"]
    assert routing["negative_examples"] == ["don't call for Y"]
    assert routing["timeout_seconds"] == 60.0


@pytest.mark.asyncio
async def test_lifespan_omits_routing_when_defaults():
    """When all routing fields are defaults, _build_routing_metadata returns empty dict."""
    from agent_sdk.layer3_adapters.presenters.agent_server import (
        _build_routing_metadata,
    )

    mock_settings = MagicMock()
    mock_settings.INPUT_SCHEMA = {}
    mock_settings.OUTPUT_SCHEMA = {}
    mock_settings.REQUIRED_PARAMETERS = []
    mock_settings.NEGATIVE_EXAMPLES = []
    mock_settings.ROUTING_TIMEOUT_SECONDS = 300.0

    routing = _build_routing_metadata(mock_settings)

    assert routing == {}


@pytest.mark.asyncio
async def test_lifespan_builds_partial_routing_metadata():
    """When only some routing fields are set, only those keys appear in the result."""
    from agent_sdk.layer3_adapters.presenters.agent_server import (
        _build_routing_metadata,
    )

    mock_settings = MagicMock()
    mock_settings.INPUT_SCHEMA = {
        "type": "object",
        "properties": {"file_id": {"type": "string"}},
    }
    mock_settings.OUTPUT_SCHEMA = {}
    mock_settings.REQUIRED_PARAMETERS = ["file_id"]
    mock_settings.NEGATIVE_EXAMPLES = []
    mock_settings.ROUTING_TIMEOUT_SECONDS = 300.0

    routing = _build_routing_metadata(mock_settings)

    assert "input_schema" in routing
    assert routing["input_schema"]["properties"]["file_id"]["type"] == "string"
    assert "required_parameters" in routing
    assert routing["required_parameters"] == ["file_id"]
    # Fields left at defaults should be absent
    assert "output_schema" not in routing
    assert "negative_examples" not in routing
    assert "timeout_seconds" not in routing


def test_build_agent_registration_preserves_routing_and_registered_tool_ids():
    from agent_sdk.layer3_adapters.presenters.agent_ops import build_agent_registration

    mock_settings = MagicMock()
    mock_settings.AGENT_ADVERTISED_URL = ""
    mock_settings.AGENT_ADVERTISE_URL = ""
    mock_settings.AGENT_ADVERTISED_SCHEME = ""
    mock_settings.AGENT_ADVERTISE_SCHEME = ""
    mock_settings.AGENT_ADVERTISED_PORT = ""
    mock_settings.AGENT_ADVERTISE_PORT = ""
    mock_settings.AGENT_TYPE = "planner"
    mock_settings.AGENT_VERSION = "1.0.0"
    mock_settings.SDK_VERSION = "1.0.0"
    mock_settings.AGENT_DOMAIN = "workflow"
    mock_settings.AGENT_ADVERTISED_HOST = "planner.local"
    mock_settings.SERVER_PORT = 8000
    mock_settings.CAPABILITIES = ["plan"]
    mock_settings.DESCRIPTION = "Planner"
    mock_settings.INPUT_SCHEMA = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }
    mock_settings.OUTPUT_SCHEMA = {}
    mock_settings.REQUIRED_PARAMETERS = ["x"]
    mock_settings.NEGATIVE_EXAMPLES = []
    mock_settings.ROUTING_TIMEOUT_SECONDS = 300.0
    mock_settings.REQUIRED_CONFIRMATION = True
    mock_settings.AGENT_KIND = "SUPERVISOR"
    mock_settings.IS_PUBLISHED = False
    mock_settings.ATTACHED_AGENT_IDS = ["worker-a"]
    mock_settings.SYSTEM_PROMPT = "Plan work"
    mock_settings.MAX_CONCURRENCY = 2
    mock_settings.LLM_MODEL = None
    mock_settings.LLM_PROVIDER = None
    mock_settings.LLM_TEMPERATURE = None
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = None
    mock_settings.QUEUE_NAME = "planner.queue"
    mock_settings.QUEUE_REQUEST_TOPIC = "planner.request"
    mock_settings.QUEUE_REPLY_TOPIC = "planner.reply"
    mock_settings.QUEUE_DELIVERY_HINTS = {"priority": "high"}
    mock_settings.SUPPORTS_STREAMING = True
    mock_settings.SUPPORTS_HUMAN_IN_THE_LOOP = True

    registration = build_agent_registration(
        mock_settings,
        registered_tool_ids={"search": "tool-1", "lookup": "tool-2"},
    )

    assert registration.kind == "technical"
    assert registration.tool_ids == ["tool-1", "tool-2"]
    assert (
        registration.metadata["routing"]["input_schema"]["properties"]["x"]["type"]
        == "string"
    )
    assert registration.metadata["sdk_kind"] == "SUPERVISOR"


def test_build_routing_metadata_includes_required_confirmation_when_disabled():
    from agent_sdk.layer3_adapters.presenters.agent_server import (
        _build_routing_metadata,
    )

    mock_settings = MagicMock()
    mock_settings.INPUT_SCHEMA = {}
    mock_settings.OUTPUT_SCHEMA = {}
    mock_settings.REQUIRED_PARAMETERS = []
    mock_settings.NEGATIVE_EXAMPLES = []
    mock_settings.ROUTING_TIMEOUT_SECONDS = 300.0
    mock_settings.REQUIRED_CONFIRMATION = False

    routing = _build_routing_metadata(mock_settings)

    assert routing["required_confirmation"] is False


def test_build_registration_metadata_captures_registry_contract_fields():
    from agent_sdk.layer3_adapters.presenters.agent_server import (
        _build_agent_registration,
    )

    mock_settings = MagicMock()
    mock_settings.AGENT_ADVERTISED_URL = ""
    mock_settings.AGENT_ADVERTISE_URL = ""
    mock_settings.AGENT_ADVERTISED_SCHEME = ""
    mock_settings.AGENT_ADVERTISE_SCHEME = ""
    mock_settings.AGENT_ADVERTISED_PORT = ""
    mock_settings.AGENT_ADVERTISE_PORT = ""
    mock_settings.AGENT_TYPE = "planner"
    mock_settings.AGENT_VERSION = "1.0.0"
    mock_settings.SDK_VERSION = "1.0.0"
    mock_settings.AGENT_DOMAIN = "workflow"
    mock_settings.AGENT_ADVERTISED_HOST = "planner.local"
    mock_settings.SERVER_PORT = 8000
    mock_settings.CAPABILITIES = ["plan"]
    mock_settings.DESCRIPTION = "Planner"
    mock_settings.INPUT_SCHEMA = {}
    mock_settings.OUTPUT_SCHEMA = {}
    mock_settings.REQUIRED_PARAMETERS = []
    mock_settings.NEGATIVE_EXAMPLES = []
    mock_settings.ROUTING_TIMEOUT_SECONDS = 300.0
    mock_settings.REQUIRED_CONFIRMATION = True
    mock_settings.AGENT_KIND = "SUPERVISOR"
    mock_settings.IS_PUBLISHED = False
    mock_settings.ATTACHED_AGENT_IDS = ["worker-a"]
    mock_settings.SYSTEM_PROMPT = "Plan work"
    mock_settings.MAX_CONCURRENCY = 2
    mock_settings.LLM_MODEL = "gpt-4o-mini"
    mock_settings.LLM_PROVIDER = "openai"
    mock_settings.LLM_TEMPERATURE = 0.2
    mock_settings.LLM_TIMEOUT = 30.0
    mock_settings.LLM_MAX_TOKENS = 1024
    mock_settings.LLM_MAX_RETRIES = 3
    mock_settings.QUEUE_NAME = "planner.queue"
    mock_settings.QUEUE_REQUEST_TOPIC = "planner.request"
    mock_settings.QUEUE_REPLY_TOPIC = "planner.reply"
    mock_settings.QUEUE_DELIVERY_HINTS = {"priority": "high"}
    mock_settings.SUPPORTS_STREAMING = True
    mock_settings.SUPPORTS_HUMAN_IN_THE_LOOP = True

    registration = _build_agent_registration(
        mock_settings,
        registered_tool_ids={"search": "tool-1"},
    )

    assert registration.kind == "technical"
    assert registration.is_published is False
    assert registration.attached_agent_ids == ["worker-a"]
    assert registration.tool_ids == ["tool-1"]
    assert registration.agent_runtime_config.system_prompt == "Plan work"
    assert registration.execution_policy.supports_streaming is True
    assert registration.queue_metadata.queue_name == "planner.queue"
    assert registration.metadata["sdk_kind"] == "SUPERVISOR"


def test_build_queue_metadata_derives_from_event_mesh_when_queue_vars_empty():
    from unittest.mock import MagicMock

    from agent_sdk.layer3_adapters.presenters.agent_ops import build_queue_metadata

    mock_settings = MagicMock()
    mock_settings.QUEUE_NAME = ""
    mock_settings.QUEUE_REQUEST_TOPIC = ""
    mock_settings.QUEUE_REPLY_TOPIC = ""
    mock_settings.QUEUE_DELIVERY_HINTS = {}
    mock_settings.APP_MODE = "CONSUMER"
    mock_settings.MESSAGING_MODE = "sap"
    mock_settings.INFRA_MODE = ""
    mock_settings.EVENT_MESH_REQUEST_TOPIC = "agent.request"
    mock_settings.EVENT_MESH_REPLY_TOPIC = ""
    mock_settings.EVENT_MESH_NAMESPACE = "myns"

    result = build_queue_metadata(mock_settings)

    assert result is not None
    assert result.request_topic == "agent.request"
    assert result.queue_name == "myns/agent.request"
    assert result.reply_topic == "agent.request"


def test_build_queue_metadata_sap_mode_case_insensitive():
    from unittest.mock import MagicMock

    from agent_sdk.layer3_adapters.presenters.agent_ops import build_queue_metadata

    mock_settings = MagicMock()
    mock_settings.QUEUE_NAME = ""
    mock_settings.QUEUE_REQUEST_TOPIC = ""
    mock_settings.QUEUE_REPLY_TOPIC = ""
    mock_settings.QUEUE_DELIVERY_HINTS = {}
    mock_settings.APP_MODE = "CONSUMER"
    mock_settings.MESSAGING_MODE = "SAP"
    mock_settings.INFRA_MODE = ""
    mock_settings.EVENT_MESH_REQUEST_TOPIC = "my.topic"
    mock_settings.EVENT_MESH_REPLY_TOPIC = "my.reply"
    mock_settings.EVENT_MESH_NAMESPACE = "ns"

    result = build_queue_metadata(mock_settings)

    assert result is not None
    assert result.request_topic == "my.topic"
    assert result.reply_topic == "my.reply"
    assert result.queue_name == "ns/my.topic"


def test_build_queue_metadata_derives_from_kafka_when_queue_vars_empty():
    from unittest.mock import MagicMock

    from agent_sdk.layer3_adapters.presenters.agent_ops import build_queue_metadata

    mock_settings = MagicMock()
    mock_settings.QUEUE_NAME = ""
    mock_settings.QUEUE_REQUEST_TOPIC = ""
    mock_settings.QUEUE_REPLY_TOPIC = ""
    mock_settings.QUEUE_DELIVERY_HINTS = {}
    mock_settings.APP_MODE = "CONSUMER"
    mock_settings.MESSAGING_MODE = "local"
    mock_settings.INFRA_MODE = ""
    mock_settings.KAFKA_REQUEST_TOPIC = "agent.request"
    mock_settings.KAFKA_RESPONSE_TOPIC = "agent.responses"

    result = build_queue_metadata(mock_settings)

    assert result is not None
    assert result.request_topic == "agent.request"
    assert result.reply_topic == "agent.responses"


def test_build_queue_metadata_returns_none_for_server_mode_with_no_queue_vars():
    from unittest.mock import MagicMock

    from agent_sdk.layer3_adapters.presenters.agent_ops import build_queue_metadata

    mock_settings = MagicMock()
    mock_settings.QUEUE_NAME = ""
    mock_settings.QUEUE_REQUEST_TOPIC = ""
    mock_settings.QUEUE_REPLY_TOPIC = ""
    mock_settings.QUEUE_DELIVERY_HINTS = {}
    mock_settings.APP_MODE = "SERVER"

    result = build_queue_metadata(mock_settings)

    assert result is None
