import json
from unittest.mock import patch

import pytest

from agent_sdk import Settings


def test_default_settings():
    s = Settings()
    assert s.APP_MODE in ("SERVER", "CONSUMER")
    assert s.SDK_VERSION == "1.0.0"
    assert s.AGENT_TYPE == "generic"


def test_server_mode_is_default():
    s = Settings()
    assert s.APP_MODE == "SERVER"


# --- New generic LLM settings ---


def test_settings_has_openai_api_key_field():
    s = Settings()
    assert hasattr(s, "OPENAI_API_KEY")


def test_settings_openai_api_key_defaults_to_empty_string():
    s = Settings()
    assert s.OPENAI_API_KEY == ""


def test_settings_llm_api_key_defaults_to_empty_string():
    s = Settings()
    assert s.LLM_API_KEY == ""


def test_settings_has_llm_model_field():
    s = Settings()
    assert hasattr(s, "LLM_MODEL")


def test_settings_llm_model_default():
    s = Settings()
    assert s.LLM_MODEL == "gpt-4o-mini"


def test_settings_has_llm_temperature_field():
    s = Settings()
    assert hasattr(s, "LLM_TEMPERATURE")


def test_settings_llm_temperature_default():
    s = Settings()
    assert s.LLM_TEMPERATURE == 0.01


def test_settings_has_llm_provider_field():
    s = Settings()
    assert hasattr(s, "LLM_PROVIDER")


def test_settings_llm_provider_default():
    s = Settings()
    assert s.LLM_PROVIDER == ""


def test_settings_has_llm_timeout_field():
    s = Settings()
    assert hasattr(s, "LLM_TIMEOUT")


def test_settings_llm_timeout_default():
    s = Settings()
    assert s.LLM_TIMEOUT is None


def test_settings_has_llm_max_tokens_field():
    s = Settings()
    assert hasattr(s, "LLM_MAX_TOKENS")


def test_settings_llm_max_tokens_default():
    s = Settings()
    assert s.LLM_MAX_TOKENS is None


def test_settings_has_llm_max_retries_field():
    s = Settings()
    assert hasattr(s, "LLM_MAX_RETRIES")


def test_settings_llm_max_retries_default():
    s = Settings()
    assert s.LLM_MAX_RETRIES == 6


def test_settings_llm_model_kwargs_default(monkeypatch):
    monkeypatch.delenv("LLM_MODEL_KWARGS", raising=False)
    s = Settings()
    assert s.LLM_MODEL_KWARGS == {}


def test_settings_llm_model_kwargs_from_env_json(monkeypatch):
    model_kwargs = {
        "reasoning": {"effort": "medium"},
    }
    monkeypatch.setenv("LLM_MODEL_KWARGS", json.dumps(model_kwargs))

    s = Settings()

    assert s.LLM_MODEL_KWARGS == model_kwargs


def test_settings_has_default_tenant_id_field():
    s = Settings()
    assert hasattr(s, "DEFAULT_TENANT_ID")


def test_settings_default_tenant_id_default():
    s = Settings()
    assert s.DEFAULT_TENANT_ID == "default"


# --- Event mesh / messaging settings ---


def test_kafka_request_topic_default():
    s = Settings()
    assert s.KAFKA_REQUEST_TOPIC == "agent.request"


def test_infra_mode_default():
    s = Settings()
    assert s.INFRA_MODE == "mock"


def test_event_mesh_namespace_default():
    s = Settings()
    assert s.EVENT_MESH_NAMESPACE == "default"


def test_messaging_mode_default():
    s = Settings()
    assert s.MESSAGING_MODE == ""


def test_kafka_group_id_has_default():
    s = Settings()
    assert isinstance(s.KAFKA_GROUP_ID, str)


def test_event_mesh_token_url_default():
    s = Settings()
    assert s.EVENT_MESH_TOKEN_URL == ""


def test_event_mesh_client_id_default():
    s = Settings()
    assert s.EVENT_MESH_CLIENT_ID == ""


def test_event_mesh_client_secret_default():
    s = Settings()
    assert s.EVENT_MESH_CLIENT_SECRET == ""


def test_event_mesh_broker_url_default():
    s = Settings()
    assert s.EVENT_MESH_BROKER_URL == ""


def test_event_mesh_messaging_url_default():
    s = Settings()
    assert s.EVENT_MESH_MESSAGING_URL == "http://localhost:18080"


def test_event_mesh_management_url_default():
    s = Settings()
    assert s.EVENT_MESH_MANAGEMENT_URL == "http://localhost:18080"


def test_resolve_hana_defaults_rejects_invalid_env_port(monkeypatch):
    from agent_sdk.layer4_frameworks.config import app_config

    monkeypatch.setenv("HANA_PORT", "not-a-port")
    monkeypatch.delenv("GET_FROM_VCAP", raising=False)

    with patch.object(app_config, "_cached_hana_credentials", return_value=None):
        with pytest.raises(
            ValueError,
            match="Invalid HANA port value for HANA_PORT",
        ):
            app_config._resolve_hana_defaults()
