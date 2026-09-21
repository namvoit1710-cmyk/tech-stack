from __future__ import annotations

from typing import Any

from agent_sdk.layer4_frameworks.ai import openai_service as openai_module
from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService


class _FakeChatModel:
    pass


def _capture_init_chat_model(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_init_chat_model(model: str | None, **kwargs: Any) -> _FakeChatModel:
        calls.append({"model": model, "kwargs": kwargs})
        return _FakeChatModel()

    monkeypatch.setattr(openai_module, "init_chat_model", fake_init_chat_model)
    return calls


def test_reasoning_effort_is_off_by_default_for_gpt_4o_mini(monkeypatch):
    calls = _capture_init_chat_model(monkeypatch)

    service = LangChainLLMService(model="gpt-4o-mini", provider="openai")
    service.get_chat_client()

    kwargs = calls[0]["kwargs"]
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs


def test_legacy_nested_reasoning_kwargs_are_not_forwarded_to_gpt_4o_mini(monkeypatch):
    calls = _capture_init_chat_model(monkeypatch)

    service = LangChainLLMService(
        model="gpt-4o-mini",
        provider="openai",
        model_kwargs={"reasoning": {"effort": "low"}},
    )
    service.get_chat_client()

    kwargs = calls[0]["kwargs"]
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs


def test_reasoning_effort_is_forwarded_for_reasoning_model_only(monkeypatch):
    calls = _capture_init_chat_model(monkeypatch)

    service = LangChainLLMService(
        model="o4-mini",
        provider="openai",
        reasoning_effort="low",
    )
    service.get_chat_client()

    assert calls[0]["kwargs"]["reasoning_effort"] == "low"
