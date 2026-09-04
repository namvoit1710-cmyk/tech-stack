from app.layer4_frameworks.config.app_config import Settings


def test_settings_defaults_are_typed_strings() -> None:
    settings = Settings()

    assert isinstance(settings.APP_NAME, str)
    assert isinstance(settings.APP_VERSION, str)
    assert isinstance(settings.LOG_LEVEL, str)
    assert isinstance(settings.LOG_FORMAT, str)


def test_settings_env_overrides_defaults(monkeypatch) -> None:
    # Environment variables take precedence over the in-class defaults.
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("APP_NAME", "Custom Governance API")

    settings = Settings()

    assert settings.LOG_LEVEL == "DEBUG"
    assert settings.APP_NAME == "Custom Governance API"


def test_settings_ignores_unknown_env_vars(monkeypatch) -> None:
    # model_config extra="ignore" -> an unrelated env var must not raise.
    monkeypatch.setenv("GOVERNANCE_SMART_API_TOTALLY_UNKNOWN", "whatever")

    settings = Settings()

    assert isinstance(settings, Settings)
