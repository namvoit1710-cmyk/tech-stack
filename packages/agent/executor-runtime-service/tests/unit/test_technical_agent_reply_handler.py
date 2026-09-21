from __future__ import annotations

import asyncio

import pytest

from app.layer4_frameworks.messaging.technical_agent_reply_handler import (
    TechnicalAgentReplyHandler,
)
from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter


class _FakeLogger:
    def __init__(self) -> None:
        self.infos: list = []
        self.warnings: list = []
        self.debugs: list = []

    def info(self, message: str, *args, **kwargs) -> None:
        self.infos.append((message, kwargs))

    def warning(self, message: str, *args, **kwargs) -> None:
        self.warnings.append((message, kwargs))

    def debug(self, message: str, *args, **kwargs) -> None:
        self.debugs.append((message, kwargs))


@pytest.mark.asyncio
async def test_reply_handler_ignores_processing_and_resolves_success():
    waiter = AgentReplyWaiter()
    logger = _FakeLogger()
    handler = TechnicalAgentReplyHandler(waiter, logger)

    waiter.register("corr-1")
    await handler(
        {
            "type": "agent.step.status",
            "message_id": "msg-1",
            "correlation_id": "corr-1",
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {"id": "step-1", "status": "processing", "output_data": {}},
            },
        }
    )
    assert "corr-1" in waiter._pending

    task = asyncio.create_task(waiter.wait("corr-1", 1.0))
    await handler(
        {
            "type": "agent.step.status",
            "message_id": "msg-2",
            "correlation_id": "corr-1",
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {
                    "id": "step-1",
                    "status": "success",
                    "output_data": {"ok": True},
                },
            },
        }
    )
    reply = await task

    assert reply["payload"]["step"]["output_data"]["ok"] is True
    assert any(
        message == "Received terminal technical agent reply"
        for message, _ in logger.infos
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "in_progress", "interrupted"])
async def test_reply_handler_ignores_non_terminal_statuses(status: str):
    waiter = AgentReplyWaiter()
    logger = _FakeLogger()
    handler = TechnicalAgentReplyHandler(waiter, logger)

    waiter.register("corr-non-terminal")

    await handler(
        {
            "type": "agent.step.status",
            "message_id": "msg-non-terminal",
            "correlation_id": "corr-non-terminal",
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {
                    "id": "step-1",
                    "status": status,
                    "output_data": {},
                },
            },
        }
    )

    assert "corr-non-terminal" in waiter._pending
    assert not logger.warnings


@pytest.mark.asyncio
async def test_reply_handler_ignores_unknown_correlation_id():
    waiter = AgentReplyWaiter()
    logger = _FakeLogger()
    handler = TechnicalAgentReplyHandler(waiter, logger)

    await handler(
        {
            "type": "agent.step.status",
            "message_id": "msg-3",
            "correlation_id": "missing",
            "conv_id": "conv-1",
            "payload": {
                "from": "agent.file",
                "to": "executor.runtime",
                "type": "step",
                "step": {"id": "step-1", "status": "success", "output_data": {}},
            },
        }
    )

    assert logger.warnings
