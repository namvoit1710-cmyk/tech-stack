from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.layer1_domain.entities.orchestrator.turn_meter import (
    TurnMeter,
    reset_turn,
    start_turn,
)
from app.layer4_frameworks.ai.instrumented_llm_provider import InstrumentedLLMProvider


class StubProvider:
    """Mimics OpenAILLMProvider's usage_observer seam without a real OpenAI call."""

    def __init__(self, results: list, usages: list) -> None:
        self._results = list(results)
        self._usages = list(usages)
        self.usage_observer = None
        self.call_count = 0

    def complete_with_format(self, *, messages, response_model):
        self.call_count += 1
        if self.usage_observer is not None:
            self.usage_observer(self._usages.pop(0))
        return self._results.pop(0)


async def _drain_pending_tasks() -> None:
    import asyncio

    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_two_calls_in_one_turn_emit_cumulative_processing_events_with_shared_message_id():
    inner = StubProvider(
        results=["result-1", "result-2"],
        usages=[SimpleNamespace(total_tokens=820), SimpleNamespace(total_tokens=862)],
    )
    notifier = SimpleNamespace(send_token_info=AsyncMock())
    provider = InstrumentedLLMProvider(inner=inner, notifier=notifier)

    meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")
    token = start_turn(meter)
    try:
        result1 = provider.complete_with_format(messages=[], response_model=object)
        result2 = provider.complete_with_format(messages=[], response_model=object)
        await _drain_pending_tasks()
    finally:
        reset_turn(token)

    assert result1 == "result-1"
    assert result2 == "result-2"
    assert inner.call_count == 2  # no additional LLM round-trip introduced
    assert notifier.send_token_info.await_count == 2

    first_call = notifier.send_token_info.await_args_list[0].kwargs
    second_call = notifier.send_token_info.await_args_list[1].kwargs
    assert first_call["conv_id"] == "conv-1"
    assert first_call["message_id"] == "msg-1"
    assert first_call["status"] == "processing"
    assert "820" in first_call["content"]
    assert second_call["message_id"] == "msg-1"
    assert "1,682" in second_call["content"]


@pytest.mark.asyncio
async def test_missing_usage_gives_time_only_string() -> None:
    inner = StubProvider(results=["result-1"], usages=[SimpleNamespace()])
    notifier = SimpleNamespace(send_token_info=AsyncMock())
    provider = InstrumentedLLMProvider(inner=inner, notifier=notifier)

    meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")
    token = start_turn(meter)
    try:
        provider.complete_with_format(messages=[], response_model=object)
        await _drain_pending_tasks()
    finally:
        reset_turn(token)

    content = notifier.send_token_info.await_args.kwargs["content"]
    assert "tokens" not in content


@pytest.mark.asyncio
async def test_no_active_turn_emits_nothing() -> None:
    inner = StubProvider(results=["result-1"], usages=[SimpleNamespace(total_tokens=10)])
    notifier = SimpleNamespace(send_token_info=AsyncMock())
    provider = InstrumentedLLMProvider(inner=inner, notifier=notifier)

    result = provider.complete_with_format(messages=[], response_model=object)
    await _drain_pending_tasks()

    assert result == "result-1"
    notifier.send_token_info.assert_not_awaited()
