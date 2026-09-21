import os

# Deterministic test environment (the deploy sets AGENT_TYPE=profile-agent;
# the suite pins profile-manager — see SA-1442 risk register).
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("PROFILE_MANAGEMENT_BASE_URL", "http://localhost:8080")
os.environ.setdefault("AGENT_TYPE", "profile-manager")

import pytest
from fastapi.testclient import TestClient


class MockOpenAIService:
    """Stand-in for the LLM seam so the container builds without a real provider.

    Returns a minimal valid execution plan; the integration tests only exercise
    /health and /api/v1/info, so the LLM is never actually invoked.
    """

    async def get_chat_completion(self, *args, **kwargs):
        return '{"status": "ok", "execution_plan": []}'


class MockTokenProvider:
    """Supplies a token so AuthMiddleware lets requests through (as prod would).

    Keeps the middleware untouched (C1/guardrail) — the test provides the token
    source instead of hard-coding a bypass.
    """

    async def get_token(self) -> str:
        return "test-token"


@pytest.fixture
def client(monkeypatch):
    # Patch the seams BEFORE importing create_app so bootstrap wires the mocks.
    import bootstrap

    monkeypatch.setattr(bootstrap, "OpenAIService", lambda: MockOpenAIService())
    monkeypatch.setattr(bootstrap, "OAuthTokenProvider", lambda: MockTokenProvider())

    # Now import AFTER patching
    from main import create_app

    return TestClient(create_app())
