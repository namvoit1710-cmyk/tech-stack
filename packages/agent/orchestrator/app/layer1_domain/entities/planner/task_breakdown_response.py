from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus


@dataclass(slots=True)
class StrategicWorkflowPlan:
	context_summary: str = ""
	logical_steps: list[str] = field(default_factory=list)
	dependency_rules: str = ""


@dataclass(slots=True)
class ParsedSharedStateTaskModel:
	id: str = ""
	goal: str = ""
	execute_by: str = ""
	input_schema: list[str] = field(default_factory=list)
	output_schema: list[str] = field(default_factory=list)
	status: TaskStatus = TaskStatus.PENDING
	input_data: dict[str, Any] = field(default_factory=dict)
	output_data: dict[str, Any] = field(default_factory=dict)
	depend_on: list[str] = field(default_factory=list)
	workflow_id: str = ""
	agent_id: str = ""


@dataclass(slots=True)
class TaskBreakdownResponse:
	reasoning: str = ""
	tasks: list[ParsedSharedStateTaskModel] = field(default_factory=list)


@dataclass(slots=True)
class OrchestrationPlan:
	"""Single-call LLM response: routing + task breakdown + conversation title in one shot."""

	status: str = "no_match"
	conversation_title: str = ""
	context_summary: str = ""
	reasoning: str = ""
	# unambiguous path
	selected_agent_id: str = ""
	tasks: list[ParsedSharedStateTaskModel] = field(default_factory=list)
	# ambiguous path
	candidate_agent_ids: list[str] = field(default_factory=list)
	clarification_question: str = ""
	# no_match path
	fallback_message: str = ""
	# direct_answer path (greeting / thanks / meta / capability question)
	direct_message: str = ""
