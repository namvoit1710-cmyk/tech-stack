from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepStatus(str, Enum):
    PROCESSING = "processing"
    SUCCESS = "success"
    ERROR = "error"


class ExecutionTarget(str, Enum):
    LOCAL_TOOL = "LOCAL_TOOL"
    TECHNICAL_AGENT = "TECHNICAL_AGENT"


@dataclass
class Step:
    id: str
    execute_by: str
    input_data: dict[str, Any] = field(default_factory=dict)
    goal: str = ""
    message: str = ""
    output_data: dict[str, Any] = field(default_factory=dict)
    next_step_id: str | None = None
    uploaded_file_ids: list[str] = field(default_factory=list)
    status: StepStatus | None = None
    error: str | None = None


@dataclass
class StepResult:
    step_id: str
    status: StepStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # True => this failure is a transient infra error safe to retry (timeout, raised
    # exception, delegation send failure). False (default) => terminal / business failure
    # that must NOT be retried (capability config error, agent-reported failure).
    retryable: bool = False


@dataclass
class ResolvedCapability:
    capability_id: str
    execution_target: ExecutionTarget
    tool_name: str | None = None
    agent_type: str | None = None
    queue_metadata: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class DispatchStepRequest:
    step: Step
    message_id: str
    correlation_id: str
    conv_id: str
    batch_id: str
    from_agent: str
