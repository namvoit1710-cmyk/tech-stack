from __future__ import annotations

import pytest

from agent_sdk.layer2_application.services.llm_cost_calculator import LLMCostCalculator
from agent_sdk.layer2_application.services.llm_usage_recorder import (
    LoggingLLMUsageRecorder,
)
from agent_sdk.layer4_frameworks.ai.usage_tracking_chat_model import (
    UsageTrackingChatModel,
)


class _Logger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict]] = []

    def info(self, message: str, **kwargs):
        self.infos.append((message, kwargs))

    def error(self, message: str, **kwargs):
        pass

    def warning(self, message: str, **kwargs):
        pass

    def debug(self, message: str, **kwargs):
        pass


class _Monitor:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, float]] = []

    def track(self, metric: str, value: float) -> None:
        self.metrics.append((metric, value))


class _Message:
    def __init__(self, content: str, **kwargs) -> None:
        self.content = content
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeChatModel:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response or _Message(
            "hello",
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        )
        self.error = error
        self.bound_tools = None

    def invoke(self, input, config=None, **kwargs):
        if self.error is not None:
            raise self.error
        return self.response

    async def ainvoke(self, input, config=None, **kwargs):
        if self.error is not None:
            raise self.error
        return self.response

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        return self

    def with_structured_output(self, *args, **kwargs):
        return self

    def bind(self, **kwargs):
        return self


def _tracked(fake=None, *, enabled=True):
    logger = _Logger()
    monitor = _Monitor()
    recorder = LoggingLLMUsageRecorder(logger=logger, monitor=monitor, enabled=enabled)
    calculator = LLMCostCalculator.from_json(
        {"openai:gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60}},
        currency="USD",
        enabled=True,
    )
    model = UsageTrackingChatModel(
        fake or _FakeChatModel(),
        recorder=recorder,
        cost_calculator=calculator,
        provider="openai",
        model="gpt-4o-mini",
        default_metadata={"agent_type": "echo", "agent_id": "agent-1"},
    )
    return model, logger, monitor


def test_usage_tracking_extracts_provider_usage_and_costs_from_sync_invoke():
    model, logger, monitor = _tracked()

    response = model.invoke(
        [_Message("hi")],
        config={
            "metadata": {
                "conversation_id": "conv-1",
                "thread_id": "thread-1",
                "correlation_id": "corr-1",
            }
        },
    )

    assert response.content == "hello"
    assert logger.infos[0][0] == "llm.usage"
    record = logger.infos[0][1]
    assert record["agent_type"] == "echo"
    assert record["agent_id"] == "agent-1"
    assert record["conversation_id"] == "conv-1"
    assert record["thread_id"] == "thread-1"
    assert record["correlation_id"] == "corr-1"
    assert record["operation"] == "chat"
    assert record["prompt_tokens"] == 10
    assert record["completion_tokens"] == 5
    assert record["total_tokens"] == 15
    assert record["estimated_tokens"] is False
    assert record["input_cost"] == "0.00000150 USD"
    assert record["output_cost"] == "0.00000300 USD"
    assert record["total_cost"] == "0.00000450 USD"
    assert ("llm_usage_total_tokens", 15.0) in monitor.metrics


@pytest.mark.asyncio
async def test_usage_tracking_estimates_tokens_when_provider_returns_no_usage():
    response = _Message("estimated response")
    model, logger, _ = _tracked(_FakeChatModel(response=response))

    await model.ainvoke([_Message("hello world")])

    record = logger.infos[0][1]
    assert record["estimated_tokens"] is True
    assert record["prompt_tokens"] > 0
    assert record["completion_tokens"] > 0
    assert (
        record["total_tokens"] == record["prompt_tokens"] + record["completion_tokens"]
    )


@pytest.mark.asyncio
async def test_bind_tools_keeps_usage_tracking_for_tool_agent_calls():
    fake = _FakeChatModel()
    model, logger, _ = _tracked(fake)

    bound = model.bind_tools([object()])
    await bound.ainvoke([_Message("use a tool")])

    assert fake.bound_tools is not None
    assert logger.infos[0][1]["operation"] == "tool_agent"


def test_with_structured_output_keeps_usage_tracking_for_structured_calls():
    model, logger, _ = _tracked()

    structured = model.with_structured_output(dict)
    structured.invoke("return json")

    assert logger.infos[0][1]["operation"] == "structured_output"


def test_usage_tracking_records_failures_and_reraises():
    model, logger, monitor = _tracked(_FakeChatModel(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        model.invoke("hello")

    record = logger.infos[0][1]
    assert record["success"] is False
    assert record["error_type"] == "RuntimeError"
    assert record["estimated_tokens"] is True
    assert ("llm_usage_errors", 1) in monitor.metrics


def test_usage_recorder_disabled_mode_does_not_log_or_track():
    model, logger, monitor = _tracked(enabled=False)

    model.invoke("hello")

    assert logger.infos == []
    assert monitor.metrics == []
