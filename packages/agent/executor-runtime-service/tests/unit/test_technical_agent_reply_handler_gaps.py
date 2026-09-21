"""Gap-filling unit specs for TechnicalAgentReplyHandler.

Targets branches uncovered by test_technical_agent_reply_handler.py:
  - payload is not a dict -> early return (technical_agent_reply_handler.py:21)
  - missing correlation_id -> warning + return (25-26)
  - terminal reply with no waiter registered -> warning (already covered) but also
    the _extract_status / _extract_step_payload coercion of non-dict nested payload/step
    (54, 57, 63, 67).
Collaborators: waiter (sync methods) -> MagicMock; logger (sync) -> MagicMock.
Expectations grounded in technical_agent_reply_handler.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging.technical_agent_reply_handler import (
    TechnicalAgentReplyHandler,
)


def _handler(resolve_result=True):
    waiter = MagicMock()
    waiter.resolve.return_value = resolve_result
    logger = MagicMock()
    return TechnicalAgentReplyHandler(waiter, logger), waiter, logger


# --- invalid input: non-dict payload returns early, touches nothing (line 21) ---
@pytest.mark.asyncio
async def test_non_dict_payload_early_return():
    handler, waiter, logger = _handler()
    await handler("i am not a dict")  # type: ignore[arg-type]
    waiter.resolve.assert_not_called()
    logger.warning.assert_not_called()


# --- invalid input: missing correlation_id -> warning + return (lines 25-26) ---
@pytest.mark.asyncio
async def test_missing_correlation_id_warns_and_returns():
    handler, waiter, logger = _handler()
    await handler({"type": "agent.step.status", "payload": {}})
    waiter.resolve.assert_not_called()
    assert logger.warning.called


# --- happy path: terminal success resolves the waiter (lines 37-45) ---
@pytest.mark.asyncio
async def test_terminal_success_resolves_waiter():
    handler, waiter, _logger = _handler(resolve_result=True)
    payload = {
        "type": "agent.step.status",
        "correlation_id": "corr-1",
        "payload": {"step": {"id": "s1", "status": "success"}},
    }
    await handler(payload)
    waiter.resolve.assert_called_once_with("corr-1", payload)


# --- failure path: terminal reply for unknown correlation warns (lines 46-49) ---
@pytest.mark.asyncio
async def test_terminal_unknown_correlation_warns():
    handler, waiter, logger = _handler(resolve_result=False)
    payload = {
        "type": "agent.step.status",
        "correlation_id": "corr-missing",
        "payload": {"step": {"status": "success"}},
    }
    await handler(payload)
    assert logger.warning.called


# --- edge: status read from top-level when nested payload is not a dict (54,57) ---
@pytest.mark.asyncio
async def test_status_falls_back_to_top_level_when_payload_not_dict():
    handler, waiter, _logger = _handler(resolve_result=True)
    payload = {
        "correlation_id": "corr-2",
        "payload": "not-a-dict",  # coerced to {}
        "status": "success",  # fallback source
    }
    await handler(payload)
    # success is terminal -> resolve attempted
    waiter.resolve.assert_called_once()


# --- edge: nested step is not a dict -> step_payload coerced to {} (63,67) ---
@pytest.mark.asyncio
async def test_step_not_dict_coerced():
    handler, waiter, _logger = _handler(resolve_result=True)
    payload = {
        "correlation_id": "corr-3",
        "payload": {"step": "not-a-dict"},
        "status": "completed",
    }
    await handler(payload)
    waiter.resolve.assert_called_once()


# --- edge: non-terminal status is ignored, waiter not resolved (lines 29-35) ---
@pytest.mark.asyncio
async def test_non_terminal_status_ignored():
    handler, waiter, logger = _handler()
    payload = {
        "correlation_id": "corr-4",
        "payload": {"step": {"status": "running"}},
    }
    await handler(payload)
    waiter.resolve.assert_not_called()
    assert logger.debug.called
