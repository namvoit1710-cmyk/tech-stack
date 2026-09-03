import json

import pytest
from pydantic import ValidationError

from agent_sdk.layer4_frameworks.config import app_config
from agent_sdk.layer4_frameworks.config.app_config import Settings
from agent_sdk.layer4_frameworks.config.vcap_util import _extract_event_mesh_credentials


def _credentials() -> dict:
    return {
        "namespace": "ns1",
        "messaging": [
            {
                "protocol": ["mqtt311ws"],
                "uri": "https://mqtt.example",
                "oa2": {
                    "tokenendpoint": "https://auth.example/oauth/token",
                    "clientid": "mqtt-client",
                    "clientsecret": "mqtt-secret",
                },
            },
            {
                "protocol": ["httprest"],
                "uri": "https://rest.example",
                "oa2": {
                    "tokenendpoint": "https://auth.example/oauth/token",
                    "clientid": "rest-client",
                    "clientsecret": "rest-secret",
                },
            },
            {
                "protocol": ["amqp10ws"],
                "uri": "https://amqp.example",
                "oa2": {
                    "tokenendpoint": "https://auth.example/oauth/token",
                    "clientid": "amqp-client",
                    "clientsecret": "amqp-secret",
                },
            },
        ],
        "management": [
            {
                "protocol": ["httprest"],
                "uri": "https://management.example",
            }
        ],
    }


def test_event_mesh_vcap_defaults_to_httprest(monkeypatch) -> None:
    monkeypatch.delenv("EVENT_MESH_PROTOCOL", raising=False)
    monkeypatch.delenv("EVENT_MESH_MESSAGING_PROTOCOL", raising=False)

    creds = _extract_event_mesh_credentials(_credentials())

    assert creds is not None
    assert creds.messaging_url == "https://rest.example"
    assert creds.messaging_protocol == "httprest"
    assert creds.client_id == "rest-client"
    assert creds.client_secret == "rest-secret"


def test_event_mesh_vcap_selects_amqp10ws_when_requested() -> None:
    creds = _extract_event_mesh_credentials(
        _credentials(),
        protocol_hint="amqp10ws",
    )

    assert creds is not None
    assert creds.messaging_url == "https://amqp.example"
    assert creds.messaging_protocol == "amqp10ws"
    assert creds.client_id == "amqp-client"
    assert creds.client_secret == "amqp-secret"


def test_event_mesh_vcap_selects_mqtt311ws_when_requested() -> None:
    creds = _extract_event_mesh_credentials(
        _credentials(),
        protocol_hint="mqtt311ws",
    )

    assert creds is not None
    assert creds.messaging_url == "https://mqtt.example"
    assert creds.messaging_protocol == "mqtt311ws"
    assert creds.client_id == "mqtt-client"
    assert creds.client_secret == "mqtt-secret"


def test_event_mesh_vcap_accepts_protocol_string_not_only_list() -> None:
    credentials = _credentials()
    credentials["messaging"][1]["protocol"] = "httprest"

    creds = _extract_event_mesh_credentials(credentials, protocol_hint="httprest")

    assert creds is not None
    assert creds.messaging_url == "https://rest.example"
    assert creds.messaging_protocol == "httprest"


# ── BOSS-3 regression: VCAP protocol auto-selection wired end-to-end ──────────


def _vcap_amqp_only() -> str:
    """VCAP binding that advertises ONLY an AMQP (amqp10ws) messaging endpoint."""
    return json.dumps(
        {
            "enterprise-messaging": [
                {
                    "label": "enterprise-messaging",
                    "credentials": {
                        "namespace": "vcap-ns",
                        "messaging": [
                            {
                                "protocol": ["amqp10ws"],
                                "uri": "wss://amqp.vcap.example/protocol/amqp10ws",
                                "oa2": {
                                    "tokenendpoint": "https://auth.vcap.example/oauth/token",
                                    "clientid": "vcap-amqp-client",
                                    "clientsecret": "vcap-amqp-secret",
                                },
                            }
                        ],
                        "management": [
                            {
                                "protocol": ["httprest"],
                                "uri": "https://mgmt.vcap.example",
                            }
                        ],
                    },
                }
            ]
        }
    )


def test_vcap_amqp_binding_adopted_when_no_explicit_protocol(monkeypatch) -> None:
    """A VCAP binding advertising only amqp10ws must be adopted when the user has
    not explicitly set a protocol — the resolved messaging protocol becomes amqp
    (not the httprest default) and the messaging URL is the wss AMQP endpoint."""
    app_config._cached_event_mesh_credentials.cache_clear()
    monkeypatch.delenv("EVENT_MESH_PROTOCOL", raising=False)
    monkeypatch.delenv("EVENT_MESH_MESSAGING_PROTOCOL", raising=False)
    monkeypatch.setenv("GET_FROM_VCAP", "true")
    monkeypatch.setenv("VCAP_SERVICES", _vcap_amqp_only())

    try:
        settings = Settings(_env_file=None)

        assert settings.EVENT_MESH_MESSAGING_PROTOCOL == "amqp10ws"
        assert (
            settings.EVENT_MESH_MESSAGING_URL
            == "wss://amqp.vcap.example/protocol/amqp10ws"
        )
        assert settings.MESSAGING_MODE == "sap"
    finally:
        app_config._cached_event_mesh_credentials.cache_clear()


def test_explicit_httprest_protocol_wins_over_vcap_amqp(monkeypatch) -> None:
    """An explicitly configured protocol must always win over a VCAP-advertised one."""
    app_config._cached_event_mesh_credentials.cache_clear()
    monkeypatch.delenv("EVENT_MESH_MESSAGING_PROTOCOL", raising=False)
    monkeypatch.setenv("EVENT_MESH_PROTOCOL", "httprest")
    monkeypatch.setenv("GET_FROM_VCAP", "true")
    monkeypatch.setenv("VCAP_SERVICES", _vcap_amqp_only())

    try:
        settings = Settings(_env_file=None)
        assert settings.EVENT_MESH_MESSAGING_PROTOCOL == "httprest"
    finally:
        app_config._cached_event_mesh_credentials.cache_clear()


# ── Range validators for the new AMQP/MQTT hardening fields ───────────────────


@pytest.mark.parametrize("qos", [3, -1, 5])
def test_mqtt_qos_rejects_out_of_range(qos) -> None:
    with pytest.raises(ValidationError):
        Settings(EVENT_MESH_MQTT_QOS=qos, _env_file=None)


@pytest.mark.parametrize("qos", [3, -1])
def test_mqtt_last_will_qos_rejects_out_of_range(qos) -> None:
    with pytest.raises(ValidationError):
        Settings(EVENT_MESH_MQTT_LAST_WILL_QOS=qos, _env_file=None)


@pytest.mark.parametrize("qos", [0, 1, 2])
def test_mqtt_qos_accepts_valid_values(qos) -> None:
    settings = Settings(
        EVENT_MESH_MQTT_QOS=qos, EVENT_MESH_MQTT_LAST_WILL_QOS=qos, _env_file=None
    )
    assert settings.EVENT_MESH_MQTT_QOS == qos
    assert settings.EVENT_MESH_MQTT_LAST_WILL_QOS == qos


def test_retry_attempts_reject_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(EVENT_MESH_AMQP_PUBLISH_RETRY_ATTEMPTS=-1, _env_file=None)


def test_retry_delay_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(
            EVENT_MESH_MQTT_PUBLISH_RETRY_INITIAL_DELAY_SECONDS=-0.5, _env_file=None
        )
