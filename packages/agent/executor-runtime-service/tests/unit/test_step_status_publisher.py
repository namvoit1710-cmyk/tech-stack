"""Tests for DirectStepStatusPublisher."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.layer1_domain.entities.step import Step, StepStatus
from app.layer1_domain.interfaces.step_status_publisher_interface import (
    StepStatusPublisherInput,
)
from app.layer4_frameworks.messaging.direct_step_status_publisher import (
    DirectStepStatusPublisher,
)


class _CapturedPublisher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, topic: str, message: dict, key: str | None = None) -> None:
        self.calls.append({"topic": topic, "message": message, "key": key})


@pytest.mark.asyncio
async def test_direct_step_status_publisher_builds_executor_step_status(monkeypatch):
    captured = _CapturedPublisher()
    publisher = DirectStepStatusPublisher(
        publisher=captured,
        topic="executor.step.status",
    )

    monkeypatch.setattr(
        "app.layer4_frameworks.messaging.direct_step_status_publisher.uuid7",
        lambda: UUID("019eaa80-93d8-7610-8ee6-23aca89bf8ae"),
    )

    step = Step(
        id="step_1",
        execute_by="tool.abc",
        input_data={"input_text": "Echo the input"},
        goal="Echo the input",
        message="echo: Echo the input",
        output_data={"result": "echo: Echo the input"},
        status=StepStatus.SUCCESS,
    )

    await publisher.publish_status(
        StepStatusPublisherInput(
            step=step,
            to_agent="agent.business",
            conv_id="conv_1",
            correlation_id="corr_1",
        )
    )

    assert len(captured.calls) == 1
    call = captured.calls[0]
    assert call["topic"] == "executor.step.status"
    assert call["key"] == "corr_1"
    assert call["message"]["type"] == "executor.step.status"
    assert call["message"]["message_id"] == "019eaa80-93d8-7610-8ee6-23aca89bf8ae"
    assert call["message"]["payload"]["from"] == "executor.runtime"
    assert call["message"]["payload"]["to"] == "agent.business"
    assert call["message"]["payload"]["steps"][0]["input_data"] == {
        "input_text": "Echo the input"
    }
    assert call["message"]["payload"]["steps"][0]["output_data"] == {
        "result": "echo: Echo the input"
    }
    assert call["message"]["payload"]["steps"][0]["goal"] == "Echo the input"
    assert call["message"]["payload"]["steps"][0]["message"] == "echo: Echo the input"
    assert call["message"]["payload"]["steps"][0]["status"] == "success"
    assert call["message"]["payload"]["steps"][0]["error"] == ""


@pytest.mark.asyncio
async def test_direct_step_status_publisher_swallows_publish_errors(caplog):
    class _FailingPublisher:
        async def publish(
            self, topic: str, message: dict, key: str | None = None
        ) -> None:
            raise RuntimeError("publish failed")

    publisher = DirectStepStatusPublisher(
        publisher=_FailingPublisher(),
        topic="executor.step.status",
    )

    step = Step(
        id="step_1",
        execute_by="tool.abc",
        input_data={"input_text": "Echo the input"},
        goal="Echo the input",
        message="echo: Echo the input",
        output_data={"result": "echo: Echo the input"},
        status=StepStatus.SUCCESS,
    )

    await publisher.publish_status(
        StepStatusPublisherInput(
            step=step,
            to_agent="agent.business",
            conv_id="conv_1",
            correlation_id="corr_1",
        )
    )

    assert "Failed to publish executor.step.status" in caplog.text
