from app.layer4_frameworks.config.app_config import Settings


def test_business_agent_defaults_to_single_inflight_consumer_message(monkeypatch):
    monkeypatch.delenv("CONSUMER_MAX_IN_FLIGHT_MESSAGES", raising=False)

    settings = Settings()

    assert settings.CONSUMER_MAX_IN_FLIGHT_MESSAGES == 1


def test_business_agent_allows_consumer_inflight_override(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_IN_FLIGHT_MESSAGES", "3")

    settings = Settings()

    assert settings.CONSUMER_MAX_IN_FLIGHT_MESSAGES == 3