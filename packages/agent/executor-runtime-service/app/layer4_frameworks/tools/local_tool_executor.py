"""
Local Tool Executor.

Resolves and invokes local LangChain tools by name.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool

from app.layer1_domain.entities.step import Step, StepResult, StepStatus
from app.layer1_domain.interfaces.tool_executor_interface import IToolExecutor
from app.layer2_application.interfaces.observability import ILogger

logger = logging.getLogger(__name__)


class LocalToolExecutor(IToolExecutor):
    """
    Executes local tools by name lookup from a pre-registered tool list.

    Tools are discovered via the tool_registry scan and passed in as a list.
    Each tool is a LangChain BaseTool instance invoked with the step's
    input_data as structured input.
    """

    def __init__(
        self,
        tools: list[BaseTool] | None = None,
        logger_instance: ILogger | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._logger = logger_instance
        self._timeout_seconds = timeout_seconds
        for t in tools or []:
            name = getattr(t, "name", None)
            if name:
                self._tools[name] = t

    async def execute(self, tool_name: str, step: Step) -> StepResult:
        """
        Look up and invoke the named tool with step data as input.

        Returns StepResult with success or error.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Local tool '{tool_name}' not found in executor tool registry",
            )

        # Build structured tool input from the business-agent request.
        tool_input = step.input_data or {}

        self._log_info(
            "Executing local tool",
            step_id=step.id,
            execute_by=step.execute_by,
            tool_name=tool_name,
            input_message=step.message,
            tool_input=dict(tool_input),
        )

        try:
            # LangChain tools support both sync and async invoke. When a timeout is
            # configured, bound each attempt so a hung tool cannot block the step forever
            # (sync invoke runs in a worker thread so wait_for can time it out).
            if hasattr(tool, "ainvoke"):
                invocation = tool.ainvoke(tool_input)
            else:
                invocation = asyncio.to_thread(tool.invoke, tool_input)
            if self._timeout_seconds:
                result = await asyncio.wait_for(invocation, self._timeout_seconds)
            else:
                result = await invocation

            # Normalize output
            output = {"result": result} if not isinstance(result, dict) else result

            self._log_info(
                "Local tool execution completed",
                step_id=step.id,
                execute_by=step.execute_by,
                tool_name=tool_name,
                raw_result=result,
                output_message=self._extract_output_message(output),
                output_data=dict(output),
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.SUCCESS,
                output=output,
            )

        except asyncio.TimeoutError:
            self._log_error(
                "Local tool execution timed out",
                step_id=step.id,
                execute_by=step.execute_by,
                tool_name=tool_name,
                timeout_seconds=self._timeout_seconds,
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Tool '{tool_name}' timed out after {self._timeout_seconds}s",
                retryable=True,  # a hung tool is transient — safe to retry
            )
        except Exception as exc:
            self._log_error(
                "Local tool execution failed",
                step_id=step.id,
                execute_by=step.execute_by,
                tool_name=tool_name,
                input_message=step.message,
                tool_input=dict(tool_input),
                error=str(exc),
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Tool '{tool_name}' execution failed: {exc}",
                retryable=True,  # transient tool exception — safe to retry
            )

    def _extract_output_message(self, output_data: dict[str, Any]) -> str:
        message = output_data.get("message") or output_data.get("result")
        if isinstance(message, str):
            return message
        return ""

    def _log_info(self, message: str, **kwargs: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, **kwargs)
            return
        logger.info("%s %s", message, kwargs)

    def _log_error(self, message: str, **kwargs: Any) -> None:
        if self._logger is not None:
            self._logger.error(message, **kwargs)
            return
        logger.error("%s %s", message, kwargs)
