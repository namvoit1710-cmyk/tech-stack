from fastapi.testclient import TestClient

from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app
from agent_sdk.layer4_frameworks.config.app_config import Settings


def test_settings_allow_origins_defaults_to_wildcard():
    """Settings().ALLOW_ORIGINS should default to ['*']."""
    s = Settings()
    assert s.ALLOW_ORIGINS == ["*"]


def test_cors_wildcard_origin_no_credentials():
    """With ALLOW_ORIGINS=['*'], preflight returns '*' and no allow-credentials: true."""
    settings = Settings()
    container = {"_dependencies": {"settings": settings}}
    app = create_agent_app(container)
    client = TestClient(app, raise_server_exceptions=True)

    response = client.options(
        "/health",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("access-control-allow-origin") == "*"
    assert response.headers.get("access-control-allow-credentials") != "true"


def test_cors_specific_origin_with_credentials():
    """With ALLOW_ORIGINS=['https://example.com'], preflight returns exact origin and allow-credentials: true."""

    class _SpecificSettings(Settings):
        ALLOW_ORIGINS: list = ["https://example.com"]

    settings = _SpecificSettings()
    container = {"_dependencies": {"settings": settings}}
    app = create_agent_app(container)
    client = TestClient(app, raise_server_exceptions=True)

    response = client.options(
        "/health",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("access-control-allow-origin") == "https://example.com"
    assert response.headers.get("access-control-allow-credentials") == "true"
