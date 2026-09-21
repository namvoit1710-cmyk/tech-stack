from __future__ import annotations

import pytest

from agent_sdk.layer4_frameworks.ai import openai_service
from agent_sdk.layer4_frameworks.ai.openai_service import (
    LangChainLLMService,
    supports_temperature,
)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-4o-mini", True),
        ("gpt-4o", True),
        ("gpt-4.1", True),
        ("gpt-3.5-turbo", True),
        ("gpt-5", False),
        ("gpt-5-mini", False),
        ("o1", False),
        ("o1-mini", False),
        ("o3", False),
        ("o3-mini", False),
        ("o4-mini", False),
    ],
)
def test_supports_temperature_classifies_reasoning_models(
    model: str, expected: bool
) -> None:
    assert supports_temperature("openai", model) is expected


def _capture_chat_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch init_chat_model to record the kwargs get_chat_client builds."""
    captured: dict = {}

    def fake_init(model, **kwargs):  # noqa: ANN001, ANN003
        captured["model"] = model
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(openai_service, "init_chat_model", fake_init)
    return captured


def test_get_chat_client_omits_temperature_for_reasoning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_chat_kwargs(monkeypatch)
    service = LangChainLLMService(api_key="x", model="gpt-5", temperature=0.01)
    service.get_chat_client()
    assert "temperature" not in captured["kwargs"]


def test_get_chat_client_sends_temperature_for_chat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_chat_kwargs(monkeypatch)
    service = LangChainLLMService(api_key="x", model="gpt-4o-mini", temperature=0.01)
    service.get_chat_client()
    assert captured["kwargs"]["temperature"] == 0.01
