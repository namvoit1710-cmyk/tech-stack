from unittest.mock import MagicMock

import pytest

from tests.helpers.testing import StubLogger, StubMonitor


@pytest.fixture
def stub_logger():
    return StubLogger()


@pytest.fixture
def stub_monitor():
    return StubMonitor()


@pytest.fixture(autouse=True)
def stub_init_chat_model():
    """Stub the lazy chat-model initializer so tests never import langchain.

    The stub is a plain MagicMock, so:
    - Tests that *don't* care about the LLM get a harmless mock injected.
    - Tests that *do* care can patch ``agent_sdk.layer4_frameworks.ai.openai_service.init_chat_model``
      with their own mock; that inner patch takes precedence and the call
      assertions still work because the lazy import path is never bypassed.

    The fixture also handles ``OpenAIService.get_chat_client`` which follows
    the same lazy-import pattern.
    """
    import agent_sdk.layer4_frameworks.ai.openai_service as _openai_svc

    # ── openai_service.init_chat_model (same lazy pattern) ─────────────────
    original_svc_icm = _openai_svc.init_chat_model
    stub_icm = MagicMock(name="init_chat_model", return_value=MagicMock(name="llm"))
    _openai_svc.init_chat_model = stub_icm

    yield stub_icm

    # Restore originals so each test starts from a clean slate
    _openai_svc.init_chat_model = original_svc_icm
