"""Local-tool timeout + transient-failure classification (SA-1716)."""

from __future__ import annotations

import asyncio

import pytest

from app.layer1_domain.entities.step import Step, StepStatus
from app.layer4_frameworks.tools.local_tool_executor import LocalToolExecutor


class _HangTool:
    name = "hang"

    async def ainvoke(self, _input):
        await asyncio.sleep(5)  # longer than the configured timeout
        return "never"


class _BoomTool:
    name = "boom"

    async def ainvoke(self, _input):
        raise RuntimeError("kaboom")


def _step(execute_by="tool.x"):
    return Step(id="s1", execute_by=execute_by, input_data={"a": 1})


@pytest.mark.asyncio
async def test_local_tool_timeout_is_retryable():
    ex = LocalToolExecutor(tools=[_HangTool()], timeout_seconds=0.05)
    result = await ex.execute("hang", _step())
    assert result.status == StepStatus.ERROR
    assert result.retryable is True
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_local_tool_exception_is_retryable():
    ex = LocalToolExecutor(tools=[_BoomTool()])
    result = await ex.execute("boom", _step())
    assert result.status == StepStatus.ERROR
    assert result.retryable is True
    assert "kaboom" in result.error


@pytest.mark.asyncio
async def test_local_tool_not_found_is_terminal():
    ex = LocalToolExecutor(tools=[])
    result = await ex.execute("missing", _step())
    assert result.status == StepStatus.ERROR
    assert result.retryable is False
    assert "not found" in result.error
