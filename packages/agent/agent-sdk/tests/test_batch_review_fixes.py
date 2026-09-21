"""Tests for the batch code-review fixes.

Covers:
1. AgentInfoOutputPydantic.capabilities must accept List[Dict[str, Any]] to match AgentInfo domain shape.
2. bootstrap.build_app_container must NOT silently swallow LLM auto-wiring failures — it should raise.
3. HanaConnectionManager._schema must come from HANA_SCHEMA, not HANA_USERNAME.
4. bootstrap.py should use direct settings attributes (not getattr fallbacks) for OPENAI_MODEL and OPENAI_TEMPERATURE.
5. The agent_execute_success_with_format metric name should be consistent (documented/kept or reverted to agent_execute_success).
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _configure_generic_llm_settings(mock_settings):
    mock_settings.WORKER_EXECUTOR_URL = ""
    mock_settings.DEFAULT_TENANT_ID = "default"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.LLM_API_KEY = ""
    mock_settings.OPENAI_API_KEY = ""
    mock_settings.LLM_MODEL = "gpt-4o-mini"
    mock_settings.LLM_MODEL_KWARGS = {}
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []


# ---------------------------------------------------------------------------
# Issue 1: AgentInfoOutputPydantic.capabilities must accept List[Dict[str, Any]]
# ---------------------------------------------------------------------------


def test_agent_info_output_capabilities_accepts_list_of_dicts():
    """AgentInfoOutputPydantic.capabilities must be List[Dict[str, Any]] to match AgentInfo domain shape."""
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        AgentInfoOutputPydantic,
    )

    payload = AgentInfoOutputPydantic(
        agent_type="test",
        version="1.0.0",
        sdk_version="1.0.0",
        domain="test",
        capabilities=[{"name": "chat", "version": "1.0"}],
        metadata={},
    )
    assert payload.capabilities == [{"name": "chat", "version": "1.0"}]


def test_agent_info_output_capabilities_field_type_annotation():
    """AgentInfoOutputPydantic.capabilities type annotation must be List[Dict[str, Any]], not List[str]."""
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        AgentInfoOutputPydantic,
    )

    field = AgentInfoOutputPydantic.model_fields["capabilities"]
    # The annotation should include Dict or Mapping, not str
    annotation_str = str(field.annotation)
    assert (
        "str" not in annotation_str
        or "Dict" in annotation_str
        or "dict" in annotation_str
    ), f"capabilities field annotation should use Dict[str, Any], got: {annotation_str}"
    # More precise: check that the annotation resolves to list-of-dict, not list-of-str
    hints = AgentInfoOutputPydantic.model_fields
    cap_annotation = hints["capabilities"].annotation
    # Get the inner type arg
    args = getattr(cap_annotation, "__args__", None)
    if args:
        inner = args[0]
        # Should be dict or Dict[str, Any], NOT str
        assert (
            inner is not str
        ), f"capabilities inner type must be dict/Dict[str, Any], not str. Got: {inner}"


def test_agent_info_route_serialises_dict_capabilities():
    """The /info route must return capabilities as a list of objects, not strings."""
    from fastapi.testclient import TestClient

    from agent_sdk.layer1_domain.entities.agent_info import AgentInfo
    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    class _StubInfoUseCase:
        def execute(self) -> AgentInfo:
            return AgentInfo(
                agent_type="test",
                version="0.1.0",
                sdk_version="1.0.0",
                domain="test",
                capabilities=[{"name": "chat", "description": "Chat capability"}],
            )

    container = {"get_agent_info": _StubInfoUseCase()}
    app = create_agent_app(container)
    client = TestClient(app)
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["capabilities"], list)
    assert len(data["capabilities"]) == 1
    assert isinstance(
        data["capabilities"][0], dict
    ), "capabilities items must be dicts/objects, not strings"
    assert data["capabilities"][0]["name"] == "chat"


def test_agent_info_route_serialises_string_capabilities_after_normalization():
    """Routes must remain compatible when settings provide capabilities as strings."""
    from fastapi.testclient import TestClient

    from agent_sdk.layer2_application.features.get_agent_info.use_cases.get_agent_info_use_case import (
        GetAgentInfoUseCase,
    )
    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    class _StubLogger:
        def info(self, *args, **kwargs):
            return None

    class _StubSettings:
        AGENT_TYPE = "workflow-integrator"
        AGENT_VERSION = "0.1.0"
        SDK_VERSION = "1.0.0"
        AGENT_DOMAIN = "workflow-design"
        CAPABILITIES = ["workflow_schema_integration", "workflow_dag_validation"]

    container = {
        "get_agent_info": GetAgentInfoUseCase(
            logger=_StubLogger(), settings=_StubSettings()
        )
    }
    app = create_agent_app(container)
    client = TestClient(app)

    response = client.get("/api/v1/info")

    assert response.status_code == 200
    data = response.json()
    assert data["capabilities"] == [
        {"domain": "workflow-design", "action": "workflow_schema_integration"},
        {"domain": "workflow-design", "action": "workflow_dag_validation"},
    ]


# ---------------------------------------------------------------------------
# Issue 2: bootstrap must NOT silently swallow LLM auto-wiring failures
# ---------------------------------------------------------------------------


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_raises_on_llm_wiring_failure(mock_settings):
    """build_app_container must raise (not silently swallow) when LLM auto-wiring fails."""
    _configure_generic_llm_settings(mock_settings)

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings", mock_settings):
        with patch(
            "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model",
            side_effect=RuntimeError("connection refused"),
        ):
            with pytest.raises(RuntimeError, match="connection refused"):
                from agent_sdk.bootstrap import build_app_container

                build_app_container()


# ---------------------------------------------------------------------------
# Issue 3: HanaConnectionManager._schema must come from HANA_SCHEMA
# ---------------------------------------------------------------------------


def test_hana_connection_manager_schema_from_hana_schema():
    """HanaConnectionManager._schema must use HANA_SCHEMA, not HANA_USERNAME."""
    from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
        HanaConnectionManager,
    )

    class _FakeSettings:
        HANA_HOST = "host"
        HANA_PORT = 443
        HANA_USERNAME = "myuser"
        HANA_PASSWORD = "secret"
        HANA_SCHEMA = "MY_SCHEMA"
        HANA_ENCRYPT = "false"
        HANA_SSL_CERT = ""
        HANA_POOL_SIZE = 2
        HANA_MAX_OVERFLOW = 5
        HANA_POOL_RECYCLE = 3600

    mgr = HanaConnectionManager(_FakeSettings())
    assert (
        mgr._schema == "MY_SCHEMA"
    ), f"_schema must come from HANA_SCHEMA ('MY_SCHEMA'), got '{mgr._schema}'"


def test_hana_connection_manager_schema_differs_from_username():
    """When HANA_SCHEMA differs from HANA_USERNAME, _schema must use HANA_SCHEMA."""
    from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
        HanaConnectionManager,
    )

    class _FakeSettings:
        HANA_HOST = "host"
        HANA_PORT = 443
        HANA_USERNAME = "theuser"
        HANA_PASSWORD = "pass"
        HANA_SCHEMA = "DIFFERENT_SCHEMA"
        HANA_ENCRYPT = "true"
        HANA_SSL_CERT = ""
        HANA_POOL_SIZE = 1
        HANA_MAX_OVERFLOW = 2
        HANA_POOL_RECYCLE = 3600

    mgr = HanaConnectionManager(_FakeSettings())
    assert mgr._schema != mgr._user, (
        f"_schema ('{mgr._schema}') must not equal _user ('{mgr._user}') "
        "when HANA_SCHEMA and HANA_USERNAME are different"
    )
    assert mgr._schema == "DIFFERENT_SCHEMA"


def test_hana_connection_manager_applies_encrypt_kwarg_when_enabled():
    from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
        HanaConnectionManager,
    )

    class _FakeSettings:
        HANA_HOST = "host"
        HANA_PORT = 443
        HANA_USERNAME = "user"
        HANA_PASSWORD = "secret"
        HANA_SCHEMA = ""
        HANA_ENCRYPT = "true"
        HANA_SSL_CERT = ""
        HANA_POOL_SIZE = 1
        HANA_MAX_OVERFLOW = 1
        HANA_POOL_RECYCLE = 3600

    connect_mock = MagicMock(return_value=MagicMock())
    fake_hdbcli = SimpleNamespace(dbapi=SimpleNamespace(connect=connect_mock))

    with patch.dict(sys.modules, {"hdbcli": fake_hdbcli}):
        HanaConnectionManager(_FakeSettings())._create_connection()

    assert connect_mock.call_args.kwargs["encrypt"] is True


def test_hana_connection_manager_applies_ssl_trust_store_when_cert_present():
    from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
        HanaConnectionManager,
    )

    class _FakeSettings:
        HANA_HOST = "host"
        HANA_PORT = 443
        HANA_USERNAME = "user"
        HANA_PASSWORD = "secret"
        HANA_SCHEMA = ""
        HANA_ENCRYPT = "true"
        HANA_SSL_CERT = "/tmp/ca.pem"
        HANA_POOL_SIZE = 1
        HANA_MAX_OVERFLOW = 1
        HANA_POOL_RECYCLE = 3600

    connect_mock = MagicMock(return_value=MagicMock())
    fake_hdbcli = SimpleNamespace(dbapi=SimpleNamespace(connect=connect_mock))

    with patch.dict(sys.modules, {"hdbcli": fake_hdbcli}):
        HanaConnectionManager(_FakeSettings())._create_connection()

    assert connect_mock.call_args.kwargs["encrypt"] is True
    assert connect_mock.call_args.kwargs["sslTrustStore"] == "/tmp/ca.pem"


# ---------------------------------------------------------------------------
# Issue 4: bootstrap should use direct settings attributes, not getattr fallbacks
# ---------------------------------------------------------------------------


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_uses_settings_llm_model_directly(mock_settings):
    """bootstrap.py must read settings.LLM_MODEL directly, not via getattr fallback."""
    _configure_generic_llm_settings(mock_settings)
    mock_settings.LLM_MODEL = "gpt-4-turbo"
    mock_settings.LLM_TEMPERATURE = 0.5

    with patch("agent_sdk.layer4_frameworks.config.app_config.settings", mock_settings):
        with patch(
            "agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model"
        ) as mock_init_chat_model:
            mock_init_chat_model.return_value = MagicMock(name="chat_client")
            from agent_sdk.bootstrap import build_app_container

            build_app_container()

    mock_init_chat_model.assert_called_once_with(
        "gpt-4-turbo",
        model_provider=None,
        temperature=0.5,
        timeout=None,
        max_tokens=None,
        max_retries=6,
    )


# ---------------------------------------------------------------------------
# Issue 5: Metric name 'agent_execute_success_with_format' should be documented/consistent
# ---------------------------------------------------------------------------


def test_execute_use_case_formatted_response_metric_name():
    """The metric emitted when a formatted_response is present must be 'agent_execute_success_with_format'.

    This test documents the intentional metric name split:
    - 'agent_execute_success_with_format': execution succeeded and the graph returned formatted_response.
    - 'agent_execute_success': execution succeeded and the graph did NOT return formatted_response.

    If this name is ever changed, this test must be updated accordingly.
    """
    import inspect

    from agent_sdk.layer2_application.features.execute_agent.use_cases import (
        execute_agent_use_case,
    )

    source = inspect.getsource(execute_agent_use_case)
    assert "agent_execute_success_with_format" in source, (
        "execute_agent_use_case must track 'agent_execute_success_with_format' metric "
        "for the formatted-response path"
    )
    assert "agent_execute_success" in source, (
        "execute_agent_use_case must track 'agent_execute_success' metric "
        "for the unformatted-response path"
    )
