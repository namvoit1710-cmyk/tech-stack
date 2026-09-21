from __future__ import annotations

import pytest

from app.layer1_domain.entities.step import Step, StepStatus
from app.layer4_frameworks.tools.local_tool_executor import LocalToolExecutor


class _AsyncTool:
    name = "async_tool"

    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, tool_input):
        self.calls.append(tool_input)
        return self.result


class _SyncTool:
    name = "sync_tool"

    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def invoke(self, tool_input):
        self.calls.append(tool_input)
        return self.result


class _FailingTool:
    name = "failing_tool"

    async def ainvoke(self, tool_input):
        raise RuntimeError("boom")


class _FakeLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict]] = []
        self.errors: list[tuple[str, dict]] = []

    def info(self, message: str, *args, **kwargs) -> None:
        self.infos.append((message, kwargs))

    def error(self, message: str, *args, **kwargs) -> None:
        self.errors.append((message, kwargs))


def _build_step() -> Step:
    return Step(
        id="step-1",
        execute_by="tool.test",
        input_data={"value": 1},
        goal="test",
        message="run tool",
    )


@pytest.mark.asyncio
async def test_execute_returns_error_when_tool_missing():
    executor = LocalToolExecutor([])

    result = await executor.execute("missing_tool", _build_step())

    assert result.status == StepStatus.ERROR
    assert (
        result.error == "Local tool 'missing_tool' not found in executor tool registry"
    )


@pytest.mark.asyncio
async def test_execute_uses_ainvoke_and_preserves_dict_output():
    tool = _AsyncTool({"ok": True})
    logger = _FakeLogger()
    executor = LocalToolExecutor([tool], logger_instance=logger)

    result = await executor.execute("async_tool", _build_step())

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"ok": True}
    assert tool.calls == [{"value": 1}]
    assert any(message == "Executing local tool" for message, _ in logger.infos)
    assert any(
        message == "Local tool execution completed" for message, _ in logger.infos
    )


@pytest.mark.asyncio
async def test_execute_uses_invoke_when_ainvoke_is_missing():
    tool = _SyncTool("done")
    executor = LocalToolExecutor([tool])

    result = await executor.execute("sync_tool", _build_step())

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"result": "done"}
    assert tool.calls == [{"value": 1}]


@pytest.mark.asyncio
async def test_execute_wraps_tool_exceptions():
    logger = _FakeLogger()
    executor = LocalToolExecutor([_FailingTool()], logger_instance=logger)

    result = await executor.execute("failing_tool", _build_step())

    assert result.status == StepStatus.ERROR
    assert result.error == "Tool 'failing_tool' execution failed: boom"
    assert any(
        message == "Local tool execution failed" for message, _ in logger.errors
    )


@pytest.mark.asyncio
async def test_execute_normalizes_none_result_to_result_wrapper():
    tool = _SyncTool(None)
    executor = LocalToolExecutor([tool])

    result = await executor.execute("sync_tool", _build_step())

    assert result.status == StepStatus.SUCCESS
    assert result.output == {"result": None}
