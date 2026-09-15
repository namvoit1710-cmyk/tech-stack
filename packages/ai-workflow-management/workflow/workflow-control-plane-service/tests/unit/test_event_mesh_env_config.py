"""Tests for env-first Event Mesh configuration and primary-broker selection.

A complete EVENT_MESH_* environment set:
1. overrides any VCAP_SERVICES Event Mesh binding (credential precedence), and
2. selects Event Mesh as the primary message broker regardless of INFRA_MODE
   (an explicit MESSAGING_MODE still wins).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.layer4_frameworks import _create_message_broker_client
from app.layer4_frameworks.config.app_config import _resolve_event_mesh_config
from app.layer4_frameworks.messaging.event_mesh_broker_client import EventMeshBrokerClient

_COMPLETE_ENV = {
    "EVENT_MESH_TOKEN_URL": "https://env.token",
    "EVENT_MESH_MESSAGING_URL": "https://env.messaging",
    "EVENT_MESH_MANAGEMENT_URL": "https://env.management",
    "EVENT_MESH_CLIENT_ID": "env-client",
    "EVENT_MESH_CLIENT_SECRET": "env-secret",
    "EVENT_MESH_NAMESPACE": "env/ns",
}

_VCAP_CREDS = SimpleNamespace(
    token_url="https://vcap.token",
    messaging_url="https://vcap.messaging",
    management_url="https://vcap.management",
    client_id="vcap-client",
    client_secret="vcap-secret",
    namespace="vcap/ns",
    amqp_url="wss://vcap.amqp/protocols/amqp10ws",
)


class TestResolveEventMeshConfig:
    @patch("app.layer4_frameworks.config.app_config.get_event_mesh_credentials")
    def test_complete_env_wins_for_creds_but_amqp_url_filled_from_vcap(
        self, mock_vcap: MagicMock
    ) -> None:
        mock_vcap.return_value = _VCAP_CREDS
        # Complete env set, but no EVENT_MESH_AMQP_URL in env.
        config, source = _resolve_event_mesh_config(env=_COMPLETE_ENV)

        assert source == "env"
        # Env wins for everything it provides.
        assert config["token_url"] == "https://env.token"
        assert config["client_id"] == "env-client"
        assert config["namespace"] == "env/ns"
        # amqp_url wasn't in env → auto-discovered from the bound VCAP service,
        # so AMQP works by binding the service (no EVENT_MESH_AMQP_URL needed).
        assert config["amqp_url"] == "wss://vcap.amqp/protocols/amqp10ws"

    @patch("app.layer4_frameworks.config.app_config.get_event_mesh_credentials")
    def test_explicit_env_amqp_url_wins_over_vcap(self, mock_vcap: MagicMock) -> None:
        mock_vcap.return_value = _VCAP_CREDS
        env = {**_COMPLETE_ENV, "EVENT_MESH_AMQP_URL": "wss://env.amqp/x"}
        config, source = _resolve_event_mesh_config(env=env)

        assert source == "env"
        assert config["amqp_url"] == "wss://env.amqp/x"

    @patch("app.layer4_frameworks.config.app_config.get_event_mesh_credentials")
    def test_partial_env_falls_back_to_vcap(self, mock_vcap: MagicMock) -> None:
        mock_vcap.return_value = _VCAP_CREDS
        partial = {**_COMPLETE_ENV, "EVENT_MESH_CLIENT_SECRET": ""}

        config, source = _resolve_event_mesh_config(env=partial)

        assert source == "vcap"
        assert config["token_url"] == "https://vcap.token"
        assert config["client_secret"] == "vcap-secret"

    @patch("app.layer4_frameworks.config.app_config.get_event_mesh_credentials")
    def test_partial_env_without_vcap_returns_partial_values(self, mock_vcap: MagicMock) -> None:
        mock_vcap.return_value = None
        partial = {**_COMPLETE_ENV, "EVENT_MESH_CLIENT_SECRET": ""}

        config, source = _resolve_event_mesh_config(env=partial)

        assert source == ""
        assert config["token_url"] == "https://env.token"
        assert config["client_secret"] == ""

    @patch("app.layer4_frameworks.config.app_config.get_event_mesh_credentials")
    def test_empty_env_without_vcap(self, mock_vcap: MagicMock) -> None:
        mock_vcap.return_value = None

        config, source = _resolve_event_mesh_config(env={})

        assert source == ""
        assert all(value == "" for value in config.values())


def _settings(**overrides) -> SimpleNamespace:
    base = {
        "INFRA_MODE": "mock",
        "MESSAGING_MODE": "",
        "EVENT_MESH_CONFIG_SOURCE": "",
        "EVENT_MESH_TOKEN_URL": "https://env.token",
        "EVENT_MESH_MESSAGING_URL": "https://env.messaging",
        "EVENT_MESH_MANAGEMENT_URL": "https://env.management",
        "EVENT_MESH_CLIENT_ID": "env-client",
        "EVENT_MESH_CLIENT_SECRET": "env-secret",
        "EVENT_MESH_NAMESPACE": "env/ns",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestPrimaryBrokerSelection:
    def test_env_source_selects_event_mesh_despite_local_infra_mode(self) -> None:
        broker = _create_message_broker_client(
            _settings(INFRA_MODE="local", EVENT_MESH_CONFIG_SOURCE="env")
        )
        assert isinstance(broker, EventMeshBrokerClient)

    def test_env_source_selects_event_mesh_despite_mock_infra_mode(self) -> None:
        broker = _create_message_broker_client(
            _settings(INFRA_MODE="mock", EVENT_MESH_CONFIG_SOURCE="env")
        )
        assert isinstance(broker, EventMeshBrokerClient)

    def test_vcap_source_keeps_existing_mode_based_selection(self) -> None:
        # source "vcap" must not override INFRA_MODE; mock stays brokerless.
        broker = _create_message_broker_client(
            _settings(INFRA_MODE="mock", EVENT_MESH_CONFIG_SOURCE="vcap")
        )
        assert broker is None

    def test_sap_mode_with_vcap_source_still_builds_event_mesh(self) -> None:
        broker = _create_message_broker_client(
            _settings(INFRA_MODE="sap", EVENT_MESH_CONFIG_SOURCE="vcap")
        )
        assert isinstance(broker, EventMeshBrokerClient)

    def test_explicit_messaging_mode_wins_over_env_source(self) -> None:
        # MESSAGING_MODE is the escape hatch: forcing a non-event-mesh mode
        # must not be overridden by env-provided Event Mesh credentials.
        broker = _create_message_broker_client(
            _settings(MESSAGING_MODE="mock", EVENT_MESH_CONFIG_SOURCE="env")
        )
        assert broker is None
