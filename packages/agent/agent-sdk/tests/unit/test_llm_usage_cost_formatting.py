from __future__ import annotations

from typing import Any

from agent_sdk.layer1_domain.entities.llm_usage import LLMUsageRecord
from agent_sdk.layer2_application.services.llm_usage_recorder import (
    LoggingLLMUsageRecorder,
)


class _CaptureLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.calls.append((event, kwargs))


def test_llm_usage_recorder_formats_tiny_costs_without_scientific_notation():
    record = LLMUsageRecord(
        input_cost=7.349999999999999e-06,
        output_cost=1.2e-06,
        total_cost=8.55e-06,
        currency="USD",
    )
    logger = _CaptureLogger()
    recorder = LoggingLLMUsageRecorder(logger=logger)

    recorder.record(record)

    assert len(logger.calls) == 1
    event, data = logger.calls[0]
    assert event == "llm.usage"
    assert data["input_cost"] == "0.00000735 USD"
    assert data["output_cost"] == "0.00000120 USD"
    assert data["total_cost"] == "0.00000855 USD"
    assert "e-" not in str(data["input_cost"]).lower()
    assert "e-" not in str(data["output_cost"]).lower()
    assert "e-" not in str(data["total_cost"]).lower()


def test_llm_usage_record_keeps_numeric_cost_attributes_for_metrics():
    record = LLMUsageRecord(input_cost=7.35e-06, total_cost=8.55e-06)

    assert isinstance(record.input_cost, float)
    assert isinstance(record.total_cost, float)
    assert record.input_cost == 7.35e-06
    assert record.total_cost == 8.55e-06
