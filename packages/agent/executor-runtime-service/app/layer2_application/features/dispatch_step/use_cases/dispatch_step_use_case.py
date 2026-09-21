"""
Dispatch Step Use Case.

Routes a validated step to either local tool execution or technical-agent delegation.
"""

from __future__ import annotations

from app.layer1_domain.entities.step import (
    ExecutionTarget,
    ResolvedCapability,
    Step,
    StepResult,
    StepStatus,
    DispatchStepRequest,
)
from app.layer1_domain.interfaces.capability_resolver_interface import (
    ICapabilityResolver,
)
from app.layer1_domain.interfaces.tool_executor_interface import IToolExecutor
from app.layer2_application.interfaces.technical_agent_step_executor_interface import (
    ITechnicalAgentStepExecutor,
)
from app.layer2_application.interfaces.observability import ILogger
from app.layer2_application.services.retry_policy import RetryPolicy


class DispatchStepUseCase:
    def __init__(
        self,
        capability_resolver: ICapabilityResolver,
        tool_executor: IToolExecutor,
        logger: ILogger,
        technical_agent_step_executor: ITechnicalAgentStepExecutor | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._capability_resolver = capability_resolver
        self._tool_executor = tool_executor
        self._logger = logger
        self._technical_agent_step_executor = technical_agent_step_executor
        self._retry_policy = retry_policy

    async def dispatch(self, request: DispatchStepRequest) -> StepResult:
        """
        Resolve the capability and dispatch the step for execution.

        Returns a StepResult with success or error status.
        """
        step = request.step
        capability = await self._capability_resolver.resolve(step.execute_by)

        if capability is None:
            self._logger.error(
                f"Capability resolution failed: execute_by='{step.execute_by}'"
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Capability not found for execute_by='{step.execute_by}'",
            )

        if not capability.enabled:
            self._logger.error(f"Capability '{capability.capability_id}' is disabled")
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Capability '{capability.capability_id}' is disabled",
            )

        self._logger.info(
            "Resolved step capability",
            step_id=step.id,
            execute_by=step.execute_by,
            capability_id=capability.capability_id,
            execution_target=capability.execution_target.value,
            tool_name=capability.tool_name,
            agent_type=capability.agent_type,
        )

        if capability.execution_target == ExecutionTarget.LOCAL_TOOL:

            async def _attempt() -> StepResult:
                return await self._execute_local(step, capability)

        elif capability.execution_target == ExecutionTarget.TECHNICAL_AGENT:

            async def _attempt() -> StepResult:
                return await self._delegate_to_agent(request, capability)

        else:
            self._logger.error(
                f"Unknown execution_target '{capability.execution_target}' for capability_id='{capability.capability_id}'"
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Unknown execution_target: {capability.execution_target}",
            )

        # Retry transient failures (backoff + budget) when a policy is wired; otherwise
        # execute exactly once (pre-SA-1716 behavior).
        if self._retry_policy is None:
            return await _attempt()
        return await self._retry_policy.run(_attempt, step_id=step.id)

    async def _execute_local(
        self, step: Step, capability: ResolvedCapability
    ) -> StepResult:
        """Execute step through local tool executor."""
        tool_name = capability.tool_name
        if not tool_name:
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Capability '{capability.capability_id}' has no tool_name for LOCAL_TOOL",
            )

        try:
            result = await self._tool_executor.execute(tool_name, step)
            self._logger.info(
                f"Local tool execution completed: step_id={step.id} tool={tool_name} result={result}"
            )
            return result
        except Exception as exc:
            self._logger.error(
                f"Local tool execution failed: step_id={step.id} tool={tool_name} error={exc}"
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Local tool execution failed: {exc}",
                retryable=True,
            )

    async def _delegate_to_agent(
        self,
        request: DispatchStepRequest,
        capability: ResolvedCapability,
    ) -> StepResult:
        """
        Delegate step to a technical agent via queue.
        """
        step = request.step
        self._logger.info(
            "Delegating step to technical agent",
            step_id=step.id,
            execute_by=step.execute_by,
            agent_type=capability.agent_type,
            input_message=step.message,
            input_data=dict(step.input_data or {}),
        )
        if self._technical_agent_step_executor is not None:
            return await self._technical_agent_step_executor.execute(
                request=request,
                capability=capability,
            )

        return StepResult(
            step_id=step.id,
            status=StepStatus.ERROR,
            error=(
                f"Technical agent delegation not yet implemented. "
                f"agent_type='{capability.agent_type}'"
            ),
        )
